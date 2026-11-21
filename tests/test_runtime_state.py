from __future__ import annotations

import math

import pytest

from risk_agent.monitoring import (
    BudgetLimitExceeded,
    BudgetState,
    RuntimeState,
    ToolHistoryEntry,
)


def history_entry() -> ToolHistoryEntry:
    return ToolHistoryEntry(
        step_id="step_001",
        tool_name="calculator",
        tool_arguments={"expression": "1 + 1", "optional": None, "count": 0},
        tool_result={"value": 2, "cached": False},
        error_type=None,
        timestamp="2026-11-21T00:00:00+00:00",
        token_usage={"input_tokens": 0, "output_tokens": 2},
        latency_seconds=0.0,
        worker_id="worker-0",
        environment_id="environment-001",
        checkpoint_id=None,
    )


def runtime_state() -> RuntimeState:
    return RuntimeState(
        seed=7,
        agent_memory=[
            {
                "role": "tool",
                "content": [None, 0, False, "", {"nested": [1, 2.5, True]}],
            }
        ],
        tool_history=[history_entry()],
        tool_state={"cursor": 0, "optional": None, "enabled": False},
        budget=BudgetState(
            max_tokens=100,
            max_tool_calls=10,
            max_latency_seconds=30.0,
            tokens_used=2,
            tool_calls_used=1,
            latency_seconds_used=0.0,
        ),
    )


def test_runtime_state_strict_json_round_trip_preserves_nested_values() -> None:
    original = runtime_state()
    document = original.to_dict()
    restored = RuntimeState.from_dict(document)

    assert restored.to_dict() == document
    content = document["agent_memory"][0]["content"]
    assert content[:4] == [None, 0, False, ""]
    assert content[0] is None
    assert content[1] == 0 and content[1] is not False
    assert content[2] is False


def test_tool_history_entry_round_trip_is_lossless() -> None:
    original = history_entry()
    restored = ToolHistoryEntry.from_dict(original.to_dict())

    assert restored.to_dict() == original.to_dict()
    assert restored.tool_arguments["optional"] is None
    assert restored.tool_arguments["count"] == 0
    assert restored.tool_result["cached"] is False


@pytest.mark.parametrize(
    "invalid_value",
    [object(), ("tuple",), {"set"}, complex(1, 2)],
)
def test_runtime_state_rejects_unsupported_objects(invalid_value: object) -> None:
    state = runtime_state()
    state.tool_state["invalid"] = invalid_value

    with pytest.raises(TypeError, match="Unsupported JSON value"):
        state.to_dict()


def test_runtime_state_rejects_non_string_dictionary_keys() -> None:
    state = runtime_state()
    state.tool_state[1] = "not allowed"  # type: ignore[index]

    with pytest.raises(TypeError, match="Non-string JSON object key"):
        state.to_dict()


@pytest.mark.parametrize("invalid_float", [math.nan, math.inf, -math.inf])
def test_runtime_state_rejects_non_finite_floats(invalid_float: float) -> None:
    state = runtime_state()
    state.tool_state["invalid_float"] = invalid_float

    with pytest.raises(ValueError, match="Non-finite float"):
        state.to_dict()


def test_tool_history_serialization_rejects_unsupported_result() -> None:
    entry = history_entry()
    object.__setattr__(entry, "tool_result", object())

    with pytest.raises(TypeError, match="Unsupported JSON value"):
        entry.to_dict()


def test_runtime_deserialization_rejects_silent_scalar_coercion() -> None:
    document = runtime_state().to_dict()
    document["seed"] = "7"

    with pytest.raises(ValueError, match="seed must be an integer"):
        RuntimeState.from_dict(document)


def test_tool_call_budget_record_never_exceeds_hard_limit() -> None:
    budget = BudgetState(
        max_tokens=100,
        max_tool_calls=12,
        max_latency_seconds=30.0,
        tool_calls_used=12,
    )

    with pytest.raises(BudgetLimitExceeded, match="tool_call_budget"):
        budget.record(tokens=0, tool_calls=1, latency_seconds=0.0)

    assert budget.tool_calls_used == 12
