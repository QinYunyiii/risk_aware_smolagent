from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import joblib
import numpy as np
import pytest
import yaml

from risk_agent.agent.agent_state_adapter import canonical_hash
from risk_agent.collection.formal_features import NAMES
from risk_agent.prediction.formal_logistic import FormalLogistic, FormalPreprocessor
from risk_agent.prediction.formal_metrics import metric_set, calibration_bins, cluster_bootstrap
from risk_agent.prediction.formal_baseline_run import load_contract, load_snapshot, grouped_oof, run_baseline

ROOT = Path(__file__).resolve().parents[1]


def rows(n=80, split="train"):
    result = []
    for i in range(n):
        failure = int((i // 2) % 5 == 0)
        values = {name: 1 for name in NAMES}
        values.update(token_budget_fraction=(i + 1) / 100, recent_model_latency=1 + failure,
                      latest_observed_error_type="timeout" if failure else "none")
        result.append({"features": values, "features_sha256": canonical_hash(values), "split": split,
            "allowed_for_weight_fitting": split == "train", "y_failure": failure, "y_continue": 1 - failure,
            "template_id": f"{split}_template_{i // 2}", "task_id": f"{split}_{i}",
            "checkpoint_id": f"cp_{split}_{i}", "risk_record_sha256": f"fixture_{split}_{i}"})
    return result


def config():
    return yaml.safe_load((ROOT / "configs/formal_logistic_baseline_v1.yaml").read_text())


@pytest.mark.parametrize("split", ["validation", "test"])
def test_nontrain_cannot_fit_preprocessing_or_weights(split):
    with pytest.raises(ValueError, match="Only Train"):
        FormalPreprocessor().fit(rows(20, split))
    with pytest.raises(ValueError, match="Only Train"):
        FormalLogistic().fit(rows(20, split), config()["models"]["logistic_regression"])


def test_validation_extremes_labels_and_metadata_do_not_change_fitted_preprocessing():
    train, val = rows(), rows(12, "validation")
    model = FormalLogistic().fit(train, config()["models"]["logistic_regression"])
    before = deepcopy(model.audit())
    first = model.predict_failure(val)
    for row in val:
        row.update(y_failure=1 - row["y_failure"], fault_type="hidden", future_result="future")
    assert np.array_equal(first, model.predict_failure(val))
    val[0]["features"]["token_budget_fraction"] = 100000
    val[0]["features_sha256"] = canonical_hash(val[0]["features"])
    model.predict_failure(val)
    assert model.audit() == before
    assert set(model.preprocessor.fit_task_ids) == {r["task_id"] for r in train}


def test_missing_values_use_training_median_and_all_missing_columns_are_dropped():
    train = rows()
    for row in train:
        row["features"]["recent_tool_latency"] = None
    train[0]["features"]["token_budget_fraction"] = None
    for row in train:
        row["features_sha256"] = canonical_hash(row["features"])
    pre = FormalPreprocessor().fit(train)
    assert "recent_tool_latency" in pre.constants
    index = pre.numeric.index("token_budget_fraction")
    assert pre.medians[index] == np.median([r["features"]["token_budget_fraction"] for r in train[1:]])
    assert np.isfinite(pre.transform(train)).all()


def test_unknown_category_refused_even_if_categorical_feature_was_constant():
    train = rows()
    for row in train:
        row["features"]["latest_observed_error_type"] = "none"
        row["features_sha256"] = canonical_hash(row["features"])
    pre = FormalPreprocessor().fit(train)
    val = rows(1, "validation")
    val[0]["features"]["latest_observed_error_type"] = "private_fault"
    val[0]["features_sha256"] = canonical_hash(val[0]["features"])
    with pytest.raises(ValueError, match="category"):
        pre.transform(val)


def test_positive_class_and_serialization_exact(tmp_path):
    train, val = rows(), rows(8, "validation")
    model = FormalLogistic().fit(train, config()["models"]["logistic_regression"])
    expected = model.classifier.predict_proba(model.preprocessor.transform(val))[:, 1]
    assert model.classifier.classes_.tolist() == [0, 1]
    assert np.array_equal(model.predict_failure(val), expected)
    path = tmp_path / "model.joblib"
    joblib.dump(model, path)
    assert np.array_equal(joblib.load(path).predict_failure(val), expected)
    train[0]["y_continue"] = train[0]["y_failure"]
    with pytest.raises(ValueError, match="direction"):
        FormalLogistic().fit(train, config()["models"]["logistic_regression"])


def test_grouped_oof_is_complete_and_every_preprocessor_stays_in_its_fit_fold():
    train = rows()
    predictions, folds = grouped_oof(train, config())
    assert len(folds) == 3 and all(np.isfinite(p).all() for p in predictions.values())
    by_id = {r["task_id"]: r for r in train}
    held = []
    for fold in folds:
        assert not set(fold["train_templates"]) & set(fold["heldout_templates"])
        assert fold["logistic_preprocessing"]["fit_task_ids"] == fold["fit_task_ids"]
        assert not set(fold["fit_task_ids"]) & set(fold["heldout_task_ids"])
        assert fold["constant_train_prevalence"] == np.mean([by_id[t]["y_failure"] for t in fold["fit_task_ids"]])
        held += fold["heldout_task_ids"]
    assert sorted(held) == sorted(by_id) and len(set(held)) == len(held)


def test_one_class_training_fold_does_not_fit_or_substitute_logistic(monkeypatch):
    import risk_agent.prediction.formal_baseline_run as module
    train = rows(20)
    fit = np.array([i for i, r in enumerate(train) if r["y_failure"] == 0])
    held = np.array([i for i, r in enumerate(train) if r["y_failure"] == 1])
    class Splitter:
        def __init__(self, **kwargs):
            pass
        def split(self, *args):
            yield fit, held
    monkeypatch.setattr(module, "StratifiedGroupKFold", Splitter)
    def forbidden(*args):
        raise AssertionError("One-class fit must not run")
    monkeypatch.setattr(FormalLogistic, "fit", forbidden)
    predictions, folds = module.grouped_oof(train, config())
    assert np.isnan(predictions["logistic_regression"]).all()
    assert folds[0]["logistic_status"] == "NOT_ESTIMABLE_ONE_CLASS_TRAIN"


def test_single_class_auc_and_no_positive_precision_remain_undefined():
    values = metric_set([0, 0], [.1, .2])
    assert values["auroc"] is None and values["auprc"] is None and values["precision"] is None
    assert values["recall"] is None and values["precision_denominator"] == 0
    values = metric_set([0, 1], [.1, .2])
    assert values["recall"] == 0 and values["recall_denominator"] == 1 and values["precision"] is None


@pytest.mark.parametrize("probabilities", [[-.1, .2], [.2, 1.1], [.1, float("nan")], [.1]])
def test_invalid_probabilities_rejected(probabilities):
    with pytest.raises(ValueError):
        metric_set([0, 1], probabilities)


def test_fractional_labels_rejected():
    with pytest.raises(ValueError):
        metric_set([0, .5], [.1, .2])


def test_calibration_has_five_equal_bins_including_probability_one():
    bins = calibration_bins([0, 1], [.0, 1.0])
    assert len(bins) == 5 and bins[0]["n"] == 1 and bins[4]["n"] == 1
    assert bins[2]["n"] == 0 and bins[2]["observed_failure_rate"] is None


def test_bootstrap_preserves_whole_clusters_and_uses_paired_draws(monkeypatch):
    import risk_agent.prediction.formal_metrics as module
    data = rows(5)
    calls = []
    def metric(y, p):
        calls.append(p.tolist())
        return {"auroc": None, "auprc": None, "brier_score": 0.0, "log_loss": 0.0, "precision": None, "recall": 0.0}
    monkeypatch.setattr(module, "metric_set", metric)
    first = np.array([.1, .2, .3, .4, .5])
    result = module.cluster_bootstrap(data, {"one": first, "two": first}, resamples=30, seed=7)
    assert calls[::2] == calls[1::2]
    for draw in calls:
        assert draw.count(.1) == draw.count(.2) and draw.count(.3) == draw.count(.4)
    assert len({len(c) for c in calls}) > 1
    assert result["models"]["one"]["auroc"]["undefined_resamples"] == 30


def test_bootstrap_is_reproducible_and_counts_single_class_draws():
    data = rows(4)
    data[0]["y_failure"] = data[1]["y_failure"] = 1
    data[2]["y_failure"] = data[3]["y_failure"] = 0
    a = cluster_bootstrap(data, {"model": np.array([.1, .2, .3, .4])}, resamples=30, seed=7)
    b = cluster_bootstrap(data, {"model": np.array([.1, .2, .3, .4])}, resamples=30, seed=7)
    assert a == b and a["models"]["model"]["auroc"]["undefined_resamples"] > 0


def test_full_local_model_workflow_uses_only_train_fit_and_exports_no_test(tmp_path):
    c = config()
    c["bootstrap"]["resamples"] = 5  # Tiny synthetic test only; live entry pins the frozen config.
    result = run_baseline(rows(), rows(12, "validation"), c, tmp_path)
    assert result["serialization_predictions_exact"] and not result["test_accessed"]
    assert not result["lightgbm_fitted"] and not result["calibrator_fitted"]
    assert set(result["full_train_preprocessing"]["fit_task_ids"]) == {r["task_id"] for r in rows()}
    exported = [json.loads(s) for s in (tmp_path / "predictions.jsonl").read_text().splitlines()]
    assert len(exported) == 2 * (80 + 12) and {r["source_split"] for r in exported} == {"train", "validation"}


def test_contract_pins_reviewed_parameters_and_snapshot_rejects_tampering(tmp_path):
    config_value, _ = load_contract(ROOT)
    assert config_value["models"]["logistic_regression"]["C"] == .1
    source = tmp_path / "runs/bad"
    source.mkdir(parents=True)
    (source / "artifact_manifest.json").write_text('{}')
    (tmp_path / "docs/phase2").mkdir(parents=True)
    (tmp_path / "docs/phase2/formal_baseline_preparation_manifest_v1.json").write_text(json.dumps(
        {"snapshot_manifest_sha256": "0" * 64}))
    with pytest.raises(ValueError, match="reviewed dataset"):
        load_snapshot(tmp_path, source)


def test_default_entry_does_not_fit_and_fit_requires_authorization(tmp_path):
    from scripts.run_formal_baseline import main
    with pytest.raises(ValueError, match="requires authorization"):
        main(["--mode", "fit", "--run-id", "unauthorized_fit_fixture", "--snapshot", str(tmp_path)])
    assert not (tmp_path / "logistic_regression.joblib").exists()


def test_existing_model_file_cannot_be_overwritten(tmp_path):
    path = tmp_path / "logistic_regression.joblib"
    path.write_bytes(b"preserved-original")
    with pytest.raises(FileExistsError):
        run_baseline(rows(), rows(12, "validation"), config(), tmp_path)
    assert path.read_bytes() == b"preserved-original"
