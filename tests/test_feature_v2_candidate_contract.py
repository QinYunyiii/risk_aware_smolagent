"""Day 2 contract tests use synthetic files; no checkpoint extraction or fit."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/risk_agent/prediction/train_public_signals_v2.py"
# Avoid importing prediction/__init__.py and its unnecessary training libraries.
spec = importlib.util.spec_from_file_location("_test_v2_candidate_contract", SOURCE)
contract = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = contract
spec.loader.exec_module(contract)


def config():
    return json.loads((ROOT / contract.CONFIG).read_text())


def base_names():
    return tuple(config()["feature_names"][:-1])


def locked_fixture(tmp_path):
    files = {contract.CONFIG: json.dumps(config()), contract.PROTOCOL: "Synthetic candidate protocol",
        contract.BASE: "NAMES = " + repr(base_names()),
        "src/risk_agent/prediction/train_public_signals.py": "# synthetic parser source",
        "src/risk_agent/prediction/train_public_signals_v2.py": "# synthetic contract source"}
    digests = {}
    for name, text in files.items():
        path = tmp_path / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text)
        digests[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    lock = {"status": contract.STATUS, "execution_authorized": False, "files": digests}
    path = tmp_path / contract.MANIFEST; path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(lock))
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_exact_v1_plus_one_and_execution_stays_disabled(tmp_path):
    _, digest = locked_fixture(tmp_path)
    result = contract.load_candidate(tmp_path, expected_manifest_sha256=digest)
    assert result.feature_names == (*base_names(), "public_completion_unknown")
    assert len(result.feature_names) == 19 and not result.execution_authorized
    assert result.status == "PROPOSED_NOT_APPROVED_NOT_EXECUTABLE"
    assert not hasattr(contract, "extract") and not hasattr(contract, "backfill")


@pytest.mark.parametrize("change", ["reorder", "remove", "extra", "base_version", "impute", "pattern",
    "future", "hidden", "identity", "backfill", "comparison", "activation", "collection", "linkage",
    "normal_zero_without_checks", "reuse", "reuse_strategy", "targets", "status"])
def test_research_boundaries_cannot_drift(change):
    c = deepcopy(config())
    if change == "reorder": c["feature_names"][:2] = reversed(c["feature_names"][:2])
    elif change == "remove": c["feature_names"].pop()
    elif change == "extra": c["feature_names"].append("task_id")
    elif change == "base_version": c["base_schema_version"] = "v0"
    elif change == "impute": c["new_feature"]["unknown_policy"] = "zero"
    elif change == "pattern": c["new_feature"]["match_pattern"] = "unknown"
    elif change == "future": c["new_feature"]["future_data_allowed"] = True
    elif change == "hidden": c["new_feature"]["hidden_metadata_allowed"] = True
    elif change == "identity": c["new_feature"]["task_or_template_identity_as_feature"] = True
    elif change in {"backfill", "comparison", "activation", "collection"}:
        c["authorization"][{"backfill": "historical_backfill", "comparison": "model_comparison",
            "activation": "production_activation", "collection": "new_collection"}[change]] = True
    elif change == "linkage": c["correspondence"]["multiple_calls"] = "trust_order"
    elif change == "normal_zero_without_checks": c["new_feature"]["normal_observation_policy"] = "always_zero"
    elif change == "reuse": c["reuse"]["modify_existing_parser"] = True
    elif change == "reuse_strategy": c["reuse"]["strategy"] = "duplicate_parser"
    elif change == "targets": c["historical_backfill_targets_not_executed_today"]["ones"] = 95
    else: c["status"] = "APPROVED"
    with pytest.raises(ValueError): contract.validate_definition(c, base_names())


@pytest.mark.parametrize("value", [0, 1, "UNKNOWN"])
def test_diagnostic_domain_is_explicit(value):
    contract.validate_diagnostic_value(value)


@pytest.mark.parametrize("value", [True, False, 0.0, 1.0, "0", "1", None, "unknown", 2])
def test_no_silent_coercion(value):
    with pytest.raises(ValueError): contract.validate_diagnostic_value(value)


def test_unknown_is_not_publishable_and_is_not_mutated():
    value = "UNKNOWN"
    with pytest.raises(ValueError, match="UNKNOWN"): contract.validate_publishable_value(value)
    assert value == "UNKNOWN"
    for v in (0, 1): contract.validate_publishable_value(v)


def test_manifest_and_member_hashes_fail_closed(tmp_path):
    _, digest = locked_fixture(tmp_path)
    with pytest.raises(ValueError, match="Manifest"):
        contract.load_candidate(tmp_path, expected_manifest_sha256="0" * 64)
    (tmp_path / contract.PROTOCOL).write_text("changed")
    with pytest.raises(ValueError, match="Hash"):
        contract.load_candidate(tmp_path, expected_manifest_sha256=digest)


@pytest.mark.parametrize("escape", ["parent", "absolute", "symlink"])
def test_dependency_path_escape_rejected(tmp_path, escape):
    path, _ = locked_fixture(tmp_path)
    lock = json.loads(path.read_text())
    if escape == "symlink":
        protocol = tmp_path / contract.PROTOCOL; protocol.unlink()
        target = tmp_path / "outside.json"; target.write_text("forbidden synthetic bytes")
        protocol.symlink_to(target)
    else:
        lock["files"]["../outside.json" if escape == "parent" else "/tmp/outside.json"] = "0" * 64
        path.write_text(json.dumps(lock))
    with pytest.raises(ValueError):
        contract.load_candidate(tmp_path, expected_manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
