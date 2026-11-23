"""Model-boundary recording, one-shot resume validation and persistent budgets."""

from __future__ import annotations

import copy
import json
import math
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from smolagents import Tool
from smolagents.models import ApiModel, ChatMessage, Model, is_rate_limit_error
from smolagents.utils import Retrying

from risk_agent.monitoring import RuntimeState

from .agent_state_adapter import canonical_hash, canonical_json, serialize_chat_message


_SENSITIVE_FINGERPRINT_PARTS = (
    "api_key",
    "authorization",
    "password",
    "private_key",
    "secret",
    "access_token",
)


class ModelRequestRejected(RuntimeError):
    """The model request violated its resume contract or a live budget."""


class ModelRequestIntercepted(RuntimeError):
    """A valid request was intentionally stopped before the underlying model ran."""


def _is_sensitive_fingerprint_key(key: str) -> bool:
    normalized = key.casefold()
    return normalized == "token" or any(
        part in normalized for part in _SENSITIVE_FINGERPRINT_PARTS
    )


def _fingerprint_value(value: Any, *, key: str = "") -> Any:
    """Return deterministic, secret-free identity material for request configuration."""

    if _is_sensitive_fingerprint_key(key):
        return {"sensitive": True, "present": value is not None and value != ""}
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if isinstance(value, Mapping):
        return {
            str(item_key): _fingerprint_value(item, key=str(item_key))
            for item_key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_fingerprint_value(item, key=key) for item in value]
    return {"type": f"{type(value).__module__}.{type(value).__qualname__}"}


def _callable_fingerprint(value: Any) -> dict[str, str]:
    if not callable(value):
        raise ModelRequestRejected("underlying_retry_policy is not callable")
    module = getattr(value, "__module__", None)
    qualname = getattr(value, "__qualname__", None)
    if not isinstance(module, str) or not isinstance(qualname, str):
        raise ModelRequestRejected("underlying_retry_policy identity is unavailable")
    return {"module": module, "qualname": qualname}


def _retryer_fingerprint(model: Model) -> dict[str, Any] | None:
    """Validate and fingerprint smolagents' effective in-process retry policy."""

    if not isinstance(model, ApiModel):
        return None
    retryer = getattr(model, "retryer", None)
    if type(retryer) is not Retrying:
        raise ModelRequestRejected("underlying_retryer type is not verifiable")
    max_attempts = getattr(retryer, "max_attempts", None)
    if isinstance(max_attempts, bool) or max_attempts != 1:
        raise ModelRequestRejected("underlying_retryer must allow exactly one attempt")
    retry_predicate = getattr(retryer, "retry_predicate", None)
    if retry_predicate is not is_rate_limit_error:
        raise ModelRequestRejected("underlying_retry_policy is not the pinned policy")
    wait_seconds = getattr(retryer, "wait_seconds", None)
    exponential_base = getattr(retryer, "exponential_base", None)
    jitter = getattr(retryer, "jitter", None)
    reraise = getattr(retryer, "reraise", None)
    for name, value in (
        ("wait_seconds", wait_seconds),
        ("exponential_base", exponential_base),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value < 0
        ):
            raise ModelRequestRejected(f"underlying_retryer {name} is not verifiable")
    if not isinstance(jitter, bool) or reraise is not True:
        raise ModelRequestRejected("underlying_retryer flags are not verifiable")
    return {
        "type": f"{type(retryer).__module__}.{type(retryer).__qualname__}",
        "stop_policy": {"type": "max_attempts", "max_attempts": max_attempts},
        "retry_policy": _callable_fingerprint(retry_predicate),
        "wait_policy": {
            "type": "exponential_jitter",
            "wait_seconds": float(wait_seconds),
            "exponential_base": float(exponential_base),
            "jitter": jitter,
        },
        "reraise": reraise,
    }


