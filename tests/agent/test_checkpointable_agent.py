from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from smolagents import Model, Tool
from smolagents.memory import ActionStep
from smolagents.models import (
    ChatMessage,
    ChatMessageToolCall,
    ChatMessageToolCallFunction,
    MessageRole,
)
from smolagents.monitoring import TokenUsage

from risk_agent.agent import (
    AgentStateAdapter,
    CheckpointableToolCallingAgent,
    ExpectedModelRequest,
    ModelRequestRejected,
    RecordingModel,
)
from risk_agent.benchmark import TaskEnvironment
from risk_agent.benchmark.schema import DataSplit, Domain, TaskSpec, ToolStep
from risk_agent.checkpoint import (
    AdapterStateMetadata,
    AdapterStateStatus,
    CheckpointIdentityV2,
    CheckpointManager,
    EnvironmentCheckpointMetadata,
    RuntimeCheckpointMetadata,
)
from risk_agent.collection import DecisionCheckpointCapture, DecisionCheckpointRejected
from risk_agent.monitoring import BudgetState, RuntimeState, ToolHistoryEntry


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class ScriptedModel(Model):
    def __init__(self, responses: list[ChatMessage]) -> None:
        super().__init__(
            model_id="qwen3-8b-local-scripted",
            seed=20261121,
            temperature=0.0,
            max_tokens=128,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        self.responses = list(responses)
        self.calls = 0

    def generate(self, *args, **kwargs) -> ChatMessage:
        self.calls += 1
        if not self.responses:
            raise AssertionError("Unexpected scripted model call")
        return self.responses.pop(0)


class IncrementTool(Tool):
    name = "increment_counter"
    description = "Increment a local counter once using an idempotency key."
    inputs = {"idempotency_key": {"type": "string", "description": "Unique key."}}
    output_type = "string"

    def __init__(self, environment: TaskEnvironment, runtime: RuntimeState) -> None:
        self.environment = environment
        self.runtime = runtime
        super().__init__()

    def forward(self, idempotency_key: str) -> str:
        path = self.environment.resolve_path("counter.txt", must_exist=False)
        seen = self.environment.resolve_path("keys.txt", must_exist=False)
        keys = set(seen.read_text().splitlines()) if seen.exists() else set()
        if idempotency_key not in keys:
            count = int(path.read_text()) if path.exists() else 0
            path.write_text(str(count + 1), encoding="utf-8")
            keys.add(idempotency_key)
            seen.write_text("\n".join(sorted(keys)) + "\n", encoding="utf-8")
        result = f"counter={path.read_text()}"
        self.runtime.tool_history.append(
            ToolHistoryEntry(
                step_id=f"tool-{len(self.runtime.tool_history) + 1}",
                tool_name=self.name,
                tool_arguments={"idempotency_key": idempotency_key},
                tool_result=result,
                error_type=None,
                timestamp="2026-11-21T00:00:00+00:00",
                token_usage={"total_tokens": 0},
                latency_seconds=0.01,
                worker_id="worker-local",
                environment_id="day3-local",
                checkpoint_id=None,
            )
        )
        self.runtime.budget.record(tokens=0, tool_calls=1, latency_seconds=0.01)
        return result


class ReadOnlyProbeTool(Tool):
    name = "read_probe"
    description = "Return a deterministic local value without mutation."
    inputs = {"value": {"type": "string", "description": "Value to return."}}
    output_type = "string"

    def __init__(self) -> None:
        self.calls = 0
        super().__init__()

    def forward(self, value: str) -> str:
        self.calls += 1
        return f"read:{value}"


class ErrorProbeTool(Tool):
    name = "error_probe"
    description = "Raise a deterministic Agent-visible local error."
    inputs = {}
    output_type = "string"

    def forward(self) -> str:
        raise ValueError("visible probe failure")


def _task() -> TaskSpec:
    return TaskSpec(
        task_id="day3_task",
        template_id="day3_template",
        instance_id="day3_instance",
        domain=Domain.CALCULATION_LOGIC,
        seed=20261121,
        prompt="Increment the counter exactly once, then return done.",
        files=(),
        inventory=(),
        orders=(),
        reference_steps=(ToolStep(tool="calculator", arguments={"expression": "1"}),),
        expected="done",
        dataset_role="day7_catalog",
        split=DataSplit.TRAIN,
    )


def _tool_call() -> ChatMessage:
    return ChatMessage(
        role=MessageRole.ASSISTANT,
        content="increment once",
        tool_calls=[
            ChatMessageToolCall(
                id="call-day3-001",
                type="function",
                function=ChatMessageToolCallFunction(
                    name="increment_counter",
                    arguments={"idempotency_key": "day3-key-001"},
                ),
            )
        ],
        token_usage=TokenUsage(input_tokens=4, output_tokens=2),
    )


def _final_answer() -> ChatMessage:
    return ChatMessage(
        role=MessageRole.ASSISTANT,
        content="done",
        tool_calls=[
            ChatMessageToolCall(
                id="call-day3-final",
                type="function",
                function=ChatMessageToolCallFunction(
                    name="final_answer",
                    arguments={"answer": "done"},
                ),
            )
        ],
        token_usage=TokenUsage(input_tokens=8, output_tokens=2),
    )


def _calls_message(calls: list[tuple[str, str, dict[str, str]]]) -> ChatMessage:
    return ChatMessage(
        role=MessageRole.ASSISTANT,
        content="probe calls",
        tool_calls=[
            ChatMessageToolCall(
                id=call_id,
                type="function",
                function=ChatMessageToolCallFunction(name=name, arguments=arguments),
            )
            for call_id, name, arguments in calls
        ],
        token_usage=TokenUsage(input_tokens=1, output_tokens=1),
    )


def _runtime(task: TaskSpec) -> RuntimeState:
    return RuntimeState(
        seed=task.seed,
        agent_memory=[],
        tool_state={"next_call_index": 1},
        budget=BudgetState(50_000, 12, 900.0),
    )


def _agent(environment: TaskEnvironment, runtime: RuntimeState, responses: list[ChatMessage]):
    model = RecordingModel(ScriptedModel(responses))
    agent = CheckpointableToolCallingAgent(
        tools=[IncrementTool(environment, runtime)],
        model=model,
        max_steps=10,
        verbosity_level=0,
        name="day3_checkpointable_agent",
        description="Local lifecycle fixture",
    )
    return agent, model


def _identity() -> CheckpointIdentityV2:
    return CheckpointIdentityV2(
        experiment_id="day3_local_only",
        run_id="day3_run",
        task_run_id="day3_task_run",
        model_version="local-scripted-v1",
        worker_id="worker-local",
        prompt_version="prompt-day3",
        tool_version="tools-day3",
        verifier_version="verifier-day3",
        verifier_content_hash=_digest("verifier"),
        catalog_hash=_digest("catalog"),
        split_manifest_hash=_digest("split"),
        controller_type="qwen3_model",
        execution_mode="model_backed_smoke",
        created_at="2026-11-21T00:00:00+00:00",
    )


def _runtime_metadata() -> RuntimeCheckpointMetadata:
    return RuntimeCheckpointMetadata(
        max_model_calls=10,
        model_calls_used=1,
        prompt_tokens_used=4,
        completion_tokens_used=2,
        resource_usage={"wall_seconds": 0.01, "gpu_used": False},
        tool_adapter_state=AdapterStateMetadata(
            AdapterStateStatus.STATEFUL, {"next_call_index": 2}
        ),
        fault_adapter_state=AdapterStateMetadata(AdapterStateStatus.EMPTY, {}),
        phase="after_tool_observation_before_model_generation",
        next_operation="model_generation",
    )


def _expected(snapshot, first_request, *, messages=None, generation=None) -> ExpectedModelRequest:
    payload = snapshot.payload
    return ExpectedModelRequest(
        messages=messages if messages is not None else payload["canonical_next_model_input"],
        next_model_input_hash=payload["next_model_input_hash"],
        public_tool_schemas=first_request.public_tool_schemas,
        generation_config=generation if generation is not None else payload["model"]["generation"],
        model_id=payload["model"]["model_id"],
        step_number=payload["agent"]["step_number"] + 1,
        remaining_steps=payload["agent"]["remaining_steps"],
        model_calls_used=1,
        max_model_calls=10,
        tool_calls_used=1,
        max_tool_calls=12,
        tokens_used=6,
        max_tokens=50_000,
        latency_seconds_used=0.01,
        max_latency_seconds=900.0,
    )


def _capture(tmp_path: Path):
    task = _task()
    environment = TaskEnvironment(tmp_path / "source", task)
    environment.reset()
    runtime = _runtime(task)
    agent, model = _agent(environment, runtime, [_tool_call(), _final_answer()])
    stream = agent.run(task.prompt, stream=True)
    event = None
    while not isinstance(event, ActionStep):
        event = next(stream)
    runtime.budget.tokens_used = 6
    assert event.observations == "counter=1"
    assert agent.step_number == 1
    capture = DecisionCheckpointCapture()
    result = capture.capture(
        event=event,
        agent=agent,
        manager=CheckpointManager(tmp_path / "checkpoints"),
        checkpoint_id="day3_checkpoint",
        environment=environment,
        runtime_state=runtime,
        identity=_identity(),
        runtime_metadata=_runtime_metadata(),
        environment_metadata=EnvironmentCheckpointMetadata(
            completed_side_effects=[{"idempotency_key": "day3-key-001", "commit_state": "committed"}],
            latest_completed_operation={"kind": "tool_call", "call_id": "call-day3-001"},
        ),
    )
    return task, environment, runtime, agent, model, stream, result


def test_capture_boundary_is_after_append_before_next_model_and_close_is_safe(tmp_path: Path) -> None:
    _, environment, runtime, agent, model, stream, result = _capture(tmp_path)
    checkpoint_hash = _digest((result.snapshot.checkpoint_path / "manifest.json").read_text())
    assert result.status == "checkpoint_captured"
    assert result.model_calls_before == result.model_calls_after == 1
    assert model.attempted_calls == model.succeeded_calls == 1
    assert agent.memory.steps[-1].observations == "counter=1"
    assert runtime.budget.tool_calls_used == 1
    stream.close()
    assert model.attempted_calls == 1
    assert environment.resolve_path("counter.txt").read_text() == "1"
    assert _digest((result.snapshot.checkpoint_path / "manifest.json").read_text()) == checkpoint_hash


def test_exact_original_and_restored_first_inputs_and_no_tool_replay(tmp_path: Path) -> None:
    task, source, _, agent, source_model, source_stream, captured = _capture(tmp_path)
    snapshot = AgentStateAdapter.capture(agent)
    expected = _expected(snapshot, source_model.requests[0])
    source_model.expect(expected)
    source_model.intercept_valid_request = True
    next(source_stream)
    source_stream.close()
    original_request = source_model.requests[-1]
    assert original_request.status == "intercepted"

    restored_environment = TaskEnvironment(tmp_path / "restored", task)
    restored_environment.reset()
    restored = CheckpointManager(tmp_path / "checkpoints").restore_v2(
        checkpoint_id="day3_checkpoint",
        environment=restored_environment,
    )
    restored_agent, restored_model = _agent(
        restored_environment, restored.runtime_state, [_final_answer()]
    )
    restored_model.restore_model_calls_used(1)
    restored_agent.restore_snapshot(restored.agent_state_snapshot)
    restored_model.expect(expected)
    list(restored_agent.resume(stream=True))
    restored_request = restored_model.requests[0]

    assert original_request.canonical_bytes == restored_request.canonical_bytes
    assert original_request.input_hash == captured.snapshot.manifest.hashes["next_model_input_hash"]
    assert restored_request.input_hash == captured.snapshot.manifest.hashes["next_model_input_hash"]
    assert restored_agent.step_number == 3
    assert restored_model.model_calls_used == 2
    action_steps = [step for step in restored_agent.memory.steps if isinstance(step, ActionStep)]
    assert [step.step_number for step in action_steps] == [1, 2]
    assert restored.runtime_state.budget.tokens_used == 6
    assert restored.runtime_state.budget.tool_calls_used == 1
    assert restored.runtime_state.budget.latency_seconds_used == 0.01
    assert restored_environment.resolve_path("counter.txt").read_text() == "1"
    assert source.resolve_path("counter.txt").read_text() == "1"
    assert len(restored.runtime_state.tool_history) == 1


@pytest.mark.parametrize("mutation", ["observation", "call_id", "generation"])
def test_restored_request_mismatch_is_rejected_before_model_execution(
    tmp_path: Path, mutation: str
) -> None:
    _, _, _, agent, source_model, source_stream, captured = _capture(tmp_path)
    snapshot = AgentStateAdapter.capture(agent)
    messages = snapshot.payload["canonical_next_model_input"]
    generation = snapshot.payload["model"]["generation"]
    if mutation == "observation":
        messages = [dict(item) for item in messages]
        messages[-1] = {**messages[-1], "content": [{"type": "text", "text": "tampered"}]}
    elif mutation == "call_id":
        messages = [dict(item) for item in messages]
        for item in messages:
            content = item.get("content")
            if isinstance(content, list) and any(
                "call-day3-001" in str(block.get("text")) for block in content
            ):
                item["content"] = [
                    {
                        **block,
                        "text": str(block.get("text", "")).replace(
                            "call-day3-001", "tampered-call"
                        ),
                    }
                    for block in content
                ]
                break
    else:
        generation = {**generation, "temperature": 0.5}
    expected = _expected(
        snapshot,
        source_model.requests[0],
        messages=messages,
        generation=generation,
    )
    source_model.expect(expected)
    with pytest.raises(ModelRequestRejected):
        source_model.generate(
            agent.write_memory_to_messages(),
            tools_to_call_from=agent.tools_and_managed_agents,
        )
    assert source_model.executed_calls == 1
    source_stream.close()
    assert captured.snapshot.checkpoint_path.is_dir()


def test_capture_rejects_missing_observation_and_exhausted_budget(tmp_path: Path) -> None:
    task = _task()
    environment = TaskEnvironment(tmp_path / "source", task)
    environment.reset()
    runtime = _runtime(task)
    agent, _ = _agent(environment, runtime, [_tool_call()])
    event = ActionStep(step_number=1, timing=type("Timing", (), {})())  # invalid and unattached
    capture = DecisionCheckpointCapture()
    with pytest.raises(DecisionCheckpointRejected, match="not_appended"):
        capture.capture(
            event=event,
            agent=agent,
            manager=CheckpointManager(tmp_path / "checkpoints"),
            checkpoint_id="rejected",
            environment=environment,
            runtime_state=runtime,
            identity=_identity(),
            runtime_metadata=_runtime_metadata(),
            environment_metadata=EnvironmentCheckpointMetadata([], None),
        )
    assert not (tmp_path / "checkpoints" / "rejected").exists()


@pytest.mark.parametrize(
    ("case", "expected_reason"),
    [
        ("missing_observation", "tool_observation_missing"),
        ("unfinished_batch", "tool_call_batch_not_settled"),
        ("verifier_ran", "verifier_already_ran"),
        ("token_exhausted", "token_budget_exhausted"),
        ("terminal", "final_answer_is_terminal"),
    ],
)
def test_capture_invariants_are_measured_and_rejections_have_reasons(
    tmp_path: Path, case: str, expected_reason: str
) -> None:
    _, environment, runtime, agent, _, stream, _ = _capture(tmp_path)
    event = agent.memory.steps[-1]
    assert isinstance(event, ActionStep)
    active_tool_calls = 0
    verifier_has_run = False
    if case == "missing_observation":
        event.observations = None
    elif case == "unfinished_batch":
        active_tool_calls = 1
    elif case == "verifier_ran":
        verifier_has_run = True
    elif case == "token_exhausted":
        runtime.budget.tokens_used = runtime.budget.max_tokens
    else:
        event.is_final_answer = True
    capture = DecisionCheckpointCapture()
    with pytest.raises(DecisionCheckpointRejected, match=expected_reason):
        capture.capture(
            event=event,
            agent=agent,
            manager=CheckpointManager(tmp_path / "checkpoints"),
            checkpoint_id=f"rejected_{case}",
            environment=environment,
            runtime_state=runtime,
            identity=_identity(),
            runtime_metadata=_runtime_metadata(),
            environment_metadata=EnvironmentCheckpointMetadata([], None),
            verifier_has_run=verifier_has_run,
            active_tool_calls=active_tool_calls,
        )
    assert capture.decisions[-1].reason == expected_reason
    assert environment.resolve_path("counter.txt").read_text() == "1"
    assert not (tmp_path / "checkpoints" / f"rejected_{case}").exists()
    stream.close()


def test_read_only_and_multi_tool_batches_use_real_framework_lifecycle() -> None:
    tool = ReadOnlyProbeTool()
    model = RecordingModel(
        ScriptedModel(
            [
                _calls_message(
                    [
                        ("call-b", "read_probe", {"value": "B"}),
                        ("call-a", "read_probe", {"value": "A"}),
                    ]
                )
            ]
        )
    )
    agent = CheckpointableToolCallingAgent(
        tools=[tool], model=model, max_steps=3, verbosity_level=0
    )
    stream = agent.run("Read two values.", stream=True)
    event = None
    while not isinstance(event, ActionStep):
        event = next(stream)
    assert [call.id for call in event.tool_calls or []] == ["call-a", "call-b"]
    assert event.observations == "read:A\nread:B"
    assert tool.calls == 2
    stream.close()
    assert model.model_calls_used == 1
    assert tool.calls == 2


def test_agent_visible_tool_error_preserves_call_id_and_close_has_no_retry() -> None:
    tool = ErrorProbeTool()
    model = RecordingModel(
        ScriptedModel([_calls_message([("call-error", "error_probe", {})])])
    )
    agent = CheckpointableToolCallingAgent(
        tools=[tool], model=model, max_steps=3, verbosity_level=0
    )
    stream = agent.run("Run the failing probe once.", stream=True)
    event = None
    while not isinstance(event, ActionStep):
        event = next(stream)
    assert event.error is not None
    assert "visible probe failure" in str(event.error)
    assert [call.id for call in event.tool_calls or []] == ["call-error"]
    assert agent.write_memory_to_messages()[-1].role is MessageRole.TOOL_RESPONSE
    stream.close()
    assert model.model_calls_used == 1


def test_restored_exhausted_model_budget_blocks_before_underlying_request(tmp_path: Path) -> None:
    _, _, _, agent, source_model, stream, _ = _capture(tmp_path)
    snapshot = AgentStateAdapter.capture(agent)
    expected = replace(
        _expected(snapshot, source_model.requests[0]),
        model_calls_used=10,
        max_model_calls=10,
    )
    blocked = RecordingModel(ScriptedModel([_final_answer()]))
    blocked.restore_model_calls_used(10)
    blocked.expect(expected)
    with pytest.raises(ModelRequestRejected, match="model_call_budget"):
        blocked.generate(
            agent.write_memory_to_messages(),
            tools_to_call_from=agent.tools_and_managed_agents,
        )
    assert blocked.executed_calls == blocked.succeeded_calls == 0
    stream.close()
