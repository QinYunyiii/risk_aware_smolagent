"""T-learner outcome and component-cost models with OOF utility evaluation."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.dummy import DummyRegressor
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import brier_score_loss, mean_absolute_error
from sklearn.model_selection import GroupKFold, StratifiedGroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from risk_agent.prediction.dataset import FEATURE_NAMES

from .dataset import ACTIONS
from .schema import UtilityExample


COST_COMPONENTS = ("tokens", "tool_calls", "latency_seconds")


class BetaBernoulliEstimator:
    """Smoothed constant probability model for an action with one observed class."""

    def __init__(self, *, alpha: float = 1.0, beta: float = 1.0) -> None:
        if alpha <= 0 or beta <= 0:
            raise ValueError("Beta prior parameters must be positive")
        self.alpha = float(alpha)
        self.beta = float(beta)
        self.success_probability_: float | None = None

    def fit(self, features: np.ndarray, labels: np.ndarray) -> "BetaBernoulliEstimator":
        if len(features) != len(labels) or len(labels) == 0:
            raise ValueError("BetaBernoulliEstimator requires aligned non-empty data")
        successes = float(np.sum(labels))
        self.success_probability_ = (successes + self.alpha) / (
            len(labels) + self.alpha + self.beta
        )
        return self

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        if self.success_probability_ is None:
            raise RuntimeError("BetaBernoulliEstimator has not been fitted")
        success = np.full(len(features), self.success_probability_, dtype=float)
        return np.column_stack((1.0 - success, success))


class NonNegativeRegressor:
    """Wrap a fitted cost regressor and clamp every prediction to zero or above."""

    def __init__(self, estimator: Any) -> None:
        self.estimator = estimator

    def fit(self, features: np.ndarray, labels: np.ndarray) -> "NonNegativeRegressor":
        self.estimator.fit(features, labels)
        return self

    def predict(self, features: np.ndarray) -> np.ndarray:
        return np.maximum(np.asarray(self.estimator.predict(features), dtype=float), 0.0)


def _matrix(examples: Sequence[UtilityExample]) -> np.ndarray:
    return np.asarray(
        [[example.features[name] for name in FEATURE_NAMES] for example in examples],
        dtype=float,
    )


def _folds(
    labels: np.ndarray, groups: np.ndarray, requested: int, seed: int
) -> list[tuple[np.ndarray, np.ndarray]]:
    group_count = len(set(groups.tolist()))
    if group_count < 2:
        raise ValueError("Action model requires at least two template groups")
    class_counts = np.bincount(labels.astype(int), minlength=2)
    populated = class_counts[class_counts > 0]
    if len(populated) == 2:
        count = min(requested, int(populated.min()), group_count)
        splitter = StratifiedGroupKFold(n_splits=count, shuffle=True, random_state=seed)
        return list(splitter.split(np.zeros((len(labels), 1)), labels, groups))
    count = min(requested, group_count)
    splitter = GroupKFold(n_splits=count)
    return list(splitter.split(np.zeros((len(labels), 1)), labels, groups))


def _outcome_estimator(config: Mapping[str, Any], labels: np.ndarray, seed: int) -> Any:
    outcome = dict(config["outcome_model"])
    if len(np.unique(labels)) < 2:
        return BetaBernoulliEstimator(
            alpha=float(outcome["beta_prior_alpha"]),
            beta=float(outcome["beta_prior_beta"]),
        )
    return Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "classifier",
                LogisticRegression(
                    C=float(outcome["C"]),
                    max_iter=int(outcome["max_iter"]),
                    class_weight=str(outcome["class_weight"]),
                    random_state=seed,
                ),
            ),
        ]
    )


def _cost_estimator(config: Mapping[str, Any], labels: np.ndarray) -> Any:
    if np.allclose(labels, labels[0]):
        return NonNegativeRegressor(
            DummyRegressor(strategy="constant", constant=float(labels[0]))
        )
    return NonNegativeRegressor(
        Pipeline(
            [
                ("scaler", StandardScaler()),
                ("regressor", Ridge(alpha=float(config["cost_model"]["ridge_alpha"]))),
            ]
        )
    )


def _actual_component(example: UtilityExample, component: str) -> float:
    value = {
        "tokens": example.extra_tokens,
        "tool_calls": example.extra_tool_calls,
        "latency_seconds": example.extra_latency_seconds,
    }[component]
    if value is None:
        raise ValueError("Inapplicable action has no cost label")
    return float(value)


def _weighted_cost(values: Mapping[str, float], weights: Mapping[str, float]) -> float:
    return sum(float(weights[name]) * float(values[name]) for name in COST_COMPONENTS)


def train_and_evaluate_utility_models(
    *,
    examples: Sequence[UtilityExample],
    config: Mapping[str, Any],
    model_directory: str | Path,
) -> dict[str, Any]:
    if config.get("hyperparameter_search") is not False:
        raise ValueError("Day 11 initial run must not perform hyperparameter search")
    if tuple(config.get("actions", ())) != ACTIONS:
        raise ValueError("Day 11 actions must be CONTINUE, RETRY, and REPLAN")
    weights = {name: float(config["cost_weights"][name]) for name in COST_COMPONENTS}
    utility_lambda = float(config["utility_lambda"])
    if any(value < 0 for value in weights.values()) or utility_lambda < 0:
        raise ValueError("Cost weights and utility lambda must be non-negative")
    seed = int(config["random_seed"])
    requested_folds = int(config["cv_folds"])
    destination = Path(model_directory)
    destination.mkdir(parents=True, exist_ok=False)

    applicable = [example for example in examples if example.action_applicable]
    predictions: list[dict[str, Any]] = []
    outcome_metrics: dict[str, Any] = {}
    cost_metrics: dict[str, Any] = {}
    model_metadata: dict[str, Any] = {}

    for action in ACTIONS:
        action_examples = [example for example in applicable if example.action == action]
        features = _matrix(action_examples)
        labels = np.asarray([example.outcome_success for example in action_examples], dtype=int)
        groups = np.asarray([example.template_id for example in action_examples])
        folds = _folds(labels, groups, requested_folds, seed)
        outcome_probabilities = np.full(len(action_examples), np.nan, dtype=float)
        cost_predictions = {
            component: np.full(len(action_examples), np.nan, dtype=float)
            for component in COST_COMPONENTS
        }
        fold_reports: list[dict[str, Any]] = []
        for fold_number, (train_indices, validation_indices) in enumerate(folds, start=1):
            train_labels = labels[train_indices]
            outcome_model = _outcome_estimator(config, train_labels, seed + fold_number)
            outcome_model.fit(features[train_indices], train_labels)
            outcome_probabilities[validation_indices] = outcome_model.predict_proba(
                features[validation_indices]
            )[:, 1]
            for component in COST_COMPONENTS:
                actual = np.asarray(
                    [_actual_component(example, component) for example in action_examples],
                    dtype=float,
                )
                cost_model = _cost_estimator(config, actual[train_indices])
                cost_model.fit(features[train_indices], actual[train_indices])
                predicted = cost_model.predict(features[validation_indices])
                cost_predictions[component][validation_indices] = np.maximum(predicted, 0.0)
            fold_reports.append(
                {
                    "fold": fold_number,
                    "train_templates": sorted(groups[train_indices].tolist()),
                    "validation_templates": sorted(groups[validation_indices].tolist()),
                    "template_overlap": sorted(
                        set(groups[train_indices]) & set(groups[validation_indices])
                    ),
                }
            )
        if np.isnan(outcome_probabilities).any() or any(
            np.isnan(values).any() for values in cost_predictions.values()
        ):
            raise RuntimeError(f"Missing OOF utility predictions for {action}")
        if any(report["template_overlap"] for report in fold_reports):
            raise RuntimeError(f"Template leakage detected for {action}")

        final_outcome_model = _outcome_estimator(config, labels, seed)
        final_outcome_model.fit(features, labels)
        outcome_path = destination / f"{action.lower()}_outcome.joblib"
        joblib.dump(final_outcome_model, outcome_path)
        final_cost_paths: dict[str, str] = {}
        for component in COST_COMPONENTS:
            actual = np.asarray(
                [_actual_component(example, component) for example in action_examples], dtype=float
            )
            model = _cost_estimator(config, actual)
            model.fit(features, actual)
            if np.any(model.predict(features) < 0):
                raise RuntimeError(f"Final cost model produced negative {component} predictions")
            path = destination / f"{action.lower()}_cost_{component}.joblib"
            joblib.dump(model, path)
            final_cost_paths[component] = str(path.resolve())

        actual_costs = {
            component: np.asarray(
                [_actual_component(example, component) for example in action_examples], dtype=float
            )
            for component in COST_COMPONENTS
        }
        actual_weighted = np.asarray(
            [
                _weighted_cost(
                    {component: actual_costs[component][index] for component in COST_COMPONENTS},
                    weights,
                )
                for index in range(len(action_examples))
            ]
        )
        predicted_weighted = np.asarray(
            [
                _weighted_cost(
                    {
                        component: cost_predictions[component][index]
                        for component in COST_COMPONENTS
                    },
                    weights,
                )
                for index in range(len(action_examples))
            ]
        )
        outcome_metrics[action] = {
            "sample_count": len(action_examples),
            "success_count": int(np.sum(labels)),
            "failure_count": int(len(labels) - np.sum(labels)),
            "model_kind": (
                "beta_bernoulli_constant"
                if len(np.unique(labels)) < 2
                else "logistic_regression"
            ),
            "brier": float(brier_score_loss(labels, outcome_probabilities)),
            "mae": float(mean_absolute_error(labels, outcome_probabilities)),
            "probability_min": float(outcome_probabilities.min()),
            "probability_max": float(outcome_probabilities.max()),
            "folds": fold_reports,
        }
        cost_metrics[action] = {
            "sample_count": len(action_examples),
            "component_mae": {
                component: float(mean_absolute_error(actual_costs[component], predicted))
                for component, predicted in cost_predictions.items()
            },
            "weighted_cost_mae": float(
                mean_absolute_error(actual_weighted, predicted_weighted)
            ),
            "actual_weighted_cost_mean": float(np.mean(actual_weighted)),
            "predicted_weighted_cost_mean": float(np.mean(predicted_weighted)),
            "all_predictions_nonnegative": all(
                bool(np.all(predicted >= 0)) for predicted in cost_predictions.values()
            ),
        }
        model_metadata[action] = {
            "outcome_model": str(outcome_path.resolve()),
            "cost_models": final_cost_paths,
        }
        fold_lookup = {
            int(index): fold_number
            for fold_number, (_, validation_indices) in enumerate(folds, start=1)
            for index in validation_indices
        }
        for index, example in enumerate(action_examples):
            predicted_components = {
                component: float(cost_predictions[component][index])
                for component in COST_COMPONENTS
            }
            actual_components = {
                component: float(actual_costs[component][index])
                for component in COST_COMPONENTS
            }
            predictions.append(
                {
                    "checkpoint_id": example.checkpoint_id,
                    "task_id": example.task_id,
                    "template_id": example.template_id,
                    "split": example.split,
                    "action": action,
                    "action_applicable": True,
                    "fold": fold_lookup[index],
                    "actual_success": int(labels[index]),
                    "predicted_success_probability": float(outcome_probabilities[index]),
                    "actual_cost_components": actual_components,
                    "predicted_cost_components": predicted_components,
                    "actual_weighted_cost": float(actual_weighted[index]),
                    "predicted_weighted_cost": float(predicted_weighted[index]),
                }
            )

    by_checkpoint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in predictions:
        by_checkpoint[str(record["checkpoint_id"])].append(record)
    utility_errors_all: list[float] = []
    utility_errors_non_continue: list[float] = []
    regrets: list[float] = []
    policy_records: list[dict[str, Any]] = []
    selected_counts: Counter[str] = Counter()
    for checkpoint_id, records in sorted(by_checkpoint.items()):
        continue_record = next(
            (record for record in records if record["action"] == "CONTINUE"), None
        )
        if continue_record is None:
            raise RuntimeError(f"Checkpoint lacks Continue prediction: {checkpoint_id}")
        continue_predicted_value = float(continue_record["predicted_success_probability"]) - (
            utility_lambda * float(continue_record["predicted_weighted_cost"])
        )
        continue_actual_value = float(continue_record["actual_success"]) - (
            utility_lambda * float(continue_record["actual_weighted_cost"])
        )
        for record in records:
            predicted_value = float(record["predicted_success_probability"]) - (
                utility_lambda * float(record["predicted_weighted_cost"])
            )
            actual_value = float(record["actual_success"]) - (
                utility_lambda * float(record["actual_weighted_cost"])
            )
            record["predicted_relative_utility"] = predicted_value - continue_predicted_value
            record["actual_relative_utility"] = actual_value - continue_actual_value
            error = abs(
                float(record["predicted_relative_utility"])
                - float(record["actual_relative_utility"])
            )
            utility_errors_all.append(error)
            if record["action"] != "CONTINUE":
                utility_errors_non_continue.append(error)
        selected = max(
            records,
            key=lambda record: float(record["predicted_success_probability"])
            - utility_lambda * float(record["predicted_weighted_cost"]),
        )
        actual_values = {
            str(record["action"]): float(record["actual_success"])
            - utility_lambda * float(record["actual_weighted_cost"])
            for record in records
        }
        best_action = max(actual_values, key=actual_values.get)
        regret = max(actual_values.values()) - actual_values[str(selected["action"])]
        regrets.append(max(0.0, regret))
        selected_counts[str(selected["action"])] += 1
        policy_records.append(
            {
                "checkpoint_id": checkpoint_id,
                "applicable_actions": [str(record["action"]) for record in records],
                "selected_action": selected["action"],
                "oracle_action": best_action,
                "regret": max(0.0, regret),
            }
        )

    inapplicable_predictions = [
        {
            "checkpoint_id": example.checkpoint_id,
            "task_id": example.task_id,
            "template_id": example.template_id,
            "split": example.split,
            "action": example.action,
            "action_applicable": False,
            "applicability_reason": example.applicability_reason,
            "predicted_success_probability": None,
            "predicted_cost_components": None,
            "predicted_relative_utility": None,
        }
        for example in examples
        if not example.action_applicable
    ]
    total_applicable = len(predictions)
    aggregate_outcome_brier = sum(
        outcome_metrics[action]["brier"] * outcome_metrics[action]["sample_count"]
        for action in ACTIONS
    ) / total_applicable
    aggregate_outcome_mae = sum(
        outcome_metrics[action]["mae"] * outcome_metrics[action]["sample_count"]
        for action in ACTIONS
    ) / total_applicable
    aggregate_cost_mae = sum(
        cost_metrics[action]["weighted_cost_mae"] * cost_metrics[action]["sample_count"]
        for action in ACTIONS
    ) / total_applicable
    return {
        "outcome_metrics": outcome_metrics,
        "cost_metrics": cost_metrics,
        "utility_metrics": {
            "outcome_brier_all_applicable": float(aggregate_outcome_brier),
            "outcome_mae_all_applicable": float(aggregate_outcome_mae),
            "weighted_cost_mae_all_applicable": float(aggregate_cost_mae),
            "utility_mae_all_applicable": float(np.mean(utility_errors_all)),
            "utility_mae_non_continue": float(np.mean(utility_errors_non_continue)),
            "policy_regret_mean": float(np.mean(regrets)),
            "policy_regret_max": float(np.max(regrets)),
            "checkpoint_count": len(by_checkpoint),
            "selected_action_counts": dict(sorted(selected_counts.items())),
        },
        "predictions": predictions + inapplicable_predictions,
        "policy_records": policy_records,
        "models": model_metadata,
        "cost_weights": weights,
        "utility_lambda": utility_lambda,
    }
