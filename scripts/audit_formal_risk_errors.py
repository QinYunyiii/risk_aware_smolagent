#!/usr/bin/env python3
"""Offline evidence-only error and feature audit; cannot fit a model."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np

from risk_agent.collection.formal_contract import write_once_json
from risk_agent.prediction.formal_baseline_run import load_contract, load_snapshot
from risk_agent.prediction.formal_error_audit import describe
from risk_agent.prediction.formal_logistic import FormalLogistic, FormalPreprocessor


def main(argv=None):
    try:
        from scripts.run_frozen_continue_acceptance import _offline_guard
    except ModuleNotFoundError:
        from run_frozen_continue_acceptance import _offline_guard
    _offline_guard()
    def no_fit(*args, **kwargs):
        raise RuntimeError("Error audit prohibits any fitting")
    FormalLogistic.fit = no_fit
    FormalPreprocessor.fit = no_fit
    from sklearn.linear_model import LogisticRegression
    LogisticRegression.fit = no_fit
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--proof", type=Path, required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--output-root", type=Path, default=Path("runs/phase2_formal_error_audit"))
    args = p.parse_args(argv)
    root = args.root.resolve()
    from risk_agent.benchmark.schema import validate_identifier
    validate_identifier(args.run_id, "run_id")
    proof = json.loads(args.proof.read_text())
    run = Path(proof["model_run"]).resolve()
    if not run.is_relative_to(root / "runs"):
        raise ValueError("Model evidence must be in local run storage")
    manifest = run / "artifact_manifest.json"
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != proof["model_manifest_sha256"]:
        raise ValueError("Frozen model artifact manifest changed")
    for item in json.loads(manifest.read_text())["files"]:
        path = (run / item["path"]).resolve()
        if (not path.is_relative_to(run) or path.stat().st_size != item["size_bytes"]
                or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]):
            raise ValueError("Frozen model artifact changed")
    load_contract(root)
    train, val, _ = load_snapshot(root, Path(proof["snapshot"]))
    predictions = [json.loads(s) for s in (run / "predictions.jsonl").read_text().splitlines()]
    result = describe(train + val, predictions)
    model = joblib.load(run / "logistic_regression.joblib")  # Only after trusted artifact verification.
    actual = model.predict_failure(val)
    expected = np.array([p["p_failure"] for p in predictions
                         if p["model"] == "logistic_regression" and p["evaluation_split"] == "validation"])
    if not np.array_equal(actual, expected):
        raise ValueError("Frozen model prediction mismatch")
    model_audit = model.audit()
    names, weights = model_audit["encoded_features"], model_audit["coefficients"][0]
    result["encoded_feature_count"] = model_audit["encoded_feature_count"]
    result["encoded_matrix_rank"] = model_audit["encoded_matrix_rank"]
    result["unseen_category_coefficients"] = {c: weights[names.index("latest_observed_error_type=" + c)]
        for c in result["validation_categories_unseen_in_train"] if "latest_observed_error_type=" + c in names}
    matrix = model.preprocessor.transform(val)
    contributions = []
    for i, row in enumerate(val):
        if row["y_failure"] != 1:
            continue
        terms = matrix[i] * np.array(weights)
        logit = model_audit["intercept"][0] + float(terms.sum())
        if not np.isclose(1 / (1 + np.exp(-logit)), actual[i]):
            raise ValueError("Additive frozen logit reconstruction failed")
        contributions.append({"task_id": row["task_id"], "intercept": model_audit["intercept"][0],
            "terms": dict(zip(names, terms.tolist())), "logit": logit, "p_failure": float(actual[i]),
            "interpretation": "additive algebra only, not causal attribution"})
    result["failure_score_decomposition"] = contributions
    directory = (root / args.output_root / args.run_id).resolve()
    if not directory.is_relative_to(root / "runs"):
        raise ValueError("Audit output must be in local run storage")
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    write_once_json(directory / "error_audit.json", result)
    write_once_json(directory / "artifact_manifest.json", {"files": [
        {"path": f.name, "size_bytes": f.stat().st_size, "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
        for f in directory.iterdir() if f.is_file()]})
    print(json.dumps({key: result[key] for key in (
        "score_ranges", "validation_failure_ranks", "train_only_error_categories", "validation_categories_unseen_in_train",
        "unseen_category_coefficients", "nonconstant_exact_duplicate_columns", "numeric_range_audit")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
