from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from risk_agent.collection.formal_readiness import audit_formal_readiness, verify_engineering_gate
from risk_agent.collection.continue_acceptance_v2 import load_entry

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def gate(tmp_path):
    profile, config = load_entry(ROOT)
    run = tmp_path / "gate"; run.mkdir()
    hashes = {name: True for name in ("agent_state_hash", "next_model_input_hash", "environment_state_hash",
        "runtime_state_hash", "identity_config_hash", "combined_state_hash")}
    tasks = [{"task_id": tid, "engineering_passed": True, "resources_released": True,
        "provider_audit": {"complete": True}, "component": {"complete_chain": True,
        "first_input_exact": True, "restore_hashes": hashes, "checkpoint_immutable": True}}
        for tid in config["task_ids"]]
    (run / "gate_summary_final_audit.json").write_text(json.dumps({
        "gate": "CONTINUE_ENGINEERING_GATE_PASS", "passed": True, "offline_fixture": False,
        "connection_resources_released": True, "real_model_calls": 20,
        "run_id": "synthetic_gate_fixture", "tasks": tasks}))
    (run / "run_manifest.json").write_text(json.dumps({"freeze_manifest_sha256": profile.manifest_sha256}))
    files = [{"path": p.name, "size_bytes": p.stat().st_size,
        "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in run.iterdir()]
    (run / "artifact_manifest.json").write_text(json.dumps({"files": files}))
    return run


def test_actual_catalog_controls_expose_formal_gaps_without_making_data(gate):
    result = audit_formal_readiness(ROOT, gate)
    assert result["engineering_gate"]["verified"]
    assert result["gate"] == "FORMAL_COLLECTION_BLOCKED"
    assert not result["formal_collection_ready"] and not result["formal_collection_started"]
    assert result["task_count"] == 160 and result["templates"] == 80
    assert result["tasks_by_split"] == {"train": 96, "validation": 32, "test": 32}
    assert result["tasks_without_template_fault_control"] == 80
    assert result["f6_planned_commit_states"] == {
        "train": {"not_committed": 2}, "validation": {"committed": 2}, "test": {"unknown": 2}}
    assert result["f6_protocol_checks"] == {"train_all_three_commit_states": False,
        "validation_at_least_two_including_unknown": False, "test_at_least_two_including_unknown": True}
    assert result["candidate_feature_count"] == result["feature_extractors_still_pending"] == 21
    assert result["enabled_features"] == []
    assert result["model_requests"] == result["remote_connections"] == 0
    assert all(not t["formal_instance_schedule_registered"] for t in result["task_fault_control_inventory"])
    assert all("expected" not in t and "label" not in t for t in result["task_fault_control_inventory"])


@pytest.mark.parametrize("defect", ["bytes", "extra_file", "missing_file", "missing_gate"])
def test_proof_failure_does_not_relabel_or_promote_smoke(gate, defect):
    if defect == "bytes":
        p = gate / "gate_summary_final_audit.json"; p.write_text(p.read_text() + " ")
    if defect == "extra_file": (gate / "unregistered").write_text("new")
    if defect == "missing_file": (gate / "run_manifest.json").unlink()
    if defect == "missing_gate": gate = gate / "absent"
    profile, config = load_entry(ROOT)
    checked = verify_engineering_gate(gate, profile.manifest_sha256, config["task_ids"])
    assert not checked["verified"] and not checked["smoke_data_eligible_for_formal_dataset"]
    result = audit_formal_readiness(ROOT, gate)
    assert "REAL_ENGINEERING_GATE_PROOF_MISSING_OR_INVALID" in result["blockers"]
    assert not result["formal_collection_started"]


def test_cli_records_blocked_with_exclusive_output_and_exact_manifest(tmp_path, gate):
    from scripts.audit_formal_collection_readiness import main
    output = tmp_path / "audit"
    args = ["--root", str(ROOT), "--gate-run", str(gate), "--output-dir", str(output)]
    assert main(args) == 2
    before = (output / "formal_readiness.json").read_bytes()
    manifest = json.loads((output / "artifact_manifest.json").read_text())
    assert manifest["files"][0]["sha256"] == hashlib.sha256(before).hexdigest()
    with pytest.raises(FileExistsError): main(args)
    assert (output / "formal_readiness.json").read_bytes() == before


def test_audit_never_constructs_model_or_resolves_credentials(monkeypatch, gate):
    from risk_agent.agent.worker_router import RemoteModelSettings
    def forbidden(*args, **kwargs):
        pytest.fail("Offline audit must not resolve credentials or create model clients")
    monkeypatch.setattr(RemoteModelSettings, "resolve_api_key", forbidden)
    monkeypatch.setattr(RemoteModelSettings, "resolve_ca_bundle", forbidden)
    assert audit_formal_readiness(ROOT, gate)["gate"] == "FORMAL_COLLECTION_BLOCKED"


def test_candidate_plan_covers_all_instances_without_changing_tasks_or_split():
    from collections import Counter
    from risk_agent.faults import FaultConfig, FaultType
    plan = yaml.safe_load((ROOT / "configs/formal_collection_candidate_v1.yaml").read_text())
    profile, _ = load_entry(ROOT)
    actual = {t.task_id: t for t in profile.tasks}
    assert plan["status"] == "PROPOSED_NOT_FROZEN_NOT_EXECUTABLE"
    assert plan["formal_collection_ready"] is False
    assert plan["parent_manifest_sha256"] == profile.manifest_sha256
    assert len(plan["tasks"]) == len({r["task_id"] for r in plan["tasks"]}) == 160
    assert {r["task_id"] for r in plan["tasks"]} == set(actual)
    assert Counter(r["split"] for r in plan["tasks"]) == {"train": 96, "validation": 32, "test": 32}
    assert sum(r["scheduled_repeat"] for r in plan["tasks"]) == 32
    for row in plan["tasks"]:
        task = actual[row["task_id"]]
        assert row["split"] == task.split.value and row["template_id"] == task.template_id
        assert row["domain"] == task.domain.value
        assert "expected" not in row and "label" not in row and "prompt" not in row
        if row["fault_type"] == "F5_REPEATED_ACTION":
            assert row["parameters"]["adapter_implementation_required"] is True
            # The draft must not pretend the existing repeated-action injector
            # already implements the first-call no-progress protocol.
            with pytest.raises(ValueError, match="F5 requires"):
                FaultConfig(row["fault_id"], FaultType(row["fault_type"]), row["fault_seed"],
                    row["target_tool"], tuple(row["call_indices"]), 0.0, row["parameters"])
        else:
            FaultConfig(row["fault_id"], FaultType(row["fault_type"]), row["fault_seed"],
                row["target_tool"], tuple(row["call_indices"]), 0.0, row["parameters"])


@pytest.mark.parametrize("split", ["train", "validation", "test"])
def test_candidate_fault_coverage_and_public_versioned_state_are_honest(split):
    from collections import Counter
    plan = yaml.safe_load((ROOT / "configs/formal_collection_candidate_v1.yaml").read_text())
    rows = [r for r in plan["tasks"] if r["split"] == split]
    for domain in {r["domain"] for r in rows}:
        group = [r for r in rows if r["domain"] == domain]
        counts = Counter(r["fault_type"] for r in group)
        assert {"F0_NO_FAULT", "F1_TIMEOUT", "F2_API_503", "F3_INVALID_ARGUMENT", "F5_REPEATED_ACTION"} <= counts.keys()
        if domain == "api_workflow":
            states = Counter(r["parameters"]["commit_status"] for r in group if r["fault_type"] == "F6_PARTIAL_FAILURE")
            assert counts["F4_STALE_OBSERVATION"] >= 1
            if split == "train": assert {"not_committed", "committed", "unknown"} <= states.keys()
            else: assert sum(states.values()) >= 2 and states["unknown"] >= 1
        else:
            assert counts["F4_STALE_OBSERVATION"] == counts["F6_PARTIAL_FAILURE"] == 0


def test_candidate_repeats_and_features_are_selected_before_outcomes():
    plan = yaml.safe_load((ROOT / "configs/formal_collection_candidate_v1.yaml").read_text())
    for split, count in plan["repeat_targets_by_split"].items():
        rows = [r for r in plan["tasks"] if r["split"] == split]
        expected = sorted(rows, key=lambda r: hashlib.sha256(("formal-repeat-v1|"+r["task_id"]).encode()).hexdigest())[:count]
        assert {r["task_id"] for r in expected} == {r["task_id"] for r in rows if r["scheduled_repeat"]}
    assert len(plan["proposed_feature_names"]) == 18
    assert not {"no_progress_count", "progress_delta", "state_change_count"} & set(plan["proposed_feature_names"])
    assert plan["feature_activation_status"] == "DISABLED_PENDING_CUTOFF_AND_PROVENANCE_TESTS"
