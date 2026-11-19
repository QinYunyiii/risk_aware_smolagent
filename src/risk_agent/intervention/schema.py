"""Schemas for Day 9 checkpoint scenarios and intervention outcomes."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from risk_agent.benchmark.schema import ToolStep
from risk_agent.faults.schema import FaultType


@dataclass(frozen=True, slots=True)
class InterventionScenario:
    scenario_id: str
    fault_type: FaultType | None
    pending_step: ToolStep
    pending_read_only: bool
    last_failure: Mapping[str, Any] | None
    replan_steps: tuple[ToolStep, ...]

    def to_dict(self) -> dict[str, object]:
        raw = asdict(self)
        raw["fault_type"] = self.fault_type.value if self.fault_type is not None else None
        return raw

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "InterventionScenario":
        pending = dict(raw["pending_step"])
        fault_type = raw.get("fault_type")
        return cls(
            scenario_id=str(raw["scenario_id"]),
            fault_type=FaultType(str(fault_type)) if fault_type is not None else None,
            pending_step=ToolStep(str(pending["tool"]), dict(pending["arguments"])),
            pending_read_only=bool(raw["pending_read_only"]),
            last_failure=(
                dict(raw["last_failure"]) if raw.get("last_failure") is not None else None
            ),
            replan_steps=tuple(
                ToolStep(str(step["tool"]), dict(step["arguments"]))
                for step in raw.get("replan_steps", [])
            ),
        )


@dataclass(frozen=True, slots=True)
class PreparedCheckpoint:
    checkpoint_id: str
    task_id: str
    source_task_run_id: str
    source_day8_run_id: str
    split: str
    scenario: InterventionScenario
    state_hash: str

    def to_dict(self) -> dict[str, object]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "task_id": self.task_id,
            "source_task_run_id": self.source_task_run_id,
            "source_day8_run_id": self.source_day8_run_id,
            "split": self.split,
            "scenario": self.scenario.to_dict(),
            "state_hash": self.state_hash,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PreparedCheckpoint":
        return cls(
            checkpoint_id=str(raw["checkpoint_id"]),
            task_id=str(raw["task_id"]),
            source_task_run_id=str(raw["source_task_run_id"]),
            source_day8_run_id=str(raw["source_day8_run_id"]),
            split=str(raw["split"]),
            scenario=InterventionScenario.from_dict(dict(raw["scenario"])),
            state_hash=str(raw["state_hash"]),
        )
