import importlib.util
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "audit_phase2_history.py"
SPEC = importlib.util.spec_from_file_location("audit_phase2_history", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
build_manifest = MODULE.build_manifest


def test_phase2_history_manifest_is_deterministic_and_passes_gate() -> None:
    first = build_manifest(PROJECT_ROOT)
    second = build_manifest(PROJECT_ROOT)

    assert first == second
    assert first["gate_passed"] is True
    assert all(first["audit"]["gate_checks"].values())
    assert len(first["historical_trees"]) == 10
    assert all(len(item["tree_sha256"]) == 64 for item in first["historical_trees"])


def test_phase2_history_expected_counts_and_schema_findings() -> None:
    manifest = build_manifest(PROJECT_ROOT)
    audit = manifest["audit"]

    assert audit["day08"]["logical_task_count"] == 24
    assert audit["day08"]["raw_attempt_count"] == 43
    assert audit["day08"]["unique_task_run_id_count"] == 43
    assert audit["day09"]["checkpoint_count"] == 20
    assert audit["day09"]["branch_record_count"] == 60
    assert audit["day09"]["applicable_branch_count"] == 36
    assert audit["day10"]["feature_count"] == 26
    assert audit["day11"]["feature_schema_matches_day10"] is True
    assert audit["day13"]["policy_record_count"] == 180
    assert audit["day13"]["branch_result_count"] == 180
    assert audit["day13"]["execution_payload_mismatch_count"] == 0

    findings = {item["code"]: item for item in manifest["recorded_findings"]}
    assert findings["DAY08_CONTROLLER_TYPE_ABSENT"]["count"] == 43
    assert findings["DAY09_CHECKPOINT_HASH_ALIAS"]["count"] == 20
    assert audit["day09"]["missing_state_hash_count"] == 0
