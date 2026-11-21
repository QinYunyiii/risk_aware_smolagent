"""Serializable runtime state required by Day 5 checkpoints."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


def _validate_json_value(value: Any, *, path: str = "$") -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"Non-finite float at {path}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"Non-string JSON object key at {path}: {key!r}")
            _validate_json_value(item, path=f"{path}.{key}")
        return
    raise TypeError(f"Unsupported JSON value at {path}: {type(value).__name__}")


def _json_copy(value: Any) -> Any:
    """Return a lossless JSON copy or fail instead of coercing state."""

    _validate_json_value(value)
    return json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
    )


def _require_int(value: Any, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field} must be an integer >= {minimum}")
    return value


def _require_number(value: Any, field: str, *, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number >= {minimum}")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < minimum:
        raise ValueError(f"{field} must be a finite number >= {minimum}")
    return normalized


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


@dataclass(slots=True)
class BudgetState:
    max_tokens: int
    max_tool_calls: int
    max_latency_seconds: float
    tokens_used: int = 0
    tool_calls_used: int = 0
    latency_seconds_used: float = 0.0

    def __post_init__(self) -> None:
        _require_int(self.max_tokens, "max_tokens", minimum=1)
        _require_int(self.max_tool_calls, "max_tool_calls", minimum=1)
        _require_number(self.max_latency_seconds, "max_latency_seconds", minimum=0)
        if self.max_latency_seconds <= 0:
            raise ValueError("Budget limits must be positive")
        _require_int(self.tokens_used, "tokens_used")
        _require_int(self.tool_calls_used, "tool_calls_used")
        _require_number(self.latency_seconds_used, "latency_seconds_used")

    def record(self, *, tokens: int, tool_calls: int, latency_seconds: float) -> None:
        _require_int(tokens, "tokens")
        _require_int(tool_calls, "tool_calls")
        _require_number(latency_seconds, "latency_seconds")
        self.tokens_used += tokens
        self.tool_calls_used += tool_calls
        self.latency_seconds_used += latency_seconds

    @property
    def exhausted(self) -> bool:
        return (
            self.tokens_used >= self.max_tokens
            or self.tool_calls_used >= self.max_tool_calls
            or self.latency_seconds_used >= self.max_latency_seconds
        )

    def to_dict(self) -> dict[str, object]:
        return _json_copy(asdict(self))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "BudgetState":
        value = _json_copy(dict(raw))
        return cls(
            max_tokens=_require_int(value["max_tokens"], "max_tokens", minimum=1),
            max_tool_calls=_require_int(value["max_tool_calls"], "max_tool_calls", minimum=1),
            max_latency_seconds=_require_number(
                value["max_latency_seconds"], "max_latency_seconds"
            ),
            tokens_used=_require_int(value.get("tokens_used", 0), "tokens_used"),
            tool_calls_used=_require_int(value.get("tool_calls_used", 0), "tool_calls_used"),
            latency_seconds_used=_require_number(
                value.get("latency_seconds_used", 0.0), "latency_seconds_used"
            ),
        )


@dataclass(frozen=True, slots=True)
class ToolHistoryEntry:
    step_id: str
    tool_name: str
    tool_arguments: Mapping[str, Any]
    tool_result: Any
    error_type: str | None
    timestamp: str
    token_usage: Mapping[str, int]
    latency_seconds: float
    worker_id: str
    environment_id: str
    checkpoint_id: str | None

    def to_dict(self) -> dict[str, object]:
        return _json_copy(asdict(self))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ToolHistoryEntry":
        value = _json_copy(dict(raw))
        arguments = value.get("tool_arguments", {})
        token_usage = value.get("token_usage", {})
        if not isinstance(arguments, dict) or not isinstance(token_usage, dict):
            raise ValueError("Tool history arguments and token usage must be objects")
        error_type = value.get("error_type")
        checkpoint_id = value.get("checkpoint_id")
        if error_type is not None and not isinstance(error_type, str):
            raise ValueError("error_type must be a string or null")
        if checkpoint_id is not None and not isinstance(checkpoint_id, str):
            raise ValueError("checkpoint_id must be a string or null")
        return cls(
            step_id=_require_text(value["step_id"], "step_id"),
            tool_name=_require_text(value["tool_name"], "tool_name"),
            tool_arguments=arguments,
            tool_result=value.get("tool_result"),
            error_type=error_type,
            timestamp=_require_text(value["timestamp"], "timestamp"),
            token_usage={
                key: _require_int(item, f"token_usage.{key}")
                for key, item in token_usage.items()
            },
            latency_seconds=_require_number(value["latency_seconds"], "latency_seconds"),
            worker_id=_require_text(value["worker_id"], "worker_id"),
            environment_id=_require_text(value["environment_id"], "environment_id"),
            checkpoint_id=checkpoint_id,
        )


@dataclass(slots=True)
class RuntimeState:
    seed: int
    agent_memory: list[dict[str, Any]] = field(default_factory=list)
    tool_history: list[ToolHistoryEntry] = field(default_factory=list)
    tool_state: dict[str, Any] = field(default_factory=dict)
    budget: BudgetState = field(
        default_factory=lambda: BudgetState(
            max_tokens=100_000,
            max_tool_calls=100,
            max_latency_seconds=3_600.0,
        )
    )

    def __post_init__(self) -> None:
        _require_int(self.seed, "seed")
        if not isinstance(self.agent_memory, list) or not isinstance(self.tool_history, list):
            raise ValueError("Runtime memory and tool history must be lists")
        if not isinstance(self.tool_state, dict):
            raise ValueError("Runtime tool_state must be an object")
        if not all(isinstance(entry, ToolHistoryEntry) for entry in self.tool_history):
            raise ValueError("Runtime tool_history must contain ToolHistoryEntry values")

    def to_dict(self) -> dict[str, object]:
        return _json_copy({
            "seed": self.seed,
            "agent_memory": self.agent_memory,
            "tool_history": [entry.to_dict() for entry in self.tool_history],
            "tool_state": self.tool_state,
            "budget": self.budget.to_dict(),
        })

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RuntimeState":
        value = _json_copy(dict(raw))
        memory = value.get("agent_memory", [])
        history = value.get("tool_history", [])
        tool_state = value.get("tool_state", {})
        if not isinstance(memory, list) or not isinstance(history, list) or not isinstance(
            tool_state, dict
        ):
            raise ValueError("Malformed runtime state")
        if not all(isinstance(item, dict) for item in memory) or not all(
            isinstance(item, dict) for item in history
        ):
            raise ValueError("Malformed runtime memory or tool history")
        return cls(
            seed=_require_int(value["seed"], "seed"),
            agent_memory=[dict(item) for item in memory],
            tool_history=[ToolHistoryEntry.from_dict(dict(item)) for item in history],
            tool_state=dict(tool_state),
            budget=BudgetState.from_dict(dict(value["budget"])),
        )
