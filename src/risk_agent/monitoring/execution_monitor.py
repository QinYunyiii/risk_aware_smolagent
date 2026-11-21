"""Append-only tool trajectory monitor tied to checkpointable runtime state."""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Mapping

from .runtime_state import RuntimeState, ToolHistoryEntry
from .trajectory import JsonlTraceWriter


_SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "password",
    "private_key",
    "secret",
    "access_token",
}


_AGENT_TOOL_CALL_PRECOUNTED: ContextVar[bool] = ContextVar(
    "agent_tool_call_precounted", default=False
)


@contextmanager
def agent_tool_call_precounted():
    """Tell nested monitors that the Agent boundary already counted this attempt."""

    token = _AGENT_TOOL_CALL_PRECOUNTED.set(True)
    try:
        yield
    finally:
        _AGENT_TOOL_CALL_PRECOUNTED.reset(token)


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("Monitored JSON objects require string keys")
        return {
            key: (
                "[REDACTED]"
                if key.casefold() in _SENSITIVE_KEYS
                else redact_sensitive(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_sensitive(item) for item in value]
    return value


class ExecutionMonitor:
    def __init__(
        self,
        *,
        writer: JsonlTraceWriter,
        runtime_state: RuntimeState,
        experiment_id: str,
        run_id: str,
        task_id: str,
        template_id: str,
        instance_id: str,
        model_id: str,
        model_version: str,
        prompt_version: str,
        tool_version: str,
        worker_id: str,
        environment_id: str,
        additional_context: Mapping[str, Any] | None = None,
    ) -> None:
        self.writer = writer
        self.runtime_state = runtime_state
        self.context = {
            "experiment_id": experiment_id,
            "run_id": run_id,
            "task_id": task_id,
            "template_id": template_id,
            "instance_id": instance_id,
            "seed": runtime_state.seed,
            "model_id": model_id,
            "model_version": model_version,
            "prompt_version": prompt_version,
            "tool_version": tool_version,
            "worker_id": worker_id,
            "environment_id": environment_id,
        }
        if additional_context:
            overlap = set(additional_context) & set(self.context)
            if overlap:
                raise ValueError(
                    f"additional_context cannot replace monitor fields: {sorted(overlap)}"
                )
            self.context.update(redact_sensitive(dict(additional_context)))

    @staticmethod
    def _token_count(token_usage: Mapping[str, int]) -> int:
        if "total_tokens" in token_usage:
            return int(token_usage["total_tokens"])
        return int(token_usage.get("input_tokens", 0)) + int(
            token_usage.get("output_tokens", 0)
        )

    def execute_tool(
        self,
        *,
        step_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        token_usage: Mapping[str, int],
        checkpoint_id: str | None,
        call: Callable[[], Any],
    ) -> Any:
        normalized_tokens = {key: int(value) for key, value in token_usage.items()}
        if any(value < 0 for value in normalized_tokens.values()):
            raise ValueError("Token usage must be non-negative")
        # Count before the call so a rejected budget cannot permit side effects and a
        # failed tool attempt still consumes exactly one call. Checkpointable agents
        # count at their universal execute_tool_call boundary; the ContextVar is copied
        # into smolagents' worker threads and prevents this monitor from double counting.
        if not _AGENT_TOOL_CALL_PRECOUNTED.get():
            self.runtime_state.budget.record(
                tokens=0,
                tool_calls=1,
                latency_seconds=0.0,
            )
        started = time.perf_counter()
        result: Any = None
        error_type: str | None = None
        try:
            result = call()
            return result
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            latency = time.perf_counter() - started
            timestamp = datetime.now(timezone.utc).isoformat()
            safe_arguments = redact_sensitive(arguments)
            safe_result = redact_sensitive(result)
            entry = ToolHistoryEntry(
                step_id=step_id,
                tool_name=tool_name,
                tool_arguments=safe_arguments,
                tool_result=safe_result,
                error_type=error_type,
                timestamp=timestamp,
                token_usage=normalized_tokens,
                latency_seconds=latency,
                worker_id=str(self.context["worker_id"]),
                environment_id=str(self.context["environment_id"]),
                checkpoint_id=checkpoint_id,
            )
            self.runtime_state.tool_history.append(entry)
            self.runtime_state.budget.record(
                tokens=self._token_count(normalized_tokens),
                tool_calls=0,
                latency_seconds=latency,
            )
            self.writer.append(
                {
                    "record_type": "tool_call",
                    **self.context,
                    **entry.to_dict(),
                }
            )
