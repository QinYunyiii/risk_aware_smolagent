"""P2-D4 Day 1 qualification only; never load branch outcomes or fit models."""
from __future__ import annotations

import json
from pathlib import Path
import re

from .train_only_access import ReadPolicy, strict_path


def enroll_train(policy: ReadPolicy, root: Path, profile: dict) -> dict:
    from .train_evidence import source_directory, permitted_member
    proof_path = strict_path(root / profile["train_proof"])
    if not proof_path.is_relative_to(root / "runs") or proof_path.name != "train_proof.json":
        raise ValueError("Not a dedicated Train proof")
    policy.enroll(proof_path, profile["train_proof_sha256"])
    proof = policy.json(proof_path)
    indices, repeats, task_paths = {}, set(), []
    for spec in proof["runs"]:
        base = source_directory(root, Path(spec["directory"]))
        if spec["run_id"] in indices:
            raise ValueError("Duplicate source Run")
        manifest = strict_path(base / spec["artifact_manifest"])
        if not manifest.is_relative_to(base):
            raise ValueError("Manifest outside Train root")
        policy.enroll(manifest, spec["artifact_manifest_sha256"])
        entries = policy.json(manifest)["files"]
        index = {}
        for item in entries:
            path = strict_path(base / item["path"])
            if not path.is_relative_to(base) or item["path"] in index:
                raise ValueError("Invalid or duplicate manifest member")
            index[item["path"]] = item
            match = re.fullmatch(r"continue/branches/(cp_[a-f0-9]+)/continue_\1_attempt_002/outcome\.json", item["path"])
            if match:
                repeats.add((spec["run_id"], match[1]))
        def register(name):
            if name not in index:
                raise ValueError("Required Train metadata absent from Manifest")
            policy.enroll(base / name, index[name]["sha256"])
        for name in ("run_manifest.json", "records/risk_candidates.jsonl", "records/checkpoints.jsonl", "records/tasks.jsonl"):
            register(name)
        identity = policy.json(base / "run_manifest.json")
        if (identity.get("authorized_split") != "train" or identity.get("run_id") != spec["run_id"]
                or identity.get("formal_experiment") is not True or identity.get("offline_fixture") is not False):
            raise ValueError("Source is not actual formal Train")
        indices[spec["run_id"]] = base, index
        task_paths.append(base / "records/tasks.jsonl")
    release_path = strict_path(proof["release_path"])
    owners = [(b, i) for b, i in indices.values() if release_path.is_relative_to(b)]
    if len(owners) != 1:
        raise ValueError("Eligibility release has no unique Train owner")
    base, index = owners[0]
    relative = str(release_path.relative_to(base))
    if relative != "train_eligibility_release.json" or index[relative]["sha256"] != proof["release_sha256"]:
        raise ValueError("Train release is not Manifest-bound")
    policy.enroll(release_path, proof["release_sha256"])
    release = policy.json(release_path)
    if release.get("split") != "train" or release.get("research_dataset_eligible") is not True:
        raise ValueError("Train release not qualified")
    metadata = {}
    for rid, (base, _) in indices.items():
        risks = {r["record_sha256"]: r for r in json_lines(policy, base / "records/risk_candidates.jsonl")}
        cps = {r["record_sha256"]: r for r in json_lines(policy, base / "records/checkpoints.jsonl")}
        metadata[rid] = risks, cps
    for ref in release["risk_record_references"]:
        rid = ref["run_id"]
        risks, cps = metadata[rid]
        risk = risks[ref["record_sha256"]]
        cp = cps[risk["checkpoint_record_sha256"]]
        if (risk["task_id"] != ref["task_id"] or risk["split"] != "train" or cp["split"] != "train"):
            raise ValueError("Qualification record identity mismatch")
        base, index = indices[rid]
        task, prefix = risk["task_id"], cp["checkpoint_ref"]
        members = [prefix + "/agent/agent_state.json", prefix + "/runtime/budget_state.json",
            prefix + "/runtime/execution_position.json", risk["feature_provenance_ref"],
            f"tasks/{task}/source_model_requests.json", f"tasks/{task}/source_tools.jsonl"]
        for name in members:
            if not permitted_member(name):
                raise ValueError("Qualification points outside public decision-time artifacts")
            path = strict_path(base / name)
            if not path.is_relative_to(base):
                raise ValueError("Train source escape")
            policy.enroll(path, index[name]["sha256"])
    return {"proof_path": proof_path, "release": release, "task_paths": task_paths,
        "repeat_refs": sorted(repeats), "manifest_entries": sum(len(i) for _, i in indices.values())}


def json_lines(policy: ReadPolicy, path: Path) -> list[dict]:
    return [json.loads(s) for s in policy.verify(path).splitlines() if s]


def run_day1(policy: ReadPolicy, root: Path, profile: dict) -> dict:
    enrollment = enroll_train(policy, root, profile)
    policy.seal()
    from .train_evidence import load_train, canonical
    rows, audit = load_train(root, enrollment["proof_path"], profile["train_proof_sha256"])
    tasks, seen = [], set()
    for path in enrollment["task_paths"]:
        previous = None
        for record in json_lines(policy, path):
            if (record["previous_record_sha256"] != previous or record["split"] != "train"
                    or canonical({k: v for k, v in record.items() if k != "record_sha256"}) != record["record_sha256"]
                    or record["source_attempt_index"] != 1 or record["task_id"] in seen):
                raise ValueError("Train task attempt chain or identity mismatch")
            previous = record["record_sha256"]
            seen.add(record["task_id"])
            tasks.append(record["task_id"])
    censored = set(audit["censored_task_ids"])
    qualified = {r["task_id"] for r in rows}
    if censored & qualified or censored | qualified != set(tasks):
        raise ValueError("Censoring and qualification do not partition Source attempts")
    cp_refs = {(r["payload"]["agent"]["step_number"], r["checkpoint_id"]) for r in rows}
    if any((1, cp) not in cp_refs for _, cp in enrollment["repeat_refs"]):
        raise ValueError("Repeat manifest branch lacks qualified Checkpoint")
    observed = {"source_attempts": len(tasks), "qualified_primaries": len(rows),
        "successes": sum(r["y_failure"] == 0 for r in rows), "failures": sum(r["y_failure"] for r in rows),
        "templates": len({r["template_id"] for r in rows}), "censored": len(censored),
        "repeat_manifest_branches": len(enrollment["repeat_refs"])}
    if observed != profile["expected"] or enrollment["release"].get("repeats_are_not_additional_independent_examples") is not True:
        raise ValueError("Current Train differs from reviewed baseline")
    for name in policy.inputs:
        policy.verify(Path(name))
    policy.assert_clean()
    return {"gate": "PASS", "baseline": observed, "train_audit": audit,
        "repeat_evidence": {"source": "trusted Manifest entry paths only; no branch content opened",
            "label_disagreements_historical": profile["historical_repeat_label_disagreements"],
            "label_disagreements_recomputed": False, "branch_validity_recomputed": False,
            "historical_claim": profile["repeat_label_evidence"], "repeat_refs": enrollment["repeat_refs"]},
        "hash_scope": profile["integrity_scope"], "authorized_inputs_sha256_verified": len(policy.inputs),
        "all_18_features_recomputed": True, "validation_test_future_hidden_reads": 0,
        "formal_fit_count": 0, "new_collection_count": 0}
