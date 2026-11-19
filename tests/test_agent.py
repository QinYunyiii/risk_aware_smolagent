from __future__ import annotations

import json
import ssl

import pytest

from risk_agent.agent.agent_factory import ModelErrorCategory, classify_model_exception
from risk_agent.agent.worker_router import (
    ConfigurationError,
    UnsafeWorkerSwitchError,
    WorkerConfig,
    WorkerRouter,
)
from risk_agent.monitoring.trajectory import JsonlTraceWriter
from risk_agent.tools.calculation_tools import CalculatorTool, UnsafeExpressionError, safe_calculate


@pytest.mark.parametrize(
    ("expression", "expected"),
    [("2 + 3 * 4", "14"), ("(10 - 4) / 3", "2"), ("2 ** 8", "256")],
)
def test_safe_calculate(expression: str, expected: str) -> None:
    assert safe_calculate(expression) == expected


@pytest.mark.parametrize("expression", ["__import__('os')", "1 / 0", "2 ** 99", "[1, 2]"])
def test_safe_calculate_rejects_unsafe_input(expression: str) -> None:
    with pytest.raises(UnsafeExpressionError):
        safe_calculate(expression)


def test_calculator_records_local_execution() -> None:
    tool = CalculatorTool()
    assert tool.forward("6 * 7") == "42"
    event = tool.executions[0]
    assert event.arguments == {"expression": "6 * 7"}
    assert event.result == "42"
    assert event.execution_location in {"local_wsl", "local_python"}
    assert event.latency_seconds >= 0


def test_router_explicit_round_robin_and_safe_failover() -> None:
    workers = (
        WorkerConfig("worker-0", "https://localhost:8000/v1"),
        WorkerConfig("worker-1", "https://localhost:8001/v1"),
    )
    router = WorkerRouter(workers)
    assert router.select("worker-1").worker_id == "worker-1"
    assert [router.select().worker_id for _ in range(4)] == [
        "worker-0", "worker-1", "worker-0", "worker-1"
    ]
    assert router.failover("worker-0", tool_side_effects_committed=False).worker_id == "worker-1"
    with pytest.raises(UnsafeWorkerSwitchError):
        router.failover("worker-0", tool_side_effects_committed=True)


def test_worker_requires_https() -> None:
    with pytest.raises(ConfigurationError):
        WorkerConfig("unsafe", "http://localhost:8000/v1")


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (TimeoutError("slow"), ModelErrorCategory.TIMEOUT),
        (ssl.SSLCertVerificationError("bad certificate"), ModelErrorCategory.TLS),
        (ConnectionError("refused"), ModelErrorCategory.UNAVAILABLE),
        (RuntimeError("unexpected"), ModelErrorCategory.UNKNOWN),
    ],
)
def test_model_error_classification(error: Exception, category: ModelErrorCategory) -> None:
    assert classify_model_exception(error) == category


def test_jsonl_trace_writer_appends(tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    writer = JsonlTraceWriter(path)
    writer.append({"run_id": "one"})
    writer.append({"run_id": "two"})
    assert [json.loads(line)["run_id"] for line in path.read_text().splitlines()] == ["one", "two"]
