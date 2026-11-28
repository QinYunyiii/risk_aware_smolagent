#!/usr/bin/env python3
"""Approved Day 3 offline Train sidecars; run with Python -I -S -B."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


def main(argv=None):
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        raise RuntimeError("Use Python -I -S -B")
    root = Path(__file__).resolve().parents[1]
    def direct(name, relative):
        spec = importlib.util.spec_from_file_location(name, root / relative)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    access = direct("_backfill_access", "src/risk_agent/prediction/train_only_access.py")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--execution-manifest-sha256", required=True)
    p.add_argument("--run-id")
    p.add_argument("--runtime-preflight", action="store_true")
    args = p.parse_args(argv)
    run_id = args.run_id or "p2_d4_day3_" + uuid.uuid4().hex[:12]
    if not run_id.replace("_", "").isalnum():
        raise ValueError("Invalid Run ID")
    status = subprocess.check_output(["git", "status", "--short"], cwd=root).decode()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip()
    if status and not args.runtime_preflight:
        raise RuntimeError("Backfill must use a clean implementation Commit")
    output = access.strict_path(root / "runs/phase2_p2_d4_day3" / run_id)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    history = {str(f.relative_to(root)): [f.stat().st_size, f.stat().st_mtime_ns]
        for f in (root / "runs").rglob("*") if f.is_file() and not f.is_relative_to(output)}
    extra = [root / n for n in ("scripts/backfill_train_features_v2.py",
        "src/risk_agent/prediction/train_feature_backfill_v2.py")]
    support = access.support_inventory(root, extra, output / "absent_import_cache")
    names = {"feature_sidecars.json", "summary.json", "source_proof.json", "feature_eligibility_release.json",
        "access_policy.json", "access_audit.json", "integrity.json", "run_context.json", "artifact_manifest.json"}
    policy = access.ReadPolicy(output=output, support=support, outputs={str(output / n) for n in names})
    manifest_path = root / "docs/phase2/formal_train_features_v2_backfill_manifest_v1.json"
    policy.enroll(manifest_path, args.execution_manifest_sha256)
    for name in tuple(os.environ):
        if any(word in name.upper() for word in ("API_KEY", "TOKEN", "PASSWORD", "SECRET")):
            del os.environ[name]
    policy.install()
    documents = {}
    result = {"gate": "BLOCKED"}
    error = None
    try:
        execution = policy.json(manifest_path)
        if execution["scope"] != "approved_day3_offline_train_backfill_only":
            raise ValueError("Wrong execution scope")
        for relative, expected in execution["files"].items():
            path = access.strict_path(root / relative)
            if not path.is_relative_to(root):
                raise ValueError("Execution dependency escaped project")
            if str(path) not in policy.support:
                policy.enroll(path, expected)
            if access.sha256(path.read_bytes()) != expected:
                raise ValueError("Execution dependency Hash mismatch")
        profile = policy.json(root / "configs/p2_d4_day1_access_v1.json")
        runtime = direct("_backfill_pure_runtime", "src/risk_agent/prediction/train_day1_runtime.py")
        runtime.prepare_runtime(root, profile["frozen_reuse_sha256"], access)
        from risk_agent.prediction.train_public_signals_v2 import load_candidate
        from risk_agent.prediction.train_feature_backfill_v2 import check_approval, build_sidecars
        contract = load_candidate(root, expected_manifest_sha256=execution["candidate_manifest_sha256"])
        approval_path = root / execution["approval_path"]
        check_approval(policy.json(approval_path), contract.manifest_sha256)
        if args.runtime_preflight:
            policy.seal()
            policy.assert_clean()
            result = {"gate": "PREFLIGHT_PASS", "train_rows_loaded": 0, "sidecars": 0}
        else:
            from risk_agent.prediction.train_day1_audit import enroll_train
            from risk_agent.prediction.train_evidence import load_train
            enrollment = enroll_train(policy, root, profile)
            policy.seal()
            rows, source_proof = load_train(root, enrollment["proof_path"], profile["train_proof_sha256"])
            rows = [{**row, "split": "train"} for row in rows]
            config = policy.json(root / "configs/formal_decision_features_v2_candidate.json")
            extractor_file = "src/risk_agent/prediction/train_feature_backfill_v2.py"
            sidecars, result = build_sidecars(rows, contract=contract,
                extractor_sha256=execution["files"][extractor_file],
                execution_manifest_sha256=args.execution_manifest_sha256,
                approval_sha256=execution["files"][execution["approval_path"]],
                targets=config["historical_backfill_targets_not_executed_today"])
            documents["feature_sidecars.json"] = sidecars
            documents["source_proof.json"] = source_proof
            result.update(train_templates=len({r["template_id"] for r in rows}),
                censored_task_ids=source_proof["censored_task_ids"], repeated_branches_included=0,
                future_hidden_validation_test_reads=0, model_fits=0, agent_runs=0, qwen_requests=0)
        for name in policy.inputs:
            policy.verify(Path(name))
        policy.assert_clean()
    except Exception as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
        result = {"gate": "BLOCKED", "error": error}
    changed = [n for n, old in history.items() if not (root / n).is_file()
        or [(root / n).stat().st_size, (root / n).stat().st_mtime_ns] != old]
    if changed or policy.events:
        result["gate"] = "BLOCKED"
    if result["gate"] == "PASS":
        documents["feature_eligibility_release.json"] = {
            "scope": "qualified_historical_train_v2_sidecars_only", "run_id": run_id,
            "candidate_manifest_sha256": contract.manifest_sha256,
            "execution_manifest_sha256": args.execution_manifest_sha256,
            "complete_v2_sidecars_qualified": True, "model_comparison_authorized": False,
            "production_activation_authorized": False,
            "sidecar_record_references": [s["sidecar_record_sha256"] for s in sidecars]}
    documents.update({"summary.json": result,
        "access_policy.json": {"sealed": policy.sealed, "inputs": dict(policy.inputs), "support": dict(policy.support),
            "declared_outputs": sorted(policy.outputs)},
        "access_audit.json": {"blocked_events": policy.events, "allowed_read_counts": policy.reads},
        "integrity.json": {"historical_files_metadata_checked": len(history), "historical_metadata_changes": changed,
            "other_history_hashed": False, "authorized_inputs_sha256_reverified": len(policy.inputs)},
        "run_context.json": {"run_id": run_id, "implementation_commit": commit, "initial_git_status": status,
            "argv": sys.argv, "execution_manifest_sha256": args.execution_manifest_sha256,
            "mode": "runtime_preflight" if args.runtime_preflight else "approved_offline_backfill"}})
    for name, value in documents.items():
        policy.write_json(name, value)
    entries = []
    for name, value in documents.items():
        data = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()
        entries.append({"path": name, "size_bytes": len(data), "sha256": access.sha256(data)})
    policy.write_json("artifact_manifest.json", {"files": entries})
    print(json.dumps({"run_id": run_id, "gate": result["gate"], "observed": result.get("observed"),
        "error": error, "blocked_events": policy.events, "output": str(output)}, ensure_ascii=False))
    return 0 if result["gate"] in {"PASS", "PREFLIGHT_PASS"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
