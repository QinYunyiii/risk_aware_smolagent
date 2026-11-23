from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from smolagents.agents import AgentGenerationError
from smolagents.memory import ActionStep, TaskStep
from smolagents.models import (
    ChatMessage,
    ChatMessageToolCall,
    ChatMessageToolCallFunction,
    MessageRole,
)
from smolagents.monitoring import TokenUsage

from risk_agent.agent import AgentStateAdapter, CheckpointResumeError
from risk_agent.benchmark import TaskEnvironment
from risk_agent.checkpoint import CheckpointManager
from tests.agent.test_checkpointable_agent import (
    _agent,
    _capture,
    _expected,
    _final_answer,
)


def _increment_message(call_id: str, key: str) -> ChatMessage:
    return ChatMessage(
        role=MessageRole.ASSISTANT,
        content=f"increment with {key}",
        tool_calls=[
            ChatMessageToolCall(
                id=call_id,
                type="function",
                function=ChatMessageToolCallFunction(
                    name="increment_counter",
                    arguments={"idempotency_key": key},
                ),
            )
        ],
        token_usage=TokenUsage(input_tokens=4, output_tokens=2),
    )


def _error_message(call_id: str) -> ChatMessage:
    return ChatMessage(
        role=MessageRole.ASSISTANT,
        content="run the visible error probe",
        tool_calls=[
            ChatMessageToolCall(
                id=call_id,
                type="function",
                function=ChatMessageToolCallFunction(
                    name="error_probe",
                    arguments={},
                ),
            )
        ],
        token_usage=TokenUsage(input_tokens=3, output_tokens=2),
    )


def _checkpoint_inventory(root: Path) -> dict[str, tuple[int, str]]:
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_size,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _restore(
    tmp_path: Path,
    responses: list[ChatMessage],
    *,
    tokens_used: int | None = None,
):
    task, _, source_runtime, source_agent, source_model, source_stream, captured = _capture(
        tmp_path
    )
    snapshot = AgentStateAdapter.capture(source_agent)
    expected = _expected(snapshot, source_model.requests[0], source_runtime)
    source_stream.close()
    checkpoint_before = _checkpoint_inventory(captured.snapshot.checkpoint_path)

    environment = TaskEnvironment(tmp_path / "restored", task)
    environment.reset()
    restored = CheckpointManager(tmp_path / "checkpoints").restore_v2(
        checkpoint_id="day3_checkpoint",
        environment=environment,
    )
    if tokens_used is not None:
        restored.runtime_state.budget.tokens_used = tokens_used
        expected = replace(expected, tokens_used=tokens_used)
    agent, model = _agent(environment, restored.runtime_state, responses)
    model.restore_model_calls_used(1)
    agent.restore_snapshot(restored.agent_state_snapshot)
    model.expect(expected)
    return (
        environment,
        restored,
        agent,
        model,
        captured.snapshot.checkpoint_path,
        checkpoint_before,
    )


def test_resume_executes_three_model_requests_then_final_answer_without_replay(
    tmp_path: Path,
) -> None:
    environment, restored, agent, model, checkpoint, checkpoint_before = _restore(
        tmp_path,
        [
            _increment_message("call-resume-002", "resume-key-002"),
            _increment_message("call-resume-003", "resume-key-003"),
            _final_answer(),
        ],
    )

    result = agent.resume()

    assert result == "done"
    assert model.first_request_verified
    assert model.contract_state == "first_restored_request_verified"
    assert model.model_calls_used == 4
    assert model.executed_calls == model.succeeded_calls == 3
    assert [request.status for request in model.requests] == [
        "succeeded",
        "succeeded",
        "succeeded",
    ]
    assert model.requests[0].input_hash == restored.restored_next_model_input_hash
    assert model.requests[1].input_hash != model.requests[0].input_hash
    assert model.requests[2].input_hash != model.requests[0].input_hash
    assert [request.model_calls_used_after for request in model.requests] == [2, 3, 4]
    assert restored.runtime_state.budget.tokens_used == 28
    assert model.prompt_tokens_observed == 16
    assert model.completion_tokens_observed == 6
    assert model.model_latency_seconds_observed > 0
    assert environment.resolve_path("counter.txt").read_text() == "3"
    assert len(restored.runtime_state.tool_history) == 3
    action_steps = [step for step in agent.memory.steps if isinstance(step, ActionStep)]
    assert [step.step_number for step in action_steps] == [1, 2, 3, 4]
    assert len([step for step in agent.memory.steps if isinstance(step, TaskStep)]) == 1
    assert [step.tool_calls[0].id for step in action_steps[:3]] == [
        "call-day3-001",
        "call-resume-002",
        "call-resume-003",
    ]
    assert _checkpoint_inventory(checkpoint) == checkpoint_before


