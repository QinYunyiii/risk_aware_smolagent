"""Safety-first adaptive policy integrating risk, utility, limits, and budget."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from risk_agent.benchmark.schema import Domain
from risk_agent.branching import BranchApplicabilityEvaluator
from risk_agent.faults.schema import Action
from risk_agent.monitoring import RuntimeState
from risk_agent.prediction import extract_risk_features

from .schema import ActionScore, PolicyDecision


_CANDIDATE_ACTIONS = (Action.CONTINUE, Action.RETRY, Action.REPLAN)


class AdaptiveInterventionPolicy:
    def __init__(self, *, models: Any, config: Mapping[str, Any]) -> None:
        self.models = models
        self.low_risk_threshold = float(config["low_risk_threshold"])
        self.minimum_utility_gain = float(config["minimum_utility_gain"])
        self.max_retry_count = int(config["max_retry_count"])
        self.max_replan_count = int(config["max_replan_count"])
        self.max_same_action_streak = int(config["max_same_action_streak"])
        self.fallback_read_only_continue = bool(config["fallback_read_only_continue"])
        if not 0.0 <= self.low_risk_threshold <= 1.0:
            raise ValueError("low_risk_threshold must be in [0,1]")
        if self.minimum_utility_gain < 0:
            raise ValueError("minimum_utility_gain must be non-negative")
        if min(self.max_retry_count, self.max_replan_count, self.max_same_action_streak) < 1:
            raise ValueError("Intervention and loop limits must be positive")
        self.applicability = BranchApplicabilityEvaluator()

    @staticmethod
    def _remaining_budget(runtime: RuntimeState) -> dict[str, float]:
        return {
            "tokens": float(runtime.budget.max_tokens - runtime.budget.tokens_used),
            "tool_calls": float(
                runtime.budget.max_tool_calls - runtime.budget.tool_calls_used
            ),
            "latency_seconds": float(
                runtime.budget.max_latency_seconds - runtime.budget.latency_seconds_used
            ),
        }

    def _initial_exclusions(self, runtime: RuntimeState) -> dict[str, str]:
        excluded: dict[str, str] = {}
        counts = runtime.tool_state.get("intervention_counts", {})
        count_mapping = counts if isinstance(counts, Mapping) else {}
        history = runtime.tool_state.get("policy_action_history", [])
        action_history = [str(value) for value in history] if isinstance(history, list) else []
        for action in _CANDIDATE_ACTIONS:
            decision = self.applicability.evaluate(action, runtime)
            if not decision.applicable:
                excluded[action.value] = decision.reason
                continue
            if action == Action.RETRY and int(count_mapping.get(Action.RETRY.value, 0)) >= self.max_retry_count:
                excluded[action.value] = "Retry intervention limit reached"
                continue
            if action == Action.REPLAN and int(count_mapping.get(Action.REPLAN.value, 0)) >= self.max_replan_count:
                excluded[action.value] = "Replan intervention limit reached"
                continue
            if action in {Action.RETRY, Action.REPLAN} and len(action_history) >= self.max_same_action_streak:
                recent = action_history[-self.max_same_action_streak :]
                if all(item == action.value for item in recent):
                    excluded[action.value] = f"{action.value} loop streak limit reached"
        return excluded

    @staticmethod
    def _fits_budget(costs: Mapping[str, float], remaining: Mapping[str, float]) -> bool:
        required_tokens = math.ceil(float(costs["tokens"]))
        required_tools = max(1, math.ceil(float(costs["tool_calls"])))
        required_latency = float(costs["latency_seconds"])
        return (
            required_tokens <= remaining["tokens"]
            and required_tools <= remaining["tool_calls"]
            and required_latency <= remaining["latency_seconds"]
        )

    def _fallback(
        self,
        *,
        runtime: RuntimeState,
        remaining: Mapping[str, float],
        excluded: Mapping[str, str],
        error: Exception,
    ) -> PolicyDecision:
        pending = runtime.tool_state.get("pending_operation")
        pending_mapping = pending if isinstance(pending, Mapping) else {}
        continue_allowed = Action.CONTINUE.value not in excluded
        minimal_budget = remaining["tool_calls"] >= 1 and remaining["latency_seconds"] > 0
        if (
            self.fallback_read_only_continue
            and continue_allowed
            and bool(pending_mapping.get("read_only", False))
            and minimal_budget
        ):
            action = Action.CONTINUE
            reason = "Model failure fallback to a read-only Continue"
        else:
            action = Action.STOP
            reason = "Model failure fallback to Stop"
        return PolicyDecision(
            action=action,
            risk_probability=None,
            risk_level="unknown",
            reason=reason,
            fallback_used=True,
            model_error_type=type(error).__name__,
            action_scores={},
            excluded_actions=dict(excluded),
            remaining_budget=dict(remaining),
        )

    def decide(self, *, runtime: RuntimeState, domain: Domain) -> PolicyDecision:
        remaining = self._remaining_budget(runtime)
        if runtime.budget.exhausted or any(value < 0 for value in remaining.values()):
            return PolicyDecision(
                action=Action.STOP,
                risk_probability=None,
                risk_level="not_evaluated",
                reason="Execution budget exhausted",
                fallback_used=False,
                model_error_type=None,
                action_scores={},
                excluded_actions={action.value: "Budget exhausted" for action in _CANDIDATE_ACTIONS},
                remaining_budget=remaining,
            )
        excluded = self._initial_exclusions(runtime)
        features = extract_risk_features(runtime, domain)
        try:
            risk = self.models.predict_risk(features)
        except Exception as error:
            return self._fallback(
                runtime=runtime, remaining=remaining, excluded=excluded, error=error
            )
        risk_level = "low" if risk < self.low_risk_threshold else "high"

        if risk_level == "low":
            if Action.CONTINUE.value in excluded:
                return PolicyDecision(
                    action=Action.STOP,
                    risk_probability=risk,
                    risk_level=risk_level,
                    reason="Low risk but Continue is not safely applicable",
                    fallback_used=False,
                    model_error_type=None,
                    action_scores={},
                    excluded_actions=excluded,
                    remaining_budget=remaining,
                )
            try:
                probability, costs, weighted = self.models.predict_action(
                    Action.CONTINUE.value, features
                )
                if not self._fits_budget(costs, remaining):
                    excluded[Action.CONTINUE.value] = "Predicted Continue cost exceeds budget"
                    return PolicyDecision(
                        action=Action.STOP,
                        risk_probability=risk,
                        risk_level=risk_level,
                        reason="Low-risk Continue exceeds remaining budget",
                        fallback_used=False,
                        model_error_type=None,
                        action_scores={},
                        excluded_actions=excluded,
                        remaining_budget=remaining,
                    )
                score = ActionScore(
                    action=Action.CONTINUE,
                    success_probability=probability,
                    cost_components=costs,
                    weighted_cost=weighted,
                    net_value=probability - self.models.utility_lambda * weighted,
                    relative_utility=0.0,
                )
            except Exception as error:
                return self._fallback(
                    runtime=runtime, remaining=remaining, excluded=excluded, error=error
                )
            return PolicyDecision(
                action=Action.CONTINUE,
                risk_probability=risk,
                risk_level=risk_level,
                reason="Risk below threshold; continue current plan",
                fallback_used=False,
                model_error_type=None,
                action_scores={Action.CONTINUE.value: score},
                excluded_actions=excluded,
                remaining_budget=remaining,
            )

        raw_scores: dict[str, tuple[float, dict[str, float], float, float]] = {}
        for action in _CANDIDATE_ACTIONS:
            if action.value in excluded:
                continue
            try:
                probability, costs, weighted = self.models.predict_action(action.value, features)
            except Exception as error:
                if action == Action.CONTINUE:
                    return self._fallback(
                        runtime=runtime, remaining=remaining, excluded=excluded, error=error
                    )
                excluded[action.value] = f"Model error: {type(error).__name__}"
                continue
            if not self._fits_budget(costs, remaining):
                excluded[action.value] = "Predicted action cost exceeds budget"
                continue
            net_value = probability - self.models.utility_lambda * weighted
            raw_scores[action.value] = (probability, costs, weighted, net_value)
        if Action.CONTINUE.value not in raw_scores:
            return PolicyDecision(
                action=Action.STOP,
                risk_probability=risk,
                risk_level=risk_level,
                reason="No budget-feasible Continue baseline",
                fallback_used=False,
                model_error_type=None,
                action_scores={},
                excluded_actions=excluded,
                remaining_budget=remaining,
            )
        continue_value = raw_scores[Action.CONTINUE.value][3]
        scores = {
            action: ActionScore(
                action=Action(action),
                success_probability=values[0],
                cost_components=values[1],
                weighted_cost=values[2],
                net_value=values[3],
                relative_utility=values[3] - continue_value,
            )
            for action, values in raw_scores.items()
        }
        interventions = [
            score for action, score in scores.items() if action != Action.CONTINUE.value
        ]
        best = max(interventions, key=lambda score: score.relative_utility, default=None)
        if best is not None and best.relative_utility > self.minimum_utility_gain:
            selected = best.action
            reason = "High risk; selected the safe action with highest sufficient net utility"
        else:
            selected = Action.CONTINUE
            reason = "High risk but no safe intervention exceeds the utility threshold"
        return PolicyDecision(
            action=selected,
            risk_probability=risk,
            risk_level=risk_level,
            reason=reason,
            fallback_used=False,
            model_error_type=None,
            action_scores=scores,
            excluded_actions=excluded,
            remaining_budget=remaining,
        )

    @staticmethod
    def record_action(runtime: RuntimeState, action: Action) -> None:
        history = runtime.tool_state.setdefault("policy_action_history", [])
        if not isinstance(history, list):
            raise ValueError("policy_action_history must be a list")
        history.append(action.value)
        counts = runtime.tool_state.setdefault("intervention_counts", {})
        if not isinstance(counts, dict):
            raise ValueError("intervention_counts must be a mapping")
        if action in {Action.RETRY, Action.REPLAN}:
            counts[action.value] = int(counts.get(action.value, 0)) + 1
