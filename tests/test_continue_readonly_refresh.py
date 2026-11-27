from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx2
import pytest
import yaml
from smolagents.memory import ActionStep

from risk_agent.agent import agent_factory
from risk_agent.benchmark import TaskEnvironment
from risk_agent.collection.benchmark_tools import (
    CompletedToolReplayError, canonical_tool_call_signature, create_local_domain_tools,
)
from risk_agent.collection.continue_gate import (
    LEGACY_REPLAY_POLICY, READONLY_REFRESH_POLICY, CheckpointAuditRecord,
    ContinueGateRunner, _prohibited_source_signatures,
)
from risk_agent.collection.day5_gate import Day5GateRunner, checkpoint_inventory, sha256_file
from risk_agent.collection.protocol_freeze import (
    CURRENT_PROFILE_PATH, PROFILE_PATH, load_frozen_collection,
)
from risk_agent.monitoring import (
    BudgetState, ExecutionMonitor, JsonlTraceWriter, ProviderAuditContext,
    ProviderResponseAudit, RuntimeState, ToolHistoryEntry,
)
from risk_agent.collection.decision_checkpoint import DecisionCheckpointCapture

ROOT = Path(__file__).resolve().parents[1]


def _entry(name: str, arguments: dict[str, Any], error: str | None = None) -> ToolHistoryEntry:
    return ToolHistoryEntry(
        step_id="source-001", tool_name=name, tool_arguments=arguments,
        tool_result=None if error else {"ok": True}, error_type=error,
        timestamp="2026-11-27T00:00:00+08:00", token_usage={"total_tokens": 0},
        latency_seconds=0.1, worker_id="worker-0", environment_id="source", checkpoint_id=None,
    )


@pytest.mark.parametrize("error", [None, "TimeoutError"])
def test_new_read_observations_are_not_signature_replays(error: str | None) -> None:
    entry = _entry("get_order", {"order_id": "order-fixture"}, error)
    runtime = RuntimeState(seed=1, tool_history=[entry], budget=BudgetState(50000, 12, 900))
    assert _prohibited_source_signatures(runtime, replay_policy=READONLY_REFRESH_POLICY) == frozenset()
    legacy = _prohibited_source_signatures(runtime)
    assert bool(legacy) == (error is None)


@pytest.mark.parametrize("error,status,blocked", [
    (None, None, True), ("PartialFailureError", "committed", True),
    ("PartialFailureError", "unknown", True), ("PartialFailureError", None, True),
    ("PartialFailureError", "not_committed", False),
    ("PartialFailureError", "not_started", False),
])
def test_write_safety_and_idempotency_are_preserved(
    tmp_path: Path, error: str | None, status: str | None, blocked: bool
) -> None:
    task = next(t for t in load_frozen_collection(ROOT).tasks
                if t.task_id == "p2_api_process_control_001")
    arguments = dict(order_id=task.orders[0]["order_id"], expected_status="pending",
                     new_status="processing", idempotency_key="write-safety-fixture")
    runtime = RuntimeState(
        seed=task.seed, tool_history=[_entry("update_order", arguments, error)],
        tool_state={"last_failure": {"tool_name": "update_order", "commit_status": status}},
        budget=BudgetState(50000, 12, 900),
    )
    signatures = _prohibited_source_signatures(runtime, replay_policy=READONLY_REFRESH_POLICY)
    assert (canonical_tool_call_signature("update_order", arguments) in signatures) == blocked
    if blocked:
        environment = TaskEnvironment(tmp_path, task)
        environment.reset()
        monitor = ExecutionMonitor(
            writer=JsonlTraceWriter(tmp_path / "events.jsonl"), runtime_state=runtime,
            experiment_id="readonly_refresh_fixture", run_id="write_guard_fixture",
            task_id=task.task_id, template_id=task.template_id, instance_id=task.instance_id,
            model_id="Qwen/Qwen3-8B", model_version="fixture", prompt_version="fixture",
            tool_version="fixture", worker_id="worker-0", environment_id="write-guard-fixture",
        )
        tools = create_local_domain_tools(environment=environment, monitor=monitor,
                                          prohibited_call_signatures=signatures)
        tool = next(tool for tool in tools if tool.name == "update_order")
        before = environment.state_hash()
        with pytest.raises(CompletedToolReplayError):
            tool.forward(**arguments)
        assert environment.state_hash() == before
        assert len(runtime.tool_history) == 1 and runtime.budget.tool_calls_used == 0


