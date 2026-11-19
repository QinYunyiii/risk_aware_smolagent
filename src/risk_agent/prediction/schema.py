"""Schemas for decision-time risk examples."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class RiskExample:
    checkpoint_id: str
    task_id: str
    template_id: str
    split: str
    label_failure: int
    features: Mapping[str, float]

    def __post_init__(self) -> None:
        if self.label_failure not in {0, 1}:
            raise ValueError("Risk label must be binary")
        if not self.features:
            raise ValueError("Risk example requires decision-time features")

    def to_dict(self) -> dict[str, object]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "task_id": self.task_id,
            "template_id": self.template_id,
            "split": self.split,
            "label_failure": self.label_failure,
            "features": dict(self.features),
        }
