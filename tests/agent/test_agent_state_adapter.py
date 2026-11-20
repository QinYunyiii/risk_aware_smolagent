from __future__ import annotations

import json

import pytest
from smolagents import Model, ToolCallingAgent
from smolagents.memory import ActionStep, PlanningStep, TaskStep, ToolCall
from smolagents.models import (
    ChatMessage,
    ChatMessageToolCall,
    ChatMessageToolCallFunction,
    MessageRole,
)
from smolagents.monitoring import Timing, TokenUsage

from risk_agent.agent import (
    AgentStateAdapter,
    AgentStateAdapterError,
    AgentStateIntegrityError,
    AgentStateSnapshot,
)
from risk_agent.agent.agent_state_adapter import (
    canonical_hash,
    deserialize_chat_message,
    deserialize_memory_step,
    serialize_chat_message,
    serialize_memory_step,
)
from risk_agent.tools.calculation_tools import CalculatorTool


class StubModel(Model):
    def __init__(self, model_id: str = "checkpoint-test-model") -> None:
        super().__init__(model_id=model_id, seed=17, temperature=0.0, max_tokens=128)

    def generate(self, *args, **kwargs):  # pragma: no cover - capture must never call the model
        raise AssertionError("AgentStateAdapter must not call model.generate")


class RecordedStepError(Exception):
    def dict(self) -> dict[str, str]:
        return {"type": type(self).__name__, "message": str(self)}


def make_agent(*, model_id: str = "checkpoint-test-model") -> ToolCallingAgent:
    return ToolCallingAgent(
        tools=[CalculatorTool()],
        model=StubModel(model_id),
        max_steps=10,
        planning_interval=3,
        verbosity_level=0,
        name="checkpoint_agent",
        description="Local checkpoint adapter test agent",
    )


def tool_call_message() -> ChatMessage:
    return ChatMessage(
        role=MessageRole.ASSISTANT,
        content=[{"type": "text", "text": "I will calculate it."}],
        tool_calls=[
            ChatMessageToolCall(
                id="call-exact-001",
                type="function",
                function=ChatMessageToolCallFunction(
                    name="calculator",
                    arguments={"expression": "6 * 7", "metadata": [0, None]},
                    description="deterministic calculator call",
                ),
            )
        ],
        raw={"provider_object": "must not be checkpointed"},
        token_usage=TokenUsage(input_tokens=0, output_tokens=9),
    )


def populate_memory(agent: ToolCallingAgent) -> None:
    task = "Use the calculator once and explain the result."
    model_input = [
        ChatMessage(role=MessageRole.SYSTEM, content="system input"),
        ChatMessage(role=MessageRole.USER, content=[{"type": "text", "text": task}]),
    ]
    output = tool_call_message()
    agent.task = task
    agent.step_number = 1
    agent.state = {"cursor": 0, "optional": None, "tuple_value": ("stable", 2)}
    agent.memory.steps = [
        TaskStep(task=task),
        PlanningStep(
            model_input_messages=model_input,
            model_output_message=ChatMessage(role=MessageRole.ASSISTANT, content="plan raw"),
            plan="Call the deterministic calculator, then report its output.",
            timing=Timing(start_time=10.0, end_time=10.5),
            token_usage=TokenUsage(input_tokens=0, output_tokens=4),
        ),
        ActionStep(
            step_number=1,
            timing=Timing(start_time=11.0, end_time=12.0),
            model_input_messages=model_input,
            tool_calls=[ToolCall(name="calculator", arguments={"expression": "6 * 7"}, id="call-exact-001")],
            model_output_message=output,
            model_output="I will calculate it.",
            observations="42",
            action_output={"value": 42, "cached": False},
            token_usage=TokenUsage(input_tokens=0, output_tokens=9),
            is_final_answer=False,
        ),
    ]


def test_chat_message_round_trip_preserves_model_visible_fields_and_tool_call_id() -> None:
    original = tool_call_message()
    serialized = serialize_chat_message(original)
    restored = deserialize_chat_message(json.loads(json.dumps(serialized)))

    assert serialize_chat_message(restored) == serialized
    assert restored.role is MessageRole.ASSISTANT
    assert restored.content == original.content
    assert restored.tool_calls[0].id == "call-exact-001"
    assert restored.tool_calls[0].function.arguments == {
        "expression": "6 * 7",
        "metadata": [0, None],
    }
    assert restored.token_usage.input_tokens == 0
    assert restored.raw is None
    assert "raw" not in serialized


