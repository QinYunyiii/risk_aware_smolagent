"""Synchronous capture of the first eligible streamed decision checkpoint."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from smolagents.memory import ActionStep
from smolagents.models import MessageRole

from risk_agent.agent import AgentStateAdapter
from risk_agent.agent.checkpointable_tool_agent import CheckpointableToolCallingAgent
from risk_agent.agent.recording_model import RecordingModel
from risk_agent.benchmark import TaskEnvironment
from risk_agent.checkpoint import (
    CaptureInvariants,
    CheckpointIdentityV2,
    CheckpointManager,
    EnvironmentCheckpointMetadata,
    RuntimeCheckpointMetadata,
    SnapshotV2Result,
)
from risk_agent.monitoring import RuntimeState


class DecisionCheckpointRejected(RuntimeError):
    """A stream event did not satisfy the frozen capture boundary."""


@dataclass(frozen=True, slots=True)
class CaptureDecision:
    accepted: bool
    reason: str
    step_number: int | None


@dataclass(frozen=True, slots=True)
class DecisionCheckpointResult:
    status: str
    snapshot: SnapshotV2Result
    model_calls_before: int
    model_calls_after: int


class DecisionCheckpointCapture:
    """Validate and synchronously snapshot the first eligible ActionStep."""

    def __init__(self) -> None:
        self.decisions: list[CaptureDecision] = []
        self._captured = False

    def _reject(self, reason: str, event: object) -> None:
        step = event.step_number if isinstance(event, ActionStep) else None
        self.decisions.append(CaptureDecision(False, reason, step))
        raise DecisionCheckpointRejected(reason)

    def capture(
        self,
        *,
        event: object,
        agent: CheckpointableToolCallingAgent,
        manager: CheckpointManager,
        checkpoint_id: str,
        environment: TaskEnvironment,
        runtime_state: RuntimeState,
        identity: CheckpointIdentityV2,
        runtime_metadata: RuntimeCheckpointMetadata,
        environment_metadata: EnvironmentCheckpointMetadata,
        verifier_has_run: bool = False,
        active_tool_calls: int = 0,
    ) -> DecisionCheckpointResult:
        if self._captured:
            self._reject("primary_checkpoint_already_captured", event)
        if not isinstance(event, ActionStep):
            self._reject("event_is_not_action_step", event)
        if not agent.memory.steps or agent.memory.steps[-1] is not event:
            self._reject("action_step_not_appended_to_memory", event)
        if event.is_final_answer:
            self._reject("final_answer_is_terminal", event)
        if not event.tool_calls:
            self._reject("assistant_tool_call_missing", event)
        if any(call.name == "final_answer" for call in event.tool_calls):
            self._reject("final_answer_tool_call_is_terminal", event)
        if event.observations is None and event.error is None:
            self._reject("tool_observation_missing", event)
        if active_tool_calls != 0:
            self._reject("tool_call_batch_not_settled", event)
        assistant_calls = event.model_output_message.tool_calls if event.model_output_message else None
        if not assistant_calls:
            self._reject("assistant_model_tool_calls_missing", event)
        if {call.id for call in assistant_calls} != {call.id for call in event.tool_calls}:
            self._reject("assistant_and_executed_call_ids_differ", event)
        if verifier_has_run:
            self._reject("verifier_already_ran", event)
        if agent.step_number != event.step_number:
            self._reject("step_number_not_at_stream_yield", event)
        if agent.step_number >= agent.max_steps:
            self._reject("agent_step_budget_exhausted", event)
        budget = runtime_state.budget
        if budget.tokens_used >= budget.max_tokens:
            self._reject("token_budget_exhausted", event)
        if budget.latency_seconds_used >= budget.max_latency_seconds:
            self._reject("latency_budget_exhausted", event)
        if not isinstance(agent.model, RecordingModel):
            self._reject("recording_model_required", event)
        model_calls_before = agent.model.model_calls_used
        if model_calls_before != runtime_metadata.model_calls_used:
            self._reject("model_call_counter_inconsistent", event)
        if agent.model.attempted_calls != model_calls_before:
            self._reject("next_model_request_already_attempted", event)
        messages = agent.write_memory_to_messages()
        if not messages or messages[-1].role is not MessageRole.TOOL_RESPONSE:
            self._reject("canonical_messages_do_not_end_in_tool_response", event)

        agent_snapshot = AgentStateAdapter.capture(agent)
        invariants = CaptureInvariants(
            observation_in_memory=agent.memory.steps[-1] is event,
            next_model_request_not_started=(agent.model.attempted_calls == model_calls_before),
            non_terminal=not event.is_final_answer,
            remaining_step_available=agent.step_number < agent.max_steps,
            snapshot_synchronous=True,
            verifier_not_run=not verifier_has_run,
        )
        snapshot = manager.snapshot_v2(
            checkpoint_id=checkpoint_id,
            environment=environment,
            runtime_state=runtime_state,
            agent_state_snapshot=agent_snapshot,
            identity=identity,
            runtime_metadata=runtime_metadata,
            environment_metadata=environment_metadata,
            capture_invariants=invariants,
        )
        model_calls_after = agent.model.model_calls_used
        if model_calls_after != model_calls_before or agent.model.attempted_calls != model_calls_before:
            raise RuntimeError("Checkpoint capture advanced the model boundary")
        self._captured = True
        self.decisions.append(CaptureDecision(True, "first_eligible_observation", event.step_number))
        return DecisionCheckpointResult(
            status="checkpoint_captured",
            snapshot=snapshot,
            model_calls_before=model_calls_before,
            model_calls_after=model_calls_after,
        )


__all__ = [
    "CaptureDecision",
    "DecisionCheckpointCapture",
    "DecisionCheckpointRejected",
    "DecisionCheckpointResult",
]
