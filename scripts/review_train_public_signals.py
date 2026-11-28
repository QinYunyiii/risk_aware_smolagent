#!/usr/bin/env python3
"""Offline Train-only public signal coverage; no outcomes, fitting or API calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from risk_agent.agent.agent_state_adapter import canonical_hash
from risk_agent.collection.day5_gate import sha256_file
from risk_agent.collection.formal_contract import write_once_json
from risk_agent.collection.formal_features import visible_error
from risk_agent.prediction.formal_preparation import _index, _json, _safe
from risk_agent.prediction.train_public_signals import review_public, summarize


def main(argv=None):
    try:
        from scripts.run_frozen_continue_acceptance import _offline_guard
    except ModuleNotFoundError:
        from run_frozen_continue_acceptance import _offline_guard
    _offline_guard()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--snapshot", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    directory, output = _safe(root, args.snapshot), _safe(root, args.output)
    lock = json.loads((root / "docs/phase2/formal_baseline_preparation_manifest_v1.json").read_text())
    manifest = directory / "artifact_manifest.json"
    if sha256_file(manifest) != lock["snapshot_manifest_sha256"]:
        raise ValueError("Qualified snapshot manifest drift")
    members = {e["path"]: e for e in json.loads(manifest.read_text())["files"]}
    # Deliberately never open validation.jsonl or inspect its rows.
    for name in ("train.jsonl", "source_proof.json"):
        item = members[name]
        if (sha256_file(directory / name) != item["sha256"]
                or (directory / name).stat().st_size != item["size_bytes"]):
            raise ValueError("Train snapshot or qualification proof drift")
    common = json.loads((directory / "source_proof.json").read_text())
    if sha256_file(directory / "source_proof.json") != lock["input_proof_sha256"]:
        raise ValueError("Untrusted preparation proof")
    train_proof = _safe(root, Path(common["train_proof_path"]))
    if sha256_file(train_proof) != common["train_proof_sha256"]:
        raise ValueError("Train qualification proof drift")
    proof = json.loads(train_proof.read_text())
    rows = [json.loads(s) for s in (directory / "train.jsonl").read_text().splitlines()]
    if (len(rows) != lock["rows_by_split"]["train"] or len({r["task_id"] for r in rows}) != len(rows)
            or any(r["split"] != "train" or r["allowed_for_weight_fitting"] is not True for r in rows)):
        raise ValueError("Only qualified unique Train primaries are permitted")
    sources, verified = {}, 0
    for spec in proof["runs"]:
        base, index = _index(root, {**spec, "manifest": spec["artifact_manifest"],
            "manifest_sha256": spec["artifact_manifest_sha256"]}, "train")
        sources[spec["run_id"]] = base, index
        verified += len(index)
    release_path = _safe(root, Path(proof["release_path"]))
    if (sha256_file(release_path) != proof["release_sha256"] or not any(
            release_path.is_relative_to(base) and index.get(str(release_path.relative_to(base))) == proof["release_sha256"]
            for base, index in sources.values())):
        raise ValueError("Train eligibility release is not manifest-bound")
    release = json.loads(release_path.read_text())
    if release["split"] != "train" or release["research_dataset_eligible"] is not True or {
            (r["run_id"], r["task_id"], r["record_sha256"]) for r in release["risk_record_references"]} != {
            (r["run_id"], r["task_id"], r["risk_record_sha256"]) for r in rows}:
        raise ValueError("Train eligibility does not match the frozen cohort")
    checkpoints = {rid: {c["record_sha256"]: c for c in map(json.loads,
        (base / "records/checkpoints.jsonl").read_text().splitlines())} for rid, (base, _) in sources.items()}
    reviews = []
    for row in rows:
        base, index = sources[row["run_id"]]
        cp = checkpoints[row["run_id"]][row["checkpoint_record_sha256"]]
        if cp["split"] != "train" or any(cp[k] != row[k] for k in ("task_id", "checkpoint_id", "run_id")):
            raise ValueError("Train Checkpoint lineage mismatch")
        reference = cp["checkpoint_ref"] + "/agent/agent_state.json"
        payload = _json(base, index, reference)["payload"]
        messages = payload["canonical_next_model_input"]
        if canonical_hash(messages) != payload["next_model_input_hash"]:
            raise ValueError("Public model input hash mismatch")
        calls = [{k: c[k] for k in ("name", "arguments")} for step in payload["memory"]["steps"]
            if step["type"] == "action" for c in (step.get("tool_calls") or [])]
        def text(message):
            content = message["content"]
            return content if isinstance(content, str) else "".join(c.get("text", "") for c in content)
        if not any(payload["agent"]["task"] in text(m) for m in messages if m["role"] == "user"):
            raise ValueError("Task text is not present in captured public input")
        view = {"split": "train", "task_text": payload["agent"]["task"],
            "cutoff_step": payload["agent"]["step_number"], "calls": calls,
            "responses": [text(m) for m in messages if m["role"] == "tool-response"],
            "tool_registry": [{k: t[k] for k in ("name", "description", "inputs", "output_type")}
                              for t in payload["tool_registry"]]}
        # Only public projection crosses the semantic review interface. No labels,
        # task metadata, private state, fault configuration or future trajectory.
        signals = review_public(view)
        reviews.append({"task_id": row["task_id"], "template_id": row["template_id"],
            "checkpoint_id": row["checkpoint_id"], "cutoff_step": view["cutoff_step"],
            "agent_state_ref": str((base / reference).relative_to(root)),
            "agent_state_file_sha256": index[reference], "public_projection_sha256": canonical_hash(view),
            "old_public_error_category": visible_error(view["responses"][-1]), "signals": signals})
    result = summarize(reviews)
    if result["parse_status"].get("UNKNOWN", 0):
        raise ValueError("Unparsed public evidence: cannot publish a passing review")
    result.update(train_artifact_files_verified=verified, snapshot_manifest_sha256=sha256_file(manifest),
        train_proof_sha256=sha256_file(train_proof), raw_labels_read_for_signal_selection=False,
        validation_rows_opened=0, test_rows_opened=0, future_trajectories_parsed=0)
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    write_once_json(output / "summary.json", result)
    write_once_json(output / "public_signal_reviews.json", reviews)
    write_once_json(output / "artifact_manifest.json", {"files": [
        {"path": f.name, "size_bytes": f.stat().st_size, "sha256": sha256_file(f)}
        for f in sorted(output.iterdir()) if f.is_file()]})
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
