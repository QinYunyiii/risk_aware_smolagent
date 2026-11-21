"""Model boundary recorder and fail-closed first-request validator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from smolagents import Tool
from smolagents.models import ChatMessage, Model

from .agent_state_adapter import canonical_hash, canonical_json, serialize_chat_message


class ModelRequestRejected(RuntimeError):
    """The actual model request differed from the checkpoint contract."""


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
    tokens_used: int
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


class RecordingModel(Model):
    """Wrap a local/remote model while recording the exact generate boundary.

    Validation happens before delegation.  A mismatch therefore cannot consume a model
    request or cause a downstream tool side effect.
    """

    def __init__(self, underlying: Model, *, intercept_valid_request: bool = False) -> None:
        super().__init__(
            flatten_messages_as_text=underlying.flatten_messages_as_text,
            tool_name_key=underlying.tool_name_key,
            tool_arguments_key=underlying.tool_arguments_key,
            model_id=underlying.model_id,
            **dict(underlying.kwargs),
        )
        self.underlying = underlying
        self.intercept_valid_request = intercept_valid_request
        self.expected: ExpectedModelRequest | None = None
        self.requests: list[RecordedModelRequest] = []
        self.prior_model_calls = 0
        self.attempted_calls = 0
        self.rejected_calls = 0
        self.executed_calls = 0
        self.succeeded_calls = 0

    @property
    def model_calls_used(self) -> int:
        """Requests already delegated to the provider, including failed requests."""

        return self.prior_model_calls + self.executed_calls

    def restore_model_calls_used(self, value: int) -> None:
        if self.attempted_calls or self.executed_calls or self.succeeded_calls:
            raise ValueError("Model call counter can only be restored before requests")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("Restored model call counter must be non-negative")
        self.prior_model_calls = value

    def expect(self, expected: ExpectedModelRequest) -> None:
        self.expected = expected

    def _difference(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> str | None:
        expected = self.expected
        if expected is None:
            return None
        if self.model_id != expected.model_id:
            return "model_id"
        if self.model_calls_used != expected.model_calls_used:
            return "model_calls_used"
        if expected.model_calls_used >= expected.max_model_calls:
            return "model_call_budget"
        if expected.tokens_used >= expected.max_tokens:
            return "token_budget"
        if expected.latency_seconds_used >= expected.max_latency_seconds:
            return "latency_budget"
        if expected.tool_calls_used > expected.max_tool_calls:
            return "tool_call_budget"
        if dict(self.kwargs) != dict(expected.generation_config):
            return "generation_config"
        if tools != expected.public_tool_schemas:
            return "public_tool_schemas"
        if messages != expected.messages:
            return "messages"
        if canonical_hash(messages) != expected.next_model_input_hash:
            return "next_model_input_hash"
        if expected.step_number < 2 or expected.remaining_steps <= 0:
            return "remaining_steps"
        return None

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
        difference = self._difference(messages=serialized, tools=schemas)
        canonical_bytes = canonical_json(serialized).encode("utf-8")
        if difference is not None:
            self.rejected_calls += 1
            self.requests.append(
                RecordedModelRequest(
                    serialized,
                    canonical_bytes,
                    canonical_hash(serialized),
                    schemas,
                    "rejected",
                    difference,
                )
            )
            raise ModelRequestRejected(f"Model request differs at {difference}")
        if self.expected is not None and self.intercept_valid_request:
            self.requests.append(
                RecordedModelRequest(
                    serialized,
                    canonical_bytes,
                    canonical_hash(serialized),
                    schemas,
                    "intercepted",
                    None,
                )
            )
            raise ModelRequestIntercepted("Valid model request intercepted before execution")
        self.executed_calls += 1
        try:
            result = self.underlying.generate(
                messages,
                stop_sequences=stop_sequences,
                response_format=response_format,
                tools_to_call_from=tools_to_call_from,
                **kwargs,
            )
        except Exception:
            self.requests.append(
                RecordedModelRequest(serialized, canonical_bytes, canonical_hash(serialized), schemas, "failed", None)
            )
            raise
        self.succeeded_calls += 1
        self.requests.append(
            RecordedModelRequest(serialized, canonical_bytes, canonical_hash(serialized), schemas, "succeeded", None)
        )
        return result


__all__ = [
    "ExpectedModelRequest",
    "ModelRequestIntercepted",
    "ModelRequestRejected",
    "RecordedModelRequest",
    "RecordingModel",
]
