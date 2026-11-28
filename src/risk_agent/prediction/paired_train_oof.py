"""Fixed paired Train OOF. No Validation, tuning, final fit or production model."""
from __future__ import annotations

import warnings
import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedGroupKFold

from risk_agent.agent.agent_state_adapter import canonical_hash
from risk_agent.collection.formal_features import NAMES
from .formal_logistic import FormalLogistic, FormalPreprocessor, fitting_rows
from .formal_metrics import metric_set, calibration_bins

BIT = "public_completion_unknown"
METRICS = ("auroc", "auprc", "brier_score", "log_loss", "precision", "recall")


def v1_rows(rows):
    result = []
    for row in rows:
        values = {n: row["features"][n] for n in NAMES}
        result.append({**row, "features": values, "features_sha256": canonical_hash(values)})
    return result


class V2Preprocessor:
    def fit(self, rows):
        fitting_rows(rows)
        values = self._bit(rows)
        self.base = FormalPreprocessor().fit(v1_rows(rows))
        self.dropped = len(set(values)) == 1
        self.mean = float(values.mean())
        self.scale = float(values.std()) if not self.dropped else 1.0
        return self

    @staticmethod
    def _bit(rows):
        for r in rows:
            if (set(r["features"]) != {*NAMES, BIT} or type(r["features"][BIT]) is not int
                    or r["features"][BIT] not in (0, 1)
                    or canonical_hash(r["features"]) != r["features_sha256"]):
                raise ValueError("Exactly 19 qualified v2 features with integer bit required")
        return np.array([r["features"][BIT] for r in rows], dtype=float)

    def transform(self, rows):
        bit = self._bit(rows)
        base = self.base.transform(v1_rows(rows))
        return base if self.dropped else np.column_stack((base, (bit - self.mean) / self.scale))

    def audit(self):
        base = self.base.audit()
        return {**base, "new_feature_dropped_constant": self.dropped, "new_feature_mean": self.mean,
            "new_feature_scale": self.scale, "encoded_features": base["encoded_features"] + ([] if self.dropped else [BIT]),
            "encoded_feature_count": base["encoded_feature_count"] + int(not self.dropped)}


class V2Logistic:
    def fit(self, rows, config):
        fitting_rows(rows)
        if {r["y_failure"] for r in rows} != {0, 1}:
            raise ValueError("One-class training fold not estimable")
        self.preprocessor = V2Preprocessor().fit(rows)
        self.classifier = LogisticRegression(C=config["C"], l1_ratio=0.0, solver=config["solver"],
            max_iter=config["max_iter"], class_weight=config["class_weight"], random_state=config["random_seed"])
        matrix = self.preprocessor.transform(rows)
        self.rank = int(np.linalg.matrix_rank(matrix))
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            self.classifier.fit(matrix, np.array([r["y_failure"] for r in rows]))
        return self

    def predict_failure(self, rows):
        positive = list(self.classifier.classes_).index(1)
        return self.classifier.predict_proba(self.preprocessor.transform(rows))[:, positive]

    def audit(self):
        return {**self.preprocessor.audit(), "encoded_matrix_rank": self.rank,
            "classes": self.classifier.classes_.tolist(), "intercept": self.classifier.intercept_.tolist(),
            "coefficients": self.classifier.coef_.tolist(), "iterations": self.classifier.n_iter_.tolist()}


