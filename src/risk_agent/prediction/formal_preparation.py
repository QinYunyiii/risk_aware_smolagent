"""Read-only qualification and feature export. No model fitting or Test input."""
from __future__ import annotations

from collections import Counter
import json
import math
from pathlib import Path
from typing import Any

from risk_agent.agent.agent_state_adapter import canonical_hash
from risk_agent.collection.aggregate_target_contract import TARGET_MANIFEST, TARGET_CONFIG, load_aggregate_contract
from risk_agent.collection.day5_gate import sha256_file
from risk_agent.collection.formal_contract import read_records
from risk_agent.collection.formal_features import NAMES, extract_decision_features
from risk_agent.collection.validation_prerequisite import verify_train_prerequisite

ERROR_TYPES = ("none", "no_progress", "timeout", "unavailable", "invalid_argument", "conflict", "other")


def feature_projection(row: dict[str, Any]) -> dict[str, Any]:
    """Select the fixed 18 values, never metadata, outcomes or fault truth."""
    if tuple(row["feature_names"]) != NAMES or set(row["features"]) != set(NAMES):
        raise ValueError("Formal feature allowlist mismatch")
    result = {name: row["features"][name] for name in NAMES}
    if result["latest_observed_error_type"] not in ERROR_TYPES:
        raise ValueError("Unknown public error category")
    for name, value in result.items():
        if name == "latest_observed_error_type" or value is None:
            continue
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("Feature is not a finite number or explicit null")
    if canonical_hash(result) != row["features_sha256"]:
        raise ValueError("Feature value hash mismatch")
    return result


def _safe(root: Path, path: Path) -> Path:
    path = path.resolve()
    if not path.is_relative_to((root / "runs").resolve()):
        raise ValueError("Proof source outside run storage")
    return path


def _index(root: Path, spec: dict[str, Any], split: str) -> tuple[Path, dict[str, str]]:
    if split not in ("train", "validation"):
        raise ValueError("Test input is sealed")
    directory = _safe(root, Path(spec["directory"]))
    identity = json.loads((directory / "run_manifest.json").read_text())
    # This check precedes opening outcomes and even parsing the artifact manifest.
    if (identity["authorized_split"] != split or identity["run_id"] != spec["run_id"]
            or identity.get("formal_experiment") is not True or identity.get("offline_fixture") is not False):
        raise ValueError("Only actual qualified Train/Validation sources are allowed")
    path = (directory / spec["manifest"]).resolve()
    if not path.is_relative_to(directory) or sha256_file(path) != spec["manifest_sha256"]:
        raise ValueError("Trusted artifact manifest mismatch")
    index = {}
    for item in json.loads(path.read_text())["files"]:
        member = (directory / item["path"]).resolve()
        if (not member.is_relative_to(directory) or item["path"] in index
                or member.stat().st_size != item["size_bytes"] or sha256_file(member) != item["sha256"]):
            raise ValueError("Artifact integrity or path mismatch")
        index[item["path"]] = item["sha256"]
    required = {"run_manifest.json", *[f"records/{kind}.jsonl" for kind in
                ("tasks", "checkpoints", "continues", "risk_candidates")]}
    if not required <= index.keys():
        raise ValueError("Source manifest omits canonical records")
    return directory, index


def _json(directory: Path, index: dict[str, str], name: str) -> Any:
    if name not in index:
        raise ValueError("Decision evidence is not manifest-bound")
    return json.loads((directory / name).read_text())


