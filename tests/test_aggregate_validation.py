from __future__ import annotations

from copy import deepcopy
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from risk_agent.agent.agent_state_adapter import canonical_hash
from risk_agent.collection.aggregate_target_contract import (
    TARGET_CONFIG, TARGET_MANIFEST, claim_aggregate_cohort, load_aggregate_contract,
)
from risk_agent.collection.aggregate_target_policy import AggregateFaultInjector, POLICY, applicable
from risk_agent.collection.aggregate_validation import AggregateValidationCollector
from risk_agent.collection.aggregate_validation_fixture import AggregateValidationOfflineFactory
from risk_agent.collection.continue_gate import ContinueGateError
from risk_agent.collection.formal_contract import PLAN, read_records
from risk_agent.collection.formal_features import extract_decision_features
from risk_agent.collection.validation_collection import ValidationCollector
from risk_agent.collection.validation_continue import ValidationContinueRunner

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.timeout(180)


@pytest.fixture(scope="module")
def collected(tmp_path_factory):
    profile, plan, *_ = load_aggregate_contract(ROOT)
    factory = AggregateValidationOfflineFactory(profile.tasks, plan)
    directory = tmp_path_factory.mktemp("aggregate_validation") / "run"
    collector = AggregateValidationCollector(root=ROOT, run_directory=directory,
        run_id="aggregate_validation_fixture", offline_fixture=True, agent_factory=factory)
    try:
        summary = collector.run_validation()
    finally:
        collector.close()
    return directory, summary, factory, collector


def test_overlay_only_changes_approved_metadata():
    from risk_agent.collection.validation_contract import load_validation_contract
    _, original, _, old = load_validation_contract(ROOT)
    _, revised, _, current, _, _ = load_aggregate_contract(ROOT)
    assert original["formal_experiment_id"] != revised["formal_experiment_id"] and old == current
    changes = []
    for first, second in zip(original["tasks"], revised["tasks"]):
        expected = deepcopy(first)
        if applicable(first):
            expected["parameters"]["target_match_policy"] = POLICY
            changes.append(second["split"])
        assert expected == second
    assert changes.count("train") == 0 and changes.count("validation") == changes.count("test") == 2
    assert all("target_match_policy" not in c["parameters"] for c in original["tasks"])


def test_complete_gate_uses_alternative_aggregation_path(collected):
    directory, summary, factory, collector = collected
    assert summary["quality_passed"] and summary["valid_primary"] == 32
    assert summary["repeat_branches_valid"] == 7 and summary["fault_coverage_passed"]
    assert summary["target_match_policy"] == POLICY and not summary["formal_experiment"]
    assert not (directory / "validation_eligibility_release.json").exists()
    manifest = json.loads((directory / "run_manifest.json").read_text())
    assert manifest["aggregate_manifest_sha256"] == collector.flags["aggregate_manifest_sha256"]
    assert canonical_hash(json.loads((directory / "resolved_plan.json").read_text())) == manifest["resolved_plan_sha256"]
    for case in collector.cases.values():
        if case["split"] != "validation" or not applicable(case):
            continue
        task_id = case["task_id"]
        source = [json.loads(s) for s in (directory / "internal" / task_id / "source.jsonl").read_text().splitlines()]
        branch = [json.loads(s) for s in (directory / "internal" / task_id / "branch_1.jsonl").read_text().splitlines()]
        assert source[0]["tool_name"] == "read_csv" and not any(e["triggered"] for e in source)
        triggered = [e for e in branch if e["triggered"]]
        assert len(triggered) == 1 and triggered[0]["tool_name"] == "aggregate_records"
        assert all(e["target_match_policy"] == POLICY for e in source + branch)
    assert {r["task_id"] for r in factory.requests} == collector.allowed


