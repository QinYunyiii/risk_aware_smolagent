from __future__ import annotations

from typing import Mapping

from risk_agent.benchmark.schema import Domain
from risk_agent.evaluation import build_experiment_policies
from risk_agent.faults.schema import Action
from risk_agent.monitoring import BudgetState, RuntimeState
from scripts.run_day13_experiment import _policy_metrics, _stratified_metrics_complete


class StubModels:
    utility_lambda = 0.1

    def __init__(self, risk: float) -> None:
        self.risk = risk

    def predict_risk(self, features: Mapping[str, float]) -> float:
        return self.risk

    def predict_action(
        self, action: str, features: Mapping[str, float]
    ) -> tuple[float, dict[str, float], float]:
        probability = {"CONTINUE": 0.2, "RETRY": 0.8, "REPLAN": 0.9}[action]
        costs = {
            "tokens": 0.0,
            "tool_calls": 2.0 if action == "REPLAN" else 1.0,
            "latency_seconds": 0.0,
        }
        return probability, costs, costs["tool_calls"]


def _config() -> dict:
    return {
        "low_risk_threshold": 0.5,
        "minimum_utility_gain": 0.05,
        "max_retry_count": 1,
        "max_replan_count": 1,
        "max_same_action_streak": 1,
        "fallback_read_only_continue": True,
    }


def _runtime(fault_type: str | None) -> RuntimeState:
    state = {
        "pending_operation": {
            "tool_name": "calculator",
            "arguments": {"expression": "1 + 1"},
            "read_only": True,
        },
        "replan_steps": [
            {"tool_name": "calculator", "arguments": {"expression": "1 + 1"}}
        ],
    }
    if fault_type is not None:
        state["last_failure"] = {
            "fault_type": fault_type,
            "commit_status": "not_started" if fault_type != "F4_STALE_OBSERVATION" else "not_applicable",
            "response_received": fault_type not in {"F1_TIMEOUT", "F2_API_503"},
            "call_index": 1,
        }
    return RuntimeState(
        seed=1,
        tool_state=state,
        budget=BudgetState(max_tokens=1000, max_tool_calls=10, max_latency_seconds=10),
    )


def test_fixed_policy_set_is_complete() -> None:
    policies = build_experiment_policies(models=StubModels(0.9), policy_config=_config())
    assert tuple(policies) == (
        "vanilla",
        "always_retry",
        "rule_based",
        "risk_based",
        "risk_plus_utility",
        "full",
        "without_risk",
        "without_utility",
        "without_cost",
    )


def test_baselines_follow_fixed_semantics_and_safety() -> None:
    policies = build_experiment_policies(models=StubModels(0.9), policy_config=_config())
    transient = _runtime("F2_API_503")
    invalid = _runtime("F3_INVALID_ARGUMENT")
    assert policies["vanilla"].decide(
        runtime=invalid, domain=Domain.CALCULATION_LOGIC
    ).action == Action.CONTINUE
    assert policies["always_retry"].decide(
        runtime=transient, domain=Domain.CALCULATION_LOGIC
    ).action == Action.RETRY
    unsafe_retry = policies["always_retry"].decide(
        runtime=invalid, domain=Domain.CALCULATION_LOGIC
    )
    assert unsafe_retry.action == Action.CONTINUE
    assert "RETRY" in unsafe_retry.excluded_actions
    assert policies["rule_based"].decide(
        runtime=invalid, domain=Domain.CALCULATION_LOGIC
    ).action == Action.REPLAN
    assert policies["risk_based"].decide(
        runtime=invalid, domain=Domain.CALCULATION_LOGIC
    ).action == Action.REPLAN


def test_ablations_change_only_the_requested_signal() -> None:
    policies = build_experiment_policies(models=StubModels(0.1), policy_config=_config())
    invalid = _runtime("F3_INVALID_ARGUMENT")
    assert policies["full"].decide(
        runtime=invalid, domain=Domain.CALCULATION_LOGIC
    ).action == Action.CONTINUE
    assert policies["without_risk"].decide(
        runtime=invalid, domain=Domain.CALCULATION_LOGIC
    ).action == Action.REPLAN
    assert policies["without_utility"].decide(
        runtime=invalid, domain=Domain.CALCULATION_LOGIC
    ).action == Action.CONTINUE
    assert policies["without_cost"].decide(
        runtime=invalid, domain=Domain.CALCULATION_LOGIC
    ).action == Action.CONTINUE


def test_metrics_include_complete_domain_and_fault_strata() -> None:
    record = {
        "domain": "calculation_logic",
        "fault_type": "F3_INVALID_ARGUMENT",
        "source_continue_success": False,
        "decision": {
            "action": "REPLAN",
            "fallback_used": False,
        },
        "execution": {
            "success": True,
            "validation_passed": True,
            "extra_tokens": 0,
            "extra_tool_calls": 1,
            "extra_latency_seconds": 0.01,
        },
        "regret": 0.0,
    }
    metrics = {"full": _policy_metrics([record])}
    assert _stratified_metrics_complete(metrics)
    for grouping in ("by_domain", "by_fault_type"):
        subset = next(iter(metrics["full"][grouping].values()))
        assert subset["recovery_rate"] == 1.0
        assert subset["intervention_rate"] == 1.0
        assert subset["unnecessary_intervention_rate_among_interventions"] == 0.0
        assert subset["std_latency_seconds"] == 0.0
