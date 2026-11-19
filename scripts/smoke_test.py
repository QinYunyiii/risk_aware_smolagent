#!/usr/bin/env python3
"""Run ten real smolagents calculator tasks across the two remote workers."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from risk_agent.agent.agent_factory import run_calculator_agent
from risk_agent.agent.worker_router import RemoteModelSettings, WorkerRouter
from risk_agent.monitoring.trajectory import JsonlTraceWriter


TASKS = (
    ("12 + 30", "42"),
    ("9 * 7", "63"),
    ("100 / 4", "25"),
    ("2 ** 8", "256"),
    ("17 - 9", "8"),
    ("81 // 9", "9"),
    ("23 % 5", "3"),
    ("(6 + 4) * 3", "30"),
    ("7.5 + 2.25", "9.75"),
    ("144 / 12", "12"),
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/remote_model.yaml")
    parser.add_argument("--output", default="runs/day02/agent_trajectories.jsonl")
    parser.add_argument(
        "--task-indices",
        default="1,2,3,4,5,6,7,8,9,10",
        help="Comma-separated 1-based task indices; useful for append-only recovery runs.",
    )
    args = parser.parse_args()
    settings = RemoteModelSettings.from_yaml(args.config)
    router = WorkerRouter(settings.workers)
    writer = JsonlTraceWriter(args.output)
    records: list[dict[str, object]] = []

    explicit = ("worker-0", "worker-1", "worker-0", "worker-1")
    requested = {int(value) for value in args.task_indices.split(",") if value.strip()}
    if not requested or not requested.issubset(set(range(1, len(TASKS) + 1))):
        raise ValueError("--task-indices must contain values from 1 through 10")
    for index, (expression, expected) in enumerate(TASKS):
        if index + 1 not in requested:
            continue
        if index < len(explicit):
            worker = router.select(explicit[index])
            route_mode = "explicit"
        else:
            worker = router.select()
            route_mode = "round_robin"
        record: dict[str, object] | None = None
        for attempt in (1, 2, 3):
            candidate = run_calculator_agent(settings, worker, expression, expected)
            candidate["route_mode"] = route_mode
            candidate["task_index"] = index + 1
            candidate["attempt_count"] = attempt
            writer.append(candidate)
            record = candidate
            has_tool_event = bool(candidate["tool_executions"])
            transient_before_tool = (
                not has_tool_event
                and candidate.get("error_type")
                in {"model_api_timeout", "model_api_unavailable"}
            )
            if not transient_before_tool or attempt == 3:
                break
            print(
                f"task={index + 1} worker={worker.worker_id} "
                f"pre-tool transport failure; safe retry={attempt + 1}/3",
                flush=True,
            )
            time.sleep(1)
        assert record is not None
        records.append(record)
        print(
            f"task={index + 1} worker={worker.worker_id} route={route_mode} "
            f"tool_success={record['tool_success']} state={record['agent_state']}",
            flush=True,
        )

    current_per_worker = {
        worker.worker_id: sum(record["worker_id"] == worker.worker_id for record in records)
        for worker in settings.workers
    }
    all_records = [
        json.loads(line)
        for line in Path(args.output).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    all_agent_records = [
        record for record in all_records if record.get("task_kind") == "day2_calculator_smoke"
    ]
    tool_events = [
        (record, event)
        for record in all_agent_records
        for event in record.get("tool_executions", [])
    ]
    tool_calls = len(tool_events)
    tool_successes = sum(
        event.get("error_type") is None and event.get("result") == record.get("expected_result")
        for record, event in tool_events
    )
    complete_agent_successes = sum(
        record.get("agent_state") == "success" and bool(record.get("tool_success"))
        for record in all_agent_records
    )
    token_records = sum(
        record.get("token_usage") is not None
        for record in all_agent_records
        if record.get("tool_executions")
    )
    local_calls = sum(
        event.get("execution_location") == "local_wsl" for _, event in tool_events
    )
    successful_route_modes = {
        record.get("route_mode")
        for record in all_agent_records
        if record.get("agent_state") == "success" and record.get("tool_success")
    }
    passed = (
        tool_calls >= 10
        and tool_successes >= 9
        and complete_agent_successes >= 9
        and token_records >= 9
        and local_calls == tool_calls
        and successful_route_modes == {"explicit", "round_robin"}
    )
    summary = {
        "passed": passed,
        "current_run_per_worker": current_per_worker,
        "aggregate_tool_calls": tool_calls,
        "aggregate_tool_successes": tool_successes,
        "aggregate_complete_agent_successes": complete_agent_successes,
        "aggregate_tool_records_with_tokens": token_records,
        "aggregate_local_wsl_tool_calls": local_calls,
        "successful_route_modes": sorted(successful_route_modes),
        "trajectory": str(Path(args.output).resolve()),
    }
    summary_path = Path(args.output).with_name("smoke_summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
