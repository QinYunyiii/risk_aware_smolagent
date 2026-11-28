#!/usr/bin/env python3
"""One fixed paired Train OOF experiment; Python -I -S -B only."""
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
        module = importlib.util.module_from_spec(spec);sys.modules[name] = module
        spec.loader.exec_module(module);return module
    access = direct("_oof_access", "src/risk_agent/prediction/train_only_access.py")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest-sha256", required=True)
    p.add_argument("--mode", choices=("runtime-preflight", "data-preflight", "compare"), default="data-preflight")
    p.add_argument("--run-id")
    args = p.parse_args(argv)
    manifest_path = root / "docs/phase2/p2_d4_day4_paired_oof_manifest_v1.json"
    # Only fixed repo contract metadata is read during trusted bootstrap. All
    # datasets remain unopened until the guard is installed and inputs enrolled.
    raw = manifest_path.read_bytes()
    if access.sha256(raw) != args.manifest_sha256:raise ValueError("Execution Manifest hash mismatch")
    execution = json.loads(raw)
    config_path = root / "configs/p2_d4_day4_paired_oof_v1.json"
    raw_config = config_path.read_bytes()
    if access.sha256(raw_config) != execution["files"][str(config_path.relative_to(root))]:raise ValueError("Config hash mismatch")
    config = json.loads(raw_config)
    if execution["scope"] != "approved_paired_train_oof_only":raise ValueError("Wrong experiment scope")
    run_id = args.run_id or (config["planned_comparison_run_id"] if args.mode == "compare" else "oof_preflight_" + uuid.uuid4().hex[:12])
    if not run_id.replace("_", "").isalnum():raise ValueError("Invalid Run ID")
    if args.mode == "compare" and run_id != config["planned_comparison_run_id"]:raise ValueError("Only frozen one-time comparison Run ID allowed")
    if args.mode != "compare" and run_id == config["planned_comparison_run_id"]:raise ValueError("Preflight cannot consume comparison directory")
    status = subprocess.check_output(["git", "status", "--short"], cwd=root).decode()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip()
    if args.mode == "compare" and status:raise RuntimeError("Comparison requires a clean committed implementation")
    output = access.strict_path(root / "runs/phase2_p2_d4_day4" / run_id)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    history = {str(f.relative_to(root)): [f.stat().st_size, f.stat().st_mtime_ns]
        for f in (root / "runs").rglob("*") if f.is_file() and not f.is_relative_to(output)}
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = str(config["numerical_threads"])
    for name in tuple(os.environ):
        if any(word in name.upper() for word in ("API_KEY", "TOKEN", "PASSWORD", "SECRET")):del os.environ[name]
    extra = [root / n for n in ("scripts/compare_train_features_v2.py", "src/risk_agent/prediction/paired_train_oof.py",
        "src/risk_agent/prediction/train_oof_inputs.py", "src/risk_agent/prediction/train_oof_runtime.py")]
    support = access.support_inventory(root, extra, output / "absent_import_cache")
    numeric_runtime = direct("_oof_numeric_runtime", "src/risk_agent/prediction/train_oof_runtime.py")
    try:
        support.update(numeric_runtime.numerical_support(root, config, access))
    except Exception as exc:
        failure = {"gate": "BLOCKED", "stage": "runtime_support_bootstrap", "formal_fit_invocations": 0,
            "error": {"type": type(exc).__name__, "message": str(exc)}, "commit": commit}
        data = (json.dumps(failure, indent=2) + "\n").encode()
        with (output / "bootstrap_failure.json").open("xb") as f:f.write(data)
        with (output / "artifact_manifest.json").open("x") as f:json.dump({"files": [{"path": "bootstrap_failure.json",
            "size_bytes": len(data), "sha256": access.sha256(data)}]}, f, indent=2)
        print(json.dumps(failure));return 2
    names = {"fit_claim.json", "summary.json", "oof_predictions.json", "fold_models.json", "bootstrap_draws.json",
        "source_proof.json", "access_policy.json", "access_audit.json", "integrity.json", "run_context.json", "artifact_manifest.json"}
    names |= {f"fit_{fold}_{arm}_{stage}.json" for fold in range(1, 4) for arm in ("v1", "v2") for stage in ("started", "completed")}
    policy = numeric_runtime.runtime_policy_class(access, [n for n, h in support.items() if h is None])(
        output=output, support=support, outputs={str(output / n) for n in names})
    policy.enroll(manifest_path, args.manifest_sha256)
    policy.install()
    documents, live_receipts, fit_events = {}, {}, []
    error = None
    result = {"gate": "BLOCKED"}
    try:
        execution = policy.json(manifest_path)
        for relative, expected in execution["files"].items():
            path = access.strict_path(root / relative)
            if not path.is_relative_to(root):raise ValueError("Escaped experiment dependency")
            if str(path) not in support:policy.enroll(path, expected)
            if access.sha256(path.read_bytes()) != expected:raise ValueError("Experiment dependency drift")
        config = policy.json(config_path)
        profile = policy.json(root / "configs/p2_d4_day1_access_v1.json")
        runtime = direct("_oof_pure_runtime", "src/risk_agent/prediction/train_day1_runtime.py")
        runtime.prepare_runtime(root, profile["frozen_reuse_sha256"], access)
        from risk_agent.prediction.train_oof_inputs import check_config, enroll_and_load
        check_config(config, policy.json(root / execution["approval_path"]))
        sys.path.append(str(root / ".venv/lib/python3.12/site-packages"))
        import numpy as np
        import scipy, sklearn, joblib
        versions = {"numpy": np.__version__, "scipy": scipy.__version__, "sklearn": sklearn.__version__, "joblib": joblib.__version__}
        if versions != config["versions"]:raise ValueError("Numerical runtime version drift")
        from risk_agent.prediction.paired_train_oof import paired_oof, folds_for, paired_bootstrap, evaluation, METRICS
        if args.mode == "runtime-preflight":
            from sklearn.linear_model import LogisticRegression
            # Exercise lazy numerical-runtime reads using four synthetic rows,
            # before opening any formal Train data or consuming the fit claim.
            LogisticRegression(C=.1, l1_ratio=0.0, solver="lbfgs", max_iter=2000, random_state=20261128).fit(
                np.array([[0., 0.], [1., 0.], [0., 1.], [1., 1.]]), np.array([0, 1, 0, 1]))
            policy.seal();result = {"gate": "PREFLIGHT_PASS", "formal_fit_invocations": 0, "versions": versions}
            result["synthetic_runtime_fit_invocations"] = 1
        else:
            rows, proof = enroll_and_load(policy, root, profile, config)
            documents["source_proof.json"] = proof
            if args.mode == "data-preflight":
                splits = folds_for(rows, config["folds"])
                result = {"gate": "PREFLIGHT_PASS", "formal_fit_invocations": 0, "cohort": proof["cohort"],
                    "folds": [{"fit_n": len(a), "held_n": len(b), "fit_failures": sum(rows[i]["y_failure"] for i in a),
                        "held_failures": sum(rows[i]["y_failure"] for i in b)} for a, b in splits], "versions": versions}
            else:
                claim = {"run_id": run_id, "manifest_sha256": args.manifest_sha256, "commit": commit,
                    "ordered_rows_sha256": proof["ordered_rows_sha256"], "maximum_fits": 6, "retries": 0}
                policy.write_json("fit_claim.json", claim);live_receipts["fit_claim.json"] = claim
                def notify(fold, arm, stage, detail):
                    value = {"fold": fold, "arm": arm, "stage": stage, "detail": detail}
                    fit_events.append(value);name = f"fit_{fold}_{arm}_{stage}.json"
                    policy.write_json(name, value);live_receipts[name] = value
                predictions, folds = paired_oof(rows, config, notify)
                documents["fold_models.json"] = folds
                if not folds["complete_predictions"]:
                    result = {"gate": "CONDITIONAL_PASS", "reason": "OOF not estimable in all folds", "fold_audit": folds}
                else:
                    labels = [r["y_failure"] for r in rows]
                    metrics = {arm: evaluation(labels, predictions[arm]) for arm in ("v1", "v2")}
                    boot, draws = paired_bootstrap(rows, predictions, resamples=config["bootstrap"]["resamples"], seed=config["bootstrap"]["random_seed"])
                    documents["bootstrap_draws.json"] = draws
                    assigned = {i: f["fold"] for f in folds["folds"] for i in f["heldout_indices"]}
                    documents["oof_predictions.json"] = [{"task_id": r["task_id"], "template_id": r["template_id"],
                        "risk_record_sha256": r["risk_record_sha256"], "sidecar_record_sha256": r["sidecar_record_sha256"],
                        "y_failure": r["y_failure"], "fold": assigned[i], "v1": float(predictions["v1"][i]),
                        "v2": float(predictions["v2"][i])} for i, r in enumerate(rows)]
                    result = {"gate": "PASS", "study_scope": "exploratory_paired_train_oof_only", "cohort": proof["cohort"],
                        "metrics": metrics, "delta_v2_minus_v1": {m: metrics["v2"][m] - metrics["v1"][m]
                            if metrics["v2"][m] is not None and metrics["v1"][m] is not None else None for m in METRICS},
                        "bootstrap": boot, "formal_fit_invocations": folds["fit_invocations"],
                        "changed_probability_rows": int((np.abs(predictions["v2"] - predictions["v1"]) > 1e-12).sum()),
                        "max_probability_difference": float(np.max(np.abs(predictions["v2"] - predictions["v1"]))),
                        "versions": versions, "validation_test_access": False, "threshold_search": False,
                        "hyperparameter_search": False, "full_train_final_fit": False, "lightgbm_fitted": False}
        for name in policy.inputs:policy.verify(Path(name))
        policy.assert_clean()
    except Exception as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
        result = {"gate": "BLOCKED", "error": error, "fit_events": fit_events}
    changed = [n for n, old in history.items() if not (root / n).is_file()
        or [(root / n).stat().st_size, (root / n).stat().st_mtime_ns] != old]
    if changed or policy.events:result["gate"] = "BLOCKED"
    documents.update({"summary.json": result, "access_policy.json": {"inputs": dict(policy.inputs), "support": dict(policy.support),
        "system_role": config["runtime_system_read"], "outputs": sorted(policy.outputs)},
        "access_audit.json": {"blocked_events": policy.events, "read_counts": policy.reads},
        "integrity.json": {"historical_files_metadata_checked": len(history), "historical_metadata_changes": changed,
            "authorized_inputs_sha256_reverified": len(policy.inputs), "other_history_hashed": False},
        "run_context.json": {"run_id": run_id, "mode": args.mode, "implementation_commit": commit,
            "initial_git_status": status, "manifest_sha256": args.manifest_sha256, "argv": sys.argv}})
    for name, value in documents.items():policy.write_json(name, value)
    all_outputs = {**live_receipts, **documents}
    entries = []
    for name, value in all_outputs.items():
        data = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()
        entries.append({"path": name, "size_bytes": len(data), "sha256": access.sha256(data)})
    policy.write_json("artifact_manifest.json", {"files": entries})
    print(json.dumps({"run_id": run_id, "gate": result["gate"], "error": error,
        "metrics": result.get("metrics"), "fit_events": len(fit_events), "blocked_events": policy.events}, ensure_ascii=False))
    return 0 if result["gate"] in {"PASS", "PREFLIGHT_PASS", "CONDITIONAL_PASS"} else 2


if __name__ == "__main__":raise SystemExit(main())
