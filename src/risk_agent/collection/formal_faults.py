"""Approved formal fault controls, with public responses separated from audit truth."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from risk_agent.faults import CommitStatus, FaultConfig, FaultInjector, FaultType
from risk_agent.faults.injector import InjectedFaultError, InjectedInvalidArgumentError
from risk_agent.tools.task_tool_registry import TaskToolRegistry
from .benchmark_tools import DOMAIN_TOOL_NAMES, LocalBenchmarkTool


@dataclass(frozen=True)
class FormalFaultConfig:
    fault_id: str
    fault_type: FaultType
    seed: int
    target_tool: str
    call_indices: tuple[int, ...]
    probability: float
    parameters: dict[str, Any]

    @classmethod
    def from_case(cls, case: dict[str, Any]) -> FormalFaultConfig:
        result = cls(case["fault_id"], FaultType(case["fault_type"]), case["fault_seed"],
            case["target_tool"], tuple(case["call_indices"]), 0.0, dict(case["parameters"]))
        if result.fault_type is FaultType.F5_REPEATED_ACTION:
            if result.call_indices != (1,) or result.parameters.get("variant") != "visible_no_progress_without_execution":
                raise ValueError("Only the approved first-call visible no-progress F5 profile is supported")
        else:
            FaultConfig(**result.__dict__)
        return result


class VisibleToolError(RuntimeError):
    """A public error, containing neither fault IDs nor hidden commit subtypes."""


class NoProgressFailure(InjectedFaultError):
    pass


class FormalFaultInjector(FaultInjector):
    def execute(self, operation, arguments, call):
        index = self._call_counts[operation.tool_name] + 1
        if self.config.fault_type is FaultType.F5_REPEATED_ACTION and self._should_trigger(operation.tool_name, index):
            if not operation.read_only:
                raise RuntimeError("Approved F5 plan must target a read-only operation")
            self._call_counts[operation.tool_name] += 1
            event = self._event(operation, arguments, index, triggered=True,
                phase="visible_no_progress_without_execution", commit_status=CommitStatus.NOT_STARTED,
                response_received=True, error_type="NoProgressFailure")
            raise NoProgressFailure("No progress: operation was not performed", event)
        if self.config.fault_type is FaultType.F3_INVALID_ARGUMENT and any(
            event.triggered and event.tool_name == operation.tool_name
            and event.arguments_hash == self._arguments_hash(arguments) for event in self.events):
            self._call_counts[operation.tool_name] += 1
            event = self._event(operation, arguments, index, triggered=False,
                phase="unchanged_invalid_arguments", commit_status=CommitStatus.NOT_STARTED,
                response_received=True, error_type="InvalidArgumentError")
            raise InjectedInvalidArgumentError("Arguments still invalid", event)
        return super().execute(operation, arguments, call)


class FormalBenchmarkTool(LocalBenchmarkTool):
    def forward(self, *args, **kwargs):
        try:
            return super().forward(*args, **kwargs)
        except InjectedFaultError as exc:
            messages = {
                FaultType.F1_TIMEOUT: "Timeout: tool request timed out; completion status is unknown.",
                FaultType.F2_API_503: "503 Service Unavailable: operation was not started.",
                FaultType.F3_INVALID_ARGUMENT: "Invalid argument: revise the parameters using the public tool contract; unchanged arguments remain invalid.",
                FaultType.F5_REPEATED_ACTION: "No progress: operation was not performed.",
                FaultType.F6_PARTIAL_FAILURE: "Tool request failed before a response; completion status is unknown. Query current state before another write.",
            }
            # Hide the exception chain from the Agent's str(error). Internal events
            # remain in the fault adapter checkpoint/audit, never in model messages.
            raise VisibleToolError(messages[exc.event.fault_type]) from None


def create_formal_tools(*, environment, monitor, checkpoint_id=None,
                        prohibited_call_signatures=frozenset(), fault_injector=None):
    registry = TaskToolRegistry(environment)
    return [FormalBenchmarkTool(tool_name=name, domain=environment.task.domain,
        registry=registry, monitor=monitor, checkpoint_id=checkpoint_id,
        prohibited_call_signatures=prohibited_call_signatures, fault_injector=fault_injector)
        for name in DOMAIN_TOOL_NAMES[environment.task.domain]]
