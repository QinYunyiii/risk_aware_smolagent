#!/usr/bin/env python3
"""Offline-only fixed formal baseline; defaults to preflight without fitting."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys

from risk_agent.collection.formal_contract import write_once_json
from risk_agent.collection.day5_gate import sha256_file
from risk_agent.benchmark.schema import validate_identifier
from risk_agent.prediction.formal_baseline_run import CONFIG, MANIFEST, load_contract, load_snapshot, run_baseline


def main(argv=None):
    try:
        from scripts.run_frozen_continue_acceptance import _offline_guard
    except ModuleNotFoundError:
        from run_frozen_continue_acceptance import _offline_guard
    _offline_guard()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--mode", choices=("local-preflight", "fit"), default="local-preflight")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-root", type=Path, default=Path("runs/phase2_formal_baseline"))
    parser.add_argument("--authorize-fit", action="store_true")
    parser.add_argument("--acceptance-receipt", type=Path)
    parser.add_argument("--plot-python", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    config, manifest = load_contract(root)
    validate_identifier(args.run_id, "run_id")
    if args.mode == "fit":
        if not args.authorize_fit or not args.acceptance_receipt or not args.plot_python:
            raise ValueError("Fixed formal fit requires authorization, local receipt and isolated plot interpreter")
        receipt = json.loads(args.acceptance_receipt.read_text())
        if (receipt.get("passed") is not True or receipt.get("formal_model_fitted") is not False
                or receipt.get("manifest_sha256") != sha256_file(root / MANIFEST)):
            raise ValueError("Local qualification receipt mismatch")
        if subprocess.check_output(["git", "status", "--short"], cwd=root, text=True).strip():
            raise ValueError("Formal fitting requires clean committed code")
    train, val, audit = load_snapshot(root, args.snapshot)
    directory = (root / args.output_root / args.run_id).resolve()
    if not directory.is_relative_to(root / "runs"):
        raise ValueError("Model artifacts must remain in local run storage")
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    write_once_json(directory / "run_manifest.json", {"run_id": args.run_id,
        "mode": args.mode, "snapshot_manifest_sha256": sha256_file(args.snapshot / "artifact_manifest.json"),
        "qualified_row_digest": audit["qualified_row_digest"], "baseline_manifest_sha256": sha256_file(root / MANIFEST),
        "config_sha256": sha256_file(root / CONFIG), "git_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "versions": {name: importlib.metadata.version(name) for name in ("numpy", "scikit-learn", "scipy", "joblib")},
        "test_accessed": False, "lightgbm_enabled": False})
    summary = {"passed": False, "gate": "FORMAL_BASELINE_BLOCKED", "formal_model_fitted": False}
    try:
        if args.mode == "local-preflight":
            summary.update(passed=True, gate="FORMAL_BASELINE_PREFLIGHT_PASS", manifest_sha256=sha256_file(root / MANIFEST))
        else:
            claim = root / "runs/phase2_formal_baseline_control" / sha256_file(root / MANIFEST) / "fit_claim.json"
            write_once_json(claim, {"run_id": args.run_id, "snapshot_manifest_sha256": sha256_file(args.snapshot / "artifact_manifest.json")})
            evaluation = run_baseline(train, val, config, directory)
            summary["formal_model_fitted"] = True
            environment = {**os.environ, "MPLCONFIGDIR": str(directory / "plot_cache")}
            with (directory / "plot.stdout.log").open("xb") as out, (directory / "plot.stderr.log").open("xb") as err:
                subprocess.run([str(args.plot_python), str(root / "scripts/render_formal_reliability.py"), str(directory)],
                    stdout=out, stderr=err, env=environment, check=True, timeout=60)
            summary.update(passed=True, gate="FORMAL_LOGISTIC_BASELINE_PASS", test_accessed=False,
                lightgbm_fitted=False, validation=evaluation["validation"], train_oof=evaluation["train_oof"],
                serialization_predictions_exact=evaluation["serialization_predictions_exact"])
    except Exception as exc:
        summary.update(error_type=type(exc).__name__, error_message=str(exc))
    finally:
        write_once_json(directory / "summary.json", summary)
        write_once_json(directory / "artifact_manifest.json", {"files": [
            {"path": str(f.relative_to(directory)), "size_bytes": f.stat().st_size, "sha256": sha256_file(f)}
            for f in sorted(directory.rglob("*")) if f.is_file()]})
    print(json.dumps(summary, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
