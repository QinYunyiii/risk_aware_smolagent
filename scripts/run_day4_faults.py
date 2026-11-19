#!/usr/bin/env python3
"""Exercise F0--F6 locally and record deterministic safety evidence."""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from risk_agent.benchmark import TaskEnvironment, load_tasks
from risk_agent.benchmark.schema import validate_identifier
from risk_agent.faults import (
    Action,
    CommitStatus,
    FaultInjector,
    FaultType,
    InjectedFaultError,
    RetryGuard,
    RetrySafetyEvaluator,
    UnsafeRetryError,
    describe_operation,
    load_fault_configs,
)
from risk_agent.monitoring.trajectory import JsonlTraceWriter
from risk_agent.tools.task_tool_registry import TaskToolRegistry


def default_run_id(started_at: datetime) -> str:
    return f"day4_{started_at.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def scenario_for(fault_type: FaultType) -> tuple[str, str, dict[str, Any]]:
    scenarios = {
        FaultType.F0_NO_FAULT: (
            "logic_multistep_calculation",
            "calculator",
            {"expression": "2 + 3"},
        ),
        FaultType.F1_TIMEOUT: (
            "api_inventory_widget",
            "query_inventory",
            {"sku": "widget"},
        ),
        FaultType.F2_API_503: (
            "api_inventory_widget",
            "query_inventory",
            {"sku": "widget"},
        ),
        FaultType.F3_INVALID_ARGUMENT: (
            "logic_multistep_calculation",
            "calculator",
            {"expression": "2 + 3"},
        ),
        FaultType.F4_STALE_OBSERVATION: (
            "api_inventory_widget",
            "query_inventory",
            {"sku": "widget"},
        ),
        FaultType.F5_REPEATED_ACTION: (
            "logic_multistep_calculation",
            "calculator",
            {"expression": "2 + 3"},
        ),
        FaultType.F6_PARTIAL_FAILURE: (
            "api_ship_order",
            "update_order",
            {
                "order_id": "ord-100",
                "expected_status": "processing",
                "new_status": "shipped",
                "idempotency_key": "day4-f6-step-1",
            },
        ),
    }
    return scenarios[fault_type]


def expected_pass(
    fault_type: FaultType,
    result: Any,
    exception_type: str | None,
    event_triggered: bool,
    retry_allowed: bool,
    guard_blocked: bool,
    state_evidence: dict[str, Any] | None,
) -> bool:
    if fault_type == FaultType.F0_NO_FAULT:
        return result == "5" and exception_type is None and not event_triggered
    if fault_type == FaultType.F1_TIMEOUT:
        return exception_type == "InjectedTimeoutError" and event_triggered and retry_allowed
    if fault_type == FaultType.F2_API_503:
        return (
            exception_type == "InjectedServiceUnavailableError"
            and event_triggered
            and retry_allowed
        )
    if fault_type == FaultType.F3_INVALID_ARGUMENT:
        return exception_type == "InjectedInvalidArgumentError" and guard_blocked
    if fault_type == FaultType.F4_STALE_OBSERVATION:
        return result == {"sku": "widget", "quantity": 99} and guard_blocked
    if fault_type == FaultType.F5_REPEATED_ACTION:
        return result == "5" and guard_blocked
    if fault_type == FaultType.F6_PARTIAL_FAILURE:
        return (
            exception_type == "PartialFailureError"
            and guard_blocked
            and state_evidence == {"order_id": "ord-100", "status": "shipped", "version": 2}
        )
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="configs/tasks.yaml")
    parser.add_argument("--faults", default="configs/faults.yaml")
    parser.add_argument("--output-root", default="runs/day04")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()
    started_at = datetime.now(timezone.utc)
    run_id = validate_identifier(args.run_id or default_run_id(started_at), "run_id")
    run_directory = Path(args.output_root) / run_id
    run_directory.mkdir(parents=True, exist_ok=False)
    trace_writer = JsonlTraceWriter(run_directory / "fault_events.jsonl")
    tasks = {task.task_id: task for task in load_tasks(args.tasks)}
    configs = load_fault_configs(args.faults)
    evaluator = RetrySafetyEvaluator()
    guard = RetryGuard(evaluator)
    results: list[dict[str, object]] = []

    for config in configs:
        task_id, tool_name, arguments = scenario_for(config.fault_type)
        task = tasks[task_id]
        environment = TaskEnvironment(run_directory / "environments", task)
        environment.reset()
        registry = TaskToolRegistry(environment)
        operation = describe_operation(tool_name, arguments)
        injector = FaultInjector(config, trace_writer=trace_writer)
        result: Any = None
        exception_type: str | None = None
        if config.fault_type == FaultType.F5_REPEATED_ACTION:
            injector.execute(
                operation,
                arguments,
                lambda: registry.execute(task.domain, tool_name, arguments),
            )
        try:
            result = injector.execute(
                operation,
                arguments,
                lambda: registry.execute(task.domain, tool_name, arguments),
            )
        except InjectedFaultError as exc:
            exception_type = type(exc).__name__
        event = injector.events[-1]
        decision = evaluator.evaluate(event, operation)
        guard_blocked = False
        try:
            guard.require_safe(event, operation)
        except UnsafeRetryError:
            guard_blocked = True
        state_evidence: dict[str, Any] | None = None
        if config.fault_type == FaultType.F6_PARTIAL_FAILURE:
            state_evidence = registry.execute(
                task.domain, "get_order", {"order_id": "ord-100"}
            )

        replay = FaultInjector(config)
        if config.fault_type == FaultType.F5_REPEATED_ACTION:
            replay.execute(operation, arguments, lambda: "replay-probe")
        try:
            replay.execute(operation, arguments, lambda: "replay-probe")
        except InjectedFaultError:
            pass
        reproducible = (
            event.reproducibility_signature() == replay.events[-1].reproducibility_signature()
        )
        passed = reproducible and expected_pass(
            config.fault_type,
            result,
            exception_type,
            event.triggered,
            decision.retry_allowed,
            guard_blocked,
            state_evidence,
        )
        results.append(
            {
                "fault_id": config.fault_id,
                "fault_type": config.fault_type.value,
                "seed": config.seed,
                "task_id": task_id,
                "tool_name": tool_name,
                "operation": {
                    "read_only": operation.read_only,
                    "idempotency_key_present": operation.idempotency_key_present,
                },
                "passed": passed,
                "result": result,
                "exception_type": exception_type,
                "event": event.to_dict(),
                "safety_decision": decision.to_dict(),
                "retry_guard_blocked": guard_blocked,
                "state_evidence": state_evidence,
                "same_seed_reproducible": reproducible,
            }
        )

    summary = {
        "experiment_id": "day4_fault_smoke_local",
        "run_id": run_id,
        "created_at": started_at.isoformat(),
        "fault_types": [fault_type.value for fault_type in FaultType],
        "scenario_count": len(results),
        "passed": sum(bool(result["passed"]) for result in results),
        "failed": sum(not bool(result["passed"]) for result in results),
        "results": results,
    }
    output = run_directory / "fault_results.json"
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if len(results) == 7 and summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