def test_revision_changes_only_the_approved_profile_fields() -> None:
    old = yaml.safe_load((ROOT / PROFILE_PATH).read_text())
    new = yaml.safe_load((ROOT / CURRENT_PROFILE_PATH).read_text())
    assert new.pop("implementation_revision") == "readonly-refresh-v1"
    new["dependencies"]["freeze_manifest"] = old["dependencies"]["freeze_manifest"]
    assert new["continue"].pop("replay_policy") == READONLY_REFRESH_POLICY
    assert new == old


def _completion(index: int, calls: list[tuple[str, str, dict[str, Any]]]) -> dict[str, Any]:
    return {
        "id": f"fixture-{index}", "object": "chat.completion", "created": 1800000000,
        "model": "Qwen/Qwen3-8B",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "",
            "tool_calls": [{"id": call_id, "type": "function", "function": {
                "name": name, "arguments": json.dumps(arguments)}}
                for call_id, name, arguments in calls]}, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 40, "completion_tokens": 10, "total_tokens": 50},
    }


@pytest.mark.parametrize("case", ["fresh_read", "historical_id", "branch_id", "duplicate_batch", "legacy"])
def test_first_checkpoint_update_reread_full_resume_and_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    runner = ContinueGateRunner.from_frozen_collection(
        root=ROOT, run_directory=tmp_path / "continue", checkpoint_run=tmp_path / "source",
        run_id="readonly_refresh_fixture",
    )
    if case == "legacy":
        runner.replay_policy = LEGACY_REPLAY_POLICY
    settings, worker = runner.settings, runner.settings.workers[0]
    task = runner.tasks["p2_api_process_control_001"]
    ca = tmp_path / "mock-ca-marker"
    ca.write_text("MockTransport fixture; not a CA certificate")
    monkeypatch.setenv(settings.api_key_env, "fixture-value")
    monkeypatch.setenv(settings.ca_bundle_env, str(ca))
    arguments = {"order_id": task.orders[0]["order_id"]}
    read_id = {"historical_id": "source-read", "branch_id": "continue-update"}.get(case, "new-read")
    reads = [(read_id, "get_order", arguments)]
    if case == "duplicate_batch":
        reads *= 2
    responses = [
        _completion(1, [("source-read", "get_order", arguments)]),
        _completion(2, [("continue-update", "update_order", {
            **arguments, "expected_status": "pending", "new_status": "processing",
            "idempotency_key": f"{task.template_id}-001"})]),
        _completion(3, reads),
        _completion(4, [("final-response", "final_answer", {"answer": task.expected["result"]})]),
    ]
    requests, clients = [], []
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        requests.append(body)
        assert body["max_tokens"] == 1024 and body["seed"] == 20261118
        return httpx2.Response(200, json=responses.pop(0))

    def client_factory(*args: Any, **kwargs: Any) -> httpx2.Client:
        client = httpx2.Client(transport=httpx2.MockTransport(handler), trust_env=False)
        clients.append(client)
        return client

    monkeypatch.setattr(agent_factory, "_http_client", client_factory)
    audit = ProviderResponseAudit(tmp_path / "provider_audit")
    def factory(*args: Any, **kwargs: Any):
        return agent_factory.create_checkpointable_benchmark_agent(
            *args, **kwargs, provider_audit=audit,
            provider_audit_context=ProviderAuditContext(
                run_id=runner.run_id, task_id=task.task_id, attempt_index=1,
                worker_id=worker.worker_id, model_id=settings.model_id,
                model_version=settings.model_version, endpoint=worker.base_url,
                phase="source" if not clients else "continue",
            ),
        )
    runner.agent_factory = factory
    profile = load_frozen_collection(ROOT)
    source = Day5GateRunner(
        settings=settings, run_directory=tmp_path / "source", run_id=runner.run_id,
        catalog_hash=profile.audit_summary()["catalog_sha256"],
        split_hash=profile.audit_summary()["split_manifest_sha256"],
        verifier_content_hash=sha256_file(ROOT / "src/risk_agent/benchmark/verifier.py"),
    )
    environment = TaskEnvironment(tmp_path / "source_workspace", task)
    environment.reset()
    runtime = RuntimeState(seed=task.seed, budget=BudgetState(50000, 12, 900))
    tools = create_local_domain_tools(environment=environment, monitor=source._monitor(
        task=task, runtime=runtime, worker=worker, task_run_id="source_fixture", environment_id="source"))
    agent, model, underlying = factory(settings, worker, tools, runtime,
        max_steps=10, max_model_calls=10, agent_name="readonly_refresh_fixture")
    stream = agent.run(task.prompt, stream=True)
    try:
        event = next(event for event in stream if isinstance(event, ActionStep))
        assert event.step_number == 1 and event.error is None
        snapshot = DecisionCheckpointCapture().capture(
            event=event, agent=agent, manager=source.manager, checkpoint_id="first_observation",
            environment=environment, runtime_state=runtime,
            identity=source._identity(task_run_id="source_fixture", worker=worker),
            runtime_metadata=source._runtime_metadata(runtime=runtime, model=model, tools=tools),
            environment_metadata=source._environment_metadata(runtime=runtime, event=event),
        ).snapshot
    finally:
        stream.close()
        underlying.client.close()
    assert len(requests) == 1 and len(runtime.tool_history) == 1
    before = checkpoint_inventory(snapshot.checkpoint_path)
    record = CheckpointAuditRecord(
        checkpoint_id="first_observation", checkpoint_path=str(snapshot.checkpoint_path),
        task_id=task.task_id, task_run_id="source_fixture", worker_id=worker.worker_id,
        schema_version="2.0.0", manifest_sha256="a" * 64, artifact_count=1,
        inventory_file_count=len(before), step_number=1, next_step_number=2,
        model_calls_used=1, tool_calls_used=1, hashes=snapshot.manifest.hashes,
        all_hashes_verified=True, checkpoint_inventory=before,
    )
    result = runner.run_checkpoint(audit=record, worker=worker, branch_attempt_index=1)
    success = case == "fresh_read"
    assert result["complete_chain"] == success
    assert result["y_continue"] == int(success) and result["verifier_passed"] == success
    assert result["tool_replay_detected"] != success
    assert result["first_input_exact"] and all(result["restore_hashes"].values())
    assert len(requests) == (4 if success else 3)
    assert checkpoint_inventory(snapshot.checkpoint_path) == before
    assert all(client.is_closed for client in clients)
    assert environment.state_manifest()["orders"][0]["status"] == "pending"
    branch = next((tmp_path / "continue/branches").glob("*/*"))
    state = json.loads((branch / "final_environment_state.json").read_text())
    assert state["orders"][0]["status"] == "processing"
    assert len(state["idempotency_keys"]) == 1
    budget = json.loads((tmp_path / "continue/budget_audit.jsonl").read_text())
    assert budget["model_calls_before"] == 1 and budget["model_calls_after"] == len(requests)
    assert budget["tool_calls_before"] == 1
    assert budget["tool_calls_after"] == (3 if success or case == "legacy" else 2)
    assert budget["tokens_before"] == 50 and budget["tokens_after"] == len(requests) * 50
    rows = [json.loads(line) for line in (tmp_path / "continue/continue_outcomes.jsonl").read_text().splitlines()]
    assert rows[0]["protocol_id"] == "risk-continue-v2.1.0"
    assert rows[0]["research_dataset_eligible"] is False
    parsing = [json.loads(line) for p in (tmp_path / "provider_audit").rglob("*parsing*jsonl")
               for line in p.read_text().splitlines()]
    assert len(parsing) == len(requests)
