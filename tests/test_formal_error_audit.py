from copy import deepcopy

import pytest

from risk_agent.collection.formal_features import NAMES
from risk_agent.prediction.formal_error_audit import describe, failure_ranks


def data():
    rows, predictions = [], []
    for split in ("train", "validation"):
        for i in range(4):
            features = {n: 1 for n in NAMES}
            category = ("other" if i == 0 else "none") if split == "train" else ("invalid_argument" if i == 0 else "none")
            features.update(latest_observed_error_type=category, recent_tool_latency=i, average_tool_latency=i,
                            observed_error_count=int(category != "none"))
            row = {"task_id": f"{split}_{i}", "split": split, "template_id": f"{split}_t{i}",
                   "risk_record_sha256": f"hash_{split}_{i}", "features": features, "y_failure": int(i < 2)}
            rows.append(row)
            predictions.append({"task_id": row["task_id"], "source_split": split,
                "evaluation_split": "train_oof" if split == "train" else "validation", "model": "logistic_regression",
                "p_failure": .1 + i * .01, "y_failure": row["y_failure"], "risk_record_sha256": row["risk_record_sha256"]})
    return rows, predictions


def test_ties_are_half_pairs_and_rank_intervals_are_explicit():
    r = failure_ranks([{"task_id": "fail", "y_failure": 1, "p_failure": .2},
                       {"task_id": "pass", "y_failure": 0, "p_failure": .2}])
    assert r["auroc"] == .5 and r["pair_credit"] == .5
    assert r["positive_rows"][0]["best_rank"] == 1 and r["positive_rows"][0]["worst_rank"] == 2


@pytest.mark.parametrize("label", [0, 1])
def test_single_class_auc_is_not_fabricated(label):
    result = failure_ranks([{"task_id": "one", "y_failure": label, "p_failure": .2}])
    assert result["auroc"] is None and result["pair_count"] == 0


def test_support_differences_and_fixed_threshold_do_not_mutate_inputs():
    rows, predictions = data()
    before = deepcopy((rows, predictions))
    result = describe(rows, predictions)
    assert result["train_only_error_categories"] == ["other"]
    assert result["validation_categories_unseen_in_train"] == ["invalid_argument"]
    assert result["score_ranges"]["validation"]["predicted_at_fixed_0_5"] == 0
    assert not result["threshold_search"] and not result["model_refitted"]
    assert (rows, predictions) == before


def test_range_audit_uses_train_bounds_only_and_detects_exact_duplicates():
    rows, predictions = data()
    rows[-1]["features"]["recent_tool_latency"] = 1000
    result = describe(rows, predictions)
    assert result["numeric_range_audit"]["recent_tool_latency"]["train_max"] == 3
    assert result["numeric_range_audit"]["recent_tool_latency"]["validation_outside_count"] == 1
    assert ("recent_tool_latency", "average_tool_latency") in result["nonconstant_exact_duplicate_columns"]


@pytest.mark.parametrize("kind", ["label", "hash", "scope", "missing", "duplicate", "probability", "test"])
def test_lineage_scope_and_probability_errors_fail_closed(kind):
    rows, predictions = data()
    if kind == "label":
        predictions[0]["y_failure"] = 1 - predictions[0]["y_failure"]
    elif kind == "hash":
        predictions[0]["risk_record_sha256"] = "foreign"
    elif kind == "scope":
        predictions[0]["evaluation_split"] = "validation"
    elif kind == "missing":
        predictions.pop()
    elif kind == "duplicate":
        predictions.append(deepcopy(predictions[0]))
    elif kind == "probability":
        predictions[0]["p_failure"] = float("nan")
    else:
        rows[0]["split"] = "test"
    with pytest.raises(ValueError):
        describe(rows, predictions)
