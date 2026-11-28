"""Train-only fixed Logistic baseline over the audited formal feature schema."""
from __future__ import annotations

import json
from typing import Any
import warnings

import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression

from risk_agent.collection.formal_features import NAMES
from .formal_preparation import ERROR_TYPES, feature_projection


def features(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [feature_projection({"feature_names": list(NAMES), "features": row["features"],
                                "features_sha256": row["features_sha256"]}) for row in rows]


def fitting_rows(rows: list[dict[str, Any]]) -> None:
    if not rows or any(row["split"] != "train" or row["allowed_for_weight_fitting"] is not True for row in rows):
        raise ValueError("Only Train rows can fit preprocessing or weights")
    if any(type(row["y_failure"]) is not int or row["y_failure"] not in (0, 1)
           or row["y_failure"] != 1 - row["y_continue"] for row in rows):
        raise ValueError("Failure label direction mismatch")


class FormalPreprocessor:
    def fit(self, rows: list[dict[str, Any]]) -> FormalPreprocessor:
        fitting_rows(rows)
        values = features(rows)
        self.fit_task_ids = [r["task_id"] for r in rows]
        self.constants = [n for n in NAMES if len({json.dumps(v[n], sort_keys=True) for v in values}) == 1]
        self.numeric = [n for n in NAMES if n != "latest_observed_error_type" and n not in self.constants]
        self.categorical = "latest_observed_error_type" not in self.constants
        matrix = self._numeric(values)
        self.medians = np.nanmedian(matrix, axis=0) if self.numeric else np.empty(0)
        matrix = np.where(np.isnan(matrix), self.medians, matrix)
        self.means = matrix.mean(axis=0) if self.numeric else np.empty(0)
        scales = matrix.std(axis=0) if self.numeric else np.empty(0)
        self.scales = np.where(scales == 0, 1.0, scales)
        self.encoded_names = self.numeric + (["latest_observed_error_type=" + c for c in ERROR_TYPES[1:]]
                                             if self.categorical else [])
        if not self.encoded_names:
            raise ValueError("No estimable nonconstant features")
        return self

    def _numeric(self, values):
        return np.array([[np.nan if v[n] is None else v[n] for n in self.numeric] for v in values], dtype=float)

    def transform(self, rows: list[dict[str, Any]]) -> np.ndarray:
        values = features(rows)
        numeric = self._numeric(values)
        numeric = (np.where(np.isnan(numeric), self.medians, numeric) - self.means) / self.scales
        if self.categorical:
            categorical = np.array([[float(v["latest_observed_error_type"] == c) for c in ERROR_TYPES[1:]]
                                    for v in values], dtype=float)
            numeric = np.column_stack((numeric, categorical))
        return numeric

    def audit(self) -> dict[str, Any]:
        return {"fit_task_ids": self.fit_task_ids, "dropped_constants": self.constants,
                "numeric_columns": self.numeric, "numeric_medians": self.medians.tolist(),
                "numeric_means": self.means.tolist(), "numeric_scales": self.scales.tolist(),
                "categorical_values": list(ERROR_TYPES), "categorical_reference": "none",
                "encoded_features": self.encoded_names, "encoded_feature_count": len(self.encoded_names)}


class FormalLogistic:
    def fit(self, rows: list[dict[str, Any]], config: dict[str, Any]) -> FormalLogistic:
        fitting_rows(rows)
        if {r["y_failure"] for r in rows} != {0, 1}:
            raise ValueError("One-class training fold is not estimable")
        self.preprocessor = FormalPreprocessor().fit(rows)
        # sklearn 1.9 expresses L2 via l1_ratio=0; penalty='l2' is deprecated.
        self.classifier = LogisticRegression(C=config["C"], l1_ratio=0.0, solver=config["solver"],
            max_iter=config["max_iter"], class_weight=config["class_weight"], random_state=config["random_seed"])
        matrix = self.preprocessor.transform(rows)
        self.encoded_matrix_rank = int(np.linalg.matrix_rank(matrix))
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            self.classifier.fit(matrix, np.array([r["y_failure"] for r in rows]))
        return self

    def predict_failure(self, rows: list[dict[str, Any]]) -> np.ndarray:
        positive = list(self.classifier.classes_).index(1)
        return self.classifier.predict_proba(self.preprocessor.transform(rows))[:, positive]

    def audit(self) -> dict[str, Any]:
        return {**self.preprocessor.audit(), "positive_label": "continue_failure",
                "encoded_matrix_rank": self.encoded_matrix_rank,
                "classes": self.classifier.classes_.tolist(), "intercept": self.classifier.intercept_.tolist(),
                "coefficients": self.classifier.coef_.tolist(), "iterations": self.classifier.n_iter_.tolist()}
