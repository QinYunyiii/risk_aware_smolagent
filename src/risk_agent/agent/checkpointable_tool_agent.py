"""Version-pinned ToolCallingAgent continuation without replaying the task step."""

from __future__ import annotations

import time
from importlib.metadata import version
from typing import Any, Generator

from smolagents import ToolCallingAgent
from smolagents.agents import (
    ActionOutput,
    AgentError,
    AgentGenerationError,
    AgentToolExecutionError,
    handle_agent_output_types,
)
from smolagents.memory import ActionStep, FinalAnswerStep, PlanningStep, ToolCall
from smolagents.models import ChatMessageStreamDelta
from smolagents.monitoring import Timing

from risk_agent.monitoring import agent_tool_call_precounted

from .agent_state_adapter import AgentStateAdapter, AgentStateSnapshot
from .recording_model import ModelRequestRejected, RecordingModel


SUPPORTED_SMOLAGENTS_VERSION = "1.26.0"


class CheckpointResumeError(RuntimeError):
    """Raised when the installed framework cannot honor the pinned resume contract."""


class CheckpointableToolCallingAgent(ToolCallingAgent):
    """ToolCallingAgent with an explicit continuation entry point.

    ``ToolCallingAgent.run(reset=False)`` is not a continuation API: it appends another
    TaskStep and its private loop resets ``step_number`` to one.  This subclass preserves
    the framework's normal step implementation and only owns the version-sensitive outer
    loop required to begin at the next step.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        installed = version("smolagents")
        if installed != SUPPORTED_SMOLAGENTS_VERSION:
            raise CheckpointResumeError(
                f"Checkpoint resume requires smolagents=={SUPPORTED_SMOLAGENTS_VERSION}; "
                f"found {installed}"
            )
        super().__init__(*args, **kwargs)
        if isinstance(self.model, RecordingModel):
            self.model.bind_step_state_provider(
                lambda: (self.step_number, self.max_steps)
            )

    def restore_snapshot(self, snapshot: AgentStateSnapshot) -> CheckpointableToolCallingAgent:
        AgentStateAdapter.restore(self, snapshot)
        return self

    def process_tool_calls(self, chat_message: Any, memory_step: ActionStep):
        """Preserve announced call IDs even when framework tool execution raises.

        Upstream 1.26.0 assigns ``memory_step.tool_calls`` only after every tool has
        completed.  Its error path therefore loses the call ID from the ActionStep even
        though the Assistant call and Agent-visible error response exist.  Recording each
        public ToolCall yield keeps that lineage without changing execution semantics.
        """

        announced = [
            ToolCall(
                name=call.function.name,
                arguments=call.function.arguments,
                id=call.id,
            )
            for call in (chat_message.tool_calls or [])
        ]
        memory_step.tool_calls = sorted(announced, key=lambda call: call.id)
        if isinstance(self.model, RecordingModel):
            task_tool_count = sum(
                call.name != "final_answer" for call in announced
            )
            try:
                self.model.require_tool_response_allowed()
                self.model.preflight_tool_call_batch(task_tool_count)
            except ModelRequestRejected as exc:
                raise AgentToolExecutionError(str(exc), self.logger) from exc

        yielded: list[ToolCall] = []
        generator = super().process_tool_calls(chat_message, memory_step)
        try:
            for output in generator:
                if isinstance(output, ToolCall):
                    yielded.append(output)
                    memory_step.tool_calls = sorted(yielded, key=lambda call: call.id)
                yield output
        finally:
            if announced:
                memory_step.tool_calls = sorted(announced, key=lambda call: call.id)

    def execute_tool_call(self, tool_name: str, arguments: dict[str, str] | str) -> Any:
        """Count every real task-tool attempt once, before its implementation runs."""

        if tool_name == "final_answer" or not isinstance(self.model, RecordingModel):
            return super().execute_tool_call(tool_name, arguments)
        self.model.record_tool_call_attempt()
        with agent_tool_call_precounted():
            return super().execute_tool_call(tool_name, arguments)

    def resume(
        self, *, stream: bool = False
    ) -> Any | Generator[ActionStep | PlanningStep | FinalAnswerStep | ChatMessageStreamDelta, None, None]:
        """Continue after the completed ActionStep stored in restored memory."""

        if not self.task or not self.memory.steps:
            raise CheckpointResumeError("Resume requires restored task and memory state")
        generator = self._resume_stream()
        if stream:
            return generator
        events = list(generator)
        if not events or not isinstance(events[-1], FinalAnswerStep):
            raise CheckpointResumeError("Resume did not produce a FinalAnswerStep")
        return events[-1].output

    def _resume_stream(
        self,
    ) -> Generator[ActionStep | PlanningStep | FinalAnswerStep | ChatMessageStreamDelta, None, None]:
        """Pinned equivalent of ``_run_stream`` starting after the captured step."""

        if not isinstance(self.model, RecordingModel):
            raise CheckpointResumeError("Resume requires RecordingModel budget enforcement")
        if not self.model.runtime_state_bound:
            raise CheckpointResumeError("Resume requires a live RuntimeState budget authority")
        if not self.model.resume_contract_pending:
            raise CheckpointResumeError("Resume requires an unconsumed first-request contract")
        completed_step = self.step_number
        if completed_step < 1 or completed_step >= self.max_steps:
            raise CheckpointResumeError("No Agent step budget remains for continuation")
        self.step_number = completed_step + 1
        returned_final_answer = False
        final_answer: Any = None
        action_step: ActionStep | None = None

        while not returned_final_answer and self.step_number <= self.max_steps:
            if getattr(self, "interrupt_switch", False):
                raise CheckpointResumeError("Agent was interrupted before continuation")

            if self.planning_interval is not None and (
                self.step_number == 1
                or (self.step_number - 1) % self.planning_interval == 0
            ):
                planning_start = time.time()
                planning_step = None
                for element in self._generate_planning_step(
                    self.task,
                    is_first_step=False,
                    step=self.step_number,
                ):
                    yield element
                    planning_step = element
                if not isinstance(planning_step, PlanningStep):
                    raise CheckpointResumeError("Planning stream did not finish with PlanningStep")
                planning_step.timing = Timing(
                    start_time=planning_start,
                    end_time=time.time(),
                )
                self._finalize_step(planning_step)
                self.memory.steps.append(planning_step)

            action_step = ActionStep(
                step_number=self.step_number,
                timing=Timing(start_time=time.time()),
            )
            try:
                for output in self._step_stream(action_step):
                    yield output
                    if isinstance(output, ActionOutput) and output.is_final_answer:
                        final_answer = output.output
                        if self.final_answer_checks:
                            self._validate_final_answer(final_answer)
                        returned_final_answer = True
                        action_step.is_final_answer = True
            except AgentGenerationError:
                raise
            except AgentError as exc:
                action_step.error = exc
            finally:
                self._finalize_step(action_step)
                self.memory.steps.append(action_step)
                yield action_step
                self.step_number += 1

        if not returned_final_answer and self.step_number == self.max_steps + 1:
            final_answer = self._handle_max_steps_reached(self.task)
        final_step = FinalAnswerStep(handle_agent_output_types(final_answer))
        self._finalize_step(final_step)
        yield final_step


__all__ = [
    "CheckpointResumeError",
    "CheckpointableToolCallingAgent",
    "SUPPORTED_SMOLAGENTS_VERSION",
]
