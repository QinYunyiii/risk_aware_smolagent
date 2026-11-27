"""Versioned audit-only adaptation of the frozen lossless Agent."""
from __future__ import annotations

from typing import Any, Callable, Generator

from smolagents.agents import AgentToolExecutionError
from smolagents.memory import ToolCall

from risk_agent.agent import ModelRequestRejected, RecordingModel
from .output_contract import CheckpointableToolCallingAgent as LosslessAgent


class CheckpointableToolCallingAgent(LosslessAgent):
    """Account for a returned response blocked before the parser's first yield.

    Do not infer parsing success from the SDK's tool-call object. The frozen
    superclass remains authoritative for parsing, execution and all guards.
    This public basename is required by the unchanged Checkpoint v2 whitelist;
    the module-qualified identity distinguishes this implementation revision.
    """

    def process_tool_calls(self, chat_message: Any, memory_step: Any) -> Generator[Any, None, None]:
        stream = super().process_tool_calls(chat_message, memory_step)
        yielded_call = False
        try:
            for event in stream:
                if isinstance(event, ToolCall):
                    yielded_call = True
                yield event
        except AgentToolExecutionError as exc:
            if (not yielded_call and isinstance(exc.__cause__, ModelRequestRejected)
                    and isinstance(self.model, RecordingModel)):
                self.model.record_parsing_outcome(
                    chat_message, parsing_status="not_attempted",
                    reason=f"tool_execution_preflight_blocked:{exc.__cause__}")
            raise
        finally:
            stream.close()


class BudgetBoundaryAgentFactory:
    """Reuse the frozen provider factory, replace only its empty Agent/recorder."""

    def __init__(self, factory: Callable[..., Any]):
        self.factory = factory

    def __call__(self, settings: Any, worker: Any, tools: Any, runtime: Any, **kwargs: Any):
        original, _, underlying = self.factory(settings, worker, tools, runtime, **kwargs)
        try:
            if original.memory.steps:
                raise ValueError("Budget audit revision must bind before Agent execution")
            recorder = RecordingModel(underlying, runtime_state=runtime,
                max_model_calls=kwargs["max_model_calls"],
                provider_audit=kwargs.get("provider_audit"),
                provider_audit_context=kwargs.get("provider_audit_context"))
            agent = CheckpointableToolCallingAgent(tools=tools, model=recorder,
                max_steps=kwargs["max_steps"], verbosity_level=0, name=kwargs["agent_name"],
                description="Versioned lossless local benchmark Agent")
        except BaseException:
            underlying.client.close()
            raise
        return agent, recorder, underlying
