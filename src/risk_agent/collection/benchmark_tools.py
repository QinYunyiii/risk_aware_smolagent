"""smolagents adapters that keep benchmark tools inside the local task environment."""

from __future__ import annotations

import json
import platform
from dataclasses import dataclass
from typing import Any, Mapping

from smolagents import Tool

from risk_agent.benchmark import TaskEnvironment
from risk_agent.benchmark.schema import Domain
from risk_agent.faults import FaultInjector, InjectedFaultError, describe_operation
from risk_agent.monitoring import ExecutionMonitor
from risk_agent.tools.task_tool_registry import TaskToolRegistry


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    description: str
    inputs: Mapping[str, Mapping[str, Any]]


class CompletedToolReplayError(RuntimeError):
    """A restored branch tried to replay an already completed source call."""


def canonical_tool_call_signature(tool_name: str, arguments: Mapping[str, Any]) -> str:
    """Return the stable signature used by the B3 source-call replay guard."""

    return json.dumps(
        {"tool_name": tool_name, "arguments": dict(arguments)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


TOOL_DEFINITIONS: dict[str, ToolDefinition] = {
    "read_csv": ToolDefinition(
        "Read every row from a UTF-8 CSV file in the current task workspace.",
        {"path": {"type": "string", "description": "Workspace-relative .csv path."}},
    ),
    "filter_data": ToolDefinition(
        "Filter CSV rows using eq, gt, gte, lt, or lte and return all matching rows.",
        {
            "path": {"type": "string", "description": "Workspace-relative .csv path."},
            "column": {"type": "string", "description": "Column to filter."},
            "operator": {"type": "string", "description": "One of eq, gt, gte, lt, lte."},
            "value": {"type": "string", "description": "Comparison value as text."},
        },
    ),
    "filter_records": ToolDefinition(
        "Filter rows returned by an earlier tool call using eq, gt, gte, lt, or lte.",
        {
            "records": {
                "type": "array",
                "description": "Rows returned by read_csv or another record tool.",
                "items": {"type": "object"},
            },
            "column": {"type": "string", "description": "Column to filter."},
            "operator": {"type": "string", "description": "One of eq, gt, gte, lt, lte."},
            "value": {"type": "string", "description": "Comparison value as text."},
        },
    ),
    "aggregate_data": ToolDefinition(
        "Group CSV rows and compute sum, mean, or count for each group.",
        {
            "path": {"type": "string", "description": "Workspace-relative .csv path."},
            "group_by": {"type": "string", "description": "Grouping column."},
            "value_column": {"type": "string", "description": "Numeric value column."},
            "operation": {"type": "string", "description": "One of sum, mean, count."},
        },
    ),
    "aggregate_records": ToolDefinition(
        "Aggregate rows returned by an earlier tool call by sum, mean, or count.",
        {
            "records": {
                "type": "array",
                "description": "Filtered rows returned by an earlier tool call.",
                "items": {"type": "object"},
            },
            "group_by": {"type": "string", "description": "Grouping column."},
            "value_column": {"type": "string", "description": "Numeric value column."},
            "operation": {"type": "string", "description": "One of sum, mean, count."},
        },
    ),
    "list_files": ToolDefinition(
        "Recursively list workspace-relative file paths under a directory.",
        {"directory": {"type": "string", "description": "Workspace-relative directory."}},
    ),
    "search_files": ToolDefinition(
        "Search text files recursively and return exact matching paths, line numbers, and lines.",
        {
            "directory": {"type": "string", "description": "Workspace-relative directory."},
            "query": {"type": "string", "description": "Non-empty case-insensitive query."},
        },
    ),
    "search_paths": ToolDefinition(
        "Search only the workspace file paths returned by an earlier tool call.",
        {
            "paths": {
                "type": "array",
                "description": "Workspace-relative paths returned by list_files.",
                "items": {"type": "string"},
            },
            "query": {"type": "string", "description": "Non-empty case-insensitive query."},
        },
    ),
    "read_file": ToolDefinition(
        "Read and return the exact UTF-8 contents of one workspace file.",
        {"path": {"type": "string", "description": "Workspace-relative file path."}},
    ),
    "query_inventory": ToolDefinition(
        "Return the current inventory row for an exact SKU.",
        {"sku": {"type": "string", "description": "Exact SKU to query."}},
    ),
    "get_order": ToolDefinition(
        "Return an order's current status and version without modifying it.",
        {"order_id": {"type": "string", "description": "Exact order ID."}},
    ),
    "update_order": ToolDefinition(
        "Atomically update an order from an expected status using a unique idempotency key.",
        {
            "order_id": {"type": "string", "description": "Exact order ID."},
            "expected_status": {"type": "string", "description": "Current expected status."},
            "new_status": {"type": "string", "description": "Allowed target status."},
            "idempotency_key": {"type": "string", "description": "Unique key, at most 128 characters."},
        },
    ),
    "calculator": ToolDefinition(
        "Safely evaluate arithmetic containing numeric literals and basic operators.",
        {"expression": {"type": "string", "description": "Arithmetic expression."}},
    ),
    "unit_converter": ToolDefinition(
        "Convert a decimal between one supported pair of units.",
        {
            "value": {"type": "string", "description": "Finite decimal value."},
            "from_unit": {"type": "string", "description": "Source unit."},
            "to_unit": {"type": "string", "description": "Target unit."},
        },
    ),
    "validate_result": ToolDefinition(
        "Check whether an actual value matches an expected value within a tolerance.",
        {
            "actual": {"type": "string", "description": "Actual value."},
            "expected": {"type": "string", "description": "Expected value."},
            "tolerance": {"type": "string", "description": "Non-negative tolerance."},
        },
    ),
}


DOMAIN_TOOL_NAMES: dict[Domain, tuple[str, ...]] = {
    Domain.DATA_ANALYSIS: (
        "read_csv",
        "filter_data",
        "filter_records",
        "aggregate_data",
        "aggregate_records",
    ),
    Domain.FILE_RETRIEVAL: (
        "list_files",
        "search_files",
        "search_paths",
        "read_file",
    ),
    Domain.API_WORKFLOW: ("query_inventory", "get_order", "update_order"),
    Domain.CALCULATION_LOGIC: ("calculator", "unit_converter", "validate_result"),
}


class LocalBenchmarkTool(Tool):
    """Dynamic schema adapter; all calls are monitored and dispatched locally."""

    skip_forward_signature_validation = True
    name = "local_benchmark_tool"
    description = "Execute one local benchmark operation."
    inputs: dict[str, dict[str, Any]] = {}
    output_type = "any"

    def __init__(
        self,
        *,
        tool_name: str,
        domain: Domain,
        registry: TaskToolRegistry,
        monitor: ExecutionMonitor,
        checkpoint_id: str | None = None,
        prohibited_call_signatures: frozenset[str] = frozenset(),
        fault_injector: FaultInjector | None = None,
    ) -> None:
        definition = TOOL_DEFINITIONS[tool_name]
        self.name = tool_name
        self.description = definition.description
        self.inputs = {key: dict(value) for key, value in definition.inputs.items()}
        self.domain = domain
        self.registry = registry
        self.monitor = monitor
        self.checkpoint_id = checkpoint_id
        self.prohibited_call_signatures = prohibited_call_signatures
        self.fault_injector = fault_injector
        self._next_step_index = 1
        self.execution_location = (
            "local_wsl" if "microsoft" in platform.release().lower() else "local_python"
        )
        super().__init__()

    def forward(self, *args: Any, **kwargs: Any) -> Any:
        if args:
            if kwargs or len(args) != len(self.inputs):
                raise TypeError(f"{self.name} requires named inputs {tuple(self.inputs)}")
            kwargs = dict(zip(self.inputs, args, strict=True))
        arguments = dict(kwargs)
        signature = canonical_tool_call_signature(self.name, arguments)
        if signature in self.prohibited_call_signatures:
            raise CompletedToolReplayError(
                "checkpoint_resume_replay_detected: "
                f"{self.name} already completed before checkpoint capture"
            )
        step_id = f"agent_tool_{self._next_step_index:03d}"
        self._next_step_index += 1
        self.monitor.runtime_state.tool_state["pending_operation"] = {
            "tool_name": self.name,
            "arguments": arguments,
            "read_only": describe_operation(self.name, arguments).read_only,
        }

        def call() -> Any:
            underlying = lambda: self.registry.execute(self.domain, self.name, arguments)
            if self.fault_injector is None:
                return underlying()
            return self.fault_injector.execute(
                describe_operation(self.name, arguments),
                arguments,
                underlying,
            )

        try:
            result = self.monitor.execute_tool(
                step_id=step_id,
                tool_name=self.name,
                arguments=arguments,
                token_usage={"total_tokens": 0},
                checkpoint_id=self.checkpoint_id,
                call=call,
            )
        except InjectedFaultError as exc:
            event = exc.event.to_dict()
            self.monitor.runtime_state.tool_state["latest_fault_event"] = event
            self.monitor.runtime_state.tool_state["last_failure"] = event
            raise
        if self.fault_injector is not None and self.fault_injector.events:
            self.monitor.runtime_state.tool_state["latest_fault_event"] = (
                self.fault_injector.events[-1].to_dict()
            )
        return result

    def checkpoint_state(self) -> dict[str, int]:
        """Return the future-call cursor required for deterministic restore."""

        return {"next_step_index": self._next_step_index}

    def restore_checkpoint_state(self, state: Mapping[str, Any]) -> None:
        if set(state) != {"next_step_index"}:
            raise ValueError(f"Invalid adapter state for {self.name}")
        value = state["next_step_index"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"Invalid next_step_index for {self.name}")
        self._next_step_index = value


def create_local_domain_tools(
    *,
    environment: TaskEnvironment,
    monitor: ExecutionMonitor,
    checkpoint_id: str | None = None,
    prohibited_call_signatures: frozenset[str] = frozenset(),
    fault_injector: FaultInjector | None = None,
) -> list[LocalBenchmarkTool]:
    registry = TaskToolRegistry(environment)
    return [
        LocalBenchmarkTool(
            tool_name=tool_name,
            domain=environment.task.domain,
            registry=registry,
            monitor=monitor,
            checkpoint_id=checkpoint_id,
            prohibited_call_signatures=prohibited_call_signatures,
            fault_injector=fault_injector,
        )
        for tool_name in DOMAIN_TOOL_NAMES[environment.task.domain]
    ]


def capture_local_tool_adapter_state(
    tools: list[LocalBenchmarkTool],
) -> dict[str, dict[str, int]]:
    """Capture every local adapter cursor without serializing live tool objects."""

    return {tool.name: tool.checkpoint_state() for tool in tools}


def restore_local_tool_adapter_state(
    tools: list[LocalBenchmarkTool],
    state: Mapping[str, Any],
) -> None:
    by_name = {tool.name: tool for tool in tools}
    if set(state) != set(by_name):
        raise ValueError("Restored local tool adapter names differ from live tools")
    for name, tool in by_name.items():
        value = state[name]
        if not isinstance(value, Mapping):
            raise ValueError(f"Invalid adapter state for {name}")
        tool.restore_checkpoint_state(value)
