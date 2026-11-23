"""Protocol-v2 CONTINUE outcome validity, verifier, and tri-state label logic."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Mapping

from risk_agent.benchmark import DeterministicVerifier, TaskEnvironment, ValidationResult
from risk_agent.benchmark.schema import TaskSpec


class ContinueTermination(StrEnum):
    FINAL_ANSWER = "final_answer"
    AGENT_ERROR = "agent_error"
    BUDGET_EXHAUSTION = "budget_exhaustion"
    MODEL_SERVICE_FAILURE = "model_service_failure"
    ENVIRONMENT_FAILURE = "environment_failure"
    MANUAL_ABORT = "manual_abort"
    UNVERIFIABLE = "unverifiable"


@dataclass(frozen=True, slots=True)
class ContinueLineage:
    checkpoint_id: str
    source_task_run_id: str
    branch_id: str
    branch_attempt_id: str
    restored_state_hash: str
    first_model_input_hash: str | None
    model_event_ids: tuple[str, ...]
    tool_event_ids: tuple[str, ...]
    final_agent_state_ref: str
    final_environment_state_ref: str


@dataclass(frozen=True, slots=True)
class ContinueOutcome:
    continue_outcome_id: str
    verifier_outcome_id: str | None
    termination: str
    outcome_status: str
    failure_category: str
    y_continue: int | None
    verifier_passed: bool | None
    verifier_checks: Mapping[str, bool] | None
    verifier_error: str | None
    tool_error_observed: bool
    lineage: ContinueLineage
    formal_experiment: bool = False
    execution_mode: str = "model_backed_smoke"
    controller_type: str = "qwen3_model"
    research_dataset_eligible: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ContinueOutcomeEvaluator:
    """Invoke the deterministic verifier only for valid, observable outcomes."""

    _CENSORED = {
        ContinueTermination.MODEL_SERVICE_FAILURE: "model_service_failure",
        ContinueTermination.ENVIRONMENT_FAILURE: "environment_failure",
        ContinueTermination.MANUAL_ABORT: "manual_abort",
        ContinueTermination.UNVERIFIABLE: "environment_failure",
    }

    def __init__(self, verifier: DeterministicVerifier | None = None) -> None:
        self.verifier = verifier or DeterministicVerifier()

    def evaluate(
        self,
        *,
        task: TaskSpec,
        environment: TaskEnvironment,
        candidate_result: Any,
        termination: ContinueTermination,
        tool_error_observed: bool,
        lineage: ContinueLineage,
        continue_outcome_id: str | None = None,
        verifier_outcome_id: str | None = None,
    ) -> ContinueOutcome:
        outcome_id = continue_outcome_id or f"continue_outcome_{uuid.uuid4().hex}"
        if termination in self._CENSORED:
            return ContinueOutcome(
                continue_outcome_id=outcome_id,
                verifier_outcome_id=None,
                termination=termination.value,
                outcome_status="censored",
                failure_category=self._CENSORED[termination],
                y_continue=None,
                verifier_passed=None,
                verifier_checks=None,
                verifier_error=None,
                tool_error_observed=tool_error_observed,
                lineage=lineage,
            )

        actual_verifier_id = verifier_outcome_id or f"verifier_outcome_{uuid.uuid4().hex}"
        try:
            validation: ValidationResult = self.verifier.verify(
                task, environment, candidate_result
            )
        except Exception as exc:
            return ContinueOutcome(
                continue_outcome_id=outcome_id,
                verifier_outcome_id=actual_verifier_id,
                termination=ContinueTermination.UNVERIFIABLE.value,
                outcome_status="censored",
                failure_category="environment_failure",
                y_continue=None,
                verifier_passed=None,
                verifier_checks=None,
                verifier_error=f"{type(exc).__name__}: {exc}",
                tool_error_observed=tool_error_observed,
                lineage=lineage,
            )

        if validation.passed:
            category = "tool_failure_observed" if tool_error_observed else "none"
            label = 1
            status = "verified_success"
        else:
            label = 0
            status = "verified_failure"
            if termination is ContinueTermination.AGENT_ERROR:
                category = "agent_failure"
            elif termination is ContinueTermination.BUDGET_EXHAUSTION:
                category = "budget_exhaustion"
            elif tool_error_observed:
                category = "tool_failure_observed"
            else:
                category = "verification_failure"
        return ContinueOutcome(
            continue_outcome_id=outcome_id,
            verifier_outcome_id=actual_verifier_id,
            termination=termination.value,
            outcome_status=status,
            failure_category=category,
            y_continue=label,
            verifier_passed=validation.passed,
            verifier_checks=dict(validation.checks),
            verifier_error=None,
            tool_error_observed=tool_error_observed,
            lineage=lineage,
        )


__all__ = [
    "ContinueLineage",
    "ContinueOutcome",
    "ContinueOutcomeEvaluator",
    "ContinueTermination",
]
