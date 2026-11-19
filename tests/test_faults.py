from __future__ import annotations

import json
from pathlib import Path

import pytest

from risk_agent.benchmark import TaskEnvironment, load_tasks
from risk_agent.faults import (
    CommitStatus,
    FaultConfig,
    FaultInjector,
    FaultType,
    InjectedFaultError,
    PartialFailureError,
    RetryGuard,
    RetrySafetyEvaluator,
    UnsafeRetryError,
    describe_operation,
    load_fault_configs,
)
from risk_agent.monitoring.trajectory import JsonlTraceWriter
from risk_agent.tools.task_tool_registry import TaskToolRegistry


ROOT = Path(__file__).parents[1]
TASK_CONFIG = ROOT / "configs" / "tasks.yaml"
FAULT_CONFIG = ROOT / "configs" / "faults.yaml"


def config_for(fault_type: FaultType) -> FaultConfig:
    return next(config for config in load_fault_configs(FAULT_CONFIG) if config.fault_type == fault_type)


def test_loads_exactly_f0_through_f6() -> None:
    configs = load_fault_configs(FAULT_CONFIG)
    assert len(configs) == 7
    assert {config.fault_type for config in configs} == set(FaultType)
    assert len({config.seed for config in configs}) == 7


def test_repeated_action_profile_cannot_trigger_on_first_call() -> None:
    with pytest.raises(ValueError):
        FaultConfig(
            fault_id="bad_repeat",
            fault_type=FaultType.F5_REPEATED_ACTION,
            seed=1,
            target_tool="calculator",
            call_indices=(1,),
            probability=0.0,
            parameters={},
        )


def test_probability_trigger_sequence_is_reproducible() -> None:
    config = FaultConfig(
        fault_id="seeded_timeout",
        fault_type=FaultType.F1_TIMEOUT,
        seed=99117,
        target_tool="calculator",
        call_indices=(),
        probability=0.5,
        parameters={"commit_status": "not_started"},
    )
    operation = describe_operation("calculator", {"expression": "1 + 1"})

    def sequence() -> list[bool]:
        injector = FaultInjector(config)
        for _ in range(24):
            try:
                injector.execute(operation, {"expression": "1 + 1"}, lambda: "2")
            except InjectedFaultError:
                pass
        return [event.triggered for event in injector.events]

    first = sequence()
    second = sequence()
    assert first == second
    assert any(first) and not all(first)


def test_call_index_profile_triggers_only_configured_calls() -> None:
    config = FaultConfig(
        fault_id="indexed_503",
        fault_type=FaultType.F2_API_503,
        seed=7,
        target_tool="query_inventory",
        call_indices=(2, 4),
        probability=0.0,
        parameters={},
    )
    operation = describe_operation("query_inventory", {"sku": "widget"})
    injector = FaultInjector(config)
    for _ in range(5):
        try:
            injector.execute(operation, {"sku": "widget"}, lambda: {"quantity": 12})
        except InjectedFaultError:
            pass
    assert [event.triggered for event in injector.events] == [False, True, False, True, False]


@pytest.mark.parametrize(
    ("fault_type", "expected_result", "retry_allowed"),
    [
        (FaultType.F0_NO_FAULT, "real", False),
        (FaultType.F4_STALE_OBSERVATION, {"sku": "widget", "quantity": 99}, False),
        (FaultType.F5_REPEATED_ACTION, "5", False),
    ],
)
def test_result_faults_and_no_fault(
    fault_type: FaultType, expected_result: object, retry_allowed: bool
) -> None:
    config = config_for(fault_type)
    tool_name = config.target_tool
    arguments = {"sku": "widget"} if tool_name == "query_inventory" else {"expression": "2+3"}
    operation = describe_operation(tool_name, arguments)
    injector = FaultInjector(config)
    if fault_type == FaultType.F5_REPEATED_ACTION:
        assert injector.execute(operation, arguments, lambda: "5") == "5"
    result = injector.execute(operation, arguments, lambda: "real")
    assert result == expected_result
    decision = RetrySafetyEvaluator().evaluate(injector.events[-1], operation)
    assert decision.retry_allowed is retry_allowed


def test_committed_partial_failure_changes_state_and_blocks_retry(tmp_path) -> None:
    task = next(task for task in load_tasks(TASK_CONFIG) if task.task_id == "api_ship_order")
    environment = TaskEnvironment(tmp_path, task)
    environment.reset()
    registry = TaskToolRegistry(environment)
    arguments = {
        "order_id": "ord-100",
        "expected_status": "processing",
        "new_status": "shipped",
        "idempotency_key": "partial-key",
    }
    operation = describe_operation("update_order", arguments)
    injector = FaultInjector(config_for(FaultType.F6_PARTIAL_FAILURE))
    with pytest.raises(PartialFailureError) as captured:
        injector.execute(
            operation,
            arguments,
            lambda: registry.execute(task.domain, "update_order", arguments),
        )
    event = captured.value.event
    assert event.commit_status == CommitStatus.COMMITTED
    assert event.response_received is False
    assert registry.execute(task.domain, "get_order", {"order_id": "ord-100"}) == {
        "order_id": "ord-100",
        "status": "shipped",
        "version": 2,
    }
    with pytest.raises(UnsafeRetryError):
        RetryGuard().require_safe(event, operation)


def test_known_uncommitted_partial_failure_allows_retry() -> None:
    config = FaultConfig(
        fault_id="partial_before_commit",
        fault_type=FaultType.F6_PARTIAL_FAILURE,
        seed=5,
        target_tool="update_order",
        call_indices=(1,),
        probability=0.0,
        parameters={"commit": False},
    )
    arguments = {"idempotency_key": "key"}
    operation = describe_operation("update_order", arguments)
    called = False

    def write() -> None:
        nonlocal called
        called = True

    injector = FaultInjector(config)
    with pytest.raises(PartialFailureError) as captured:
        injector.execute(operation, arguments, write)
    assert called is False
    assert captured.value.event.commit_status == CommitStatus.NOT_COMMITTED
    assert RetryGuard().require_safe(captured.value.event, operation).retry_allowed is True


def test_invalid_argument_retry_is_blocked() -> None:
    config = config_for(FaultType.F3_INVALID_ARGUMENT)
    arguments = {"expression": "2+3"}
    operation = describe_operation("calculator", arguments)
    injector = FaultInjector(config)
    with pytest.raises(InjectedFaultError) as captured:
        injector.execute(operation, arguments, lambda: "5")
    with pytest.raises(UnsafeRetryError):
        RetryGuard().require_safe(captured.value.event, operation)


def test_fault_log_is_append_only_jsonl(tmp_path) -> None:
    path = tmp_path / "faults.jsonl"
    writer = JsonlTraceWriter(path)
    config = config_for(FaultType.F0_NO_FAULT)
    operation = describe_operation("calculator", {"expression": "1+1"})
    for _ in range(2):
        FaultInjector(config, trace_writer=writer).execute(
            operation, {"expression": "1+1"}, lambda: "2"
        )
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(records) == 2
    assert all(record["record_type"] == "fault_event" for record in records)
    assert all("seed" in record and "commit_status" in record for record in records)
