"""Explicit domain-aware dispatch for Day 3 local task tools."""

from __future__ import annotations

from typing import Any, Callable

from risk_agent.benchmark.environment import TaskEnvironment
from risk_agent.benchmark.schema import Domain

from .api_workflow_tools import APIWorkflowTools
from .data_analysis_tools import DataAnalysisTools
from .file_retrieval_tools import FileRetrievalTools
from .logic_tools import LogicTools


class ToolRegistryError(ValueError):
    pass


class TaskToolRegistry:
    def __init__(self, environment: TaskEnvironment) -> None:
        data = DataAnalysisTools(environment)
        files = FileRetrievalTools(environment)
        api = APIWorkflowTools(environment)
        logic = LogicTools()
        self._tools: dict[str, tuple[Domain, Callable[..., Any]]] = {
            "read_csv": (Domain.DATA_ANALYSIS, data.read_csv),
            "filter_data": (Domain.DATA_ANALYSIS, data.filter_data),
            "aggregate_data": (Domain.DATA_ANALYSIS, data.aggregate_data),
            "list_files": (Domain.FILE_RETRIEVAL, files.list_files),
            "search_files": (Domain.FILE_RETRIEVAL, files.search_files),
            "read_file": (Domain.FILE_RETRIEVAL, files.read_file),
            "query_inventory": (Domain.API_WORKFLOW, api.query_inventory),
            "get_order": (Domain.API_WORKFLOW, api.get_order),
            "update_order": (Domain.API_WORKFLOW, api.update_order),
            "calculator": (Domain.CALCULATION_LOGIC, logic.calculator),
            "unit_converter": (Domain.CALCULATION_LOGIC, logic.unit_converter),
            "validate_result": (Domain.CALCULATION_LOGIC, logic.validate_result),
        }

    def execute(self, domain: Domain, tool_name: str, arguments: dict[str, Any]) -> Any:
        try:
            registered_domain, function = self._tools[tool_name]
        except KeyError as exc:
            raise ToolRegistryError(f"Unknown tool: {tool_name}") from exc
        if registered_domain != domain:
            raise ToolRegistryError(
                f"Tool {tool_name!r} belongs to {registered_domain.value}, not {domain.value}"
            )
        return function(**arguments)
