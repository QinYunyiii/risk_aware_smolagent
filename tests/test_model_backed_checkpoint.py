from __future__ import annotations

import copy
import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from smolagents import Model
from smolagents.memory import ActionStep
from smolagents.models import (
    ChatMessage,
    ChatMessageToolCall,
    ChatMessageToolCallFunction,
    MessageRole,
)
from smolagents.monitoring import TokenUsage

from risk_agent.agent import (
    CheckpointableToolCallingAgent,
    ModelRequestIntercepted,
    RecordingModel,
)
from risk_agent.agent.agent_state_adapter import canonical_hash
from risk_agent.benchmark import (
    TaskEnvironment,
    generate_task_instances,
    load_task_templates,
)
from risk_agent.benchmark.schema import DataSplit, Domain, TaskSpec
from risk_agent.benchmark.step_references import resolve_step_value
from risk_agent.checkpoint import (
    AdapterStateMetadata,
    AdapterStateStatus,
    CheckpointIdentityV2,
    CheckpointManager,
    CheckpointStateMismatchError,
    EnvironmentCheckpointMetadata,
    RuntimeCheckpointMetadata,
    parse_checkpoint_manifest,
)
from risk_agent.collection import (
    CompletedToolReplayError,
    DecisionCheckpointCapture,
    canonical_tool_call_signature,
    capture_local_tool_adapter_state,
    create_local_domain_tools,
    expected_request_from_checkpoint,
    restore_local_tool_adapter_state,
)
from risk_agent.collection.day5_gate import (
    canonical_task_catalog_hash,
    split_manifest_hash,
)
from risk_agent.monitoring import (
    BudgetState,
    ExecutionMonitor,
    JsonlTraceWriter,
    RuntimeState,
)


