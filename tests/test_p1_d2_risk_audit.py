import importlib.util
import json
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "audit_p1_d2_risk_features.py"
SPEC = importlib.util.spec_from_file_location("audit_p1_d2_risk_features", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


@pytest.fixture(scope="module")
def audit_result():
    return MODULE.run_audit(PROJECT_ROOT)


def test_all_features_have_complete_provenance_and_safe_allowlist() -> None:
    assert tuple(item.name for item in MODULE.FEATURE_AUDIT) == MODULE.FEATURE_NAMES
    assert len(MODULE.FEATURE_AUDIT) == 26
    assert all(item.generation_path and item.source_expression for item in MODULE.FEATURE_AUDIT)
    assert not set(MODULE.LAST_FAILURE_FEATURES) & set(MODULE.DEPLOYMENT_ALLOWLIST)
    assert set(MODULE.STRICT_AGENT_VISIBLE_FEATURES) <= set(MODULE.DEPLOYMENT_ALLOWLIST)


def test_audit_reproduces_folds_and_required_ablations(audit_result) -> None:
    audit, predictions = audit_result
    assert audit["gate_passed"] is True
    assert all(audit["checks"].values())
    assert audit["population"] == {
        "sample_count": 20,
        "positive_failures": 9,
        "negative_successes": 11,
        "template_count": 20,
        "splits": ["train"],
        "official_validation_or_test_accessed": False,
    }
    assert audit["original_day10_reproduction"]["matches_at_absolute_tolerance_1e_12"]
    assert len(predictions) == 20 * (len(MODULE.ABLATIONS) * 2 + 1)
    assert all(not fold["template_overlap"] for fold in audit["folds"])


def test_feature_shortcut_and_fold_class_counts_are_explicit(audit_result) -> None:
    audit, _ = audit_result
    separation = {
        item["feature"]: item["unique_value_sets_disjoint"]
        for item in audit["feature_label_audit"]
    }
    assert separation["failure_response_received"] is True
    assert audit["day9_construction_evidence"]["last_failure_present_count"] == 15
    assert audit["day9_construction_evidence"]["synthetic_tool_history_nonempty_count"] == 15
    for fold in audit["folds"]:
        assert fold["train_positive_failures"] + fold["train_negative_successes"] == 16
        assert (
            fold["validation_positive_failures"] + fold["validation_negative_successes"]
            == 4
        )


def test_writer_creates_requested_machine_readable_outputs(tmp_path, audit_result) -> None:
    audit, predictions = audit_result
    MODULE.write_outputs(tmp_path, audit, predictions)

    document = json.loads((tmp_path / "risk_feature_ablation.json").read_text())
    lines = (tmp_path / "risk_oof_predictions.jsonl").read_text().splitlines()
    csv_lines = (tmp_path / "risk_feature_ablation.csv").read_text().splitlines()
    assert document["gate_passed"] is True
    assert len(lines) == len(predictions)
    assert csv_lines[0].startswith("feature_set,model,scope,fold")
    with pytest.raises(FileExistsError):
        MODULE.write_outputs(tmp_path, audit, predictions)
