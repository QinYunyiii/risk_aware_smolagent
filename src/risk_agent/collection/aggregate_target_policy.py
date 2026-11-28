"""Opt-in aggregation matching; actual tool counters and events remain unchanged."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from risk_agent.faults import FaultType, ToolOperation
from .formal_faults import FormalFaultConfig, FormalFaultInjector

POLICY = "aggregate-family-v1"
MEMBERS = frozenset({"aggregate_data", "aggregate_records"})
FAULTS = frozenset({FaultType.F2_API_503, FaultType.F5_REPEATED_ACTION})


def applicable(case: dict[str, Any]) -> bool:
    return (case["domain"] == "data_analysis" and case["fault_type"] in FAULTS
            and case["target_tool"] in MEMBERS)


class AggregateFaultInjector(FormalFaultInjector):
    def __init__(self, config: FormalFaultConfig, *, domain: str, trace_writer=None) -> None:
        marker = config.parameters.get("target_match_policy")
        if "target_match_policy" in config.parameters and marker != POLICY:
            raise ValueError("Unknown aggregate target policy")
        self.aggregate_policy_active = marker == POLICY
        if self.aggregate_policy_active and (
                domain != "data_analysis" or config.fault_type not in FAULTS
                or config.target_tool not in MEMBERS or config.call_indices != (1,)
                or config.probability != 0):
            raise ValueError("Aggregate policy requires first-call read-only Data Analysis F2/F5")
        super().__init__(config, trace_writer=trace_writer)

    def execute(self, operation: ToolOperation, arguments: dict[str, Any], call: Callable[[], Any]) -> Any:
        if self.aggregate_policy_active and operation.tool_name in MEMBERS and not operation.read_only:
            raise ValueError("Aggregate target policy cannot execute a write operation")
        return super().execute(operation, arguments, call)

    def _should_trigger(self, tool_name: str, call_index: int) -> bool:
        if not self.aggregate_policy_active:
            return super()._should_trigger(tool_name, call_index)
        if tool_name not in MEMBERS:
            return False
        # Current index works both before the F5 increment and after the F2 increment.
        ordinal = call_index + sum(self._call_counts.get(other, 0)
                                   for other in MEMBERS if other != tool_name)
        return ordinal in self.config.call_indices