ROOT = Path(__file__).resolve().parents[1]
SELECTED_TASK_IDS = {
    Domain.DATA_ANALYSIS: "p2_data_region_units_sum_001",
    Domain.FILE_RETRIEVAL: "p2_file_report_control_001",
    Domain.API_WORKFLOW: "p2_api_process_control_001",
    Domain.CALCULATION_LOGIC: "p2_logic_chain_control_001",
}
HASH_KEYS = {
    "agent_state_hash",
    "next_model_input_hash",
    "environment_state_hash",
    "runtime_state_hash",
    "identity_config_hash",
    "combined_state_hash",
}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ScriptedQwenModel(Model):
    """Local deterministic provider stub; the Agent/tool lifecycle remains real."""

    def __init__(self, responses: list[ChatMessage]) -> None:
        super().__init__(
            model_id="Qwen/Qwen3-8B",
            seed=20261124,
            temperature=0.0,
            max_tokens=128,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        self.responses = list(responses)
        self.calls = 0
        self.client_kwargs = {
            "base_url": "https://p2-d2-local.invalid/v1",
            "api_key": "fixture-secret",
            "max_retries": 0,
        }

    def generate(self, *args: Any, **kwargs: Any) -> ChatMessage:
        self.calls += 1
        if not self.responses:
            raise AssertionError("Unexpected scripted provider call")
        return self.responses.pop(0)


def _tool_message(call_id: str, tool_name: str, arguments: dict[str, Any]) -> ChatMessage:
    return ChatMessage(
        role=MessageRole.ASSISTANT,
        content=f"Call {tool_name} with the observation-derived arguments.",
        tool_calls=[
            ChatMessageToolCall(
                id=call_id,
                type="function",
                function=ChatMessageToolCallFunction(
                    name=tool_name,
                    arguments=arguments,
                ),
            )
        ],
        token_usage=TokenUsage(input_tokens=7, output_tokens=3),
    )


def _catalog() -> tuple[tuple[TaskSpec, ...], dict[Domain, TaskSpec]]:
    tasks = generate_task_instances(
        load_task_templates(ROOT / "configs" / "task_templates_v2.yaml"),
        2,
    )
    by_id = {task.task_id: task for task in tasks}
    selected = {domain: by_id[task_id] for domain, task_id in SELECTED_TASK_IDS.items()}
    assert all(task.split is DataSplit.TRAIN for task in selected.values())
    assert all(task.template_id.startswith("p2_") for task in selected.values())
    return tasks, selected


def _monitor(
    *,
    task: TaskSpec,
    runtime: RuntimeState,
    trace_path: Path,
    environment_id: str,
) -> ExecutionMonitor:
    return ExecutionMonitor(
        writer=JsonlTraceWriter(trace_path),
        runtime_state=runtime,
        experiment_id="p2_d2_local_compatibility",
        run_id="p2_d2_local_20261124_001",
        task_id=task.task_id,
        template_id=task.template_id,
        instance_id=task.instance_id,
        model_id="Qwen/Qwen3-8B",
        model_version="local-scripted-lifecycle-v1",
        prompt_version="p2-d1-v2",
        tool_version="p2-d2-local-tools-v1",
        worker_id="worker-local",
        environment_id=environment_id,
        additional_context={"split": task.split.value, "formal_experiment": False},
    )


def _agent(
    *,
    tools: list[Any],
    runtime: RuntimeState,
    responses: list[ChatMessage],
    intercept_valid_request: bool = False,
) -> tuple[CheckpointableToolCallingAgent, RecordingModel, ScriptedQwenModel]:
    underlying = ScriptedQwenModel(responses)
    model = RecordingModel(
        underlying,
        runtime_state=runtime,
        max_model_calls=10,
        intercept_valid_request=intercept_valid_request,
    )
    agent = CheckpointableToolCallingAgent(
        tools=tools,
        model=model,
        max_steps=10,
        verbosity_level=0,
        name="p2_d2_checkpoint_agent",
        description="P2-D2 local real-lifecycle checkpoint compatibility agent",
    )
    return agent, model, underlying


def _next_action(stream: Any) -> ActionStep:
    while True:
        event = next(stream)
        if isinstance(event, ActionStep):
            return event


def _inventory(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.mark.parametrize("visible_error", [False, True])
def test_formal_first_observation_capture_preserves_consumed_budget(
    tmp_path: Path, visible_error: bool
) -> None:
    """Local lifecycle evidence only; this fixture does not call real Qwen."""
    from risk_agent.collection.decision_checkpoint import DecisionCheckpointRejected
    from risk_agent.collection.protocol_freeze import build_formal_catalog

    tasks = build_formal_catalog(ROOT)
    task = next(item for item in tasks if item.task_id == "p2_api_process_control_001")
    environment = TaskEnvironment(tmp_path / "source", task)
    environment.reset()
    runtime = RuntimeState(seed=task.seed, budget=BudgetState(50_000, 12, 900.0))
    tools = create_local_domain_tools(
        environment=environment,
        monitor=_monitor(task=task, runtime=runtime, trace_path=tmp_path / "tools.jsonl",
                         environment_id="first-observation-fixture"),
    )
    order_id = "missing-order-fixture" if visible_error else task.orders[0]["order_id"]
    agent, model, underlying = _agent(
        tools=tools, runtime=runtime,
        responses=[_tool_message("first-call", "get_order", {"order_id": order_id})],
    )
    stream = agent.run(task.prompt, stream=True)
    event = _next_action(stream)
    assert bool(event.error) == visible_error
    assert model.model_calls_used == underlying.calls == 1
    assert len(runtime.tool_history) == 1
    manager = CheckpointManager(tmp_path / "checkpoints")
    capture = DecisionCheckpointCapture()
    arguments = dict(
        event=event, agent=agent, manager=manager, checkpoint_id="first_observation",
        environment=environment, runtime_state=runtime,
        identity=CheckpointIdentityV2(
            experiment_id="protocol_freeze_local", run_id="first_observation_fixture",
            task_run_id=task.task_id, model_version="local-scripted-lifecycle-v1",
            worker_id="worker-local", prompt_version="p2-multi-api-exact-identifiers-v1",
            tool_version="local-fixture", verifier_version="deterministic-verifier-not-run",
            verifier_content_hash=_digest("not-run"), catalog_hash=canonical_task_catalog_hash(tasks),
            split_manifest_hash=split_manifest_hash(tasks), controller_type="qwen3_model",
            execution_mode="model_backed_smoke", created_at="2026-11-27T00:00:00+08:00",
        ),
        runtime_metadata=RuntimeCheckpointMetadata(
            max_model_calls=10, model_calls_used=model.model_calls_used,
            prompt_tokens_used=model.prompt_tokens_observed,
            completion_tokens_used=model.completion_tokens_observed,
            resource_usage={"model_latency_seconds": model.model_latency_seconds_observed},
            tool_adapter_state=AdapterStateMetadata(
                AdapterStateStatus.STATEFUL, {"tools": capture_local_tool_adapter_state(tools)}),
            fault_adapter_state=AdapterStateMetadata(AdapterStateStatus.NOT_INSTANTIATED, None),
            phase="after_tool_observation_before_model_generation", next_operation="model_generation",
        ),
        environment_metadata=EnvironmentCheckpointMetadata(
            completed_side_effects=[],
            latest_completed_operation={"kind": "tool_call", "call_id": "first-call"},
        ),
    )
    snapshot = capture.capture(**arguments).snapshot
    stream.close()
    assert snapshot.manifest.identity["step_index"] == 1
    assert underlying.calls == 1
    restored_environment = TaskEnvironment(tmp_path / "restored", task)
    restored_environment.reset()
    restored = manager.restore_v2(checkpoint_id="first_observation", environment=restored_environment)
    assert restored.runtime_state.budget.to_dict() == runtime.budget.to_dict()
    assert restored.runtime_state.budget.tool_calls_used == 1
    assert restored.runtime_state.budget.tokens_used > 0
    with pytest.raises(DecisionCheckpointRejected, match="primary_checkpoint_already_captured"):
        capture.capture(**arguments)


def _historical_v2_copy(
    *, source: Path, destination_root: Path, checkpoint_id: str
) -> CheckpointManager:
    """Emulate the pre-P2-D2 schema-2.0.0 shape without an identity seed."""

    target = destination_root / checkpoint_id
    shutil.copytree(source, target)
    identity_path = target / "identity" / "identity_config.json"
    identity_config = json.loads(identity_path.read_text(encoding="utf-8"))
    identity_config.pop("seed")
    identity_path.write_text(
        json.dumps(identity_config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["identity"].pop("seed")
    for artifact in manifest["artifacts"]:
        if artifact["path"] == "identity/identity_config.json":
            artifact["sha256"] = hashlib.sha256(identity_path.read_bytes()).hexdigest()
            artifact["size_bytes"] = identity_path.stat().st_size
            break
    else:  # pragma: no cover - a valid v2 checkpoint always registers this artifact
        raise AssertionError("identity_config artifact is not registered")
    manifest["hashes"]["identity_config_hash"] = canonical_hash(identity_config)
    manifest["hashes"]["combined_state_hash"] = canonical_hash(
        {
            "schema_version": "2.0.0",
            "agent_state_hash": manifest["hashes"]["agent_state_hash"],
            "next_model_input_hash": manifest["hashes"]["next_model_input_hash"],
            "environment_state_hash": manifest["hashes"]["environment_state_hash"],
            "runtime_state_hash": manifest["hashes"]["runtime_state_hash"],
            "identity_config_hash": manifest["hashes"]["identity_config_hash"],
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return CheckpointManager(destination_root)


@pytest.mark.parametrize("domain", list(Domain))
def test_p2_d2_checkpoint_after_two_dependent_tool_calls_restores_without_replay(
    tmp_path: Path, domain: Domain
) -> None:
    tasks, selected = _catalog()
    task = selected[domain]
    source_environment = TaskEnvironment(tmp_path / "source", task)
    source_environment.reset()
    source_runtime = RuntimeState(
        seed=task.seed,
        budget=BudgetState(50_000, 12, 900.0),
    )
    source_tools = create_local_domain_tools(
        environment=source_environment,
        monitor=_monitor(
            task=task,
            runtime=source_runtime,
            trace_path=tmp_path / "source_tools.jsonl",
            environment_id=f"source-{domain.value}",
        ),
    )
    first_step = task.reference_steps[0]
    agent, model, underlying = _agent(
        tools=source_tools,
        runtime=source_runtime,
        responses=[
            _tool_message(
                "p2-d2-call-001",
                first_step.tool,
                copy.deepcopy(dict(first_step.arguments)),
            )
        ],
    )

    stream = agent.run(task.prompt, stream=True)
    first_event = _next_action(stream)
    assert first_event.observations is not None and first_event.error is None
    assert len(source_runtime.tool_history) == 1

    second_step = task.reference_steps[1]
    second_arguments = resolve_step_value(
        second_step.arguments,
        {"step_1": source_runtime.tool_history[0].tool_result},
    )
    underlying.responses.append(
        _tool_message(
            "p2-d2-call-002",
            second_step.tool,
            copy.deepcopy(dict(second_arguments)),
        )
    )
    second_event = _next_action(stream)
    assert second_event.observations is not None and second_event.error is None
    assert agent.memory.steps[-1] is second_event
    assert model.model_calls_used == 2
    assert underlying.calls == 2
    assert len(source_runtime.tool_history) == 2

    checkpoint_id = f"p2d2_{domain.value}_checkpoint"
    manager = CheckpointManager(tmp_path / "checkpoints")
    identity = CheckpointIdentityV2(
        experiment_id="p2_d2_local_compatibility",
        run_id="p2_d2_local_20261124_001",
        task_run_id=f"{task.task_id}_local",
        model_version="local-scripted-lifecycle-v1",
        worker_id="worker-local",
        prompt_version="p2-d1-v2",
        tool_version="p2-d2-local-tools-v1",
        verifier_version="deterministic-verifier-not-run",
        verifier_content_hash=_digest("deterministic-verifier-not-run"),
        catalog_hash=canonical_task_catalog_hash(tasks),
        split_manifest_hash=split_manifest_hash(tasks),
        controller_type="qwen3_model",
        execution_mode="model_backed_smoke",
        created_at="2026-11-24T00:00:00+08:00",
    )
    snapshot = DecisionCheckpointCapture().capture(
        event=second_event,
        agent=agent,
        manager=manager,
        checkpoint_id=checkpoint_id,
        environment=source_environment,
        runtime_state=source_runtime,
        identity=identity,
        runtime_metadata=RuntimeCheckpointMetadata(
            max_model_calls=10,
            model_calls_used=model.model_calls_used,
            prompt_tokens_used=model.prompt_tokens_observed,
            completion_tokens_used=model.completion_tokens_observed,
            resource_usage={
                "model_latency_seconds": model.model_latency_seconds_observed,
                "tool_latency_seconds": sum(
                    item.latency_seconds for item in source_runtime.tool_history
                ),
                "unobserved": None,
            },
            tool_adapter_state=AdapterStateMetadata(
                AdapterStateStatus.STATEFUL,
                {"tools": capture_local_tool_adapter_state(source_tools)},
            ),
            fault_adapter_state=AdapterStateMetadata(
                AdapterStateStatus.NOT_INSTANTIATED,
                None,
            ),
            phase="after_tool_observation_before_model_generation",
            next_operation="model_generation",
        ),
        environment_metadata=EnvironmentCheckpointMetadata(
            completed_side_effects=[
                {
                    "tool_name": item.tool_name,
                    "call_id": call_id,
                    "commit_state": "completed",
                }
                for item, call_id in zip(
                    source_runtime.tool_history,
                    ("p2-d2-call-001", "p2-d2-call-002"),
                    strict=True,
                )
            ],
            latest_completed_operation={
                "kind": "tool_call",
                "call_id": "p2-d2-call-002",
            },
        ),
    ).snapshot
    stream.close()

    assert underlying.calls == 2
    assert snapshot.manifest.identity["seed"] == task.seed
    assert snapshot.manifest.identity["step_index"] == 2
    assert set(snapshot.manifest.hashes) == HASH_KEYS
    agent_payload = json.loads(
        (snapshot.checkpoint_path / "agent" / "agent_state.json").read_text(encoding="utf-8")
    )["payload"]
    assert source_runtime.agent_memory == agent_payload["memory"]["steps"]
    assert agent_payload["agent"]["task"] == task.prompt
    assert "reference_steps" not in agent_payload
    assert "expected" not in agent_payload["agent"]
    checkpoint_before = _inventory(snapshot.checkpoint_path)
    source_state_before = source_environment.state_hash()
    source_manifest_before = source_environment.state_manifest()

    restored_environment = TaskEnvironment(tmp_path / "restored", task)
    restored_environment.reset()
    restored = manager.restore_v2(
        checkpoint_id=checkpoint_id,
        environment=restored_environment,
    )
    hashes = snapshot.manifest.hashes
    assert restored.restored_agent_state_hash == hashes["agent_state_hash"]
    assert restored.restored_next_model_input_hash == hashes["next_model_input_hash"]
    assert restored.restored_environment_hash == hashes["environment_state_hash"]
    assert restored.restored_runtime_hash == hashes["runtime_state_hash"]
    assert restored.restored_combined_state_hash == hashes["combined_state_hash"]
    identity_config = json.loads(
        (snapshot.checkpoint_path / "identity" / "identity_config.json").read_text(
            encoding="utf-8"
        )
    )
    assert canonical_hash(identity_config) == hashes["identity_config_hash"]
    assert restored_environment.state_hash() == source_state_before
    assert restored_environment.state_manifest() == source_manifest_before
    if domain is Domain.API_WORKFLOW:
        assert restored_environment.state_manifest()["orders"][0]["status"] == "processing"
        assert len(restored_environment.state_manifest()["idempotency_keys"]) == 1

    prohibited = frozenset(
        canonical_tool_call_signature(item.tool_name, item.tool_arguments)
        for item in restored.runtime_state.tool_history
        if item.error_type is None
    )
    restored_tools = create_local_domain_tools(
        environment=restored_environment,
        monitor=_monitor(
            task=task,
            runtime=restored.runtime_state,
            trace_path=tmp_path / "restored_tools.jsonl",
            environment_id=f"restored-{domain.value}",
        ),
        checkpoint_id=checkpoint_id,
        prohibited_call_signatures=prohibited,
    )
    adapter_document = json.loads(
        (
            snapshot.checkpoint_path
            / snapshot.manifest.runtime_state["tool_adapter_state_ref"]
        ).read_text(encoding="utf-8")
    )
    restore_local_tool_adapter_state(restored_tools, adapter_document["state"]["tools"])
    restored_agent, restored_model, restored_underlying = _agent(
        tools=restored_tools,
        runtime=restored.runtime_state,
        responses=[],
        intercept_valid_request=True,
    )
    restored_model.restore_model_calls_used(2)
    restored_agent.restore_snapshot(restored.agent_state_snapshot)
    restored_model.expect(
        expected_request_from_checkpoint(
            manifest=restored.manifest,
            snapshot=restored.agent_state_snapshot,
            runtime_state=restored.runtime_state,
        )
    )
    with pytest.raises(ModelRequestIntercepted):
        restored_agent.request_next_model_response()
    assert restored_underlying.calls == 0
    assert restored_model.requests[-1].input_hash == hashes["next_model_input_hash"]

    replay_entry = restored.runtime_state.tool_history[-1]
    replay_tool = next(tool for tool in restored_tools if tool.name == replay_entry.tool_name)
    restored_state_before_replay = restored_environment.state_hash()
    with pytest.raises(CompletedToolReplayError, match="checkpoint_resume_replay_detected"):
        replay_tool.forward(**copy.deepcopy(dict(replay_entry.tool_arguments)))
    assert restored_environment.state_hash() == restored_state_before_replay
    assert len(restored.runtime_state.tool_history) == 2
    assert _inventory(snapshot.checkpoint_path) == checkpoint_before

    mismatched_task = replace(task, split=DataSplit.VALIDATION)
    mismatched_environment = TaskEnvironment(tmp_path / "wrong-split", mismatched_task)
    mismatched_environment.reset()
    with pytest.raises(
        CheckpointStateMismatchError,
        match="Checkpoint identity does not match restore target",
    ):
        manager.restore_v2(
            checkpoint_id=checkpoint_id,
            environment=mismatched_environment,
        )

    legacy_shape = snapshot.manifest.to_dict()
    legacy_shape["identity"].pop("seed")
    assert parse_checkpoint_manifest(legacy_shape).schema_version == "2.0.0"

    legacy_manager = _historical_v2_copy(
        source=snapshot.checkpoint_path,
        destination_root=tmp_path / "historical-v2-checkpoints",
        checkpoint_id=checkpoint_id,
    )
    legacy_environment = TaskEnvironment(tmp_path / "historical-v2-restore", task)
    legacy_environment.reset()
    legacy_restored = legacy_manager.restore_v2(
        checkpoint_id=checkpoint_id,
        environment=legacy_environment,
    )
    assert "seed" not in legacy_restored.manifest.identity
    assert legacy_restored.runtime_state.seed == task.seed
    assert legacy_environment.state_hash() == source_state_before
