"""Rules that prevent unsafe blind retries after injected or real failures."""

from __future__ import annotations

from .schema import (
    Action,
    CommitStatus,
    FaultEvent,
    FaultType,
    SafetyDecision,
    ToolOperation,
)


class UnsafeRetryError(RuntimeError):
    pass


class RetrySafetyEvaluator:
    def evaluate(self, event: FaultEvent, operation: ToolOperation) -> SafetyDecision:
        if not event.triggered or event.fault_type == FaultType.F0_NO_FAULT:
            return SafetyDecision(
                requested_action=Action.RETRY,
                action_applicable=False,
                retry_allowed=False,
                recommended_action=Action.CONTINUE,
                requires_state_check=False,
                reason="No injected failure requires a retry",
            )
        if event.fault_type == FaultType.F1_TIMEOUT:
            safe = operation.read_only or event.commit_status in {
                CommitStatus.NOT_STARTED,
                CommitStatus.NOT_COMMITTED,
            }
            return SafetyDecision(
                requested_action=Action.RETRY,
                action_applicable=True,
                retry_allowed=safe,
                recommended_action=Action.RETRY if safe else Action.REPLAN,
                requires_state_check=not safe,
                reason=(
                    "Read-only or known uncommitted timeout"
                    if safe
                    else "Write commit state is unknown; query state before any retry"
                ),
            )
        if event.fault_type == FaultType.F2_API_503:
            safe = event.commit_status in {CommitStatus.NOT_STARTED, CommitStatus.NOT_COMMITTED}
            return SafetyDecision(
                requested_action=Action.RETRY,
                action_applicable=True,
                retry_allowed=safe,
                recommended_action=Action.RETRY if safe else Action.REPLAN,
                requires_state_check=not safe,
                reason="503 occurred before execution" if safe else "503 commit state is uncertain",
            )
        if event.fault_type == FaultType.F3_INVALID_ARGUMENT:
            return SafetyDecision(
                requested_action=Action.RETRY,
                action_applicable=False,
                retry_allowed=False,
                recommended_action=Action.REPLAN,
                requires_state_check=False,
                reason="Retrying unchanged invalid arguments cannot make progress",
            )
        if event.fault_type == FaultType.F4_STALE_OBSERVATION:
            return SafetyDecision(
                requested_action=Action.RETRY,
                action_applicable=False,
                retry_allowed=False,
                recommended_action=Action.REPLAN,
                requires_state_check=True,
                reason="Refresh state before acting on a stale observation",
            )
        if event.fault_type == FaultType.F5_REPEATED_ACTION:
            return SafetyDecision(
                requested_action=Action.RETRY,
                action_applicable=False,
                retry_allowed=False,
                recommended_action=Action.REPLAN,
                requires_state_check=False,
                reason="Another identical action would continue the no-progress loop",
            )
        if event.fault_type == FaultType.F6_PARTIAL_FAILURE:
            known_uncommitted = event.commit_status in {
                CommitStatus.NOT_STARTED,
                CommitStatus.NOT_COMMITTED,
            }
            safe = known_uncommitted and (
                operation.read_only or operation.idempotency_key_present
            )
            return SafetyDecision(
                requested_action=Action.RETRY,
                action_applicable=True,
                retry_allowed=safe,
                recommended_action=Action.RETRY if safe else Action.REPLAN,
                requires_state_check=not safe,
                reason=(
                    "The operation is known not to have committed and is idempotent"
                    if safe
                    else (
                        "Known uncommitted write lacks an idempotency key"
                        if known_uncommitted
                        else "Partial failure committed or is uncertain; verify state before retry"
                    )
                ),
            )
        raise AssertionError(f"Unhandled fault type: {event.fault_type}")


class RetryGuard:
    def __init__(self, evaluator: RetrySafetyEvaluator | None = None) -> None:
        self.evaluator = evaluator or RetrySafetyEvaluator()

    def require_safe(self, event: FaultEvent, operation: ToolOperation) -> SafetyDecision:
        decision = self.evaluator.evaluate(event, operation)
        if not decision.retry_allowed:
            raise UnsafeRetryError(decision.reason)
        return decision
