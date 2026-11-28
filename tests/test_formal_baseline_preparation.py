from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from risk_agent.agent.agent_state_adapter import canonical_hash
from risk_agent.collection.formal_features import NAMES
from risk_agent.prediction.formal_preparation import _index, audit_rows, feature_projection


def risk():
    values = {name: 1 for name in NAMES}
    values["latest_observed_error_type"] = "none"
    return {"feature_names": list(NAMES), "features": values, "features_sha256": canonical_hash(values)}


def example(split="train", index=1):
    return {"split": split, "task_id": f"{split}_{index}", "checkpoint_id": f"cp_{split}_{index}",
        "template_id": f"template_{split}", "y_continue": 1, "y_failure": 0, "features": risk()["features"]}


def test_exact_features_exclude_metadata_and_future_outcome():
    row = risk()
    row.update(fault_type="F6_PARTIAL_FAILURE", label=0, future_cost=999, expected="hidden", worker_id="worker-0")
    assert feature_projection(row) == row["features"]
    assert set(feature_projection(row)) == set(NAMES)


@pytest.mark.parametrize("bad", [True, float("inf"), float("nan"), "1"])
def test_numeric_bool_nonfinite_and_string_rejected(bad):
    row = risk()
    row["features"]["step_index"] = bad
    with pytest.raises(ValueError):
        feature_projection(row)


def test_explicit_missing_not_zero_filled():
    row = risk()
    row["features"]["recent_tool_latency"] = None
    row["features_sha256"] = canonical_hash(row["features"])
    assert feature_projection(row)["recent_tool_latency"] is None


@pytest.mark.parametrize("kind", ["extra", "category", "order", "hash"])
def test_allowlist_category_order_and_hash_rejected(kind):
    row = risk()
    if kind == "extra":
        row["features"]["fault_id"] = "hidden"
    elif kind == "category":
        row["features"]["latest_observed_error_type"] = "internal_fault_code"
    elif kind == "order":
        row["feature_names"].reverse()
    else:
        row["features"]["step_index"] = 2
    with pytest.raises(ValueError):
        feature_projection(row)


def test_constants_defined_only_from_train_and_lightgbm_remains_blocked():
    train = [example("train", i) for i in range(95)]
    for row in train[:10]:
        row.update(y_continue=0, y_failure=1)
    val = [example("validation", i) for i in range(32)]
    val[0]["features"]["step_index"] = 999
    report = audit_rows(train + val)
    assert "step_index" in report["train_constant_features"]
    assert report["logistic_feasibility_sample_gate"] and not report["lightgbm_gate_passed"]
    assert report["train"]["failures"] == 10 and report["model_fitted"] is False


@pytest.mark.parametrize("kind", ["test", "duplicate_task", "duplicate_cp", "overlap"])
def test_test_rows_duplicates_and_template_overlap_rejected(kind):
    rows = [example(), example("validation")]
    if kind == "test":
        rows.append(example("test"))
    elif kind == "duplicate_task":
        rows[1]["task_id"] = rows[0]["task_id"]
    elif kind == "duplicate_cp":
        rows[1]["checkpoint_id"] = rows[0]["checkpoint_id"]
    else:
        rows[1]["template_id"] = rows[0]["template_id"]
    with pytest.raises(ValueError):
        audit_rows(rows)


@pytest.mark.parametrize("split,formal,fixture", [("test", True, False), ("validation", False, True), ("train", True, False)])
def test_wrong_source_refused_before_manifest_or_labels(tmp_path, split, formal, fixture):
    directory = tmp_path / "runs/source"
    directory.mkdir(parents=True)
    (directory / "run_manifest.json").write_text(json.dumps({"authorized_split": split, "run_id": "source",
        "formal_experiment": formal, "offline_fixture": fixture}))
    with pytest.raises(ValueError, match="actual qualified"):
        _index(tmp_path, {"directory": str(directory), "run_id": "source"}, "validation")


def test_index_refuses_test_parameter_before_files(tmp_path):
    with pytest.raises(ValueError, match="sealed"):
        _index(tmp_path, {}, "test")


def test_index_requires_trusted_manifest_digest(tmp_path):
    directory = tmp_path / "runs/source"
    directory.mkdir(parents=True)
    (directory / "run_manifest.json").write_text(json.dumps({"authorized_split": "validation", "run_id": "source",
        "formal_experiment": True, "offline_fixture": False}))
    (directory / "artifact_manifest.json").write_text('{"files":[]}')
    with pytest.raises(ValueError, match="Trusted"):
        _index(tmp_path, {"directory": str(directory), "run_id": "source", "manifest": "artifact_manifest.json",
                          "manifest_sha256": "0" * 64}, "validation")


@pytest.mark.parametrize("label", [None, True, -1, 2])
def test_null_or_invalid_outcome_never_recoded(tmp_path, monkeypatch, label):
    from types import SimpleNamespace
    import risk_agent.prediction.formal_preparation as preparation
    row = {**risk(), "record_sha256": "risk", "split": "train", "formal_experiment": True,
        "offline_fixture": False, "execution_mode": "model_backed_formal", "controller_type": "qwen3_model",
        "task_id": "task", "template_id": "template", "label": label, "future_data_excluded": True}
    def read(path):
        return [row] if path.name == "risk_candidates.jsonl" else []
    monkeypatch.setattr(preparation, "read_records", read)
    task = SimpleNamespace(split=SimpleNamespace(value="train"), template_id="template")
    with pytest.raises(ValueError, match="Unqualified"):
        preparation._select(tmp_path, {}, ["risk"], "train", {"task": task})


def test_duplicate_release_reference_rejected_before_outcomes(tmp_path, monkeypatch):
    import risk_agent.prediction.formal_preparation as preparation
    def no_read(path):
        raise AssertionError("Outcomes accessed before checking duplicate eligibility references")
    monkeypatch.setattr(preparation, "read_records", no_read)
    with pytest.raises(ValueError, match="Duplicate eligibility"):
        preparation._select(tmp_path, {}, ["same", "same"], "train", {})


def test_sample_count_is_not_sufficient_for_lightgbm():
    train = [example("train", i) for i in range(200)]
    for row in train[:30]:
        row.update(y_continue=0, y_failure=1)
    report = audit_rows(train + [example("validation")])
    assert report["lightgbm_sample_count_gate_passed"] and not report["lightgbm_gate_passed"]