def test_second_resumed_provider_failure_is_counted_and_not_retried(
    tmp_path: Path,
) -> None:
    environment, restored, agent, model, checkpoint, checkpoint_before = _restore(
        tmp_path,
        [_increment_message("call-resume-002", "resume-key-002")],
    )

    with pytest.raises(AgentGenerationError, match="Unexpected scripted model call"):
        agent.resume()

    assert model.model_calls_used == 3
    assert model.executed_calls == 2
    assert model.succeeded_calls == 1
    assert [request.status for request in model.requests] == ["succeeded", "failed"]
    assert model.underlying.calls == 2
    assert environment.resolve_path("counter.txt").read_text() == "2"
    assert len(restored.runtime_state.tool_history) == 2
    assert _checkpoint_inventory(checkpoint) == checkpoint_before


def test_exact_token_budget_blocks_resumed_tool_and_next_provider_request(
    tmp_path: Path,
) -> None:
    environment, restored, agent, model, checkpoint, checkpoint_before = _restore(
        tmp_path,
        [
            _increment_message("call-resume-002", "resume-key-002"),
            _final_answer(),
        ],
        tokens_used=49_994,
    )

    with pytest.raises(AgentGenerationError, match="token_budget"):
        agent.resume()

    assert model.model_calls_used == 2
    assert model.executed_calls == model.succeeded_calls == 1
    assert model.rejected_calls == 1
    assert [request.status for request in model.requests] == ["succeeded", "rejected"]
    assert model.requests[-1].difference == "token_budget"
    assert model.underlying.calls == 1
    assert restored.runtime_state.budget.tokens_used == 50_000
    action_steps = [step for step in agent.memory.steps if isinstance(step, ActionStep)]
    assert action_steps[1].error is not None
    assert "terminal_token_budget" in str(action_steps[1].error)
    assert environment.resolve_path("counter.txt").read_text() == "1"
    assert len(restored.runtime_state.tool_history) == 1
    assert _checkpoint_inventory(checkpoint) == checkpoint_before


def test_agent_visible_tool_error_preserves_id_and_later_request_continues(
    tmp_path: Path,
) -> None:
    environment, restored, agent, model, checkpoint, checkpoint_before = _restore(
        tmp_path,
        [_error_message("call-visible-error-002"), _final_answer()],
    )

    result = agent.resume()

    assert result == "done"
    assert model.model_calls_used == 3
    assert model.executed_calls == model.succeeded_calls == 2
    action_steps = [step for step in agent.memory.steps if isinstance(step, ActionStep)]
    assert [step.step_number for step in action_steps] == [1, 2, 3]
    assert action_steps[1].error is not None
    assert "visible probe failure" in str(action_steps[1].error)
    assert [call.id for call in action_steps[1].tool_calls or []] == [
        "call-visible-error-002"
    ]
    assert environment.resolve_path("counter.txt").read_text() == "1"
    assert len(restored.runtime_state.tool_history) == 1
    assert _checkpoint_inventory(checkpoint) == checkpoint_before


def test_resume_without_first_request_contract_fails_closed(tmp_path: Path) -> None:
    task, _, _, _, _, source_stream, _ = _capture(tmp_path)
    source_stream.close()
    environment = TaskEnvironment(tmp_path / "restored", task)
    environment.reset()
    restored = CheckpointManager(tmp_path / "checkpoints").restore_v2(
        checkpoint_id="day3_checkpoint",
        environment=environment,
    )
    agent, model = _agent(environment, restored.runtime_state, [_final_answer()])
    model.restore_model_calls_used(1)
    agent.restore_snapshot(restored.agent_state_snapshot)

    with pytest.raises(CheckpointResumeError, match="first-request contract"):
        agent.resume()

    assert model.attempted_calls == model.executed_calls == 0
