from __future__ import annotations

from pathlib import Path

import pytest

from risk_agent.benchmark import TaskEnvironment
from risk_agent.benchmark.schema import DataSplit, Domain, TaskSpec, ToolStep
from risk_agent.collection.continue_outcome import (
    ContinueLineage,
    ContinueOutcomeEvaluator,
    ContinueTermination,
)


def _task() -> TaskSpec:
    return TaskSpec(
        task_id="b3_outcome_task",
        template_id="b3_outcome_template",
        instance_id="b3_outcome_001",
        domain=Domain.CALCULATION_LOGIC,
        seed=3,
        prompt="Return 2.",
        files=(),
        inventory=(),
        orders=(),
        reference_steps=(ToolStep("calculator", {"expression": "1 + 1"}),),
        expected={"result": "2"},
        dataset_role="day7_catalog",
        split=DataSplit.TRAIN,
    )


def _lineage() -> ContinueLineage:
    return ContinueLineage(
        checkpoint_id="checkpoint_b3_outcome_001",
        source_task_run_id="source_task_run_001",
        branch_id="continue_branch_001",
        branch_attempt_id="continue_attempt_001",
        restored_state_hash="a" * 64,
        first_model_input_hash="b" * 64,
        model_event_ids=("model_request_001",),
        tool_event_ids=(),
        final_agent_state_ref="branches/final_agent.json",
        final_environment_state_ref="branches/final_environment.json",
    )


@pytest.mark.parametrize(
    ("termination", "candidate", "tool_error", "expected_label", "category"),
    [
        (ContinueTermination.FINAL_ANSWER, "2", False, 1, "none"),
        (ContinueTermination.FINAL_ANSWER, "wrong", False, 0, "verification_failure"),
        (ContinueTermination.AGENT_ERROR, None, False, 0, "agent_failure"),
        (ContinueTermination.BUDGET_EXHAUSTION, None, False, 0, "budget_exhaustion"),
        (ContinueTermination.FINAL_ANSWER, "2", True, 1, "tool_failure_observed"),
        (ContinueTermination.FINAL_ANSWER, "wrong", True, 0, "tool_failure_observed"),
        (ContinueTermination.MODEL_SERVICE_FAILURE, None, False, None, "model_service_failure"),
        (ContinueTermination.ENVIRONMENT_FAILURE, None, False, None, "environment_failure"),
        (ContinueTermination.MANUAL_ABORT, None, False, None, "manual_abort"),
        (ContinueTermination.UNVERIFIABLE, None, False, None, "environment_failure"),
    ],
)
def test_continue_outcome_tri_state_contract(
    tmp_path: Path,
    termination: ContinueTermination,
    candidate: object,
    tool_error: bool,
    expected_label: int | None,
    category: str,
) -> None:
    task = _task()
    environment = TaskEnvironment(tmp_path, task)
    environment.reset()

    outcome = ContinueOutcomeEvaluator().evaluate(
        task=task,
        environment=environment,
        candidate_result=candidate,
        termination=termination,
        tool_error_observed=tool_error,
        lineage=_lineage(),
    )

    assert outcome.y_continue == expected_label
    assert outcome.failure_category == category
    assert outcome.research_dataset_eligible is False
    if expected_label is None:
        assert outcome.verifier_outcome_id is None
        assert outcome.verifier_passed is None
    else:
        assert outcome.verifier_outcome_id is not None
        assert outcome.verifier_passed is (expected_label == 1)


def test_verifier_exception_is_censored_not_converted_to_failure(tmp_path: Path) -> None:
    class BrokenVerifier:
        def verify(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("workspace became unavailable")

    task = _task()
    environment = TaskEnvironment(tmp_path, task)
    environment.reset()

    outcome = ContinueOutcomeEvaluator(BrokenVerifier()).evaluate(  # type: ignore[arg-type]
        task=task,
        environment=environment,
        candidate_result="2",
        termination=ContinueTermination.FINAL_ANSWER,
        tool_error_observed=False,
        lineage=_lineage(),
    )

    assert outcome.y_continue is None
    assert outcome.termination == "unverifiable"
    assert outcome.failure_category == "environment_failure"
    assert outcome.verifier_outcome_id is not None
    assert outcome.verifier_error == "RuntimeError: workspace became unavailable"