def test_frozen_lifecycle_and_all_restores_features_provenance(collected):
    directory, _, _, collector = collected
    for method in ("_capture", "_branch", "_packet", "_persist_records", "summary", "run_validation"):
        assert getattr(AggregateValidationCollector, method) is getattr(ValidationCollector, method)
    for path in directory.glob("tasks/*/task_result.json"):
        result = json.loads(path.read_text())
        assert result["capture_step"] == 1 and result["source_unchanged"] and result["resources_released"]
        for component in [result["component"], *([result["repeat"]] if "repeat" in result else [])]:
            assert component["complete_chain"] and component["first_input_exact"] and component["checkpoint_immutable"]
            assert len(component["restore_hashes"]) == 6 and all(component["restore_hashes"].values())
            assert not component["tool_replay_detected"]
        providers = json.loads((path.parent / "provider_archive.json").read_text())
        assert collector.order.verify(result["task_id"], providers)
        source = json.loads((path.parent / "source_result.json").read_text())
        snapshot = json.loads((Path(source["checkpoint"]["checkpoint_path"]) / "agent/agent_state.json").read_text())["payload"]
        tools = [json.loads(s) for s in (path.parent / "source_tools.jsonl").read_text().splitlines()]
        actual = extract_decision_features(agent_payload=snapshot, budget=source["source_budget"],
            source_model_records=json.loads((path.parent / "source_model_requests.json").read_text()), source_tool_records=tools)
        packet = json.loads((path.parent / "decision_features.json").read_text())
        assert actual["values"] == packet["values"] and actual["features_sha256"] == packet["features_sha256"]
        assert len(packet["values"]) == 18
    for kind in ("tasks", "checkpoints", "continues", "risk_candidates"):
        rows = read_records(directory / "records" / (kind + ".jsonl"))
        assert all(r["aggregate_manifest_sha256"] == collector.flags["aggregate_manifest_sha256"]
                   and r["policy_sha256"] == collector.flags["policy_sha256"] and r["split"] == "validation" for r in rows)


@pytest.mark.parametrize("fault", ["F2_API_503", "F5_REPEATED_ACTION"])
def test_fault_consumed_in_source_not_reinjected_after_restore(tmp_path, fault):
    profile, plan, *_ = load_aggregate_contract(ROOT)
    target = next(c for c in plan["tasks"] if c["split"] == "validation" and applicable(c) and c["fault_type"] == fault)
    factory = AggregateValidationOfflineFactory(profile.tasks, plan, source_aggregate=True)
    collector = AggregateValidationCollector(root=ROOT, run_directory=tmp_path / "run", run_id="consumed_fixture",
        offline_fixture=True, agent_factory=factory)
    clients = []
    try:
        result = collector.run_task(target["task_id"])
        # Test-only second branch: not a scheduled/formal sample or persisted risk row.
        from risk_agent.collection.continue_gate import CheckpointAuditRecord
        task = collector.tasks[target["task_id"]]
        source = json.loads((collector.directory / "tasks" / task.task_id / "source_result.json").read_text())
        record = CheckpointAuditRecord(**source["checkpoint"])
        repeated = collector._branch(task, record, collector.workers[target["worker_id"]], clients,
            collector.clock() - source["source_budget"]["latency_seconds_used"], 2)
        assert repeated["complete_chain"] and repeated["first_input_exact"]
        assert all(repeated["restore_hashes"].values()) and repeated["checkpoint_immutable"]
    finally:
        for _, client in clients:
            client.close()
        collector.close()
    assert result["engineering_passed"] and result["accepted_y_continue"] == 1
    source = [json.loads(s) for s in (collector.directory / "internal" / target["task_id"] / "source.jsonl").read_text().splitlines()]
    branch = [json.loads(s) for s in (collector.directory / "internal" / target["task_id"] / "branch_1.jsonl").read_text().splitlines()]
    assert sum(e["triggered"] for e in source) == 1 and not any(e["triggered"] for e in branch)
    assert any(e["tool_name"] == "aggregate_records" for e in branch)
    repeat_events = [json.loads(s) for s in
        (collector.directory / "internal" / target["task_id"] / "branch_2.jsonl").read_text().splitlines()]
    assert not any(e["triggered"] for e in repeat_events)


@pytest.mark.parametrize("phase", ["source", "branch_1"])
def test_wrong_adapter_rejected_before_sdk(tmp_path, phase):
    profile, plan, *_ = load_aggregate_contract(ROOT)
    target = next(c for c in plan["tasks"] if c["split"] == "validation" and applicable(c))
    factory = AggregateValidationOfflineFactory(profile.tasks, plan)
    collector = AggregateValidationCollector(root=ROOT, run_directory=tmp_path / "run", run_id="wrong_adapter_fixture",
        offline_fixture=True, agent_factory=factory)
    original = collector.fault_injector
    collector.fault_injector = lambda task, selected: (ValidationCollector.fault_injector(collector, task, selected)
        if selected == phase else original(task, selected))
    try:
        result = collector.run_task(target["task_id"])
    finally:
        collector.close()
    assert result["batch_must_stop"] and result["accepted_y_continue"] is None
    assert len(factory.requests) == (0 if phase == "source" else 1)


