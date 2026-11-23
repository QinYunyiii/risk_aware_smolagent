"""Seeded, configuration-driven fault injection around one tool call."""

from __future__ import annotations

import hashlib
import json
import random
import uuid
from collections import defaultdict
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from risk_agent.monitoring.trajectory import JsonlTraceWriter

from .schema import CommitStatus, FaultConfig, FaultEvent, FaultType, ToolOperation


class InjectedFaultError(RuntimeError):
    def __init__(self, message: str, event: FaultEvent) -> None:
        super().__init__(message)
        self.event = event


class InjectedTimeoutError(InjectedFaultError):
    pass


class InjectedServiceUnavailableError(InjectedFaultError):
    pass


class InjectedInvalidArgumentError(InjectedFaultError):
    pass


class PartialFailureError(InjectedFaultError):
    def __init__(self, message: str, event: FaultEvent, committed_result: Any = None) -> None:
        super().__init__(message, event)
        self.committed_result = committed_result


class FaultInjector:
    def __init__(
        self,
        config: FaultConfig,
        *,
        trace_writer: JsonlTraceWriter | None = None,
    ) -> None:
        self.config = config
        self.trace_writer = trace_writer
        self.events: list[FaultEvent] = []
        self._call_counts: dict[str, int] = defaultdict(int)
        self._last_results: dict[str, Any] = {}
        self._random = random.Random(config.seed)

    @staticmethod
    def _json_copy(value: Any) -> Any:
        return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))

    @staticmethod
    def _tuple_tree(value: Any) -> Any:
        if isinstance(value, list):
            return tuple(FaultInjector._tuple_tree(item) for item in value)
        return value

    def checkpoint_state(self) -> dict[str, Any]:
        """Capture all future-behaviour state required by a restored adapter."""

        return {
            "fault_identity": {
                "fault_id": self.config.fault_id,
                "fault_type": self.config.fault_type.value,
                "seed": self.config.seed,
                "target_tool": self.config.target_tool,
                "call_indices": list(self.config.call_indices),
                "probability": self.config.probability,
                "parameters": self._json_copy(dict(self.config.parameters)),
            },
            "call_counts": dict(sorted(self._call_counts.items())),
            "last_results": self._json_copy(self._last_results),
            "events": [event.to_dict() for event in self.events],
            "random_state": self._json_copy(self._random.getstate()),
        }

    def restore_checkpoint_state(self, state: Mapping[str, Any]) -> None:
        """Restore state only when it belongs to this exact fault configuration."""

        value = self._json_copy(dict(state))
        expected_identity = self.checkpoint_state()["fault_identity"]
        if value.get("fault_identity") != expected_identity:
            raise ValueError("Restored fault adapter identity differs from live configuration")
        call_counts = value.get("call_counts")
        last_results = value.get("last_results")
        events = value.get("events")
        random_state = value.get("random_state")
        if (
            not isinstance(call_counts, dict)
            or not isinstance(last_results, dict)
            or not isinstance(events, list)
        ):
            raise ValueError("Malformed fault adapter state")
        normalized_counts: dict[str, int] = {}
        for tool_name, count in call_counts.items():
            if not isinstance(tool_name, str) or isinstance(count, bool) or not isinstance(count, int):
                raise ValueError("Malformed fault adapter call counter")
            if count < 0:
                raise ValueError("Fault adapter call counters must be non-negative")
            normalized_counts[tool_name] = count
        try:
            self._random.setstate(self._tuple_tree(random_state))
        except (TypeError, ValueError) as exc:
            raise ValueError("Malformed fault adapter random state") from exc
        self._call_counts = defaultdict(int, normalized_counts)
        self._last_results = dict(last_results)
        try:
            self.events = [
                FaultEvent(
                    event_id=str(item["event_id"]),
                    fault_id=str(item["fault_id"]),
                    fault_type=FaultType(str(item["fault_type"])),
                    seed=int(item["seed"]),
                    tool_name=str(item["tool_name"]),
                    call_index=int(item["call_index"]),
                    triggered=bool(item["triggered"]),
                    phase=str(item["phase"]),
                    commit_status=CommitStatus(str(item["commit_status"])),
                    response_received=bool(item["response_received"]),
                    error_type=(
                        str(item["error_type"])
                        if item.get("error_type") is not None
                        else None
                    ),
                    arguments_hash=str(item["arguments_hash"]),
                    timestamp=str(item["timestamp"]),
                )
                for raw in events
                for item in [dict(raw)]
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Malformed fault adapter event history") from exc

    @staticmethod
    def _arguments_hash(arguments: dict[str, Any]) -> str:
        encoded = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str).encode(
            "utf-8"
        )
        return hashlib.sha256(encoded).hexdigest()

    def _should_trigger(self, tool_name: str, call_index: int) -> bool:
        if self.config.fault_type == FaultType.F0_NO_FAULT:
            return False
        if self.config.target_tool not in {"*", tool_name}:
            return False
        if self.config.call_indices:
            return call_index in self.config.call_indices
        return self._random.random() < self.config.probability

    def _event(
        self,
        operation: ToolOperation,
        arguments: dict[str, Any],
        call_index: int,
        *,
        triggered: bool,
        phase: str,
        commit_status: CommitStatus,
        response_received: bool,
        error_type: str | None,
    ) -> FaultEvent:
        event = FaultEvent(
            event_id=str(uuid.uuid4()),
            fault_id=self.config.fault_id,
            fault_type=self.config.fault_type,
            seed=self.config.seed,
            tool_name=operation.tool_name,
            call_index=call_index,
            triggered=triggered,
            phase=phase,
            commit_status=commit_status,
            response_received=response_received,
            error_type=error_type,
            arguments_hash=self._arguments_hash(arguments),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self.events.append(event)
        if self.trace_writer is not None:
            self.trace_writer.append({"record_type": "fault_event", **event.to_dict()})
        return event

    def execute(
        self,
        operation: ToolOperation,
        arguments: dict[str, Any],
        call: Callable[[], Any],
    ) -> Any:
        self._call_counts[operation.tool_name] += 1
        call_index = self._call_counts[operation.tool_name]
        triggered = self._should_trigger(operation.tool_name, call_index)
        if not triggered:
            try:
                result = call()
            except Exception as exc:
                self._event(
                    operation,
                    arguments,
                    call_index,
                    triggered=False,
                    phase="tool_execution",
                    commit_status=(
                        CommitStatus.NOT_APPLICABLE
                        if operation.read_only
                        else CommitStatus.UNKNOWN
                    ),
                    response_received=False,
                    error_type=type(exc).__name__,
                )
                raise
            self._event(
                operation,
                arguments,
                call_index,
                triggered=False,
                phase="completed",
                commit_status=(
                    CommitStatus.NOT_APPLICABLE if operation.read_only else CommitStatus.COMMITTED
                ),
                response_received=True,
                error_type=None,
            )
            self._last_results[operation.tool_name] = result
            return result

        fault_type = self.config.fault_type
        if fault_type == FaultType.F1_TIMEOUT:
            event = self._event(
                operation,
                arguments,
                call_index,
                triggered=True,
                phase="request",
                commit_status=CommitStatus(str(self.config.parameters.get("commit_status", "unknown"))),
                response_received=False,
                error_type="InjectedTimeoutError",
            )
            raise InjectedTimeoutError("Injected timeout", event)
        if fault_type == FaultType.F2_API_503:
            event = self._event(
                operation,
                arguments,
                call_index,
                triggered=True,
                phase="before_execution",
                commit_status=CommitStatus.NOT_STARTED,
                response_received=True,
                error_type="InjectedServiceUnavailableError",
            )
            raise InjectedServiceUnavailableError("Injected API 503", event)
        if fault_type == FaultType.F3_INVALID_ARGUMENT:
            event = self._event(
                operation,
                arguments,
                call_index,
                triggered=True,
                phase="argument_validation",
                commit_status=CommitStatus.NOT_STARTED,
                response_received=True,
                error_type="InjectedInvalidArgumentError",
            )
            raise InjectedInvalidArgumentError("Injected invalid argument", event)
        if fault_type == FaultType.F4_STALE_OBSERVATION:
            self._event(
                operation,
                arguments,
                call_index,
                triggered=True,
                phase="observation",
                commit_status=CommitStatus.NOT_APPLICABLE,
                response_received=True,
                error_type=None,
            )
            return self.config.parameters.get("stale_result")
        if fault_type == FaultType.F5_REPEATED_ACTION:
            if operation.tool_name not in self._last_results:
                raise RuntimeError("Repeated-action injection requires a prior successful result")
            self._event(
                operation,
                arguments,
                call_index,
                triggered=True,
                phase="action_selection",
                commit_status=CommitStatus.NOT_APPLICABLE,
                response_received=True,
                error_type=None,
            )
            return self._last_results[operation.tool_name]
        if fault_type == FaultType.F6_PARTIAL_FAILURE:
            commit = bool(self.config.parameters.get("commit", True))
            if commit:
                committed_result = call()
                status = CommitStatus.COMMITTED
            else:
                committed_result = None
                status = CommitStatus.NOT_COMMITTED
            event = self._event(
                operation,
                arguments,
                call_index,
                triggered=True,
                phase="after_commit" if commit else "before_commit",
                commit_status=status,
                response_received=False,
                error_type="PartialFailureError",
            )
            raise PartialFailureError(
                "Injected partial failure after commit" if commit else "Injected failure before commit",
                event,
                committed_result,
            )
        raise AssertionError(f"Unhandled fault type: {fault_type}")
