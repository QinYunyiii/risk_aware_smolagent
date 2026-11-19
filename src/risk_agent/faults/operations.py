"""Central side-effect metadata used by fault safety rules."""

from __future__ import annotations

from typing import Any

from .schema import ToolOperation


_READ_ONLY_TOOLS = {
    "read_csv",
    "filter_data",
    "aggregate_data",
    "list_files",
    "search_files",
    "read_file",
    "query_inventory",
    "get_order",
    "calculator",
    "unit_converter",
    "validate_result",
}
_WRITE_TOOLS = {"update_order"}


def describe_operation(tool_name: str, arguments: dict[str, Any]) -> ToolOperation:
    if tool_name in _READ_ONLY_TOOLS:
        return ToolOperation(tool_name=tool_name, read_only=True)
    if tool_name in _WRITE_TOOLS:
        key = arguments.get("idempotency_key")
        return ToolOperation(
            tool_name=tool_name,
            read_only=False,
            idempotency_key_present=isinstance(key, str) and bool(key),
        )
    raise ValueError(f"No side-effect metadata for tool: {tool_name}")
