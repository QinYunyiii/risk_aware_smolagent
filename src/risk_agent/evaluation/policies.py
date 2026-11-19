"""Safety-constrained baseline and ablation policies for Day 13."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from risk_agent.benchmark.schema import Domain
from risk_agent.branching import BranchApplicabilityEvaluator
from risk_agent.controller import AdaptiveInterventionPolicy, PolicyDecision
from risk_agent.faults.schema import Action
from risk_agent.monitoring import RuntimeState
from risk_agent.prediction import extract_risk_features


class _SafetyPolicy:
    def __init__(self) -> None:
        self.applicability = BranchApplicabilityEvaluator()

    @staticmethod
    def _remaining(runtime: RuntimeState) -> dict[str, float]:
        return {
            "tokens": float(runtime.budget.max_tokens - runtime.budget.tokens_used),
            "tool_calls": float(runtime.budget.max_tool_calls - runtime.budget.tool_calls_used),
            "latency_seconds": float(
                runtime.budget.max_latency_seconds - runtime.budget.latency_seconds_used
            ),
        }

    def _excluded(self, runtime: RuntimeState) -> dict[str, str]:
        return {
            action.value: decision.reason
            for action in (Action.CONTINUE, Action.RETRY, Action.REPLAN)
            if not (
                decision := self.applicability.evaluate(action, runtime)
            ).applicable
        }

    def _select(
        self,
        *,
        runtime: RuntimeState,
        preferred: Action,
        reason: str,
        risk_probability: float | None = None,
        risk_level: str = "not_used",
    ) -> PolicyDecision:
        remaining = self._remaining(runtime)
        excluded = self._excluded(runtime)
        if runtime.budget.exhausted:
            action = Action.STOP
            reason = "Execution budget exhausted"
        elif preferred.value not in excluded:
            action = preferred
        elif Action.CONTINUE.value not in excluded:
            action = Action.CONTINUE
            reason = f"{reason}; preferred action unavailable, Continue"
        else:
            action = Action.STOP
            reason = f"{reason}; no safe Continue"
        return PolicyDecision(
            action=action,
            risk_probability=risk_probability,
            risk_level=risk_level,
            reason=reason,
            fallback_used=False,
            model_error_type=None,
            action_scores={},
            excluded_actions=excluded,
            remaining_budget=remaining,
        )


class AlwaysContinuePolicy(_SafetyPolicy):
    def decide(self, *, runtime: RuntimeState, domain: Domain) -> PolicyDecision:
        return self._select(
            runtime=runtime,
            preferred=Action.CONTINUE,
            reason="Vanilla policy always follows the current plan",
        )


class AlwaysSafeRetryPolicy(_SafetyPolicy):
    def decide(self, *, runtime: RuntimeState, domain: Domain) -> PolicyDecision:
        return self._select(
            runtime=runtime,
            preferred=Action.RETRY,
            reason="Always-Retry baseline retries only when safety rules allow it",
        )


class RuleBasedPolicy(_SafetyPolicy):
    def decide(self, *, runtime: RuntimeState, domain: Domain) -> PolicyDecision:
        failure = runtime.tool_state.get("last_failure")
        failure_mapping = failure if isinstance(failure, Mapping) else {}
        fault_type = str(failure_mapping.get("fault_type", ""))
        if fault_type in {"F1_TIMEOUT", "F2_API_503"}:
            preferred = Action.RETRY
        elif fault_type in {
            "F3_INVALID_ARGUMENT",
            "F4_STALE_OBSERVATION",
            "F5_REPEATED_ACTION",
            "F6_PARTIAL_FAILURE",
        }:
            preferred = Action.REPLAN
        else:
            preferred = Action.CONTINUE
        return self._select(
            runtime=runtime,
            preferred=preferred,
            reason=f"Rule-based mapping for {fault_type or 'no fault'}",
        )


class RiskOnlyPolicy(_SafetyPolicy):
    def __init__(self, *, models: Any, threshold: float) -> None:
        super().__init__()
        self.models = models
        self.threshold = float(threshold)

    def decide(self, *, runtime: RuntimeState, domain: Domain) -> PolicyDecision:
        try:
            risk = self.models.predict_risk(extract_risk_features(runtime, domain))
        except Exception as error:
            pending = runtime.tool_state.get("pending_operation")
            pending_mapping = pending if isinstance(pending, Mapping) else {}
            preferred = (
                Action.CONTINUE if bool(pending_mapping.get("read_only", False)) else Action.STOP
            )
            decision = self._select(
                runtime=runtime,
                preferred=preferred,
                reason="Risk model failure safety fallback",
                risk_level="unknown",
            )
            return PolicyDecision(
                action=decision.action,
                risk_probability=None,
                risk_level="unknown",
                reason=decision.reason,
                fallback_used=True,
                model_error_type=type(error).__name__,
                action_scores={},
                excluded_actions=decision.excluded_actions,
                remaining_budget=decision.remaining_budget,
            )
        if risk < self.threshold:
            preferred = Action.CONTINUE
            risk_level = "low"
        else:
            risk_level = "high"
            excluded = self._excluded(runtime)
            if Action.REPLAN.value not in excluded:
                preferred = Action.REPLAN
            elif Action.RETRY.value not in excluded:
                preferred = Action.RETRY
            else:
                preferred = Action.CONTINUE
        return self._select(
            runtime=runtime,
            preferred=preferred,
            reason="Risk-only threshold and safe Replan-then-Retry priority",
            risk_probability=risk,
            risk_level=risk_level,
        )


class _RiskOverrideBundle:
    def __init__(self, delegate: Any, risk: float) -> None:
        self.delegate = delegate
        self.risk = float(risk)
        self.utility_lambda = delegate.utility_lambda

    def predict_risk(self, features: Mapping[str, float]) -> float:
        return self.risk

    def predict_action(self, action: str, features: Mapping[str, float]):
        return self.delegate.predict_action(action, features)


class _ZeroCostBundle:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.utility_lambda = delegate.utility_lambda

    def predict_risk(self, features: Mapping[str, float]) -> float:
        return self.delegate.predict_risk(features)

    def predict_action(self, action: str, features: Mapping[str, float]):
        probability, costs, _ = self.delegate.predict_action(action, features)
        return probability, costs, 0.0


def build_experiment_policies(
    *, models: Any, policy_config: Mapping[str, Any]
) -> dict[str, Any]:
    """Construct the five named baselines and four requested ablations."""

    risk_threshold = float(policy_config["low_risk_threshold"])
    full = lambda bundle: AdaptiveInterventionPolicy(models=bundle, config=policy_config)
    return {
        "vanilla": AlwaysContinuePolicy(),
        "always_retry": AlwaysSafeRetryPolicy(),
        "rule_based": RuleBasedPolicy(),
        "risk_based": RiskOnlyPolicy(models=models, threshold=risk_threshold),
        "risk_plus_utility": full(models),
        "full": full(models),
        "without_risk": full(_RiskOverrideBundle(models, risk=1.0)),
        "without_utility": RiskOnlyPolicy(models=models, threshold=risk_threshold),
        "without_cost": full(_ZeroCostBundle(models)),
    }