def _client_request_fingerprint(
    client: Any, *, require_no_retry: bool = False
) -> dict[str, Any]:
    if client is None:
        if require_no_retry:
            raise ModelRequestRejected("underlying_client is unavailable")
        return {}
    result: dict[str, Any] = {
        "type": f"{type(client).__module__}.{type(client).__qualname__}"
    }
    for attribute in ("base_url", "max_retries", "timeout"):
        if hasattr(client, attribute):
            value = getattr(client, attribute)
            if attribute in {"base_url", "timeout"}:
                value = str(value)
            result[attribute] = _fingerprint_value(value, key=attribute)
    if require_no_retry:
        max_retries = getattr(client, "max_retries", None)
        if isinstance(max_retries, bool) or max_retries != 0:
            raise ModelRequestRejected("underlying_client max_retries must be zero")
    return result


def _actual_model_fingerprint(model: Model) -> dict[str, Any]:
    """Capture the delegated object's request identity without retaining secrets."""

    is_api_model = isinstance(model, ApiModel)
    client_kwargs = getattr(model, "client_kwargs", {})
    if is_api_model:
        if not isinstance(client_kwargs, Mapping):
            raise ModelRequestRejected("underlying_client_kwargs are unavailable")
        configured_retries = client_kwargs.get("max_retries")
        if isinstance(configured_retries, bool) or configured_retries != 0:
            raise ModelRequestRejected(
                "underlying_client_kwargs max_retries must be zero"
            )
    optional_attributes = {}
    for attribute in (
        "model_version",
        "worker_id",
        "api_base",
        "base_url",
        "endpoint",
        "custom_role_conversions",
        "requests_per_minute",
    ):
        if hasattr(model, attribute):
            optional_attributes[attribute] = _fingerprint_value(
                getattr(model, attribute), key=attribute
            )
    return {
        "type": f"{type(model).__module__}.{type(model).__qualname__}",
        "model_id": _fingerprint_value(model.model_id, key="model_id"),
        "generation_config": _fingerprint_value(dict(model.kwargs), key="kwargs"),
        "flatten_messages_as_text": bool(model.flatten_messages_as_text),
        "tool_name_key": str(model.tool_name_key),
        "tool_arguments_key": str(model.tool_arguments_key),
        "client_kwargs": _fingerprint_value(client_kwargs, key="client_kwargs"),
        "client": _client_request_fingerprint(
            getattr(model, "client", None), require_no_retry=is_api_model
        ),
        "retryer": _retryer_fingerprint(model),
        "optional": optional_attributes,
    }


def _public_tool_schema(tools: Sequence[Tool] | None) -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "inputs": tool.inputs,
            "output_type": tool.output_type,
            "output_schema": getattr(tool, "output_schema", None),
        }
        for tool in (tools or [])
    ]


@dataclass(frozen=True, slots=True)
class ExpectedModelRequest:
    messages: list[dict[str, Any]]
    next_model_input_hash: str
    public_tool_schemas: list[dict[str, Any]]
    generation_config: Mapping[str, Any]
    model_id: str
    step_number: int
    remaining_steps: int
    model_calls_used: int
    max_model_calls: int
    tool_calls_used: int
    max_tool_calls: int
    tokens_used: int | None
    max_tokens: int
    latency_seconds_used: float
    max_latency_seconds: float


@dataclass(frozen=True, slots=True)
class RecordedModelRequest:
    messages: list[dict[str, Any]]
    canonical_bytes: bytes
    input_hash: str
    public_tool_schemas: list[dict[str, Any]]
    status: str
    difference: str | None
    model_calls_used_before: int
    model_calls_used_after: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_seconds: float = 0.0
    first_request_contract_verified: bool = False