def folds_for(rows, config):
    fitting_rows(rows)
    if len({r["task_id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate primary rows")
    splitter = StratifiedGroupKFold(n_splits=config["n_splits"], shuffle=config["shuffle"], random_state=config["random_seed"])
    y, groups = np.array([r["y_failure"] for r in rows]), np.array([r["template_id"] for r in rows])
    return [(a.tolist(), b.tolist()) for a, b in splitter.split(np.zeros((len(rows), 1)), y, groups)]


def bit_distribution(rows):
    return {"n": len(rows), "ones": sum(r["features"][BIT] for r in rows),
        "unique": sorted({r["features"][BIT] for r in rows}),
        "positive_templates": sorted({r["template_id"] for r in rows if r["features"][BIT]})}


def paired_oof(rows, config, notify=None):
    split_indices = folds_for(rows, config["folds"])
    predictions = {"v1": np.full(len(rows), np.nan), "v2": np.full(len(rows), np.nan)}
    assigned = np.zeros(len(rows), dtype=int)
    reports = []
    fits = 0
    for number, (fit_idx, held_idx) in enumerate(split_indices, 1):
        fit, held = [rows[i] for i in fit_idx], [rows[i] for i in held_idx]
        fit_templates = sorted({r["template_id"] for r in fit})
        held_templates = sorted({r["template_id"] for r in held})
        if set(fit_templates) & set(held_templates):
            raise ValueError("Template crossed fold boundary")
        assigned[held_idx] += 1
        report = {"fold": number, "fit_indices": fit_idx, "heldout_indices": held_idx,
            "fit_task_ids": [r["task_id"] for r in fit], "heldout_task_ids": [r["task_id"] for r in held],
            "fit_templates": fit_templates, "heldout_templates": held_templates,
            "fit_failures": sum(r["y_failure"] for r in fit), "heldout_failures": sum(r["y_failure"] for r in held),
            "new_feature_fit_distribution": bit_distribution(fit), "new_feature_heldout_distribution": bit_distribution(held),
            "arms": {}}
        if {r["y_failure"] for r in fit} != {0, 1}:
            report["status"] = "NOT_ESTIMABLE_ONE_CLASS_TRAIN"
            reports.append(report)
            continue
        report["status"] = "FIT"
        for name in ("v1", "v2"):
            if notify: notify(number, name, "started", None)
            fits += 1
            model = (FormalLogistic().fit(v1_rows(fit), config["logistic"]) if name == "v1"
                     else V2Logistic().fit(fit, config["logistic"]))
            p = model.predict_failure(v1_rows(held) if name == "v1" else held)
            predictions[name][held_idx] = p
            audit = model.audit()
            if audit["fit_task_ids"] != report["fit_task_ids"]:
                raise ValueError("Preprocessing fit scope differs from training fold")
            report["arms"][name] = audit
            if notify: notify(number, name, "completed", {"iterations": audit["iterations"]})
        # Both arms must have exactly the same base preprocessing fitted on fit.
        for key in ("dropped_constants", "numeric_medians", "numeric_means", "numeric_scales", "fit_task_ids"):
            if report["arms"]["v1"][key] != report["arms"]["v2"][key]:
                raise ValueError("Shared v1 preprocessing differs between arms")
        reports.append(report)
    if not (assigned == 1).all():
        raise ValueError("Each primary needs exactly one OOF assignment")
    return predictions, {"folds": reports, "fit_invocations": fits, "full_train_fit": False,
        "same_folds_for_both_arms": True, "complete_predictions": all(np.isfinite(p).all() for p in predictions.values())}


def evaluation(labels, probabilities):
    result = metric_set(labels, probabilities)
    tp, fp, positives = result["true_positives"], result["false_positives"], result["failures"]
    result["confusion_matrix"] = {"tn": len(labels) - positives - fp, "fp": fp, "fn": positives - tp, "tp": tp}
    result["calibration_bins"] = calibration_bins(labels, probabilities)
    return result


def paired_bootstrap(rows, predictions, *, resamples, seed):
    groups = sorted({r["template_id"] for r in rows})
    by_template = {g: np.array([i for i, r in enumerate(rows) if r["template_id"] == g]) for g in groups}
    y = np.array([r["y_failure"] for r in rows])
    rng = np.random.default_rng(seed)
    values = {arm: {metric: [] for metric in METRICS} for arm in ("v1", "v2", "delta_v2_minus_v1")}
    draws = []
    for _ in range(resamples):
        chosen = rng.choice(groups, size=len(groups), replace=True).tolist()
        indices = np.concatenate([by_template[g] for g in chosen])
        a, b = (metric_set(y[indices], predictions[arm][indices]) for arm in ("v1", "v2"))
        delta = {m: b[m] - a[m] if a[m] is not None and b[m] is not None else None for m in METRICS}
        for arm, metrics in (("v1", a), ("v2", b), ("delta_v2_minus_v1", delta)):
            for name in METRICS:
                if metrics[name] is not None: values[arm][name].append(metrics[name])
        draws.append({"templates": chosen, "row_count": len(indices),
            "v1": {m: a[m] for m in METRICS}, "v2": {m: b[m] for m in METRICS}, "delta_v2_minus_v1": delta})
    summary = {"unit": "template_id", "template_count": len(groups), "resamples": resamples,
        "seed": seed, "paired_draws": True, "models_refitted": False,
        "conditional_on_fixed_oof_predictions": True,
        "intervals": {arm: {m: {"lower": float(np.quantile(v, .025)) if v else None,
            "upper": float(np.quantile(v, .975)) if v else None, "valid_resamples": len(v),
            "undefined_resamples": resamples - len(v)} for m, v in metrics.items()} for arm, metrics in values.items()}}
    return summary, draws
