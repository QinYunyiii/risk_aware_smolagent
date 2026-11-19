"""Schema for applicable and inapplicable intervention examples."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class UtilityExample:
    checkpoint_id: str
    task_id: str
    template_id: str
    split: str
    action: str
    action_applicable: bool
    applicability_reason: str
    outcome_success: int | None
    extra_tokens: float | None
    extra_tool_calls: float | None
    extra_latency_seconds: float | None
    features: Mapping[str, float]

    def __post_init__(self) -> None:
        if self.action not in {"CONTINUE", "RETRY", "REPLAN"}:
            raise ValueError(f"Unsupported utility action: {self.action}")
        outcomes = (
            self.outcome_success,
            self.extra_tokens,
            self.extra_tool_calls,
            self.extra_latency_seconds,
        )
        if self.action_applicable:
            if self.outcome_success not in {0, 1} or any(value is None for value in outcomes[1:]):
                raise ValueError("Applicable utility examples require outcome and cost labels")
            if any(float(value) < 0 for value in outcomes[1:] if value is not None):
                raise ValueError("Observed costs must be non-negative")
        elif any(value is not None for value in outcomes):
            raise ValueError("Inapplicable actions must not carry outcome or cost labels")

    def to_dict(self) -> dict[str, object]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "task_id": self.task_id,
            "template_id": self.template_id,
            "split": self.split,
            "action": self.action,
            "action_applicable": self.action_applicable,
            "applicability_reason": self.applicability_reason,
            "outcome_success": self.outcome_success,
            "extra_tokens": self.extra_tokens,
            "extra_tool_calls": self.extra_tool_calls,
            "extra_latency_seconds": self.extra_latency_seconds,
            "features": dict(self.features),
        }