class RecordingModel(Model):
    """Record requests while separating resume identity from live budget state.

    A checkpoint contract is strict for exactly the first delegated request after
    restore. Model identity, generation configuration, public tools and live budgets
    remain guarded for every later request. When a ``RuntimeState`` is bound, it is the
    sole authority for cumulative Token, Tool Call and execution-latency usage.
    """

    _NORMAL = "normal_execution"
    _AWAITING = "awaiting_first_restored_request"
    _VERIFIED = "first_restored_request_verified"

    def __init__(
        self,
        underlying: Model,
        *,
        intercept_valid_request: bool = False,
        runtime_state: RuntimeState | None = None,
        max_model_calls: int | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        super().__init__(
            flatten_messages_as_text=underlying.flatten_messages_as_text,
            tool_name_key=underlying.tool_name_key,
            tool_arguments_key=underlying.tool_arguments_key,
            model_id=underlying.model_id,
            **dict(underlying.kwargs),
        )
        if max_model_calls is not None and (
            isinstance(max_model_calls, bool)
            or not isinstance(max_model_calls, int)
            or max_model_calls < 1
        ):
            raise ValueError("max_model_calls must be a positive integer")
        self.underlying = underlying
        self.intercept_valid_request = intercept_valid_request
        self._runtime_state = runtime_state
        self._max_model_calls = max_model_calls
        self._clock = clock
        self._expected: ExpectedModelRequest | None = None
        self._contract_state = self._NORMAL
        self._step_state_provider: Callable[[], tuple[int, int]] | None = None
        self._model_calls_restored = False
        self._local_tokens_used: int | None = 0
        self._local_tool_calls_used = 0
        self._local_latency_seconds_used = 0.0
        self._token_usage_complete = True
        self._delegation_lock = threading.RLock()
        self._tool_budget_lock = threading.RLock()
        self._underlying_retryer_reference = (
            getattr(underlying, "retryer", None)
            if isinstance(underlying, ApiModel)
            else None
        )
        self._underlying_client_reference = (
            getattr(underlying, "client", None)
            if isinstance(underlying, ApiModel)
            else None
        )
        self._wrapper_model_id = self.model_id
        self._wrapper_generation_fingerprint = canonical_json(
            _fingerprint_value(dict(self.kwargs), key="kwargs")
        )
        # The canonical string is the immutable fingerprint. It contains only
        # non-sensitive values and presence metadata for secret-bearing keys.
        self._underlying_fingerprint = canonical_json(
            _actual_model_fingerprint(self.underlying)
        )
        self._post_response_budget_violation: str | None = None
        self.requests: list[RecordedModelRequest] = []
        self.prior_model_calls = 0
        self.attempted_calls = 0
        self.rejected_calls = 0
        self.executed_calls = 0
        self.succeeded_calls = 0
        self.prompt_tokens_observed = 0
        self.completion_tokens_observed = 0
        self.model_latency_seconds_observed = 0.0

    @property
    def expected(self) -> ExpectedModelRequest | None:
        """Return the installed contract without allowing callers to clear it."""

        return self._expected

    @property
    def contract_state(self) -> str:
        return self._contract_state

    @property
    def first_request_verified(self) -> bool:
        return self._contract_state == self._VERIFIED

    @property
    def resume_contract_pending(self) -> bool:
        return self._contract_state == self._AWAITING

    @property
    def runtime_state_bound(self) -> bool:
        return self._runtime_state is not None

    @property
    def model_fingerprint(self) -> Mapping[str, Any]:
        """Return a detached, secret-free view of the frozen delegated identity."""

        return json.loads(self._underlying_fingerprint)

    @property
    def post_response_budget_violation(self) -> str | None:
        return self._post_response_budget_violation

    @property
    def model_calls_used(self) -> int:
        """Requests already delegated to the provider, including failed requests."""

        return self.prior_model_calls + self.executed_calls

    def bind_step_state_provider(
        self, provider: Callable[[], tuple[int, int]]
    ) -> None:
        """Bind the live Agent ``(step_number, max_steps)`` read boundary once."""

        if self._step_state_provider is not None:
            raise ValueError("Step state provider is already bound")
        self._step_state_provider = provider

    def restore_model_calls_used(self, value: int) -> None:
        if self._model_calls_restored:
            raise ValueError("Model call counter has already been restored")
        if self.attempted_calls or self.executed_calls or self.succeeded_calls:
            raise ValueError("Model call counter can only be restored before requests")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("Restored model call counter must be non-negative")
        self.prior_model_calls = value
        self._model_calls_restored = True

    def _identity_difference(self) -> str | None:
        if self.model_id != self._wrapper_model_id:
            return "model_id"
        if canonical_json(
            _fingerprint_value(dict(self.kwargs), key="kwargs")
        ) != self._wrapper_generation_fingerprint:
            return "generation_config"
        if isinstance(self.underlying, ApiModel):
            if self.underlying.retryer is not self._underlying_retryer_reference:
                return "underlying_retryer_replaced"
            if self.underlying.client is not self._underlying_client_reference:
                return "underlying_client_replaced"
        try:
            current = _actual_model_fingerprint(self.underlying)
        except ModelRequestRejected:
            return "underlying_no_retry_configuration"
        frozen = json.loads(self._underlying_fingerprint)
        if current["model_id"] != frozen["model_id"]:
            return "underlying_model_id"
        if current["generation_config"] != frozen["generation_config"]:
            return "underlying_generation_config"
        if current["retryer"] != frozen["retryer"]:
            return "underlying_retryer_configuration"
        if (
            current["client_kwargs"] != frozen["client_kwargs"]
            or current["client"] != frozen["client"]
            or current["optional"] != frozen["optional"]
        ):
            return "provider_endpoint_or_request_config"
        if current != frozen:
            return "underlying_model_fingerprint"
        return None

    def preflight_tool_call_batch(self, count: int) -> None:
        """Atomically verify the entire non-final tool batch before side effects."""

        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("Tool batch count must be a non-negative integer")
        with self._tool_budget_lock:
            _, used, _, _, maximum, _ = self._budget_values()
            if used + count > maximum:
                raise ModelRequestRejected(
                    "tool_call_budget: batch requires "
                    f"{count} call(s), but only {max(0, maximum - used)} remain"
                )

    def record_tool_call_attempt(self) -> None:
        """Count one real non-final tool attempt immediately before execution."""

        with self._tool_budget_lock:
            self.preflight_tool_call_batch(1)
            if self._runtime_state is not None:
                self._runtime_state.budget.record(
                    tokens=0, tool_calls=1, latency_seconds=0.0
                )
            else:
                self._local_tool_calls_used += 1

    def require_tool_response_allowed(self) -> None:
        if self._post_response_budget_violation is not None:
            raise ModelRequestRejected(
                "response_tool_calls_blocked: "
                f"{self._post_response_budget_violation}"
            )

    def require_task_tool_execution_allowed(self) -> None:
        """Allow exhausted budgets to terminate, but never to start another task tool."""

        if self._max_model_calls is not None and self.model_calls_used >= self._max_model_calls:
            raise ModelRequestRejected("terminal_model_call_budget")
        tokens, tool_calls, latency, max_tokens, max_tools, max_latency = self._budget_values()
        if tokens is None:
            raise ModelRequestRejected("terminal_token_usage_unavailable")
        if tokens >= max_tokens:
            raise ModelRequestRejected("terminal_token_budget")
        if latency >= max_latency:
            raise ModelRequestRejected("terminal_latency_budget")
        if tool_calls >= max_tools:
            raise ModelRequestRejected("terminal_tool_call_budget")

    def expect(self, expected: ExpectedModelRequest) -> None:
        """Install the immutable one-shot resume contract.

        Re-installation is rejected even after provider failure so a caller cannot reset
        the first-request state or lower an already consumed request counter.
        """

        if self._contract_state != self._NORMAL or self._expected is not None:
            raise ValueError("Resume request contract is already installed")
        if expected.model_calls_used != self.model_calls_used:
            raise ValueError("Resume contract Model Call counter differs from live usage")
        if self._max_model_calls is not None and self._max_model_calls != expected.max_model_calls:
            raise ValueError("Configured Model Call limit differs from resume contract")
        self._max_model_calls = expected.max_model_calls
        if self._runtime_state is not None:
            budget = self._runtime_state.budget
            if expected.tokens_used is None:
                raise ValueError("Resume contract has unobserved cumulative Token usage")
            if (
                budget.tokens_used != expected.tokens_used
                or budget.tool_calls_used != expected.tool_calls_used
                or budget.latency_seconds_used != expected.latency_seconds_used
                or budget.max_tokens != expected.max_tokens
                or budget.max_tool_calls != expected.max_tool_calls
                or budget.max_latency_seconds != expected.max_latency_seconds
            ):
                raise ValueError("Resume contract budget differs from live RuntimeState")
        else:
            self._local_tokens_used = expected.tokens_used
            self._local_tool_calls_used = expected.tool_calls_used
            self._local_latency_seconds_used = expected.latency_seconds_used
        self._expected = copy.deepcopy(expected)
        self._contract_state = self._AWAITING

    def require_pending_resume_contract(self) -> None:
        if not self.resume_contract_pending:
            raise ModelRequestRejected(
                "Checkpoint resume requires an unconsumed first-request contract"
            )

    def _budget_values(self) -> tuple[int | None, int, float, int, int, float]:
        expected = self._expected
        if self._runtime_state is not None:
            budget = self._runtime_state.budget
            tokens_used: int | None = budget.tokens_used if self._token_usage_complete else None
            return (
                tokens_used,
                budget.tool_calls_used,
                budget.latency_seconds_used,
                budget.max_tokens,
                budget.max_tool_calls,
                budget.max_latency_seconds,
            )
        return (
            self._local_tokens_used,
            self._local_tool_calls_used,
            self._local_latency_seconds_used,
            expected.max_tokens if expected is not None else 2**63 - 1,
            expected.max_tool_calls if expected is not None else 2**63 - 1,
            expected.max_latency_seconds if expected is not None else float("inf"),
        )

    def _persistent_difference(self, tools: list[dict[str, Any]]) -> str | None:
        identity_difference = self._identity_difference()
        if identity_difference is not None:
            return identity_difference
        expected = self._expected
        if expected is not None:
            if self.model_id != expected.model_id:
                return "model_id"
            if dict(self.kwargs) != dict(expected.generation_config):
                return "generation_config"
            if tools != expected.public_tool_schemas:
                return "public_tool_schemas"
        if self._max_model_calls is not None and self.model_calls_used >= self._max_model_calls:
            return "model_call_budget"
        tokens, tool_calls, latency, max_tokens, max_tools, max_latency = self._budget_values()
        if tokens is None:
            return "token_usage_unavailable"
        if tokens >= max_tokens:
            return "token_budget"
        if latency >= max_latency:
            return "latency_budget"
        # Equality is allowed so the Agent can still generate a final answer without a
        # further task-tool call. A value above the limit is invalid and fails closed.
        if tool_calls > max_tools:
            return "tool_call_budget"
        if self._step_state_provider is not None:
            step_number, max_steps = self._step_state_provider()
            if step_number < 1 or step_number > max_steps:
                return "remaining_steps"
        return None

    def _first_request_difference(
        self,
        *,
        messages: list[dict[str, Any]],
    ) -> str | None:
        expected = self._expected
        if self._contract_state != self._AWAITING or expected is None:
            return None
        if self.model_calls_used != expected.model_calls_used:
            return "model_calls_used"
        tokens, tool_calls, latency, _, _, _ = self._budget_values()
        if tokens != expected.tokens_used:
            return "tokens_used"
        if tool_calls != expected.tool_calls_used:
            return "tool_calls_used"
        if latency != expected.latency_seconds_used:
            return "latency_seconds_used"
        if self._step_state_provider is not None:
            step_number, max_steps = self._step_state_provider()
            if step_number != expected.step_number:
                return "step_number"
            if max_steps - step_number + 1 != expected.remaining_steps:
                return "remaining_steps"
        elif expected.step_number < 2 or expected.remaining_steps <= 0:
            return "remaining_steps"
        if messages != expected.messages:
            return "messages"
        if canonical_hash(messages) != expected.next_model_input_hash:
            return "next_model_input_hash"
        return None

    def _record(
        self,
        *,
        messages: list[dict[str, Any]],
        schemas: list[dict[str, Any]],
        status: str,
        difference: str | None,
        calls_before: int,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        latency_seconds: float = 0.0,
    ) -> None:
        self.requests.append(
            RecordedModelRequest(
                messages=messages,
                canonical_bytes=canonical_json(messages).encode("utf-8"),
                input_hash=canonical_hash(messages),
                public_tool_schemas=schemas,
                status=status,
                difference=difference,
                model_calls_used_before=calls_before,
                model_calls_used_after=self.model_calls_used,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_seconds=latency_seconds,
                first_request_contract_verified=self.first_request_verified,
            )
        )

    def _record_observed_cost(
        self,
        *,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        latency_seconds: float,
    ) -> str | None:
        self.model_latency_seconds_observed += latency_seconds
        token_total: int | None = None
        if prompt_tokens is not None and completion_tokens is not None:
            token_total = prompt_tokens + completion_tokens
            self.prompt_tokens_observed += prompt_tokens
            self.completion_tokens_observed += completion_tokens
        else:
            self._token_usage_complete = False
        if self._runtime_state is not None:
            self._runtime_state.budget.latency_seconds_used += latency_seconds
            if token_total is not None:
                self._runtime_state.budget.tokens_used += token_total
        else:
            self._local_latency_seconds_used += latency_seconds
            if token_total is None:
                self._local_tokens_used = None
            elif self._local_tokens_used is not None:
                self._local_tokens_used += token_total
        tokens, _, latency, max_tokens, _, max_latency = self._budget_values()
        if tokens is None:
            return "token_usage_unavailable"
        if tokens > max_tokens:
            return "token_budget_exceeded"
        if latency > max_latency:
            return "latency_budget_exceeded"
        return None

    @staticmethod
    def _numeric_timeout(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)) and math.isfinite(float(value)) and value > 0:
            return float(value)
        for attribute in ("read", "write", "connect", "pool"):
            candidate = getattr(value, attribute, None)
            if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                if math.isfinite(float(candidate)) and candidate > 0:
                    return float(candidate)
        return None

    def _delegation_limits(self, request_kwargs: Mapping[str, Any]) -> dict[str, Any]:
        """Constrain completion length and client timeout to observed remaining budgets.

        In smolagents 1.26.0/OpenAI-compatible clients, ``max_tokens`` is a
        completion-token cap, not a total-token cap. Prompt size is not exactly known
        locally, so actual provider usage remains authoritative and overage fails closed.
        """

        tokens, _, latency, max_tokens, _, max_latency = self._budget_values()
        limits: dict[str, Any] = {}
        if tokens is not None and max_tokens < 2**63 - 1:
            remaining_tokens = max_tokens - tokens
            configured = self.underlying.kwargs.get("max_tokens")
            requested = request_kwargs.get("max_tokens")
            candidates = [remaining_tokens]
            for value in (configured, requested):
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    candidates.append(value)
            limits["max_tokens"] = max(1, min(candidates))
        if math.isfinite(max_latency):
            remaining_latency = max_latency - latency
            timeout_candidates = [remaining_latency]
            for value in (
                request_kwargs.get("timeout"),
                self.underlying.kwargs.get("timeout"),
                getattr(getattr(self.underlying, "client", None), "timeout", None),
            ):
                normalized = self._numeric_timeout(value)
                if normalized is not None:
                    timeout_candidates.append(normalized)
            limits["timeout"] = max(1e-9, min(timeout_candidates))
        return limits

    @contextmanager
    def _scoped_underlying_limits(self, limits: Mapping[str, Any]):
        """Apply caps despite smolagents giving ``Model.kwargs`` highest priority."""

        missing = object()
        originals: dict[str, Any] = {}
        for key, value in limits.items():
            originals[key] = self.underlying.kwargs.get(key, missing)
            if key in self.underlying.kwargs:
                self.underlying.kwargs[key] = value
        try:
            yield
        finally:
            for key, original in originals.items():
                if original is missing:
                    self.underlying.kwargs.pop(key, None)
                else:
                    self.underlying.kwargs[key] = original

    def generate(
        self,
        messages: list[ChatMessage],
        stop_sequences: list[str] | None = None,
        response_format: dict[str, str] | None = None,
        tools_to_call_from: list[Tool] | None = None,
        **kwargs: Any,
    ) -> ChatMessage:
        self.attempted_calls += 1
        serialized = [serialize_chat_message(message) for message in messages]
        schemas = _public_tool_schema(tools_to_call_from)
        calls_before = self.model_calls_used
        difference = self._persistent_difference(schemas)
        if difference is None:
            difference = self._first_request_difference(messages=serialized)
        if difference is not None:
            self.rejected_calls += 1
            self._record(
                messages=serialized,
                schemas=schemas,
                status="rejected",
                difference=difference,
                calls_before=calls_before,
            )
            raise ModelRequestRejected(f"Model request differs at {difference}")
        if self._contract_state == self._AWAITING and self.intercept_valid_request:
            self._record(
                messages=serialized,
                schemas=schemas,
                status="intercepted",
                difference=None,
                calls_before=calls_before,
            )
            raise ModelRequestIntercepted("Valid model request intercepted before execution")

        self._post_response_budget_violation = None
        with self._delegation_lock:
            # Re-check immediately before delegation so mutation between the initial
            # validation and the provider boundary cannot race the fingerprint guard.
            difference = self._identity_difference()
            if difference is not None:
                self.rejected_calls += 1
                self._record(
                    messages=serialized,
                    schemas=schemas,
                    status="rejected",
                    difference=difference,
                    calls_before=calls_before,
                )
                raise ModelRequestRejected(f"Model request differs at {difference}")
            if self._contract_state == self._AWAITING:
                self._contract_state = self._VERIFIED
            limits = self._delegation_limits(kwargs)
            delegated_kwargs = {**kwargs, **limits}
            self.executed_calls += 1
            started = self._clock()
            try:
                with self._scoped_underlying_limits(limits):
                    result = self.underlying.generate(
                        messages,
                        stop_sequences=stop_sequences,
                        response_format=response_format,
                        tools_to_call_from=tools_to_call_from,
                        **delegated_kwargs,
                    )
            except Exception:
                latency = max(0.0, self._clock() - started)
                self._record_observed_cost(
                    prompt_tokens=None,
                    completion_tokens=None,
                    latency_seconds=latency,
                )
                self._record(
                    messages=serialized,
                    schemas=schemas,
                    status="failed",
                    difference=None,
                    calls_before=calls_before,
                    latency_seconds=latency,
                )
                raise

        latency = max(0.0, self._clock() - started)
        usage = result.token_usage
        prompt_tokens = usage.input_tokens if usage is not None else None
        completion_tokens = usage.output_tokens if usage is not None else None
        if (
            isinstance(prompt_tokens, bool)
            or not isinstance(prompt_tokens, int)
            or prompt_tokens < 0
            or isinstance(completion_tokens, bool)
            or not isinstance(completion_tokens, int)
            or completion_tokens < 0
        ):
            prompt_tokens = None
            completion_tokens = None
        violation = self._record_observed_cost(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_seconds=latency,
        )
        self._post_response_budget_violation = violation
        self.succeeded_calls += 1
        self._record(
            messages=serialized,
            schemas=schemas,
            status="budget_violation" if violation is not None else "succeeded",
            difference=violation,
            calls_before=calls_before,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_seconds=latency,
        )
        return result


__all__ = [
    "ExpectedModelRequest",
    "ModelRequestIntercepted",
    "ModelRequestRejected",
    "RecordedModelRequest",
    "RecordingModel",
]