def _select(directory: Path, index: dict[str, str], hashes: list[str], split: str,
            tasks: dict[str, Any]) -> list[dict[str, Any]]:
    if len(set(hashes)) != len(hashes):
        raise ValueError("Duplicate eligibility reference")
    rows = {r["record_sha256"]: r for r in read_records(directory / "records/risk_candidates.jsonl")}
    checkpoints = {r["record_sha256"]: r for r in read_records(directory / "records/checkpoints.jsonl")}
    outcomes = {r["record_sha256"]: r for r in read_records(directory / "records/continues.jsonl")}
    selected = []
    for digest in hashes:
        row = rows[digest]
        task = tasks.get(row["task_id"])
        if (row["split"] != split or not row["formal_experiment"] or row["offline_fixture"]
                or row["execution_mode"] != "model_backed_formal" or row["controller_type"] != "qwen3_model"
                or task is None or task.split.value != split or task.template_id != row["template_id"]
                or type(row["label"]) is not int or row["label"] not in (0, 1)
                or row["future_data_excluded"] is not True):
            raise ValueError("Unqualified, null, fixture or wrong-split risk row")
        identity = _json(directory, index, "run_manifest.json")
        if row["run_id"] != identity["run_id"]:
            raise ValueError("Risk record belongs to another source Run")
        cp, outcome = checkpoints[row["checkpoint_record_sha256"]], outcomes[row["outcome_record_sha256"]]
        if (cp["task_id"] != row["task_id"] or outcome["task_id"] != row["task_id"]
                or cp["split"] != split or outcome["split"] != split
                or cp["checkpoint_id"] != row["checkpoint_id"] or outcome["checkpoint_id"] != row["checkpoint_id"]
                or cp["run_id"] != row["run_id"] or outcome["run_id"] != row["run_id"]
                or cp["experiment_id"] != row["experiment_id"] or outcome["experiment_id"] != row["experiment_id"]
                or outcome["branch_kind"] != "primary" or outcome["y_continue"] != row["label"]
                or outcome["continue_outcome_id"] != row["continue_outcome_id"] or not outcome["complete_chain"]):
            raise ValueError("Risk/Checkpoint/primary outcome lineage mismatch")
        values = feature_projection(row)
        packet = _json(directory, index, row["feature_provenance_ref"])
        if packet["values"] != values or canonical_hash(packet["provenance"]) != row["provenance_sha256"]:
            raise ValueError("Feature provenance mismatch")
        task_root = f"tasks/{row['task_id']}"
        source = _json(directory, index, task_root + "/source_result.json")
        snapshot = _json(directory, index, cp["checkpoint_ref"] + "/agent/agent_state.json")["payload"]
        tool_name = task_root + "/source_tools.jsonl"
        if tool_name not in index:
            raise ValueError("Source tool timings not manifest-bound")
        tools = [json.loads(s) for s in (directory / tool_name).read_text().splitlines()]
        computed = extract_decision_features(agent_payload=snapshot, budget=source["source_budget"],
            source_model_records=_json(directory, index, task_root + "/source_model_requests.json"),
            source_tool_records=tools)
        if (computed["values"] != values or computed["features_sha256"] != row["features_sha256"]
                or {k: v for k, v in computed["provenance"].items() if k != "created_at"}
                != {k: v for k, v in packet["provenance"].items() if k != "created_at"}):
            raise ValueError("Source feature recomputation failed")
        result = _json(directory, index, task_root + "/task_result.json")
        if (result["accepted_y_continue"] != row["label"] or not result["engineering_passed"]
                or not result["source_unchanged"] or not result["resources_released"]):
            raise ValueError("Unqualified task result")
        selected.append({"split": split, "run_id": row["run_id"], "task_id": row["task_id"],
            "template_id": row["template_id"], "checkpoint_id": row["checkpoint_id"],
            "y_continue": row["label"], "y_failure": 1 - row["label"], "features": values,
            "risk_record_sha256": digest, "checkpoint_record_sha256": row["checkpoint_record_sha256"],
            "outcome_record_sha256": row["outcome_record_sha256"], "features_sha256": row["features_sha256"],
            "provenance_sha256": row["provenance_sha256"], "implementation_revision": row.get("implementation_revision"),
            "feature_provenance_ref": row["feature_provenance_ref"], "allowed_for_weight_fitting": split == "train"})
    return selected


