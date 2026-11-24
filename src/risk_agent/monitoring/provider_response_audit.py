"""Fail-closed, append-only auditing at the SDK-visible provider boundary.

The records produced here are deliberately external to Agent Memory, RuntimeState,
model identity and Checkpoint v2.  ``raw_response_snapshot`` is a sanitized projection
of the response object exposed by the installed SDK; it is not an HTTP wire capture.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit, urlunsplit

from .trajectory import JsonlTraceWriter


AUDIT_SCHEMA_VERSION = "1.0.0"
_MESSAGE_AUDIT_ID_ATTRIBUTE = "_risk_agent_provider_audit_request_id"
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "private_key",
    "secret",
    "access_token",
    "refresh_token",
}
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|authorization|password|access[_-]?token|secret)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_OPENAI_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")


class AuditPersistenceError(RuntimeError):
    """A required audit event could not be durably persisted."""


class _AuditWriter(Protocol):
    def append(self, record: Mapping[str, object]) -> None: ...


@dataclass(frozen=True, slots=True)
class ProviderAuditContext:
    """Non-secret identity shared by every event for one model client."""

    run_id: str
    task_id: str
    attempt_index: int
    worker_id: str
    model_id: str
    model_version: str
    endpoint: str
    phase: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def _redact_text(value: str) -> str:
    value = _BEARER_PATTERN.sub("Bearer [REDACTED]", value)
    value = _OPENAI_KEY_PATTERN.sub("[REDACTED]", value)
    return _SECRET_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value
    )


def _sensitive_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return normalized in _SENSITIVE_KEYS or any(
        token in normalized
        for token in ("authorization", "password", "private_key", "access_token")
    )


def sanitize_audit_value(value: Any) -> Any:
    """Return a JSON-safe, recursively redacted audit value."""

    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            result[text_key] = (
                "[REDACTED]"
                if _sensitive_key(text_key)
                else sanitize_audit_value(item)
            )
        return result
    if isinstance(value, (list, tuple)):
        return [sanitize_audit_value(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            return sanitize_audit_value(value.model_dump(mode="json"))
        except TypeError:
            return sanitize_audit_value(value.model_dump())
    return {"unobservable_type": f"{type(value).__module__}.{type(value).__qualname__}"}


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _optional_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _raw_mapping(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        value = dict(raw)
    elif hasattr(raw, "model_dump"):
        try:
            value = raw.model_dump(mode="json")
        except TypeError:
            value = raw.model_dump()
    else:
        value = {
            "sdk_object_type": f"{type(raw).__module__}.{type(raw).__qualname__}",
            "snapshot_observable": False,
        }
    safe = sanitize_audit_value(value)
    if not isinstance(safe, dict):
        return {"snapshot": safe}
    return safe


def _choice(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
    choices = snapshot.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        return choices[0]
    return {}


def _usage(snapshot: Mapping[str, Any]) -> dict[str, int | None]:
    raw = snapshot.get("usage")
    usage = raw if isinstance(raw, Mapping) else {}
    return {
        "prompt_tokens": _optional_nonnegative_int(usage.get("prompt_tokens")),
        "completion_tokens": _optional_nonnegative_int(usage.get("completion_tokens")),
        "total_tokens": _optional_nonnegative_int(usage.get("total_tokens")),
    }


def _message_usage(message: Any) -> dict[str, int | None]:
    usage = getattr(message, "token_usage", None)
    return {
        "prompt_tokens": _optional_nonnegative_int(getattr(usage, "input_tokens", None)),
        "completion_tokens": _optional_nonnegative_int(
            getattr(usage, "output_tokens", None)
        ),
        # smolagents TokenUsage has no provider-reported total field. Do not infer it.
        "total_tokens": None,
    }


def _usage_consistency(
    provider: Mapping[str, int | None], normalized: Mapping[str, int | None]
) -> dict[str, Any]:
    compared: dict[str, dict[str, int]] = {}
    mismatches: dict[str, dict[str, int]] = {}
    for key in ("prompt_tokens", "completion_tokens"):
        left, right = provider.get(key), normalized.get(key)
        if left is None or right is None:
            continue
        pair = {"provider": left, "chat_message": right}
        compared[key] = pair
        if left != right:
            mismatches[key] = pair
    return {
        "status": (
            "mismatch"
            if mismatches
            else "match"
            if compared
            else "not_comparable"
        ),
        "compared": compared,
        "mismatches": mismatches,
    }


def _provider_request_id(raw: Any) -> str | None:
    value = getattr(raw, "_request_id", None)
    return _redact_text(value) if isinstance(value, str) and value else None


def _provider_failure_category(exc: BaseException) -> str:
    name = type(exc).__name__.casefold()
    if isinstance(exc, (TimeoutError, ConnectionError)) or any(
        token in name for token in ("timeout", "connection", "connect")
    ):
        return "transport_failure"
    if getattr(exc, "status_code", None) is not None or any(
        token in name for token in ("statuserror", "ratelimit", "http")
    ):
        return "provider_error"
    return "provider_error"


class ProviderResponseAudit:
    """Durably relate provider outcomes to later Agent parsing outcomes."""

    def __init__(
        self,
        audit_directory: str | Path,
        *,
        response_writer: _AuditWriter | None = None,
        parsing_writer: _AuditWriter | None = None,
        id_factory: Callable[[], str] | None = None,
        wall_clock: Callable[[], str] = _utc_now,
        connection_observer: Any = None,
    ) -> None:
        self.directory = Path(audit_directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        self.response_path = self.directory / "provider_response_audit.jsonl"
        self.parsing_path = self.directory / "parsing_outcome_audit.jsonl"
        self._response_writer = response_writer or JsonlTraceWriter(self.response_path)
        self._parsing_writer = parsing_writer or JsonlTraceWriter(self.parsing_path)
        self._id_factory = id_factory or (lambda: f"local-{uuid.uuid4()}")
        self._wall_clock = wall_clock
        self.connection_observer = connection_observer

    def request_started(self, context: ProviderAuditContext, request_id: str) -> None:
        if self.connection_observer is not None:
            self.connection_observer.request_started(context.worker_id, request_id)

    def request_finished(self, context: ProviderAuditContext, request_id: str,
                         exception: BaseException | None = None) -> None:
        if self.connection_observer is not None:
            self.connection_observer.request_finished(context.worker_id, request_id, exception)

    def new_request_id(self) -> str:
        return self._id_factory()

    def timestamp(self) -> str:
        """Return the audit wall-clock timestamp used for request lineage."""

        return self._wall_clock()

    @staticmethod
    def bind_message(message: Any, audit_request_id: str) -> None:
        setattr(message, _MESSAGE_AUDIT_ID_ATTRIBUTE, audit_request_id)

    @staticmethod
    def request_id_for_message(message: Any) -> str | None:
        value = getattr(message, _MESSAGE_AUDIT_ID_ATTRIBUTE, None)
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _identity(
        *,
        context: ProviderAuditContext,
        audit_request_id: str,
        input_hash: str,
        generation_config_hash: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "audit_request_id": audit_request_id,
            "run_id": context.run_id,
            "task_id": context.task_id,
            "attempt_index": context.attempt_index,
            "worker_id": context.worker_id,
            "model_id": context.model_id,
            "model_version": context.model_version,
            "endpoint": _sanitize_endpoint(context.endpoint),
            "phase": context.phase,
            "input_hash": input_hash,
            "generation_config_hash": generation_config_hash,
        }

    @staticmethod
    def _append(writer: _AuditWriter, record: Mapping[str, Any], *, kind: str) -> None:
        try:
            writer.append(record)
        except Exception as exc:
            safe = _redact_text(str(exc))
            raise AuditPersistenceError(
                f"audit_persistence_failure: unable to persist {kind}: "
                f"{type(exc).__name__}: {safe}"
            ) from exc

    def record_response(
        self,
        *,
        context: ProviderAuditContext,
        audit_request_id: str,
        input_hash: str,
        generation_config_hash: str,
        message: Any,
        request_started_at: str,
        latency_seconds: float,
        delegated_completion_token_limit: int | None = None,
    ) -> None:
        raw = getattr(message, "raw", None)
        snapshot = _raw_mapping(raw)
        provider_usage = _usage(snapshot)
        normalized_usage = _message_usage(message)
        selected_usage = {
            key: (
                provider_usage[key]
                if provider_usage[key] is not None
                else normalized_usage[key]
            )
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        choice = _choice(snapshot)
        record = {
            **self._identity(
                context=context,
                audit_request_id=audit_request_id,
                input_hash=input_hash,
                generation_config_hash=generation_config_hash,
            ),
            "event_type": "provider_response_received",
            "request_status": "response_received",
            "error_category": None,
            "provider_request_id": _provider_request_id(raw),
            "provider_response_id": snapshot.get("id"),
            "served_model_id": snapshot.get("model"),
            "raw_response_snapshot_kind": "sdk_visible_provider_response",
            "raw_response_snapshot": snapshot,
            "raw_response_sha256": _canonical_sha256(snapshot),
            "choice_index": choice.get("index"),
            "finish_reason": choice.get("finish_reason"),
            "delegated_completion_token_limit": delegated_completion_token_limit,
            "prompt_tokens": selected_usage["prompt_tokens"],
            "completion_tokens": selected_usage["completion_tokens"],
            "total_tokens": selected_usage["total_tokens"],
            "provider_usage": provider_usage,
            "chat_message_usage": normalized_usage,
            "usage_consistency": _usage_consistency(provider_usage, normalized_usage),
            "usage_completeness": (
                "complete"
                if all(selected_usage[key] is not None for key in selected_usage)
                else "incomplete"
            ),
            "request_started_at": request_started_at,
            "response_received_at": self._wall_clock(),
            "latency_seconds": latency_seconds,
            "exception_type": None,
            "exception_message": None,
        }
        try:
            self._append(self._response_writer, record, kind="provider response event")
        finally:
            self.request_finished(context, audit_request_id)
        self.bind_message(message, audit_request_id)

    def record_provider_failure(
        self,
        *,
        context: ProviderAuditContext,
        audit_request_id: str,
        input_hash: str,
        generation_config_hash: str,
        request_started_at: str,
        latency_seconds: float,
        exception: BaseException,
        delegated_completion_token_limit: int | None = None,
    ) -> None:
        category = _provider_failure_category(exception)
        provider_request_id = getattr(exception, "request_id", None)
        record = {
            **self._identity(
                context=context,
                audit_request_id=audit_request_id,
                input_hash=input_hash,
                generation_config_hash=generation_config_hash,
            ),
            "event_type": "provider_request_failed",
            "request_status": category,
            "error_category": category,
            "provider_request_id": (
                _redact_text(provider_request_id)
                if isinstance(provider_request_id, str) and provider_request_id
                else None
            ),
            "provider_response_id": None,
            "served_model_id": None,
            "raw_response_snapshot_kind": "not_observable",
            "raw_response_snapshot": None,
            "raw_response_sha256": None,
            "choice_index": None,
            "finish_reason": None,
            "delegated_completion_token_limit": delegated_completion_token_limit,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "provider_usage": {
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
            },
            "chat_message_usage": {
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
            },
            "usage_consistency": {
                "status": "not_comparable",
                "compared": {},
                "mismatches": {},
            },
            "usage_completeness": "incomplete",
            "request_started_at": request_started_at,
            "response_received_at": None,
            "failure_recorded_at": self._wall_clock(),
            "latency_seconds": latency_seconds,
            "exception_type": type(exception).__name__,
            "exception_message": _redact_text(str(exception)),
        }
        try:
            self._append(self._response_writer, record, kind="provider failure event")
        finally:
            self.request_finished(context, audit_request_id, exception)

    def record_parsing_outcome(
        self,
        *,
        message: Any,
        parsing_status: str,
        exception: BaseException | None = None,
        reason: str | None = None,
    ) -> None:
        audit_request_id = self.request_id_for_message(message)
        if audit_request_id is None:
            raise AuditPersistenceError(
                "audit_persistence_failure: model response has no audit_request_id"
            )
        failed = exception is not None
        trace = None
        if exception is not None:
            trace = "".join(
                traceback.TracebackException.from_exception(exception).format()
            )
            trace = _redact_text(trace)
        record = {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "event_type": "parsing_outcome",
            "audit_request_id": audit_request_id,
            "parsing_status": parsing_status,
            "error_category": "tool_call_parse_failure" if failed else None,
            "exception_type": type(exception).__name__ if exception is not None else None,
            "parsing_error": _redact_text(str(exception)) if exception is not None else None,
            "traceback": trace,
            "reason": reason,
            "recorded_at": self._wall_clock(),
        }
        self._append(self._parsing_writer, record, kind="parsing outcome event")


__all__ = [
    "AUDIT_SCHEMA_VERSION",
    "AuditPersistenceError",
    "ProviderAuditContext",
    "ProviderResponseAudit",
    "sanitize_audit_value",
]
