"""Fixed grouped CV, final Train fit, and one Validation evaluation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.model_selection import StratifiedGroupKFold
import yaml

from risk_agent.agent.agent_state_adapter import canonical_hash
from risk_agent.collection.day5_gate import sha256_file
from risk_agent.collection.formal_contract import write_once_json
from .formal_preparation import audit_rows, prepare_rows
from .formal_logistic import FormalLogistic
from .formal_metrics import metric_set, calibration_bins, cluster_bootstrap

MANIFEST = "docs/phase2/formal_logistic_manifest_v1.json"
CONFIG = "configs/formal_logistic_baseline_v1.yaml"
PREPARATION = "docs/phase2/formal_baseline_preparation_manifest_v1.json"


def load_contract(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = json.loads((root / MANIFEST).read_text())
    if manifest["parent_preparation_manifest_sha256"] != sha256_file(root / PREPARATION):
        raise ValueError("Preparation parent changed")
    for entry in (manifest, json.loads((root / PREPARATION).read_text())):
        for name, digest in entry["files"].items():
            path = (root / name).resolve()
            if not path.is_relative_to(root.resolve()) or sha256_file(path) != digest:
                raise ValueError(f"Baseline input changed: {name}")
    config = yaml.safe_load((root / CONFIG).read_text())
    candidate = yaml.safe_load((root / "configs/formal_logistic_baseline_candidate_v1.yaml").read_text())
    approved = {**candidate, "status": "APPROVED_FROZEN_BASELINE_V1", "execution_authorized": True}
    if config != approved or config["lightgbm"]["enabled"] or config["decision_threshold"] != 0.5:
        raise ValueError("Only the reviewed fixed candidate is executable")
    return config, manifest


def load_snapshot(root: Path, directory: Path) -> tuple[list, list, dict]:
    directory = directory.resolve()
    if not directory.is_relative_to((root / "runs").resolve()):
        raise ValueError("Qualified snapshot must remain in run storage")
    lock = json.loads((root / PREPARATION).read_text())
    if sha256_file(directory / "artifact_manifest.json") != lock["snapshot_manifest_sha256"]:
        raise ValueError("Snapshot differs from the reviewed dataset")
    index = set()
    for item in json.loads((directory / "artifact_manifest.json").read_text())["files"]:
        path = (directory / item["path"]).resolve()
        if (not path.is_relative_to(directory) or path.stat().st_size != item["size_bytes"]
                or sha256_file(path) != item["sha256"] or item["path"] in index):
            raise ValueError("Snapshot member changed")
        index.add(item["path"])
    if not {"train.jsonl", "validation.jsonl", "source_proof.json", "audit_summary.json"} <= index:
        raise ValueError("Snapshot manifest omits required evidence")
    train = [json.loads(s) for s in (directory / "train.jsonl").read_text().splitlines()]
    val = [json.loads(s) for s in (directory / "validation.jsonl").read_text().splitlines()]
    if (any(r["split"] != "train" or r["allowed_for_weight_fitting"] is not True for r in train)
            or any(r["split"] != "validation" or r["allowed_for_weight_fitting"] is not False for r in val)
            or canonical_hash(train + val) != lock["qualified_row_digest"]):
        raise ValueError("Snapshot row scope or digest changed")
    # Independently revalidate the published eligibility sources before any fit.
    qualified, audit = prepare_rows(root, directory / "source_proof.json")
    if qualified != train + val or audit["input_proof_sha256"] != lock["input_proof_sha256"]:
        raise ValueError("Qualified source records no longer match frozen snapshot")
    return train, val, audit


def grouped_oof(train: list[dict], config: dict) -> tuple[dict, list[dict]]:
    y = np.array([r["y_failure"] for r in train])
    groups = np.array([r["template_id"] for r in train])
    splitter = StratifiedGroupKFold(n_splits=3, shuffle=True, random_state=20261128)
    predictions = {"constant_train_prevalence": np.full(len(train), np.nan),
                   "logistic_regression": np.full(len(train), np.nan)}
    folds = []
    for number, (fit_indices, held_indices) in enumerate(splitter.split(np.zeros((len(train), 1)), y, groups), 1):
        fit, held = [train[i] for i in fit_indices], [train[i] for i in held_indices]
        fit_groups, held_groups = set(groups[fit_indices]), set(groups[held_indices])
        if fit_groups & held_groups:
            raise ValueError("Template crossed CV boundary")
        prior = float(y[fit_indices].mean())
        predictions["constant_train_prevalence"][held_indices] = prior
        report = {"fold": number, "fit_task_ids": [r["task_id"] for r in fit],
                  "heldout_task_ids": [r["task_id"] for r in held], "train_templates": sorted(fit_groups),
                  "heldout_templates": sorted(held_groups), "template_overlap": [],
                  "fit_failures": int(y[fit_indices].sum()), "heldout_failures": int(y[held_indices].sum()),
                  "constant_train_prevalence": prior}
        if len(set(y[fit_indices])) != 2:
            report.update(logistic_status="NOT_ESTIMABLE_ONE_CLASS_TRAIN", logistic_preprocessing=None)
        else:
            model = FormalLogistic().fit(fit, config["models"]["logistic_regression"])
            predictions["logistic_regression"][held_indices] = model.predict_failure(held)
            report.update(logistic_status="FIT", logistic_preprocessing=model.audit())
        folds.append(report)
    return predictions, folds


def run_baseline(train: list[dict], val: list[dict], config: dict, directory: Path) -> dict[str, Any]:
    if (directory / "logistic_regression.joblib").exists() or (directory / "evaluation.json").exists():
        raise FileExistsError("Baseline run artifacts cannot be overwritten")
    data = audit_rows(train + val)
    if not data["logistic_feasibility_sample_gate"]:
        raise ValueError("Logistic feasibility gate failed")
    oof, folds = grouped_oof(train, config)
    model = FormalLogistic().fit(train, config["models"]["logistic_regression"])
    model_path = directory / "logistic_regression.joblib"
    joblib.dump(model, model_path)
    probabilities = model.predict_failure(val)
    reloaded = joblib.load(model_path)  # Only this just-created, owned artifact.
    if not np.array_equal(probabilities, reloaded.predict_failure(val)):
        raise ValueError("Serialized model predictions differ")
    prior = float(np.mean([r["y_failure"] for r in train]))
    write_once_json(directory / "constant_model.json", {"p_failure": prior, "fit_rows": len(train),
                                                       "positive_label": "continue_failure"})
    val_predictions = {"constant_train_prevalence": np.full(len(val), prior), "logistic_regression": probabilities}
    result = {"data": data, "folds": folds, "full_train_preprocessing": model.audit(),
              "serialization_predictions_exact": True, "threshold": 0.5, "threshold_selection": False,
              "calibrator_fitted": False, "hyperparameter_search": False, "test_accessed": False,
              "lightgbm_fitted": False, "validation": {}, "train_oof": {}, "bootstrap": {}, "calibration": {}}
    prediction_rows = []
    for split, rows, predicted in (("train_oof", train, oof), ("validation", val, val_predictions)):
        y = [r["y_failure"] for r in rows]
        available = {name: p for name, p in predicted.items() if np.isfinite(p).all()}
        result[split] = {name: {"status": "ESTIMATED", **metric_set(y, p)} if name in available
                         else {"status": "NOT_ESTIMABLE_INCOMPLETE_OOF", "predictions_available": int(np.isfinite(p).sum())}
                         for name, p in predicted.items()}
        result["calibration"][split] = {name: calibration_bins(y, p) for name, p in available.items()}
        result["bootstrap"][split] = cluster_bootstrap(rows, available, resamples=config["bootstrap"]["resamples"],
                                                     seed=config["bootstrap"]["random_seed"])
        for name, p in predicted.items():
            for i, row in enumerate(rows):
                prediction_rows.append({"evaluation_split": split, "source_split": row["split"], "model": name,
                    "task_id": row["task_id"], "template_id": row["template_id"], "checkpoint_id": row["checkpoint_id"],
                    "risk_record_sha256": row["risk_record_sha256"], "y_failure": row["y_failure"],
                    "p_failure": float(p[i]) if np.isfinite(p[i]) else None,
                    "prediction_at_0_5": int(p[i] >= .5) if np.isfinite(p[i]) else None})
    with (directory / "predictions.jsonl").open("x", encoding="utf-8") as handle:
        for row in prediction_rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    write_once_json(directory / "evaluation.json", result)
    return result
