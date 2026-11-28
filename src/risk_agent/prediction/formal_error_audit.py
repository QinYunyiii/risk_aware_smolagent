"""Descriptive diagnostics of frozen predictions. No fitting or threshold search."""
from __future__ import annotations

from itertools import combinations
import math
from typing import Any

from risk_agent.collection.formal_features import NAMES

def failure_ranks(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Tie-aware ranks and exact per-positive contributions to pairwise AUROC."""
    positives = [r for r in rows if r["y_failure"] == 1]
    negatives = [r for r in rows if r["y_failure"] == 0]
    if not positives or not negatives:
        return {"auroc": None, "positive_rows": [], "pair_count": 0}
    result = []
    for row in positives:
        p = row["p_failure"]
        greater = sum(r["p_failure"] > p for r in rows)
        equal = sum(r["p_failure"] == p for r in rows)
        wins = sum(p > r["p_failure"] for r in negatives)
        ties = sum(p == r["p_failure"] for r in negatives)
        result.append({"task_id": row["task_id"], "p_failure": p, "best_rank": greater + 1,
            "worst_rank": greater + equal, "negative_wins": wins, "negative_ties": ties,
            "pair_credit": wins + .5 * ties})
    pairs = len(positives) * len(negatives)
    return {"auroc": sum(r["pair_credit"] for r in result) / pairs,
            "pair_credit": sum(r["pair_credit"] for r in result), "pair_count": pairs,
            "positive_rows": sorted(result, key=lambda r: -r["p_failure"])}


def describe(rows: list[dict], predictions: list[dict]) -> dict[str, Any]:
    source = {r["task_id"]: r for r in rows}
    if len(source) != len(rows) or {r["split"] for r in rows} != {"train", "validation"}:
        raise ValueError("Unique Train/Validation rows required; Test is sealed")
    selected = [p for p in predictions if p["model"] == "logistic_regression"]
    by_task = {p["task_id"]: p for p in selected}
    if len(by_task) != len(selected) or set(by_task) != set(source):
        raise ValueError("Exactly one frozen Logistic prediction per source row required")
    for task, p in by_task.items():
        row = source[task]
        if (p["source_split"] != row["split"] or p["y_failure"] != row["y_failure"]
                or p["risk_record_sha256"] != row["risk_record_sha256"]
                or p["evaluation_split"] != ("train_oof" if row["split"] == "train" else "validation")
                or p["p_failure"] is None or not math.isfinite(p["p_failure"]) or not 0 <= p["p_failure"] <= 1):
            raise ValueError("Prediction/source lineage or probability mismatch")
    train = [r for r in rows if r["split"] == "train"]
    val = [r for r in rows if r["split"] == "validation"]
    constant = [n for n in NAMES if len({str(r["features"][n]) for r in train}) == 1]
    varying = [n for n in NAMES if n not in constant]
    duplicate = [(first, second) for first, second in combinations(varying, 2)
                 if all(r["features"][first] == r["features"][second] for r in train)]
    ranges = {}
    for name in varying:
        if name == "latest_observed_error_type":
            continue
        values = [r["features"][name] for r in train if r["features"][name] is not None]
        if not values:
            continue
        lo, hi = min(values), max(values)
        ranges[name] = {"train_min": lo, "train_max": hi, "validation_outside_count": sum(
            r["features"][name] is not None and not lo <= r["features"][name] <= hi for r in val)}
    train_errors = {r["features"]["latest_observed_error_type"] for r in train}
    val_errors = {r["features"]["latest_observed_error_type"] for r in val}
    groups = {}
    for split in ("train", "validation"):
        subset = [r for r in rows if r["split"] == split]
        table = {}
        for category in sorted({r["features"]["latest_observed_error_type"] for r in subset}):
            members = [r for r in subset if r["features"]["latest_observed_error_type"] == category]
            table[category] = {"n": len(members), "failures": sum(r["y_failure"] for r in members),
                "templates": len({r["template_id"] for r in members}),
                "mean_frozen_probability": sum(by_task[r["task_id"]]["p_failure"] for r in members) / len(members)}
        groups[split] = table
    val_predictions = [by_task[r["task_id"]] for r in val]
    ranks = failure_ranks(val_predictions)
    for entry in ranks["positive_rows"]:
        row = source[entry["task_id"]]
        entry["decision_error_type"] = row["features"]["latest_observed_error_type"]
        entry["decision_observed_error_count"] = row["features"]["observed_error_count"]
    score_ranges = {split: {"min": min(by_task[r["task_id"]]["p_failure"] for r in rows if r["split"] == split),
        "max": max(by_task[r["task_id"]]["p_failure"] for r in rows if r["split"] == split),
        "predicted_at_fixed_0_5": sum(by_task[r["task_id"]]["p_failure"] >= .5 for r in rows if r["split"] == split)}
        for split in ("train", "validation")}
    return {"scope": "posthoc_descriptive_only", "model_refitted": False, "threshold_search": False,
        "test_accessed": False, "train_constants": constant, "train_varying": varying,
        "nonconstant_exact_duplicate_columns": duplicate, "numeric_range_audit": ranges,
        "train_only_error_categories": sorted(train_errors - val_errors),
        "validation_categories_unseen_in_train": sorted(val_errors - train_errors),
        "error_category_support": groups, "score_ranges": score_ranges, "validation_failure_ranks": ranks,
        "decision_time_clean_validation_failures": sum(r["y_failure"] == 1 and r["features"]["observed_error_count"] == 0 for r in val),
        "unchanged_threshold": .5, "subgroup_metrics_are_exploratory": True}
