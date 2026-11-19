#!/usr/bin/env python3
"""Run a local Day 5 trajectory plus checkpoint/restore round trip."""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from risk_agent.benchmark import TaskEnvironment, load_tasks
from risk_agent.benchmark.schema import validate_identifier
from risk_agent.checkpoint import CheckpointManager, normalized_state_hash
from risk_agent.monitoring import BudgetState, ExecutionMonitor, JsonlTraceWriter, RuntimeState
from risk_agent.tools.task_tool_registry import TaskToolRegistry


def default_run_id(started_at: datetime) -> str:
    return f"day5_{started_at.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="configs/tasks.yaml")
    parser.add_argument("--output-root", default="runs/day05")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()
    started_at = datetime.now(timezone.utc)
    run_id = validate_identifier(args.run_id or default_run_id(started_at), "run_id")
    run_directory = Path(args.output_root) / run_id
    run_directory.mkdir(parents=True, exist_ok=False)
    task = next(task for task in load_tasks(args.tasks) if task.task_id == "api_ship_order")
    environment = TaskEnvironment(run_directory / "environments", task)
    environment.reset()
    with environment.database() as connection:
        connection.execute("INSERT INTO inventory(sku, quantity) VALUES ('widget', 12)")
        connection.commit()
    progress = environment.resolve_path("notes/progress.txt", must_exist=False)
    progress.parent.mkdir(parents=True, exist_ok=True)
    progress.write_text("checkpoint-ready\n", encoding="utf-8")

    runtime = RuntimeState(
        seed=task.seed,
        agent_memory=[
            {"role": "system", "content": "Day 5 local checkpoint smoke."},
            {"role": "user", "content": task.prompt},
        ],
        tool_state={"last_observation": None, "fault_call_counts": {"update_order": 0}},
        budget=BudgetState(max_tokens=1_000, max_tool_calls=20, max_latency_seconds=60.0),
    )
    trajectory = run_directory / "trajectory.jsonl"
    monitor = ExecutionMonitor(
        writer=JsonlTraceWriter(trajectory),
        runtime_state=runtime,
        experiment_id="day5_checkpoint_smoke_local",
        run_id=run_id,
        task_id=task.task_id,
        template_id=task.template_id,
        instance_id=task.instance_id,
        model_id="local-reference-runner",
        model_version="day5",
        prompt_version="day5-v1",
        tool_version="day5-v1",
        worker_id="local-wsl",
        environment_id=f"{run_id}:{task.task_id}",
    )
    registry = TaskToolRegistry(environment)
    first_result = monitor.execute_tool(
        step_id="step_001",
        tool_name="get_order",
        arguments={"order_id": "ord-100"},
        token_usage={"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
        checkpoint_id="checkpoint_001",
        call=lambda: registry.execute(task.domain, "get_order", {"order_id": "ord-100"}),
    )
    update_arguments = {
        "order_id": "ord-100",
        "expected_status": "processing",
        "new_status": "shipped",
        "idempotency_key": "day5-checkpoint-update",
    }
    second_result = monitor.execute_tool(
        step_id="step_002",
        tool_name="update_order",
        arguments=update_arguments,
        token_usage={"input_tokens": 12, "output_tokens": 5, "total_tokens": 17},
        checkpoint_id="checkpoint_001",
        call=lambda: registry.execute(task.domain, "update_order", update_arguments),
    )
    runtime.agent_memory.append({"role": "tool", "name": "update_order", "content": second_result})
    runtime.tool_state = {
        "last_observation": second_result,
        "fault_call_counts": {"update_order": 1},
    }

    manager = CheckpointManager(run_directory / "checkpoints")
    snapshot_runtime = runtime.to_dict()
    snapshot = manager.snapshot(
        checkpoint_id="checkpoint_001", environment=environment, runtime_state=runtime
    )

    progress.write_text("mutated-after-checkpoint\n", encoding="utf-8")
    extra = environment.resolve_path("notes/after.txt", must_exist=False)
    extra.write_text("must disappear after restore\n", encoding="utf-8")
    with environment.database() as connection:
        connection.execute(
            "UPDATE orders SET status = 'cancelled', version = 99 WHERE order_id = 'ord-100'"
        )
        connection.execute("UPDATE inventory SET quantity = 0")
        connection.commit()
    runtime.agent_memory.append({"role": "assistant", "content": "post-checkpoint mutation"})
    runtime.tool_state["fault_call_counts"] = {"update_order": 99}
    runtime.budget.record(tokens=500, tool_calls=5, latency_seconds=10.0)
    mutated_hash = normalized_state_hash(environment, runtime, task)

    restored = manager.restore(checkpoint_id="checkpoint_001", environment=environment)
    restored_order = registry.execute(task.domain, "get_order", {"order_id": "ord-100"})
    restored_inventory = registry.execute(task.domain, "query_inventory", {"sku": "widget"})
    trajectory_records = [
        json.loads(line) for line in trajectory.read_text(encoding="utf-8").splitlines()
    ]
    database_backup = snapshot.checkpoint_path / "database" / "api.sqlite3"
    with sqlite3.connect(database_backup) as connection:
        sqlite_integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]

    checks = {
        "initial_query_correct": first_result == {"order_id": "ord-100", "status": "processing", "version": 1},
        "write_result_correct": second_result == {
            "order_id": "ord-100",
            "status": "shipped",
            "version": 2,
            "idempotent_replay": False,
        },
        "mutation_changed_hash": mutated_hash != snapshot.manifest.state_hash,
        "restored_hash_matches": restored.restored_state_hash == snapshot.manifest.state_hash,
        "agent_memory_restored": restored.runtime_state.to_dict() == snapshot_runtime,
        "file_restored": environment.resolve_path("notes/progress.txt").read_text(encoding="utf-8") == "checkpoint-ready\n",
        "post_checkpoint_file_removed": not environment.resolve_path("notes/after.txt", must_exist=False).exists(),
        "sqlite_order_restored": restored_order == {"order_id": "ord-100", "status": "shipped", "version": 2},
        "mock_api_inventory_restored": restored_inventory == {"sku": "widget", "quantity": 12},
        "sqlite_backup_integrity": sqlite_integrity == "ok",
        "trajectory_complete": len(trajectory_records) == 2
        and all(
            set(("tool_name", "tool_arguments", "tool_result", "token_usage", "latency_seconds", "checkpoint_id"))
            <= set(record)
            for record in trajectory_records
        ),
    }
    summary = {
        "experiment_id": "day5_checkpoint_smoke_local",
        "run_id": run_id,
        "created_at": started_at.isoformat(),
        "checkpoint_id": snapshot.manifest.checkpoint_id,
        "checkpoint_path": str(snapshot.checkpoint_path.resolve()),
        "trajectory_path": str(trajectory.resolve()),
        "snapshot_state_hash": snapshot.manifest.state_hash,
        "mutated_state_hash": mutated_hash,
        "restored_state_hash": restored.restored_state_hash,
        "checks": checks,
        "passed": all(checks.values()),
    }
    output = run_directory / "checkpoint_results.json"
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
