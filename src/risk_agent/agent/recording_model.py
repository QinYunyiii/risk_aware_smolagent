"""Model-boundary recording, one-shot resume validation and persistent budgets."""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from smolagents import Tool
from smolagents.models import ChatMessage, Model

from risk_agent.monitoring import RuntimeState

from .agent_state_adapter import canonical_hash, canonical_json, serialize_chat_message


class ModelRequestRejected(RuntimeError):
    """The model request violated its resume contract or a live budget."""


class ModelRequestIntercepted(RuntimeError):
    """A valid request was intentionally stopped before the underlying model ran."""


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
        self._expected: ExpectedModelRequest | None = None
        self._contract_state = self._NORMAL
        self._step_state_provider: Callable[[], tuple[int, int]] | None = None
        self._model_calls_restored = False
        self._local_tokens_used: int | None = 0
        self._local_tool_calls_used = 0
        self._local_latency_seconds_used = 0.0
        self._token_usage_complete = True
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
    ) -> None:
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

        if self._contract_state == self._AWAITING:
            self._contract_state = self._VERIFIED
        self.executed_calls += 1
        started = time.perf_counter()
        try:
            result = self.underlying.generate(
                messages,
                stop_sequences=stop_sequences,
                response_format=response_format,
                tools_to_call_from=tools_to_call_from,
                **kwargs,
            )
        except Exception:
            latency = max(0.0, time.perf_counter() - started)
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

        latency = max(0.0, time.perf_counter() - started)
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
        self._record_observed_cost(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_seconds=latency,
        )
        self.succeeded_calls += 1
        self._record(
            messages=serialized,
            schemas=schemas,
            status="succeeded",
            difference=None,
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
