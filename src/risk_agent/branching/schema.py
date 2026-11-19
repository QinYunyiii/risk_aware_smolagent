"""Typed Day 6 branch execution results."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from risk_agent.faults.schema import Action


@dataclass(frozen=True, slots=True)
class ApplicabilityDecision:
    action: Action
    applicable: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BranchResult:
    checkpoint_id: str
    branch_id: str
    action: Action
    action_applicable: bool
    applicability_reason: str
    initial_state_hash: str
    restored_state_hash: str
    initial_environment_hash: str
    final_state_hash: str
    final_environment_hash: str
    success: bool
    validation_passed: bool
    validation_checks: Mapping[str, bool]
    result: Any
    extra_tokens: int
    extra_tool_calls: int
    extra_latency_seconds: float
    termination_reason: str
    workspace_path: Path
    database_path: Path
    trajectory_path: Path

    def to_dict(self) -> dict[str, object]:
        raw = asdict(self)
        raw["action"] = self.action.value
        for key in ("workspace_path", "database_path", "trajectory_path"):
            raw[key] = str(raw[key])
        return raw


@dataclass(frozen=True, slots=True)
class BranchSetResult:
    checkpoint_id: str
    checkpoint_state_hash: str
    branches: tuple[BranchResult, ...]
    checks: Mapping[str, bool]

    @property
    def passed(self) -> bool:
        return all(self.checks.values()) and all(
            branch.success and branch.validation_passed for branch in self.branches
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "checkpoint_state_hash": self.checkpoint_state_hash,
            "branches": [branch.to_dict() for branch in self.branches],
            "checks": dict(self.checks),
            "passed": self.passed,
        }