def audit_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    train = [r for r in rows if r["split"] == "train"]
    val = [r for r in rows if r["split"] == "validation"]
    if len(train) + len(val) != len(rows) or not train or not val:
        raise ValueError("Preparation requires Train and Validation only")
    if len({r["task_id"] for r in rows}) != len(rows) or len({r["checkpoint_id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate primary task or Checkpoint")
    if {r["template_id"] for r in train} & {r["template_id"] for r in val}:
        raise ValueError("Template split leakage")
    constants = [name for name in NAMES if len({json.dumps(r["features"][name], sort_keys=True) for r in train}) == 1]
    def summary(group):
        return {"n": len(group), "successes": sum(r["y_continue"] for r in group),
                "failures": sum(r["y_failure"] for r in group), "templates": len({r["template_id"] for r in group})}
    counts = Counter(r["y_failure"] for r in train)
    sample_gate = len(train) >= 200 and min(counts.get(0, 0), counts.get(1, 0)) >= 30
    return {"train": summary(train), "validation": summary(val), "feature_names": list(NAMES),
        "train_constant_features": constants, "train_nonconstant_features": [n for n in NAMES if n not in constants],
        "train_missing_counts": {n: sum(r["features"][n] is None for r in train) for n in NAMES},
        "validation_missing_counts": {n: sum(r["features"][n] is None for r in val) for n in NAMES},
        "lightgbm_sample_count_gate_passed": sample_gate,
        "lightgbm_gate_passed": False,
        "lightgbm_effective_feature_check": "NOT_IMPLEMENTED_IN_PREPARATION_STAGE",
        "logistic_feasibility_sample_gate": len(train) >= 72 and set(counts) == {0, 1},
        "test_outcomes_read": False, "model_fitted": False, "threshold_selected": False,
        "formal_dataset_ready": False}


def prepare_rows(root: Path, proof_path: Path):
    root = root.resolve()
    profile, _, _, cfg, _, _ = load_aggregate_contract(root)
    proof = json.loads(proof_path.read_text())
    train_proof = Path(proof["train_proof_path"])
    if sha256_file(train_proof) != proof["train_proof_sha256"]:
        raise ValueError("Trusted Train proof changed")
    train_audit = verify_train_prerequisite(root, train_proof)
    train_info = json.loads(train_proof.read_text())
    release = json.loads(Path(train_info["release_path"]).read_text())
    tasks = {t.task_id: t for t in profile.tasks}
    rows, verified = [], 0
    for spec in train_info["runs"]:
        source = {**spec, "manifest": spec["artifact_manifest"], "manifest_sha256": spec["artifact_manifest_sha256"]}
        directory, index = _index(root, source, "train")
        hashes = [r["record_sha256"] for r in release["risk_record_references"] if r["run_id"] == spec["run_id"]]
        rows += _select(directory, index, hashes, "train", tasks)
        verified += len(index)
    spec = proof["validation"]
    directory, index = _index(root, spec, "validation")
    if index.get("validation_eligibility_release.json") != spec["release_sha256"]:
        raise ValueError("Validation eligibility is not trusted and manifest-bound")
    val_release = _json(directory, index, "validation_eligibility_release.json")
    final = _json(directory, index, "final_summary.json")
    if (not final["passed"] or not final["quality_passed"] or not final["connection_resources_released"]
            or val_release["run_id"] != spec["run_id"] or val_release["split"] != "validation"
            or val_release["research_dataset_eligible"] is not True or val_release["allowed_for_training"] is not False
            or val_release["aggregate_manifest_sha256"] != sha256_file(root / TARGET_MANIFEST)
            or val_release["policy_sha256"] != sha256_file(root / TARGET_CONFIG)):
        raise ValueError("Validation cohort is not qualified for selection/evaluation")
    selected = _select(directory, index, val_release["risk_candidate_record_hashes"], "validation", tasks)
    if [row["task_id"] for row in selected] != cfg["task_ids"]:
        raise ValueError("Validation cohort is incomplete or reordered")
    rows += selected
    audit = audit_rows(rows)
    audit.update(input_proof_sha256=sha256_file(proof_path), artifact_files_verified=verified + len(index),
                 censored_train_task_ids=train_audit["censored_task_ids"], repeat_branches_included=0,
                 qualified_row_digest=canonical_hash(rows))
    return rows, audit
