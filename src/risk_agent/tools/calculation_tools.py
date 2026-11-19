"""A deliberately small arithmetic tool without dynamic code execution."""

from __future__ import annotations

import ast
import math
import os
import platform
import socket
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from smolagents import Tool


class UnsafeExpressionError(ValueError):
    """Raised when a calculator expression exceeds the allowed grammar."""


_BINARY_OPERATORS: dict[type[ast.operator], Callable[[float | int, float | int], float | int]] = {
    ast.Add: lambda left, right: left + right,
    ast.Sub: lambda left, right: left - right,
    ast.Mult: lambda left, right: left * right,
    ast.Div: lambda left, right: left / right,
    ast.FloorDiv: lambda left, right: left // right,
    ast.Mod: lambda left, right: left % right,
    ast.Pow: lambda left, right: left**right,
}
_UNARY_OPERATORS: dict[type[ast.unaryop], Callable[[float | int], float | int]] = {
    ast.UAdd: lambda value: value,
    ast.USub: lambda value: -value,
}


def _validate_number(value: object) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UnsafeExpressionError("Only integer and floating-point literals are allowed")
    if isinstance(value, int) and value.bit_length() > 256:
        raise UnsafeExpressionError("Integer literal is too large")
    if isinstance(value, float) and not math.isfinite(value):
        raise UnsafeExpressionError("Non-finite numbers are not allowed")
    return value


def _evaluate_node(node: ast.AST, *, depth: int = 0) -> float | int:
    if depth > 24:
        raise UnsafeExpressionError("Expression is too deeply nested")
    if isinstance(node, ast.Expression):
        return _evaluate_node(node.body, depth=depth + 1)
    if isinstance(node, ast.Constant):
        return _validate_number(node.value)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        return _UNARY_OPERATORS[type(node.op)](_evaluate_node(node.operand, depth=depth + 1))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _evaluate_node(node.left, depth=depth + 1)
        right = _evaluate_node(node.right, depth=depth + 1)
        if isinstance(node.op, ast.Pow):
            if abs(right) > 12 or abs(left) > 1_000_000:
                raise UnsafeExpressionError("Exponentiation exceeds the safety limit")
        try:
            result = _BINARY_OPERATORS[type(node.op)](left, right)
        except ZeroDivisionError as exc:
            raise UnsafeExpressionError("Division by zero") from exc
        result = _validate_number(result)
        if abs(result) > 1e100:
            raise UnsafeExpressionError("Result magnitude exceeds the safety limit")
        return result
    raise UnsafeExpressionError(f"Unsupported syntax: {type(node).__name__}")


def safe_calculate(expression: str) -> str:
    if not isinstance(expression, str) or not expression.strip():
        raise UnsafeExpressionError("Expression must be a non-empty string")
    if len(expression) > 256:
        raise UnsafeExpressionError("Expression is too long")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise UnsafeExpressionError("Invalid arithmetic syntax") from exc
    value = _evaluate_node(tree)
    if isinstance(value, int):
        return str(value)
    return format(value, ".15g")


@dataclass(frozen=True, slots=True)
class ToolExecution:
    execution_id: str
    tool_name: str
    arguments: dict[str, str]
    result: str | None
    error_type: str | None
    latency_seconds: float
    hostname: str
    process_id: int
    working_directory: str
    execution_location: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class CalculatorTool(Tool):
    name = "calculator"
    description = "Safely evaluate a basic arithmetic expression using local WSL execution."
    inputs = {
        "expression": {
            "type": "string",
            "description": "Arithmetic using numeric literals, parentheses, +, -, *, /, //, %, and **.",
        }
    }
    output_type = "string"

    def __init__(self) -> None:
        super().__init__()
        self.executions: list[ToolExecution] = []

    def forward(self, expression: str) -> str:
        started = time.perf_counter()
        result: str | None = None
        error_type: str | None = None
        try:
            result = safe_calculate(expression)
            return result
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            self.executions.append(
                ToolExecution(
                    execution_id=str(uuid.uuid4()),
                    tool_name=self.name,
                    arguments={"expression": expression},
                    result=result,
                    error_type=error_type,
                    latency_seconds=time.perf_counter() - started,
                    hostname=socket.gethostname(),
                    process_id=os.getpid(),
                    working_directory=str(Path.cwd()),
                    execution_location=(
                        "local_wsl" if "microsoft" in platform.release().lower() else "local_python"
                    ),
                )
            )

