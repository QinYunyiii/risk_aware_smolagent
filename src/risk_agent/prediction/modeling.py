"""Template-grouped cross-validation for the Day 10 risk models."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from lightgbm import LGBMClassifier
from sklearn.base import clone
from sklearn.calibration import calibration_curve
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .dataset import FEATURE_NAMES
from .schema import RiskExample


def _bounded_metric(value: float) -> float:
    return float(np.clip(value, 0.0, 1.0))


def _metric_set(labels: np.ndarray, probabilities: np.ndarray, threshold: float) -> dict[str, float | None]:
    predictions = (probabilities >= threshold).astype(int)
    both_classes = len(np.unique(labels)) == 2
    return {
        "auroc": _bounded_metric(roc_auc_score(labels, probabilities)) if both_classes else None,
        "auprc": _bounded_metric(average_precision_score(labels, probabilities)) if both_classes else None,
        "brier": _bounded_metric(brier_score_loss(labels, probabilities)),
        "precision": _bounded_metric(precision_score(labels, predictions, zero_division=0)),
        "recall": _bounded_metric(recall_score(labels, predictions, zero_division=0)),
    }


def _aggregate_fold_metrics(folds: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, float | None]]:
    aggregated: dict[str, dict[str, float | None]] = {}
    for name in ("auroc", "auprc", "brier", "precision", "recall"):
        values = [float(fold["metrics"][name]) for fold in folds if fold["metrics"][name] is not None]
        aggregated[name] = {
            "mean": float(np.mean(values)) if values else None,
            "std": float(np.std(values, ddof=0)) if values else None,
        }
    return aggregated


def _model_factories(config: Mapping[str, Any], seed: int) -> dict[str, Any]:
    logistic = dict(config["logistic_regression"])
    lightgbm = dict(config["lightgbm"])
    return {
        "logistic_regression": Pipeline(
            [
                ("scaler", StandardScaler()),
                (
                    "classifier",
                    LogisticRegression(
                        C=float(logistic["C"]),
                        max_iter=int(logistic["max_iter"]),
                        class_weight=str(logistic["class_weight"]),
                        random_state=seed,
                    ),
                ),
            ]
        ),
        "lightgbm": LGBMClassifier(
            n_estimators=int(lightgbm["n_estimators"]),
            learning_rate=float(lightgbm["learning_rate"]),
            num_leaves=int(lightgbm["num_leaves"]),
            max_depth=int(lightgbm["max_depth"]),
            min_child_samples=int(lightgbm["min_child_samples"]),
            reg_lambda=float(lightgbm["reg_lambda"]),
            class_weight=str(lightgbm["class_weight"]),
            random_state=seed,
            deterministic=True,
            force_col_wise=True,
            n_jobs=1,
            verbosity=-1,
        ),
    }


def train_and_evaluate_risk_models(
    *,
    examples: Sequence[RiskExample],
    config: Mapping[str, Any],
    model_directory: str | Path,
) -> dict[str, Any]:
    if not examples:
        raise ValueError("Risk model training requires examples")
    if any(tuple(example.features) != FEATURE_NAMES for example in examples):
        raise ValueError("Risk examples do not match the fixed feature schema")
    labels = np.asarray([example.label_failure for example in examples], dtype=int)
    if len(np.unique(labels)) != 2:
        raise ValueError("Risk model training requires both failure and success labels")
    matrix = np.asarray(
        [[example.features[name] for name in FEATURE_NAMES] for example in examples],
        dtype=float,
    )
    groups = np.asarray([example.template_id for example in examples])
    seed = int(config["random_seed"])
    folds_requested = int(config["cv_folds"])
    minority_count = int(min(np.bincount(labels)))
    if folds_requested < 2 or folds_requested > minority_count:
        raise ValueError("cv_folds must be between 2 and the minority-class count")
    threshold = float(config["decision_threshold"])
    if threshold != 0.5:
        raise ValueError("Day 10 uses the pre-specified 0.5 threshold")
    if config.get("hyperparameter_search") is not False:
        raise ValueError("Day 10 initial run must not perform hyperparameter search")

    splitter = StratifiedGroupKFold(
        n_splits=folds_requested,
        shuffle=True,
        random_state=seed,
    )
    folds = list(splitter.split(matrix, labels, groups))
    group_checks = []
    for train_indices, validation_indices in folds:
        train_groups = set(groups[train_indices])
        validation_groups = set(groups[validation_indices])
        group_checks.append(train_groups.isdisjoint(validation_groups))
    if not all(group_checks):
        raise RuntimeError("Template groups cross a cross-validation fold")

    destination = Path(model_directory)
    destination.mkdir(parents=True, exist_ok=False)
    model_results: dict[str, Any] = {}
    prediction_records: list[dict[str, object]] = []
    factories = _model_factories(config, seed)
    for model_name, prototype in factories.items():
        probabilities = np.full(len(examples), np.nan, dtype=float)
        fold_reports: list[dict[str, Any]] = []
        for fold_number, (train_indices, validation_indices) in enumerate(folds, start=1):
            model = clone(prototype)
            model.fit(matrix[train_indices], labels[train_indices])
            fold_probabilities = model.predict_proba(matrix[validation_indices])[:, 1]
            probabilities[validation_indices] = fold_probabilities
            fold_reports.append(
                {
                    "fold": fold_number,
                    "train_samples": int(len(train_indices)),
                    "validation_samples": int(len(validation_indices)),
                    "train_templates": sorted(groups[train_indices].tolist()),
                    "validation_templates": sorted(groups[validation_indices].tolist()),
                    "template_overlap": sorted(
                        set(groups[train_indices]) & set(groups[validation_indices])
                    ),
                    "metrics": _metric_set(
                        labels[validation_indices], fold_probabilities, threshold
                    ),
                }
            )
        if np.isnan(probabilities).any():
            raise RuntimeError(f"Missing OOF predictions for {model_name}")
        fraction_positive, mean_predicted = calibration_curve(
            labels,
            probabilities,
            n_bins=min(5, len(examples)),
            strategy="quantile",
        )
        final_model = clone(prototype)
        final_model.fit(matrix, labels)
        model_path = destination / f"{model_name}.joblib"
        joblib.dump(final_model, model_path)
        if model_name == "logistic_regression":
            importance_values = final_model.named_steps["classifier"].coef_[0]
            importance_kind = "standardized_log_odds_coefficient"
        else:
            importance_values = final_model.feature_importances_
            importance_kind = "split_importance"
        model_results[model_name] = {
            "oof_metrics": _metric_set(labels, probabilities, threshold),
            "fold_metrics": fold_reports,
            "fold_metric_summary": _aggregate_fold_metrics(fold_reports),
            "calibration_curve": [
                {
                    "mean_predicted_probability": float(predicted),
                    "observed_failure_fraction": float(observed),
                }
                for predicted, observed in zip(mean_predicted, fraction_positive)
            ],
            "feature_importance_kind": importance_kind,
            "feature_importance": {
                name: float(value)
                for name, value in zip(FEATURE_NAMES, importance_values)
            },
            "model_path": str(model_path.resolve()),
        }
        for index, (example, probability) in enumerate(zip(examples, probabilities)):
            prediction_records.append(
                {
                    "model": model_name,
                    "checkpoint_id": example.checkpoint_id,
                    "task_id": example.task_id,
                    "template_id": example.template_id,
                    "split": example.split,
                    "label_failure": example.label_failure,
                    "oof_failure_probability": float(probability),
                    "predicted_failure": int(probability >= threshold),
                    "fold": next(
                        fold_number
                        for fold_number, (_, validation_indices) in enumerate(folds, start=1)
                        if index in validation_indices
                    ),
                }
            )
    return {
        "models": model_results,
        "predictions": prediction_records,
        "cv": {
            "strategy": "StratifiedGroupKFold",
            "folds": folds_requested,
            "group": "template_id",
            "all_fold_template_overlaps_empty": all(group_checks),
            "threshold": threshold,
            "threshold_source": "pre-specified configuration",
            "hyperparameter_search": False,
            "official_test_set_accessed": False,
        },
    }
