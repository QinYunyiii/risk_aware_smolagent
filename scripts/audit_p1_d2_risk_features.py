#!/usr/bin/env python3
"""Audit Day 10 feature shortcuts with fixed template-grouped OOF folds.

This script reads the immutable Day 9/10 artifacts and writes only new P1-D2
derived audit files.  It does not save fitted models or access Validation/Test.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import yaml
from lightgbm import LGBMClassifier
from sklearn.base import clone
from sklearn.calibration import calibration_curve
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from risk_agent.prediction.dataset import FEATURE_NAMES


SOURCE_DAY9 = "runs/day09/day9_20261119_080843_288841fc"
SOURCE_DAY10 = "runs/day10/day10_20261119_082058_aab1d057"
SOURCE_MODELS = "models/risk/day10_20261119_082058_aab1d057"
SOURCE_CONFIG = "configs/day10_risk.yaml"

LAST_FAILURE_FEATURES = (
    "failure_present",
    "failure_response_received",
    "fault_f1_timeout",
    "fault_f2_api_503",
    "fault_f3_invalid_argument",
    "fault_f4_stale_observation",
    "fault_f5_repeated_action",
    "fault_f6_partial_failure",
)
FAULT_FEATURES = tuple(name for name in LAST_FAILURE_FEATURES if name.startswith("fault_"))
HISTORY_ONLY_FEATURES = FEATURE_NAMES[:13]
OBSERVED_RESPONSE_FEATURES = ("error_count", "no_progress_count")
STRICT_AGENT_VISIBLE_FEATURES = (
    "observed_history_count",
    "error_count",
    "repeated_action_count",
    "no_progress_count",
    "historical_tool_success_rate",
)
DEPLOYMENT_ALLOWLIST = tuple(name for name in FEATURE_NAMES if name not in LAST_FAILURE_FEATURES)


@dataclass(frozen=True)
class FeatureAudit:
    name: str
    meaning: str
    source_expression: str
    generation_path: str
    guide_group: str
    information_class: str
    observable_at_decision_time: bool
    allowed_for_deployment: bool
    strict_agent_visible: bool
    current_day9_evidence: str


def _feature(
    name: str,
    meaning: str,
    source_expression: str,
    guide_group: str,
    information_class: str,
    *,
    deployment: bool,
    strict_visible: bool = False,
    evidence: str,
) -> FeatureAudit:
    return FeatureAudit(
        name=name,
        meaning=meaning,
        source_expression=source_expression,
        generation_path=(
            "Day 9 prepare_intervention_checkpoints -> checkpoint runtime_state.json -> "
            "Day 10 build_risk_dataset -> extract_risk_features"
        ),
        guide_group=guide_group,
        information_class=information_class,
        observable_at_decision_time=True,
        allowed_for_deployment=deployment,
        strict_agent_visible=strict_visible,
        current_day9_evidence=evidence,
    )


FEATURE_AUDIT: tuple[FeatureAudit, ...] = (
    _feature(
        "execution_progress_fraction",
        "tool-call progress proxy used/(used+1)",
        "runtime.budget.tool_calls_used / max(1, used + 1)",
        "agent_history",
        "simulator_internal",
        deployment=True,
        evidence="Controller budget counter; not copied from Agent messages.",
    ),
    _feature(
        "tool_call_count",
        "number of tool calls charged to the runtime budget",
        "runtime.budget.tool_calls_used",
        "agent_history",
        "simulator_internal",
        deployment=True,
        evidence="Controller counter; Day 9 sets it to one iff synthetic failure history exists.",
    ),
    _feature(
        "observed_history_count",
        "number of stored tool-history entries",
        "len(runtime.tool_history)",
        "agent_history",
        "agent_visible",
        deployment=True,
        strict_visible=True,
        evidence="Semantically observable history, but Day 9 synthesizes 0/1 entries instead of replaying Qwen history.",
    ),
    _feature(
        "error_count",
        "stored tool-history entries with a non-null error_type",
        "count(runtime.tool_history[*].error_type is not None)",
        "observed_tool_response",
        "agent_visible",
        deployment=True,
        strict_visible=True,
        evidence="Day 9 copies last_failure.error_type into a synthetic history row; no actual Qwen-visible response is proven.",
    ),
    _feature(
        "repeated_action_count",
        "adjacent repeated tool name/argument signatures",
        "adjacent equality over runtime.tool_history tool_name + arguments",
        "agent_history",
        "agent_visible",
        deployment=True,
        strict_visible=True,
        evidence="Reconstructable from an actual Agent tool-call history; all current values are zero.",
    ),
    _feature(
        "no_progress_count",
        "error history rows with no tool result",
        "count(tool_result is None and error_type is not None)",
        "observed_tool_response",
        "agent_visible",
        deployment=True,
        strict_visible=True,
        evidence="Day 9 synthetic failure rows always use tool_result=null.",
    ),
    _feature(
        "recent_latency_mean_seconds",
        "mean latency over the latest three history rows",
        "mean(runtime.tool_history[-3:].latency_seconds)",
        "agent_history",
        "simulator_internal",
        deployment=True,
        evidence="Exact controller telemetry; every current synthetic history latency is 0.",
    ),
    _feature(
        "recent_latency_max_seconds",
        "maximum latency over the latest three history rows",
        "max(runtime.tool_history[-3:].latency_seconds)",
        "agent_history",
        "simulator_internal",
        deployment=True,
        evidence="Exact controller telemetry; every current synthetic history latency is 0.",
    ),
    _feature(
        "historical_tool_success_rate",
        "fraction of history rows with result and no error, or 0.5 when empty",
        "successful_history / len(history), else 0.5",
        "agent_history",
        "agent_visible",
        deployment=True,
        strict_visible=True,
        evidence="Reconstructable from visible history if results are actually supplied; current history is synthetic.",
    ),
    _feature(
        "tokens_used",
        "tokens charged before the decision",
        "runtime.budget.tokens_used",
        "agent_history",
        "simulator_internal",
        deployment=True,
        evidence="Copied from a Day 8 model step into the Day 9 controller budget, not an Agent message field.",
    ),
    _feature(
        "token_budget_fraction",
        "fraction of the token budget used",
        "runtime.budget.tokens_used / runtime.budget.max_tokens",
        "agent_history",
        "simulator_internal",
        deployment=True,
        evidence="Controller budget telemetry available pre-decision.",
    ),
    _feature(
        "tool_budget_fraction",
        "fraction of the tool-call budget used",
        "runtime.budget.tool_calls_used / runtime.budget.max_tool_calls",
        "agent_history",
        "simulator_internal",
        deployment=True,
        evidence="Controller budget telemetry; inherits Day 9 synthetic 0/1 tool-call construction.",
    ),
    _feature(
        "latency_budget_fraction",
        "fraction of the latency budget used",
        "runtime.budget.latency_seconds_used / runtime.budget.max_latency_seconds",
        "agent_history",
        "simulator_internal",
        deployment=True,
        evidence="Controller budget telemetry; all current values are zero.",
    ),
    _feature(
        "pending_operation_read_only",
        "whether the controller classifies the pending operation as read-only",
        "runtime.tool_state.pending_operation.read_only",
        "agent_history",
        "simulator_internal",
        deployment=True,
        evidence="Derived by the fault/operation controller, not from the Qwen-visible response.",
    ),
    _feature(
        "failure_present",
        "whether the internal last_failure object exists",
        "bool(runtime.tool_state.last_failure)",
        "injection_metadata",
        "injection_metadata",
        deployment=False,
        evidence="Directly reflects Day 9 synthetic scenario construction.",
    ),
    _feature(
        "failure_response_received",
        "internal flag stating whether the injected failure returned a response",
        "runtime.tool_state.last_failure.response_received",
        "injection_metadata",
        "injection_metadata",
        deployment=False,
        evidence="Computed by _failure() from fault type; perfectly separates current labels.",
    ),
    *(
        _feature(
            name,
            f"one-hot internal injected fault type {name.removeprefix('fault_').upper()}",
            f"runtime.tool_state.last_failure.fault_type == {name.removeprefix('fault_').upper()}",
            "injection_metadata",
            "injection_metadata",
            deployment=False,
            evidence="Direct one-hot encoding of the Day 9 injected fault type.",
        )
        for name in FAULT_FEATURES
    ),
    *(
        _feature(
            name,
            f"one-hot task domain {name.removeprefix('domain_')}",
            f"TaskSpec.domain == {name.removeprefix('domain_')}",
            "agent_history",
            "simulator_internal",
            deployment=True,
            evidence="Static task metadata; the explicit one-hot value is not read from Agent memory.",
        )
        for name in FEATURE_NAMES
        if name.startswith("domain_")
    ),
)


ABLATIONS: dict[str, tuple[str, ...]] = {
    "all_current_features": FEATURE_NAMES,
    "without_failure_response_received": tuple(
        name for name in FEATURE_NAMES if name != "failure_response_received"
    ),
    "without_fault_features": tuple(name for name in FEATURE_NAMES if not name.startswith("fault_")),
    "without_all_internal_last_failure_metadata": tuple(
        name for name in FEATURE_NAMES if name not in LAST_FAILURE_FEATURES
    ),
    "history_only": HISTORY_ONLY_FEATURES,
    "strict_agent_visible": STRICT_AGENT_VISIBLE_FEATURES,
    # Required diagnostic views from NEXT_STAGE_EXPERIMENT_GUIDE.md.
    "observed_response_only": OBSERVED_RESPONSE_FEATURES,
    "injection_metadata_only": LAST_FAILURE_FEATURES,
    "deployment_allowlist_only": DEPLOYMENT_ALLOWLIST,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metrics(labels: np.ndarray, probabilities: np.ndarray, threshold: float = 0.5) -> dict[str, float | None]:
    predictions = (probabilities >= threshold).astype(int)
    both_classes = len(np.unique(labels)) == 2
    bounded = lambda value: float(np.clip(value, 0.0, 1.0))
    return {
        "auroc": bounded(roc_auc_score(labels, probabilities)) if both_classes else None,
        "auprc": bounded(average_precision_score(labels, probabilities)) if both_classes else None,
        "brier": bounded(brier_score_loss(labels, probabilities)),
        "log_loss": float(log_loss(labels, probabilities, labels=[0, 1])),
        "precision": bounded(precision_score(labels, predictions, zero_division=0)),
        "recall": bounded(recall_score(labels, predictions, zero_division=0)),
    }


def _calibration(labels: np.ndarray, probabilities: np.ndarray) -> list[dict[str, float]]:
    observed, predicted = calibration_curve(
        labels, probabilities, n_bins=min(5, len(labels)), strategy="quantile"
    )
    return [
        {"mean_predicted_probability": float(p), "observed_failure_fraction": float(o)}
        for p, o in zip(predicted, observed)
    ]


def _factories(config: Mapping[str, Any], seed: int) -> dict[str, Any]:
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


def _fold_report(
    fold_number: int,
    train_indices: np.ndarray,
    validation_indices: np.ndarray,
    labels: np.ndarray,
    groups: np.ndarray,
    probabilities: np.ndarray,
) -> dict[str, Any]:
    train_labels = labels[train_indices]
    validation_labels = labels[validation_indices]
    return {
        "fold": fold_number,
        "train_samples": int(len(train_indices)),
        "train_positive_failures": int(train_labels.sum()),
        "train_negative_successes": int(len(train_labels) - train_labels.sum()),
        "validation_samples": int(len(validation_indices)),
        "validation_positive_failures": int(validation_labels.sum()),
        "validation_negative_successes": int(len(validation_labels) - validation_labels.sum()),
        "train_templates": sorted(groups[train_indices].tolist()),
        "validation_templates": sorted(groups[validation_indices].tolist()),
        "template_overlap": sorted(set(groups[train_indices]) & set(groups[validation_indices])),
        "metrics": _metrics(validation_labels, probabilities),
    }


def _fit_feature_set(
    *,
    feature_set: str,
    feature_names: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
    labels: np.ndarray,
    groups: np.ndarray,
    folds: Sequence[tuple[np.ndarray, np.ndarray]],
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    matrix = np.asarray(
        [[float(row["features"][name]) for name in feature_names] for row in rows], dtype=float
    )
    results: dict[str, Any] = {}
    prediction_records: list[dict[str, Any]] = []
    for model_name, prototype in _factories(config, int(config["random_seed"])).items():
        oof = np.full(len(rows), np.nan)
        fold_reports: list[dict[str, Any]] = []
        for fold_number, (train_indices, validation_indices) in enumerate(folds, start=1):
            model = clone(prototype)
            model.fit(matrix[train_indices], labels[train_indices])
            probabilities = model.predict_proba(matrix[validation_indices])[:, 1]
            oof[validation_indices] = probabilities
            fold_reports.append(
                _fold_report(
                    fold_number,
                    train_indices,
                    validation_indices,
                    labels,
                    groups,
                    probabilities,
                )
            )
            for index, probability in zip(validation_indices, probabilities):
                row = rows[int(index)]
                prediction_records.append(
                    {
                        "feature_set": feature_set,
                        "model": model_name,
                        "fold": fold_number,
                        "checkpoint_id": row["checkpoint_id"],
                        "task_id": row["task_id"],
                        "template_id": row["template_id"],
                        "split": row["split"],
                        "label_failure": int(labels[index]),
                        "oof_failure_probability": float(probability),
                        "predicted_failure": int(probability >= 0.5),
                    }
                )
        if np.isnan(oof).any():
            raise RuntimeError(f"Missing OOF probabilities for {feature_set}/{model_name}")
        final_model = clone(prototype).fit(matrix, labels)
        if model_name == "logistic_regression":
            values = final_model.named_steps["classifier"].coef_[0]
            importance_kind = "standardized_log_odds_coefficient"
        else:
            values = final_model.feature_importances_
            importance_kind = "split_importance"
        results[model_name] = {
            "oof_metrics": _metrics(labels, oof),
            "fold_metrics": fold_reports,
            "calibration_curve": _calibration(labels, oof),
            "feature_importance_kind": importance_kind,
            "feature_importance": {
                name: float(value) for name, value in zip(feature_names, values)
            },
        }
    return results, prediction_records


def _constant_baseline(
    *,
    rows: Sequence[Mapping[str, Any]],
    labels: np.ndarray,
    groups: np.ndarray,
    folds: Sequence[tuple[np.ndarray, np.ndarray]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    oof = np.full(len(rows), np.nan)
    fold_reports: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for fold_number, (train_indices, validation_indices) in enumerate(folds, start=1):
        probability = float(labels[train_indices].mean())
        probabilities = np.full(len(validation_indices), probability)
        oof[validation_indices] = probabilities
        fold = _fold_report(
            fold_number, train_indices, validation_indices, labels, groups, probabilities
        )
        fold["training_failure_rate_prediction"] = probability
        fold_reports.append(fold)
        for index in validation_indices:
            row = rows[int(index)]
            records.append(
                {
                    "feature_set": "constant_probability",
                    "model": "constant_probability",
                    "fold": fold_number,
                    "checkpoint_id": row["checkpoint_id"],
                    "task_id": row["task_id"],
                    "template_id": row["template_id"],
                    "split": row["split"],
                    "label_failure": int(labels[index]),
                    "oof_failure_probability": probability,
                    "predicted_failure": int(probability >= 0.5),
                }
            )
    return {
        "probability_source": "training-fold failure prevalence only",
        "oof_metrics": _metrics(labels, oof),
        "fold_metrics": fold_reports,
        "calibration_curve": _calibration(labels, oof),
    }, records


def _fold_manifest(
    folds: Sequence[tuple[np.ndarray, np.ndarray]], labels: np.ndarray, groups: np.ndarray
) -> list[dict[str, Any]]:
    manifest = []
    for number, (train, validation) in enumerate(folds, start=1):
        train_labels = labels[train]
        validation_labels = labels[validation]
        manifest.append(
            {
                "fold": number,
                "train_samples": len(train),
                "train_positive_failures": int(train_labels.sum()),
                "train_negative_successes": int(len(train_labels) - train_labels.sum()),
                "validation_samples": len(validation),
                "validation_positive_failures": int(validation_labels.sum()),
                "validation_negative_successes": int(
                    len(validation_labels) - validation_labels.sum()
                ),
                "train_templates": sorted(groups[train].tolist()),
                "validation_templates": sorted(groups[validation].tolist()),
                "template_overlap": sorted(set(groups[train]) & set(groups[validation])),
            }
        )
    return manifest


def _feature_label_audit(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for name in FEATURE_NAMES:
        by_label = {
            label: sorted(
                {float(row["features"][name]) for row in rows if row["label_failure"] == label}
            )
            for label in (0, 1)
        }
        output.append(
            {
                "feature": name,
                "success_label_0_values": by_label[0],
                "failure_label_1_values": by_label[1],
                "unique_value_sets_disjoint": not bool(set(by_label[0]) & set(by_label[1])),
            }
        )
    return output


def _load_original_fold_map(root: Path) -> dict[str, int]:
    records = [
        json.loads(line)
        for line in (root / SOURCE_DAY10 / "oof_predictions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    fold_map: dict[str, int] = {}
    for record in records:
        checkpoint_id = str(record["checkpoint_id"])
        fold = int(record["fold"])
        if checkpoint_id in fold_map and fold_map[checkpoint_id] != fold:
            raise RuntimeError("Original Day 10 models disagree on fold assignment")
        fold_map[checkpoint_id] = fold
    return fold_map


def run_audit(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root = root.resolve()
    config = yaml.safe_load((root / SOURCE_CONFIG).read_text(encoding="utf-8"))
    dataset = json.loads((root / SOURCE_DAY10 / "risk_dataset.json").read_text(encoding="utf-8"))
    rows = list(dataset["examples"])
    labels = np.asarray([int(row["label_failure"]) for row in rows], dtype=int)
    groups = np.asarray([str(row["template_id"]) for row in rows])
    feature_schema = tuple(rows[0]["features"])
    if feature_schema != FEATURE_NAMES or any(tuple(row["features"]) != FEATURE_NAMES for row in rows):
        raise RuntimeError("Day 10 dataset feature schema does not match source code")
    if tuple(item.name for item in FEATURE_AUDIT) != FEATURE_NAMES:
        raise RuntimeError("Feature audit does not annotate the exact 26-feature schema")
    if set(labels) != {0, 1} or len(rows) != 20 or len(set(groups)) != 20:
        raise RuntimeError("Unexpected Day 10 audit population")
    if {str(row["split"]) for row in rows} != {"train"}:
        raise RuntimeError("P1-D2 must use only the existing Train OOF population")

    splitter = StratifiedGroupKFold(
        n_splits=int(config["cv_folds"]),
        shuffle=True,
        random_state=int(config["random_seed"]),
    )
    dummy = np.zeros((len(rows), 1))
    folds = list(splitter.split(dummy, labels, groups))
    fold_by_index = {
        int(index): number
        for number, (_, validation) in enumerate(folds, start=1)
        for index in validation
    }
    original_fold_map = _load_original_fold_map(root)
    regenerated_fold_map = {
        str(row["checkpoint_id"]): fold_by_index[index] for index, row in enumerate(rows)
    }
    if original_fold_map != regenerated_fold_map:
        raise RuntimeError("Regenerated folds differ from immutable Day 10 OOF folds")

    ablation_results: dict[str, Any] = {}
    predictions: list[dict[str, Any]] = []
    for name, feature_names in ABLATIONS.items():
        models, records = _fit_feature_set(
            feature_set=name,
            feature_names=feature_names,
            rows=rows,
            labels=labels,
            groups=groups,
            folds=folds,
            config=config,
        )
        ablation_results[name] = {
            "feature_count": len(feature_names),
            "feature_names": list(feature_names),
            "models": models,
        }
        predictions.extend(records)
    baseline, baseline_records = _constant_baseline(
        rows=rows, labels=labels, groups=groups, folds=folds
    )
    predictions.extend(baseline_records)

    original_predictions = {
        (record["model"], record["checkpoint_id"]): float(record["oof_failure_probability"])
        for record in (
            json.loads(line)
            for line in (root / SOURCE_DAY10 / "oof_predictions.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line
        )
    }
    full_predictions = {
        (record["model"], record["checkpoint_id"]): record["oof_failure_probability"]
        for record in predictions
        if record["feature_set"] == "all_current_features"
    }
    max_prediction_difference = max(
        abs(original_predictions[key] - full_predictions[key]) for key in original_predictions
    )

    day09_outcomes = [
        json.loads(line)
        for line in (root / SOURCE_DAY9 / "branch_outcomes.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    runtime_documents = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((root / SOURCE_DAY9 / "checkpoints").glob("*/runtime_state.json"))
    ]
    feature_label = _feature_label_audit(rows)
    failure_response = next(
        item for item in feature_label if item["feature"] == "failure_response_received"
    )
    requested = {
        "all_current_features",
        "without_failure_response_received",
        "without_fault_features",
        "without_all_internal_last_failure_metadata",
        "history_only",
        "strict_agent_visible",
    }
    guide_required = {
        "all_current_features",
        "without_failure_response_received",
        "history_only",
        "observed_response_only",
        "injection_metadata_only",
        "deployment_allowlist_only",
    }
    audit = {
        "schema_version": "p1-d2-risk-feature-audit-v1",
        "audit_id": "p1_d2_day10_20261119_082058_aab1d057",
        "scope": "descriptive Train-only OOF shortcut audit; not a Qwen risk-capability evaluation",
        "source": {
            "day9_run": SOURCE_DAY9,
            "day10_run": SOURCE_DAY10,
            "day10_models": SOURCE_MODELS,
            "config": SOURCE_CONFIG,
            "controller_types": sorted({row["controller_type"] for row in day09_outcomes}),
            "sha256": {
                "risk_dataset": _sha256(root / SOURCE_DAY10 / "risk_dataset.json"),
                "day10_oof_predictions": _sha256(root / SOURCE_DAY10 / "oof_predictions.jsonl"),
                "day10_metrics": _sha256(root / SOURCE_DAY10 / "metrics.json"),
                "day10_feature_importance": _sha256(root / SOURCE_DAY10 / "feature_importance.json"),
                "day10_config": _sha256(root / SOURCE_CONFIG),
                "feature_extractor_code": _sha256(root / "src/risk_agent/prediction/dataset.py"),
                "day9_scenario_code": _sha256(root / "src/risk_agent/intervention/scenarios.py"),
            },
        },
        "population": {
            "sample_count": len(rows),
            "positive_failures": int(labels.sum()),
            "negative_successes": int(len(labels) - labels.sum()),
            "template_count": len(set(groups)),
            "splits": sorted({str(row["split"]) for row in rows}),
            "official_validation_or_test_accessed": False,
        },
        "day9_construction_evidence": {
            "runtime_document_count": len(runtime_documents),
            "last_failure_present_count": sum(
                bool(runtime["tool_state"].get("last_failure")) for runtime in runtime_documents
            ),
            "synthetic_tool_history_nonempty_count": sum(
                bool(runtime["tool_history"]) for runtime in runtime_documents
            ),
            "agent_memory_role_sequences": sorted(
                {tuple(item["role"] for item in runtime["agent_memory"]) for runtime in runtime_documents}
            ),
            "agent_memory_contains_tool_observation_count": sum(
                any(item.get("role") in {"tool", "observation"} for item in runtime["agent_memory"])
                for runtime in runtime_documents
            ),
            "note": (
                "Day 9 creates deterministic local-reference checkpoints and a synthetic error history; "
                "it does not establish that last_failure or the synthetic history row was shown to Qwen."
            ),
        },
        "feature_audit": [asdict(item) for item in FEATURE_AUDIT],
        "feature_label_audit": feature_label,
        "folds": _fold_manifest(folds, labels, groups),
        "fold_protocol": {
            "strategy": "StratifiedGroupKFold",
            "n_splits": int(config["cv_folds"]),
            "shuffle": True,
            "random_state": int(config["random_seed"]),
            "group": "template_id",
            "same_fold_object_used_for_every_ablation": True,
            "matches_original_day10_fold_assignment": True,
            "decision_threshold": float(config["decision_threshold"]),
            "label_or_sample_selection_changed": False,
        },
        "ablations": ablation_results,
        "constant_probability_baseline": baseline,
        "original_day10_reproduction": {
            "max_absolute_oof_prediction_difference": max_prediction_difference,
            "matches_at_absolute_tolerance_1e_12": max_prediction_difference <= 1e-12,
            "original_lightgbm_split_importance": json.loads(
                (root / SOURCE_DAY10 / "feature_importance.json").read_text(encoding="utf-8")
            )["lightgbm"],
        },
        "checks": {
            "all_26_features_annotated_once": len(FEATURE_AUDIT) == 26
            and len({item.name for item in FEATURE_AUDIT}) == 26,
            "requested_six_feature_sets_present": requested <= set(ABLATIONS),
            "guide_feature_sets_present": guide_required <= set(ABLATIONS),
            "same_template_grouped_folds": all(not fold["template_overlap"] for fold in _fold_manifest(folds, labels, groups)),
            "folds_match_original_day10": original_fold_map == regenerated_fold_map,
            "labels_and_samples_unchanged": len(rows) == 20
            and int(labels.sum()) == 9
            and int(len(labels) - labels.sum()) == 11,
            "failure_response_received_perfectly_separates_labels": bool(
                failure_response["unique_value_sets_disjoint"]
            ),
            "injection_metadata_excluded_from_deployment_allowlist": not bool(
                set(LAST_FAILURE_FEATURES) & set(DEPLOYMENT_ALLOWLIST)
            ),
            "strict_visible_is_subset_of_deployment_allowlist": set(
                STRICT_AGENT_VISIBLE_FEATURES
            )
            <= set(DEPLOYMENT_ALLOWLIST),
            "only_train_split_used": {str(row["split"]) for row in rows} == {"train"},
            "day9_controller_is_deterministic_reference": {
                row["controller_type"] for row in day09_outcomes
            }
            == {"deterministic_local_reference"},
            "full_feature_oof_reproduces_day10": max_prediction_difference <= 1e-12,
        },
        "gate_passed": False,
        "interpretation_limits": [
            "All 20 checkpoints are Train examples from deterministic_local_reference construction.",
            "The results do not measure real Qwen3-8B risk-prediction ability.",
            "Feature ablations diagnose shortcuts; they do not validate deployment generalization.",
            "No labels, samples, folds, thresholds, or model hyperparameters were tuned for this audit.",
        ],
    }
    audit["gate_passed"] = all(audit["checks"].values())
    return audit, predictions


CSV_COLUMNS = (
    "feature_set",
    "model",
    "scope",
    "fold",
    "feature_count",
    "train_samples",
    "train_positive_failures",
    "train_negative_successes",
    "validation_samples",
    "validation_positive_failures",
    "validation_negative_successes",
    "auroc",
    "auprc",
    "brier",
    "log_loss",
    "precision",
    "recall",
)


def _csv_rows(audit: Mapping[str, Any]) -> Iterable[dict[str, Any]]:
    for feature_set, result in audit["ablations"].items():
        for model_name, model in result["models"].items():
            yield {
                "feature_set": feature_set,
                "model": model_name,
                "scope": "overall_oof",
                "fold": "",
                "feature_count": result["feature_count"],
                "validation_samples": audit["population"]["sample_count"],
                "validation_positive_failures": audit["population"]["positive_failures"],
                "validation_negative_successes": audit["population"]["negative_successes"],
                **model["oof_metrics"],
            }
            for fold in model["fold_metrics"]:
                yield {
                    "feature_set": feature_set,
                    "model": model_name,
                    "scope": "fold",
                    "fold": fold["fold"],
                    "feature_count": result["feature_count"],
                    **{key: fold[key] for key in CSV_COLUMNS if key in fold},
                    **fold["metrics"],
                }
    baseline = audit["constant_probability_baseline"]
    yield {
        "feature_set": "constant_probability",
        "model": "constant_probability",
        "scope": "overall_oof",
        "fold": "",
        "feature_count": 0,
        "validation_samples": audit["population"]["sample_count"],
        "validation_positive_failures": audit["population"]["positive_failures"],
        "validation_negative_successes": audit["population"]["negative_successes"],
        **baseline["oof_metrics"],
    }
    for fold in baseline["fold_metrics"]:
        yield {
            "feature_set": "constant_probability",
            "model": "constant_probability",
            "scope": "fold",
            "fold": fold["fold"],
            "feature_count": 0,
            **{key: fold[key] for key in CSV_COLUMNS if key in fold},
            **fold["metrics"],
        }


def write_outputs(output_dir: Path, audit: Mapping[str, Any], predictions: Sequence[Mapping[str, Any]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": output_dir / "risk_feature_ablation.json",
        "csv": output_dir / "risk_feature_ablation.csv",
        "jsonl": output_dir / "risk_oof_predictions.jsonl",
    }
    existing = [str(path) for path in paths.values() if path.exists()]
    if existing:
        raise FileExistsError("Refusing to overwrite existing P1-D2 artifacts: " + ", ".join(existing))
    paths["json"].write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with paths["csv"].open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=CSV_COLUMNS,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in _csv_rows(audit):
            writer.writerow({key: "" if row.get(key) is None else row.get(key, "") for key in CSV_COLUMNS})
    paths["jsonl"].write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in predictions),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, default=Path("runs/phase2_audit"))
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    audit, predictions = run_audit(root)
    if not args.check_only:
        output = args.output_dir if args.output_dir.is_absolute() else root / args.output_dir
        write_outputs(output, audit, predictions)
        print(output)
    print(json.dumps({"gate_passed": audit["gate_passed"], "checks": audit["checks"]}, indent=2))
    return 0 if audit["gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