def test_foreign_experiment_and_other_splits_fail_without_sdk(collected, tmp_path):
    directory, _, factory, collector = collected
    checkpoint = next((directory / "source/checkpoints").iterdir())
    identity = json.loads((checkpoint / "manifest.json").read_text())["identity"]
    identity["experiment_id"] = "phase2_formal_continue_v1"
    runner = ValidationContinueRunner(settings=collector.settings, tasks=collector.profile.tasks,
        run_directory=tmp_path / "runner", checkpoint_run=directory / "source", run_id=collector.run_id,
        experiment_id=collector.experiment_id, research_flags=collector.flags, audit_valid=lambda: True,
        replay_policy=collector.profile.config["continue"]["replay_policy"], protocol_id=collector.profile.config["protocol_id"])
    before = len(factory.requests)
    with pytest.raises(ContinueGateError):
        runner._validate_identity(SimpleNamespace(manifest=SimpleNamespace(identity=identity), checkpoint_path=checkpoint),
                                  collector.workers[identity["worker_id"]])
    for split in ("train", "test"):
        task = next(t for t in collector.profile.tasks if t.split.value == split)
        with pytest.raises(ValueError, match="Only Validation"):
            collector.run_task(task.task_id)
    assert len(factory.requests) == before


def test_policy_mutation_refused_before_claim(tmp_path):
    profile, plan, *_ = load_aggregate_contract(ROOT)
    factory = AggregateValidationOfflineFactory(profile.tasks, plan)
    collector = AggregateValidationCollector(root=ROOT, run_directory=tmp_path / "run", run_id="mutation_fixture",
        offline_fixture=True, agent_factory=factory)
    task = next(c for c in collector.cases.values() if c["split"] == "validation" and applicable(c))
    task["parameters"].pop("target_match_policy")
    try:
        with pytest.raises(ValueError, match="binding changed"):
            collector.run_task(task["task_id"])
    finally:
        collector.close()
    assert not factory.requests and not collector.journal.path.exists()


def test_fixture_eligibility_cannot_be_promoted(collected):
    directory, summary, _, collector = collected
    with pytest.raises(ValueError, match="qualified real"):
        collector.publish_validation_eligibility(summary, resources_released=True)
    collector.offline_fixture = False
    try:
        with pytest.raises(ValueError, match="cannot promote fixtures"):
            collector.publish_validation_eligibility(summary, resources_released=True)
    finally:
        collector.offline_fixture = True
    assert not (directory / "validation_eligibility_release.json").exists()


def test_new_claim_does_not_touch_legacy_and_cannot_replay_after_manifest_rebuild(tmp_path, monkeypatch):
    import risk_agent.collection.aggregate_target_contract as contract
    from risk_agent.collection.day5_gate import sha256_file
    for name in (PLAN, TARGET_CONFIG, TARGET_MANIFEST):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / name).read_bytes())
    monkeypatch.setattr(contract, "load_aggregate_contract", lambda root: None)
    legacy = tmp_path / "runs/phase2_validation_control" / sha256_file(tmp_path / PLAN) / "launch_claim.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('old-blocked-claim')
    claim = claim_aggregate_cohort(tmp_path, "new_cohort_fixture")
    (tmp_path / TARGET_MANIFEST).write_text('changed-code-manifest')
    with pytest.raises(FileExistsError):
        claim_aggregate_cohort(tmp_path, "another_fixture")
    assert legacy.read_text() == 'old-blocked-claim'
    assert json.loads(claim.read_text())["run_id"] == "new_cohort_fixture"


def test_cli_refuses_missing_new_authorization_and_old_receipt(tmp_path, monkeypatch):
    import scripts.run_aggregate_validation as entry
    with pytest.raises(ValueError, match="separate authorization"):
        entry.main(["--mode", "validation", "--run-id", "unauthorized_fixture", "--output-root", str(tmp_path / "out")])
    monkeypatch.setattr(entry, "verify_train_prerequisite", lambda *args: {"proof_sha256": "train_fixture"})
    receipt = tmp_path / "old_receipt.json"
    receipt.write_text(json.dumps({"passed": True, "qualification_only": True, "offline_gate_executed": True}))
    with pytest.raises(ValueError, match="qualification binding"):
        entry.main(["--mode", "validation", "--authorize-aggregate-validation", "--run-id", "old_receipt_fixture",
            "--output-root", str(tmp_path / "out"), "--train-proof", str(receipt), "--remote-evidence", str(receipt),
            "--connection-spec", str(receipt), "--acceptance-receipt", str(receipt)])
    assert not (tmp_path / "out").exists()


