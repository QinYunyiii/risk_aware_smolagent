"""Explicit caller-supplied metadata for complete Checkpoint v2 snapshots."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("Checkpoint metadata must be finite JSON data") from exc


def _require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")


def _require_sha256(value: str, field: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


class AdapterStateStatus(StrEnum):
    STATEFUL = "stateful"
    EMPTY = "empty"
    NOT_INSTANTIATED = "not_instantiated"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class AdapterStateMetadata:
    """Preserve whether adapter state is empty, absent, unavailable, or populated."""

    status: AdapterStateStatus
    state: Mapping[str, Any] | None
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, AdapterStateStatus):
            raise ValueError("status must be an AdapterStateStatus")
        state = _json_copy(self.state)
        if self.status is AdapterStateStatus.STATEFUL and (
            not isinstance(state, dict) or not state
        ):
            raise ValueError("stateful adapter metadata requires a non-empty state object")
        if self.status is AdapterStateStatus.EMPTY and state != {}:
            raise ValueError("empty adapter metadata requires state={}")
        if self.status in {
            AdapterStateStatus.NOT_INSTANTIATED,
            AdapterStateStatus.UNAVAILABLE,
        } and state is not None:
            raise ValueError(f"{self.status.value} adapter metadata requires state=None")
        if self.status is AdapterStateStatus.UNAVAILABLE:
            _require_text(self.reason or "", "adapter reason")
        elif self.reason is not None:
            raise ValueError("adapter reason is only valid when state is unavailable")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "state": _json_copy(self.state),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class CheckpointIdentityV2:
    """Identity fields that cannot be derived from TaskSpec or AgentStateSnapshot."""

    experiment_id: str
    run_id: str
    task_run_id: str
    model_version: str
    worker_id: str
    prompt_version: str
    tool_version: str
    verifier_version: str
    verifier_content_hash: str
    catalog_hash: str
    split_manifest_hash: str
    controller_type: str
    execution_mode: str
    created_at: str

    def __post_init__(self) -> None:
        for field in (
            "experiment_id",
            "run_id",
            "task_run_id",
            "model_version",
            "worker_id",
            "prompt_version",
            "tool_version",
            "verifier_version",
        ):
            _require_text(getattr(self, field), field)
        for field in ("verifier_content_hash", "catalog_hash", "split_manifest_hash"):
            _require_sha256(getattr(self, field), field)
        if self.controller_type != "qwen3_model":
            raise ValueError("controller_type must be qwen3_model")
        if self.execution_mode not in {"model_backed_smoke", "model_backed_formal"}:
            raise ValueError("Unsupported execution_mode")
        _require_text(self.created_at, "created_at")
        try:
            parsed = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("created_at must be an ISO 8601 date-time") from exc
        if parsed.tzinfo is None:
            raise ValueError("created_at must include a timezone")


@dataclass(frozen=True, slots=True)
class RuntimeCheckpointMetadata:
    max_model_calls: int
    model_calls_used: int
    prompt_tokens_used: int | None
    completion_tokens_used: int | None
    resource_usage: Mapping[str, Any]
    tool_adapter_state: AdapterStateMetadata
    fault_adapter_state: AdapterStateMetadata
    phase: str
    next_operation: str

    def __post_init__(self) -> None:
        if isinstance(self.max_model_calls, bool) or not isinstance(self.max_model_calls, int):
            raise ValueError("max_model_calls must be an integer")
        if isinstance(self.model_calls_used, bool) or not isinstance(self.model_calls_used, int):
            raise ValueError("model_calls_used must be an integer")
        if self.max_model_calls <= 0:
            raise ValueError("max_model_calls must be positive")
        if self.model_calls_used <= 0:
            raise ValueError("model_calls_used must be positive")
        if self.model_calls_used >= self.max_model_calls:
            raise ValueError("No model call budget remains for next model_generation")
        for field in ("prompt_tokens_used", "completion_tokens_used"):
            value = getattr(self, field)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{field} must be non-negative or null")
        if not isinstance(_json_copy(self.resource_usage), dict):
            raise ValueError("resource_usage must be an object")
        if self.phase != "after_tool_observation_before_model_generation":
            raise ValueError("Invalid checkpoint execution phase")
        if self.next_operation != "model_generation":
            raise ValueError("Invalid checkpoint next operation")

    def resource_usage_dict(self) -> dict[str, Any]:
        value = _json_copy(self.resource_usage)
        if not isinstance(value, dict):
            raise ValueError("resource_usage must be an object")
        return value


@dataclass(frozen=True, slots=True)
class EnvironmentCheckpointMetadata:
    completed_side_effects: Any
    latest_completed_operation: Any

    def __post_init__(self) -> None:
        _json_copy(self.completed_side_effects)
        _json_copy(self.latest_completed_operation)

    def side_effects_dict(self) -> dict[str, Any]:
        return {"completed_side_effects": _json_copy(self.completed_side_effects)}

    def latest_operation_value(self) -> Any:
        return _json_copy(self.latest_completed_operation)


@dataclass(frozen=True, slots=True)
class CaptureInvariants:
    observation_in_memory: bool
    next_model_request_not_started: bool
    non_terminal: bool
    remaining_step_available: bool
    snapshot_synchronous: bool
    verifier_not_run: bool

    def __post_init__(self) -> None:
        if any(value is not True for value in asdict(self).values()):
            raise ValueError("All frozen capture invariants must be true")

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)


__all__ = [
    "AdapterStateMetadata",
    "AdapterStateStatus",
    "CaptureInvariants",
    "CheckpointIdentityV2",
    "EnvironmentCheckpointMetadata",
    "RuntimeCheckpointMetadata",
]
