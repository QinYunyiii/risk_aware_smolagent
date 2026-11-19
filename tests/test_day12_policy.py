from __future__ import annotations

from typing import Mapping

from risk_agent.benchmark.schema import Domain
from risk_agent.controller import AdaptiveInterventionPolicy
from risk_agent.faults.schema import Action
from risk_agent.monitoring import BudgetState, RuntimeState


class StubModels:
    utility_lambda = 0.1

    def __init__(self, *, risk: float = 0.9, fail: bool = False) -> None:
        self.risk = risk
        self.fail = fail

    def predict_risk(self, features: Mapping[str, float]) -> float:
        if self.fail:
            raise RuntimeError("model unavailable")
        return self.risk

    def predict_action(
        self, action: str, features: Mapping[str, float]
    ) -> tuple[float, dict[str, float], float]:
        probability = {"CONTINUE": 0.2, "RETRY": 0.95, "REPLAN": 0.9}[action]
        costs = {"tokens": 0.0, "tool_calls": 1.0, "latency_seconds": 0.01}
        return probability, costs, 1.01


def _config() -> dict:
    return {
        "low_risk_threshold": 0.5,
        "minimum_utility_gain": 0.05,
        "max_retry_count": 1,
        "max_replan_count": 1,
        "max_same_action_streak": 1,
        "fallback_read_only_continue": True,
    }


def _runtime(*, read_only: bool = True, replan: bool = True) -> RuntimeState:
    arguments = {"expression": "1 + 1"}
    if not read_only:
        arguments = {"order_id": "ord-1", "new_status": "processing"}
    return RuntimeState(
        seed=7,
        tool_state={
            "pending_operation": {
                "tool_name": "calculator" if read_only else "update_order",
                "arguments": arguments,
                "read_only": read_only,
            },
            "last_failure": {
                "fault_type": "F2_API_503" if read_only else "F6_PARTIAL_FAILURE",
                "commit_status": "not_started" if read_only else "not_committed",
                "response_received": False,
                "call_index": 1,
            },
            "replan_steps": (
                [{"tool_name": "calculator", "arguments": {"expression": "1 + 1"}}]
                if replan
                else []
            ),
        },
        budget=BudgetState(
            max_tokens=1000,
            max_tool_calls=10,
            max_latency_seconds=10,
            tokens_used=100,
            tool_calls_used=1,
            latency_seconds_used=0.1,
        ),
    )


def test_low_risk_continues_without_comparing_interventions() -> None:
    policy = AdaptiveInterventionPolicy(models=StubModels(risk=0.1), config=_config())
    decision = policy.decide(runtime=_runtime(), domain=Domain.CALCULATION_LOGIC)
    assert decision.action == Action.CONTINUE
    assert decision.risk_level == "low"
    assert set(decision.action_scores) == {"CONTINUE"}


def test_high_risk_compares_and_blocks_unsafe_retry() -> None:
    policy = AdaptiveInterventionPolicy(models=StubModels(risk=0.9), config=_config())
    decision = policy.decide(
        runtime=_runtime(read_only=False, replan=True), domain=Domain.API_WORKFLOW
    )
    assert decision.action == Action.REPLAN
    assert "RETRY" in decision.excluded_actions
    assert set(decision.action_scores) == {"CONTINUE", "REPLAN"}


def test_retry_limit_prevents_infinite_loop() -> None:
    policy = AdaptiveInterventionPolicy(models=StubModels(risk=0.9), config=_config())
    runtime = _runtime(replan=False)
    first = policy.decide(runtime=runtime, domain=Domain.CALCULATION_LOGIC)
    assert first.action == Action.RETRY
    policy.record_action(runtime, first.action)
    second = policy.decide(runtime=runtime, domain=Domain.CALCULATION_LOGIC)
    assert second.action != Action.RETRY
    assert "RETRY" in second.excluded_actions


def test_replan_limit_prevents_infinite_loop() -> None:
    policy = AdaptiveInterventionPolicy(models=StubModels(risk=0.9), config=_config())
    runtime = _runtime(read_only=False, replan=True)
    first = policy.decide(runtime=runtime, domain=Domain.API_WORKFLOW)
    assert first.action == Action.REPLAN
    policy.record_action(runtime, first.action)
    second = policy.decide(runtime=runtime, domain=Domain.API_WORKFLOW)
    assert second.action != Action.REPLAN
    assert "REPLAN" in second.excluded_actions


def test_model_failure_uses_read_only_continue_or_write_stop() -> None:
    policy = AdaptiveInterventionPolicy(models=StubModels(fail=True), config=_config())
    read = policy.decide(runtime=_runtime(read_only=True), domain=Domain.CALCULATION_LOGIC)
    write = policy.decide(runtime=_runtime(read_only=False), domain=Domain.API_WORKFLOW)
    assert read.action == Action.CONTINUE and read.fallback_used
    assert write.action == Action.STOP and write.fallback_used


def test_exhausted_budget_stops_before_model_call() -> None:
    policy = AdaptiveInterventionPolicy(models=StubModels(), config=_config())
    runtime = _runtime()
    runtime.budget.tool_calls_used = runtime.budget.max_tool_calls
    decision = policy.decide(runtime=runtime, domain=Domain.CALCULATION_LOGIC)
    assert decision.action == Action.STOP
    assert decision.risk_level == "not_evaluated"
