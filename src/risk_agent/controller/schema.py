"""Structured policy scores and decisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

from risk_agent.faults.schema import Action


@dataclass(frozen=True, slots=True)
class ActionScore:
    action: Action
    success_probability: float
    cost_components: Mapping[str, float]
    weighted_cost: float
    net_value: float
    relative_utility: float

    def to_dict(self) -> dict[str, object]:
        raw = asdict(self)
        raw["action"] = self.action.value
        return raw


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    action: Action
    risk_probability: float | None
    risk_level: str
    reason: str
    fallback_used: bool
    model_error_type: str | None
    action_scores: Mapping[str, ActionScore]
    excluded_actions: Mapping[str, str]
    remaining_budget: Mapping[str, float]

    def to_dict(self) -> dict[str, object]:
        return {
            "action": self.action.value,
            "risk_probability": self.risk_probability,
            "risk_level": self.risk_level,
            "reason": self.reason,
            "fallback_used": self.fallback_used,
            "model_error_type": self.model_error_type,
            "action_scores": {
                action: score.to_dict() for action, score in self.action_scores.items()
            },
            "excluded_actions": dict(self.excluded_actions),
            "remaining_budget": dict(self.remaining_budget),
        }
