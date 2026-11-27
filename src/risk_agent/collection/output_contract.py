"""Versioned, lossless JSON observations for local benchmark tools only."""

from __future__ import annotations

import json
from typing import Any, Callable, Generator

from smolagents.agents import ToolOutput

from risk_agent.agent import CheckpointableToolCallingAgent as LegacyAgent, RecordingModel


class OutputContractError(RuntimeError):
    """A local tool result cannot be represented without losing JSON information."""


def encode_observation(value: Any) -> str:
    """Reject coercions (tuple, non-string keys, NaN, cycles); never use str fallback."""
    ancestors: set[int] = set()

    def validate(item: Any) -> None:
        if item is None or isinstance(item, (str, bool, int, float)):
            return
        if not isinstance(item, (list, dict)):
            raise OutputContractError("Tool result is not a supported JSON value")
        if id(item) in ancestors:
            raise OutputContractError("Circular tool result")
        ancestors.add(id(item))
        try:
            if isinstance(item, dict):
                if any(not isinstance(key, str) for key in item):
                    raise OutputContractError("JSON object keys must be strings")
                for child in item.values():
                    validate(child)
            else:
                for child in item:
                    validate(child)
        finally:
            ancestors.remove(id(item))

    try:
        validate(value)
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        encoded.encode("utf-8")
        return encoded
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        raise OutputContractError("Lossless UTF-8 JSON observation encoding failed") from exc


class CheckpointableToolCallingAgent(LegacyAgent):
    """Keep upstream call execution, replay guards and raw outputs unchanged.

    This is a genuine subclass in a versioned module. Checkpoint v2 requires this
    public basename; the full module-qualified class remains distinct from v2.1.
    The mutable ToolOutput yielded by upstream is also retained in its output map.
    Replacing its observation before resuming that generator makes the same JSON
    text enter Memory. No second tool execution or post-capture memory patch occurs.
    """

    def process_tool_calls(self, chat_message: Any, memory_step: Any) -> Generator[Any, None, None]:
        stream = super().process_tool_calls(chat_message, memory_step)
        try:
            for event in stream:
                if isinstance(event, ToolOutput) and not event.is_final_answer:
                    event.observation = encode_observation(event.output)
                yield event
        finally:
            stream.close()


class LosslessAgentFactory:
    """Reuse the existing TLS/provider factory, rebuilding only the empty Agent."""

    def __init__(self, factory: Callable[..., Any]) -> None:
        self.factory = factory

    def __call__(self, settings: Any, worker: Any, tools: Any, runtime: Any, **kwargs: Any):
        original, _, underlying = self.factory(settings, worker, tools, runtime, **kwargs)
        try:
            if original.memory.steps:
                raise OutputContractError("Output contract must be bound before Agent execution")
            # A RecordingModel's step callback is single-owner. Reuse the provider
            # client, not the recorder already bound to the discarded empty Agent.
            recorder = RecordingModel(
                underlying, runtime_state=runtime, max_model_calls=kwargs["max_model_calls"],
                provider_audit=kwargs.get("provider_audit"),
                provider_audit_context=kwargs.get("provider_audit_context"))
            agent = CheckpointableToolCallingAgent(
                tools=tools, model=recorder, max_steps=kwargs["max_steps"], verbosity_level=0,
                name=kwargs["agent_name"], description="Versioned lossless local benchmark Agent")
        except BaseException:
            underlying.client.close()
            raise
        return agent, recorder, underlying
