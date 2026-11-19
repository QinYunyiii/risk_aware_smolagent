"""Validated loader for Day 10 risk and Day 11 utility model artifacts."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

import joblib
import numpy as np

from risk_agent.prediction import FEATURE_NAMES


class PolicyModelBundle:
    def __init__(
        self,
        *,
        risk_model: Any,
        outcome_models: Mapping[str, Any],
        cost_models: Mapping[str, Mapping[str, Any]],
        cost_weights: Mapping[str, float],
        utility_lambda: float,
        catalog_sha256: str,
    ) -> None:
        self.risk_model = risk_model
        self.outcome_models = dict(outcome_models)
        self.cost_models = {action: dict(models) for action, models in cost_models.items()}
        self.cost_weights = {name: float(value) for name, value in cost_weights.items()}
        self.utility_lambda = float(utility_lambda)
        self.catalog_sha256 = catalog_sha256

    @classmethod
    def load(
        cls,
        *,
        risk_metadata_path: str | Path,
        utility_metadata_path: str | Path,
        risk_model_name: str,
        expected_catalog_sha256: str,
    ) -> "PolicyModelBundle":
        risk_metadata = json.loads(Path(risk_metadata_path).read_text(encoding="utf-8"))
        utility_metadata = json.loads(Path(utility_metadata_path).read_text(encoding="utf-8"))
        if risk_metadata.get("catalog_sha256") != expected_catalog_sha256:
            raise ValueError("Risk model catalog hash mismatch")
        if utility_metadata.get("catalog_sha256") != expected_catalog_sha256:
            raise ValueError("Utility model catalog hash mismatch")
        if tuple(risk_metadata.get("feature_names", ())) != FEATURE_NAMES:
            raise ValueError("Risk model feature schema mismatch")
        if tuple(utility_metadata.get("feature_names", ())) != FEATURE_NAMES:
            raise ValueError("Utility model feature schema mismatch")
        risk_path = dict(risk_metadata["models"])[risk_model_name]
        utility_models = dict(utility_metadata["models"])
        outcome_models = {
            action: joblib.load(paths["outcome_model"])
            for action, paths in utility_models.items()
        }
        cost_models = {
            action: {
                component: joblib.load(path)
                for component, path in dict(paths["cost_models"]).items()
            }
            for action, paths in utility_models.items()
        }
        return cls(
            risk_model=joblib.load(risk_path),
            outcome_models=outcome_models,
            cost_models=cost_models,
            cost_weights=dict(utility_metadata["cost_weights"]),
            utility_lambda=float(utility_metadata["utility_lambda"]),
            catalog_sha256=expected_catalog_sha256,
        )

    @staticmethod
    def _matrix(features: Mapping[str, float]) -> np.ndarray:
        if tuple(features) != FEATURE_NAMES:
            raise ValueError("Policy feature order does not match the trained schema")
        matrix = np.asarray([[features[name] for name in FEATURE_NAMES]], dtype=float)
        if not np.isfinite(matrix).all():
            raise ValueError("Policy features must be finite")
        return matrix

    def predict_risk(self, features: Mapping[str, float]) -> float:
        probability = float(self.risk_model.predict_proba(self._matrix(features))[0, 1])
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise ValueError("Risk model returned an invalid probability")
        return probability

    def predict_action(
        self, action: str, features: Mapping[str, float]
    ) -> tuple[float, dict[str, float], float]:
        matrix = self._matrix(features)
        probability = float(self.outcome_models[action].predict_proba(matrix)[0, 1])
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise ValueError(f"{action} outcome model returned an invalid probability")
        costs = {
            component: float(model.predict(matrix)[0])
            for component, model in self.cost_models[action].items()
        }
        if set(costs) != set(self.cost_weights):
            raise ValueError(f"{action} cost components do not match configured weights")
        if any(not math.isfinite(value) or value < 0 for value in costs.values()):
            raise ValueError(f"{action} cost model returned an invalid value")
        weighted_cost = sum(self.cost_weights[name] * costs[name] for name in costs)
        return probability, costs, weighted_cost
