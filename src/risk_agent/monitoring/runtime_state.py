"""Serializable runtime state required by Day 5 checkpoints."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


@dataclass(slots=True)
class BudgetState:
    max_tokens: int
    max_tool_calls: int
    max_latency_seconds: float
    tokens_used: int = 0
    tool_calls_used: int = 0
    latency_seconds_used: float = 0.0

    def __post_init__(self) -> None:
        if self.max_tokens <= 0 or self.max_tool_calls <= 0 or self.max_latency_seconds <= 0:
            raise ValueError("Budget limits must be positive")
        if self.tokens_used < 0 or self.tool_calls_used < 0 or self.latency_seconds_used < 0:
            raise ValueError("Budget usage must be non-negative")

    def record(self, *, tokens: int, tool_calls: int, latency_seconds: float) -> None:
        if tokens < 0 or tool_calls < 0 or latency_seconds < 0:
            raise ValueError("Budget usage increments must be non-negative")
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
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "BudgetState":
        return cls(
            max_tokens=int(raw["max_tokens"]),
            max_tool_calls=int(raw["max_tool_calls"]),
            max_latency_seconds=float(raw["max_latency_seconds"]),
            tokens_used=int(raw.get("tokens_used", 0)),
            tool_calls_used=int(raw.get("tool_calls_used", 0)),
            latency_seconds_used=float(raw.get("latency_seconds_used", 0.0)),
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
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ToolHistoryEntry":
        return cls(
            step_id=str(raw["step_id"]),
            tool_name=str(raw["tool_name"]),
            tool_arguments=dict(raw.get("tool_arguments", {})),
            tool_result=_json_copy(raw.get("tool_result")),
            error_type=str(raw["error_type"]) if raw.get("error_type") is not None else None,
            timestamp=str(raw["timestamp"]),
            token_usage={key: int(value) for key, value in dict(raw.get("token_usage", {})).items()},
            latency_seconds=float(raw["latency_seconds"]),
            worker_id=str(raw["worker_id"]),
            environment_id=str(raw["environment_id"]),
            checkpoint_id=(
                str(raw["checkpoint_id"]) if raw.get("checkpoint_id") is not None else None
            ),
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
        if self.seed < 0:
            raise ValueError("Runtime seed must be non-negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "seed": self.seed,
            "agent_memory": _json_copy(self.agent_memory),
            "tool_history": [entry.to_dict() for entry in self.tool_history],
            "tool_state": _json_copy(self.tool_state),
            "budget": self.budget.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RuntimeState":
        memory = raw.get("agent_memory", [])
        history = raw.get("tool_history", [])
        tool_state = raw.get("tool_state", {})
        if not isinstance(memory, list) or not isinstance(history, list) or not isinstance(
            tool_state, dict
        ):
            raise ValueError("Malformed runtime state")
        return cls(
            seed=int(raw["seed"]),
            agent_memory=[dict(item) for item in _json_copy(memory)],
            tool_history=[ToolHistoryEntry.from_dict(dict(item)) for item in history],
            tool_state=dict(_json_copy(tool_state)),
            budget=BudgetState.from_dict(dict(raw["budget"])),
        )