def test_absent_group_calls_still_block_coverage(tmp_path):
    profile, plan, *_ = load_aggregate_contract(ROOT)
    targets = {c["task_id"] for c in plan["tasks"] if applicable(c)}
    def transform(task, runtime, response):
        if task.task_id in targets and runtime.tool_history:
            response["choices"][0]["message"]["tool_calls"][0]["function"].update(
                name="final_answer", arguments='{"answer":null}')
        return response
    factory = AggregateValidationOfflineFactory(profile.tasks, plan, transform=transform)
    collector = AggregateValidationCollector(root=ROOT, run_directory=tmp_path / "run", run_id="absent_group_fixture",
        offline_fixture=True, agent_factory=factory)
    try:
        summary = collector.run_validation()
        assert summary["complete"] and not summary["quality_passed"] and not summary["fault_coverage_passed"]
        assert summary["valid_primary"] == 32 and summary["repeat_branches_valid"] == 7
        for task_id in targets & collector.allowed:
            events = []
            for phase in ("source", "branch_1"):
                path = collector.directory / "internal" / task_id / (phase + ".jsonl")
                if path.exists():
                    events.extend(json.loads(s) for s in path.read_text().splitlines())
            assert not any(e["triggered"] for e in events)
        with pytest.raises(ValueError, match="qualified real"):
            collector.publish_validation_eligibility(summary, resources_released=True)
    finally:
        collector.close()
    assert not (collector.directory / "validation_eligibility_release.json").exists()


@pytest.mark.parametrize("phase", ["source", "continue"])
def test_missing_usage_stays_null_in_new_entry(tmp_path, phase):
    profile, plan, _, cfg, *_ = load_aggregate_contract(ROOT)
    def transform(task, runtime, response):
        if (phase == "source") == (not runtime.tool_history):
            response.pop("usage")
        return response
    factory = AggregateValidationOfflineFactory(profile.tasks, plan, transform=transform)
    collector = AggregateValidationCollector(root=ROOT, run_directory=tmp_path / "run", run_id="usage_target_fixture",
        offline_fixture=True, agent_factory=factory)
    try:
        result = collector.run_task(cfg["task_ids"][0])
    finally:
        collector.close()
    assert result["accepted_y_continue"] is None and result["batch_must_stop"] and result["resources_released"]
    assert not read_records(collector.directory / "records/risk_candidates.jsonl")


def test_real_injected_factory_rejected_before_creation(tmp_path):
    profile, plan, *_ = load_aggregate_contract(ROOT)
    factory = AggregateValidationOfflineFactory(profile.tasks, plan)
    with pytest.raises(ValueError, match="explicit authorization"):
        AggregateValidationCollector(root=ROOT, run_directory=tmp_path / "bad", run_id="real_fixture",
            agent_factory=factory, authorize_aggregate_validation=True)
    assert not factory.requests and not (tmp_path / "bad").exists()


def test_budget_boundary_preserves_parsing_receipt_and_original_limit(tmp_path):
    profile, plan, *_ = load_aggregate_contract(ROOT)
    task_id = next(c["task_id"] for c in plan["tasks"] if c["split"] == "validation"
                   and c["fault_type"] == "F3_INVALID_ARGUMENT" and not c["scheduled_repeat"])
    first = None
    def transform(task, runtime, response):
        nonlocal first
        call = response["choices"][0]["message"]["tool_calls"][0]
        if first is None:
            first = deepcopy(call["function"])
        else:
            call["function"] = deepcopy(first)
        return response
    factory = AggregateValidationOfflineFactory(profile.tasks, plan, transform=transform)
    collector = AggregateValidationCollector(root=ROOT, run_directory=tmp_path / "run", run_id="budget_target_fixture",
        offline_fixture=True, agent_factory=factory)
    collector.audit._wall_clock = lambda: "2000-01-01T00:00:00+00:00"
    try:
        result = collector.run_task(task_id)
    finally:
        collector.close()
    assert result["accepted_y_continue"] == 0 and not result["batch_must_stop"]
    assert result["component"]["termination"] == "budget_exhaustion" and len(factory.requests) == 10
    receipts = [json.loads(s) for s in (collector.directory / "audit/parsing_outcome_audit.jsonl").read_text().splitlines()]
    assert len(receipts) == 10 and receipts[-1]["parsing_status"] == "not_attempted"


def test_policy_does_not_enter_public_messages(collected):
    directory, _, _, _ = collected
    for path in (directory / "source/checkpoints").glob("*/agent/agent_state.json"):
        payload = json.loads(path.read_text())["payload"]
        public = json.dumps(payload["canonical_next_model_input"])
        assert POLICY not in public and "target_match_policy" not in public
