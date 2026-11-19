"""smolagents adapters that keep benchmark tools inside the local task environment."""

from __future__ import annotations

import itertools
import platform
from dataclasses import dataclass
from typing import Any, Mapping

from smolagents import Tool

from risk_agent.benchmark import TaskEnvironment
from risk_agent.benchmark.schema import Domain
from risk_agent.monitoring import ExecutionMonitor
from risk_agent.tools.task_tool_registry import TaskToolRegistry


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    description: str
    inputs: Mapping[str, Mapping[str, str]]


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
    "aggregate_data": ToolDefinition(
        "Group CSV rows and compute sum, mean, or count for each group.",
        {
            "path": {"type": "string", "description": "Workspace-relative .csv path."},
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
    Domain.DATA_ANALYSIS: ("read_csv", "filter_data", "aggregate_data"),
    Domain.FILE_RETRIEVAL: ("list_files", "search_files", "read_file"),
    Domain.API_WORKFLOW: ("query_inventory", "get_order", "update_order"),
    Domain.CALCULATION_LOGIC: ("calculator", "unit_converter", "validate_result"),
}


class LocalBenchmarkTool(Tool):
    """Dynamic schema adapter; all calls are monitored and dispatched locally."""

    skip_forward_signature_validation = True
    name = "local_benchmark_tool"
    description = "Execute one local benchmark operation."
    inputs: dict[str, dict[str, str]] = {}
    output_type = "any"

    def __init__(
        self,
        *,
        tool_name: str,
        domain: Domain,
        registry: TaskToolRegistry,
        monitor: ExecutionMonitor,
        checkpoint_id: str | None = None,
    ) -> None:
        definition = TOOL_DEFINITIONS[tool_name]
        self.name = tool_name
        self.description = definition.description
        self.inputs = {key: dict(value) for key, value in definition.inputs.items()}
        self.domain = domain
        self.registry = registry
        self.monitor = monitor
        self.checkpoint_id = checkpoint_id
        self._steps = itertools.count(1)
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
        step_id = f"agent_tool_{next(self._steps):03d}"
        return self.monitor.execute_tool(
            step_id=step_id,
            tool_name=self.name,
            arguments=arguments,
            token_usage={"total_tokens": 0},
            checkpoint_id=self.checkpoint_id,
            call=lambda: self.registry.execute(self.domain, self.name, arguments),
        )


def create_local_domain_tools(
    *,
    environment: TaskEnvironment,
    monitor: ExecutionMonitor,
) -> list[LocalBenchmarkTool]:
    registry = TaskToolRegistry(environment)
    return [
        LocalBenchmarkTool(
            tool_name=tool_name,
            domain=environment.task.domain,
            registry=registry,
            monitor=monitor,
        )
        for tool_name in DOMAIN_TOOL_NAMES[environment.task.domain]
    ]
