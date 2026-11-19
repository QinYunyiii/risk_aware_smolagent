"""Typed fault, execution-state, and intervention schemas."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Mapping

from risk_agent.benchmark.schema import validate_identifier


class FaultType(StrEnum):
    F0_NO_FAULT = "F0_NO_FAULT"
    F1_TIMEOUT = "F1_TIMEOUT"
    F2_API_503 = "F2_API_503"
    F3_INVALID_ARGUMENT = "F3_INVALID_ARGUMENT"
    F4_STALE_OBSERVATION = "F4_STALE_OBSERVATION"
    F5_REPEATED_ACTION = "F5_REPEATED_ACTION"
    F6_PARTIAL_FAILURE = "F6_PARTIAL_FAILURE"


class CommitStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    NOT_STARTED = "not_started"
    NOT_COMMITTED = "not_committed"
    COMMITTED = "committed"
    UNKNOWN = "unknown"


class Action(StrEnum):
    CONTINUE = "CONTINUE"
    RETRY = "RETRY"
    REPLAN = "REPLAN"
    STOP = "STOP"


@dataclass(frozen=True, slots=True)
class ToolOperation:
    tool_name: str
    read_only: bool
    idempotency_key_present: bool = False


@dataclass(frozen=True, slots=True)
class FaultConfig:
    fault_id: str
    fault_type: FaultType
    seed: int
    target_tool: str
    call_indices: tuple[int, ...]
    probability: float
    parameters: Mapping[str, Any]

    def __post_init__(self) -> None:
        validate_identifier(self.fault_id, "fault_id")
        if self.target_tool != "*":
            validate_identifier(self.target_tool, "target_tool")
        if self.seed < 0:
            raise ValueError("fault seed must be non-negative")
        if any(index < 1 for index in self.call_indices):
            raise ValueError("call_indices must be positive")
        if len(set(self.call_indices)) != len(self.call_indices):
            raise ValueError("call_indices must be unique")
        if not 0.0 <= self.probability <= 1.0:
            raise ValueError("probability must be between 0 and 1")
        if self.call_indices and self.probability != 0.0:
            raise ValueError("Use call_indices or probability, not both")
        if self.fault_type == FaultType.F0_NO_FAULT and (
            self.call_indices or self.probability != 0.0
        ):
            raise ValueError("F0 must not define a trigger")
        if self.fault_type == FaultType.F1_TIMEOUT and "commit_status" in self.parameters:
            CommitStatus(str(self.parameters["commit_status"]))
        if self.fault_type == FaultType.F4_STALE_OBSERVATION and "stale_result" not in self.parameters:
            raise ValueError("F4 requires stale_result")
        if self.fault_type == FaultType.F5_REPEATED_ACTION and (
            not self.call_indices or min(self.call_indices) < 2
        ):
            raise ValueError("F5 requires an explicit call index of 2 or greater")
        if self.fault_type == FaultType.F6_PARTIAL_FAILURE and not isinstance(
            self.parameters.get("commit", True), bool
        ):
            raise ValueError("F6 commit must be boolean")


@dataclass(frozen=True, slots=True)
class FaultEvent:
    event_id: str
    fault_id: str
    fault_type: FaultType
    seed: int
    tool_name: str
    call_index: int
    triggered: bool
    phase: str
    commit_status: CommitStatus
    response_received: bool
    error_type: str | None
    arguments_hash: str
    timestamp: str

    def reproducibility_signature(self) -> tuple[object, ...]:
        return (
            self.fault_id,
            self.fault_type.value,
            self.seed,
            self.tool_name,
            self.call_index,
            self.triggered,
            self.phase,
            self.commit_status.value,
            self.response_received,
            self.error_type,
            self.arguments_hash,
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SafetyDecision:
    requested_action: Action
    action_applicable: bool
    retry_allowed: bool
    recommended_action: Action
    requires_state_check: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
