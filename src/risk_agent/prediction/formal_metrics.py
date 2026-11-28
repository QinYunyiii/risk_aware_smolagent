"""Fixed-threshold metrics and paired template-cluster bootstrap, without fitting."""
from __future__ import annotations

from typing import Any
import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score


def metric_set(labels, probabilities) -> dict[str, Any]:
    raw, p = np.asarray(labels), np.asarray(probabilities, dtype=float)
    if raw.dtype.kind == "b" or not np.isin(raw, (0, 1)).all():
        raise ValueError("Non-binary labels")
    y = raw.astype(int)
    if y.ndim != 1 or y.shape != p.shape or not len(y) or not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
        raise ValueError("Invalid probability/label arrays")
    if not set(y.tolist()) <= {0, 1}:
        raise ValueError("Non-binary labels")
    predicted = p >= 0.5
    tp, fp = int(((y == 1) & predicted).sum()), int(((y == 0) & predicted).sum())
    positives, decisions = int(y.sum()), int(predicted.sum())
    clipped = np.clip(p, 1e-15, 1 - 1e-15)
    return {"n": len(y), "failures": positives, "auroc": float(roc_auc_score(y, p)) if len(set(y)) == 2 else None,
        "auprc": float(average_precision_score(y, p)) if len(set(y)) == 2 else None,
        "brier_score": float(np.mean((p - y) ** 2)),
        "log_loss": float(-np.mean(y * np.log(clipped) + (1 - y) * np.log(1 - clipped))),
        "precision": tp / decisions if decisions else None, "recall": tp / positives if positives else None,
        "predicted_failures": decisions, "true_positives": tp, "false_positives": fp,
        "precision_denominator": decisions, "recall_denominator": positives, "threshold": 0.5}


def calibration_bins(labels, probabilities) -> list[dict[str, Any]]:
    y, p = np.asarray(labels), np.asarray(probabilities)
    slots = np.minimum((p * 5).astype(int), 4)
    return [{"left": i / 5, "right": (i + 1) / 5, "n": int((slots == i).sum()),
             "mean_predicted": float(p[slots == i].mean()) if (slots == i).any() else None,
             "observed_failure_rate": float(y[slots == i].mean()) if (slots == i).any() else None} for i in range(5)]


def cluster_bootstrap(rows: list[dict[str, Any]], predictions: dict[str, np.ndarray], *, resamples: int,
                      seed: int) -> dict[str, Any]:
    groups = sorted({r["template_id"] for r in rows})
    indices = {g: np.array([i for i, r in enumerate(rows) if r["template_id"] == g]) for g in groups}
    y = np.array([r["y_failure"] for r in rows])
    names = ("auroc", "auprc", "brier_score", "log_loss", "precision", "recall")
    values = {model: {name: [] for name in names} for model in predictions}
    rng = np.random.default_rng(seed)
    for _ in range(resamples):
        chosen = np.concatenate([indices[g] for g in rng.choice(groups, size=len(groups), replace=True)])
        for model, p in predictions.items():
            metrics = metric_set(y[chosen], p[chosen])
            for name in names:
                if metrics[name] is not None:
                    values[model][name].append(metrics[name])
    return {"unit": "template_id", "template_count": len(groups), "resamples": resamples,
            "seed": seed, "paired_draws_across_models": True, "models_refitted": False,
            "intervals_conditional_on_fixed_predictions": True,
            "models": {model: {name: {"lower": float(np.quantile(v, .025)) if v else None,
                "upper": float(np.quantile(v, .975)) if v else None, "valid_resamples": len(v),
                "undefined_resamples": resamples - len(v)} for name, v in metrics.items()}
                for model, metrics in values.items()}}
