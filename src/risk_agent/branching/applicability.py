"""Applicability rules for Continue, Retry, and Replan branches."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

from risk_agent.faults import RetrySafetyEvaluator
from risk_agent.faults.schema import (
    Action,
    CommitStatus,
    FaultEvent,
    FaultType,
    ToolOperation,
)
from risk_agent.monitoring import RuntimeState

from .schema import ApplicabilityDecision


class BranchApplicabilityEvaluator:
    """Evaluate actions using only state known at the checkpoint."""

    def __init__(self, retry_safety: RetrySafetyEvaluator | None = None) -> None:
        self.retry_safety = retry_safety or RetrySafetyEvaluator()

    def evaluate(self, action: Action, runtime_state: RuntimeState) -> ApplicabilityDecision:
        pending = runtime_state.tool_state.get("pending_operation")
        if not isinstance(pending, Mapping):
            return ApplicabilityDecision(action, False, "No pending operation at checkpoint")
        tool_name = pending.get("tool_name")
        arguments = pending.get("arguments")
        if not isinstance(tool_name, str) or not isinstance(arguments, Mapping):
            return ApplicabilityDecision(action, False, "Pending operation is malformed")

        if action == Action.CONTINUE:
            return ApplicabilityDecision(action, True, "A validated current-plan operation is pending")
        if action == Action.REPLAN:
            observation = runtime_state.tool_state.get("replan_observation")
            replan_steps = runtime_state.tool_state.get("replan_steps")
            has_steps = isinstance(replan_steps, list) and bool(replan_steps)
            if not isinstance(observation, Mapping) and not has_steps:
                return ApplicabilityDecision(
                    action, False, "No corrected plan or state-refresh operation is available"
                )
            return ApplicabilityDecision(
                action, True, "Checkpoint includes a state-refresh operation and target goal"
            )
        if action != Action.RETRY:
            return ApplicabilityDecision(action, False, "Action is outside the Day 6 branch set")

        failure = runtime_state.tool_state.get("last_failure")
        if not isinstance(failure, Mapping):
            return ApplicabilityDecision(action, False, "No failed operation is available to retry")
        try:
            fault_type = FaultType(str(failure["fault_type"]))
            commit_status = CommitStatus(str(failure["commit_status"]))
        except (KeyError, ValueError):
            return ApplicabilityDecision(action, False, "Failure commit metadata is missing or invalid")
        read_only = bool(pending.get("read_only", False))
        idempotency_key = arguments.get("idempotency_key")
        has_idempotency_key = isinstance(idempotency_key, str) and bool(idempotency_key)
        if not read_only and not has_idempotency_key:
            return ApplicabilityDecision(
                action, False, "Write retry requires a non-empty idempotency key"
            )
        event = FaultEvent(
            event_id=str(failure.get("event_id", "checkpoint_failure")),
            fault_id=str(failure.get("fault_id", "day6_failure")),
            fault_type=fault_type,
            seed=runtime_state.seed,
            tool_name=tool_name,
            call_index=int(failure.get("call_index", 1)),
            triggered=True,
            phase=str(failure.get("phase", "after_execution")),
            commit_status=commit_status,
            response_received=bool(failure.get("response_received", False)),
            error_type=str(failure.get("error_type", "PartialFailureError")),
            arguments_hash=str(failure.get("arguments_hash", "checkpoint-recorded")),
            timestamp=str(failure.get("timestamp", datetime.now(timezone.utc).isoformat())),
        )
        safety = self.retry_safety.evaluate(
            event,
            ToolOperation(
                tool_name=tool_name,
                read_only=read_only,
                idempotency_key_present=has_idempotency_key,
            ),
        )
        return ApplicabilityDecision(action, safety.retry_allowed, safety.reason)