@pytest.mark.parametrize("step_kind", ["task", "action", "planning"])
def test_each_memory_step_round_trip(step_kind: str) -> None:
    agent = make_agent()
    populate_memory(agent)
    step = next(item for item in agent.memory.steps if serialize_memory_step(item)["type"] == step_kind)

    serialized = serialize_memory_step(step)
    restored = deserialize_memory_step(json.loads(json.dumps(serialized)))

    assert serialize_memory_step(restored) == serialized
    assert [serialize_chat_message(item) for item in restored.to_messages()] == [
        serialize_chat_message(item) for item in step.to_messages()
    ]


def test_action_error_round_trip_preserves_rendered_error_and_nulls() -> None:
    step = ActionStep(
        step_number=2,
        timing=Timing(start_time=0.0, end_time=None),
        tool_calls=[ToolCall(name="calculator", arguments={"expression": "1 / 0"}, id="call-error")],
        error=RecordedStepError("division failed"),  # type: ignore[arg-type]
        observations=None,
        token_usage=TokenUsage(input_tokens=0, output_tokens=0),
    )
    serialized = serialize_memory_step(step)
    restored = deserialize_memory_step(serialized)

    assert serialized["observations"] is None
    assert serialized["timing"] == {"start_time": 0.0, "end_time": None}
    assert serialized["token_usage"] == {"input_tokens": 0, "output_tokens": 0}
    assert serialize_memory_step(restored) == serialized
    assert restored.to_messages()[1].content == step.to_messages()[1].content
    assert restored.dict()["error"] == step.dict()["error"]


def test_agent_state_capture_restore_preserves_memory_and_exact_next_input() -> None:
    original = make_agent()
    populate_memory(original)

    snapshot = AgentStateAdapter.capture(original)
    wire_value = json.loads(json.dumps(snapshot.to_dict(), ensure_ascii=False))
    decoded_snapshot = AgentStateSnapshot.from_dict(wire_value)
    restored = AgentStateAdapter.restore(make_agent(), decoded_snapshot)

    assert restored.task == original.task
    assert restored.step_number == 1
    assert restored.max_steps == 10
    assert restored.state == original.state
    assert len(restored.memory.steps) == len(original.memory.steps)
    assert snapshot.payload["agent"]["remaining_steps"] == 9
    assert snapshot.payload["memory"]["audit_full_steps"]
    original_messages = [serialize_chat_message(item) for item in original.write_memory_to_messages()]
    restored_messages = [serialize_chat_message(item) for item in restored.write_memory_to_messages()]
    assert original_messages == restored_messages
    assert canonical_hash(restored_messages) == snapshot.payload["next_model_input_hash"]
    action = restored.memory.steps[-1]
    assert isinstance(action, ActionStep)
    assert action.tool_calls[0].id == "call-exact-001"
    assert action.tool_calls[0].arguments == {"expression": "6 * 7"}
    assert action.observations == "42"


def test_capture_contains_no_model_client_or_provider_raw_object() -> None:
    agent = make_agent()
    populate_memory(agent)
    snapshot_json = json.dumps(AgentStateAdapter.capture(agent).to_dict())

    assert "provider_object" not in snapshot_json
    assert "client_kwargs" not in snapshot_json
    assert "api_key" not in snapshot_json


def test_restore_rejects_corruption_and_rebuilt_identity_mismatch() -> None:
    agent = make_agent()
    populate_memory(agent)
    snapshot = AgentStateAdapter.capture(agent)
    corrupted = snapshot.to_dict()
    corrupted["payload"]["agent"]["step_number"] = 99

    with pytest.raises(AgentStateIntegrityError, match="hash mismatch"):
        AgentStateSnapshot.from_dict(corrupted)
    with pytest.raises(AgentStateAdapterError, match="model identity"):
        AgentStateAdapter.restore(make_agent(model_id="different-model"), snapshot)
