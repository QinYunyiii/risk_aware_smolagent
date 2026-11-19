"""Explicit local smoke fixtures for validating the Day 6 branch protocol."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from risk_agent.benchmark import TaskEnvironment
from risk_agent.benchmark.schema import TaskSpec
from risk_agent.checkpoint import CheckpointManager, SnapshotResult
from risk_agent.monitoring import BudgetState, RuntimeState, ToolHistoryEntry


def build_day6_smoke_runtime(task: TaskSpec, checkpoint_number: int) -> RuntimeState:
    if checkpoint_number < 1:
        raise ValueError("checkpoint_number must be positive")
    arguments = {
        "order_id": "ord-100",
        "expected_status": "processing",
        "new_status": "shipped",
        "idempotency_key": f"day6-checkpoint-{checkpoint_number:03d}-update",
    }
    arguments_hash = hashlib.sha256(
        json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    checkpoint_id = f"checkpoint_{checkpoint_number:03d}"
    failure = {
        "event_id": f"failure_{checkpoint_number:03d}",
        "fault_id": "day6_known_uncommitted_partial_failure",
        "fault_type": "F6_PARTIAL_FAILURE",
        "commit_status": "not_committed",
        "response_received": False,
        "phase": "after_execution",
        "call_index": 1,
        "error_type": "PartialFailureError",
        "arguments_hash": arguments_hash,
        "timestamp": f"2026-01-01T00:00:{checkpoint_number:02d}+00:00",
    }
    return RuntimeState(
        seed=task.seed,
        agent_memory=[
            {"role": "system", "content": "Day 6 local branch-protocol smoke fixture."},
            {"role": "user", "content": task.prompt},
            {"role": "tool", "content": "Known failure: write did not commit."},
            {"role": "checkpoint", "checkpoint_number": checkpoint_number},
        ],
        tool_history=[
            ToolHistoryEntry(
                step_id=f"pre_checkpoint_failure_{checkpoint_number:03d}",
                tool_name="update_order",
                tool_arguments=arguments,
                tool_result=None,
                error_type="PartialFailureError",
                timestamp=str(failure["timestamp"]),
                token_usage={"total_tokens": 0},
                latency_seconds=0.0,
                worker_id="local-wsl",
                environment_id=f"day6-source-{checkpoint_number:03d}",
                checkpoint_id=checkpoint_id,
            )
        ],
        tool_state={
            "pending_operation": {
                "tool_name": "update_order",
                "arguments": arguments,
                "read_only": False,
            },
            "replan_observation": {
                "tool_name": "get_order",
                "arguments": {"order_id": "ord-100"},
            },
            "goal": {"order_id": "ord-100", "target_status": "shipped"},
            "last_failure": failure,
        },
        budget=BudgetState(
            max_tokens=1_000,
            max_tool_calls=20,
            max_latency_seconds=60.0,
            tokens_used=0,
            tool_calls_used=1,
            latency_seconds_used=0.0,
        ),
    )


def create_day6_smoke_checkpoints(
    *,
    task: TaskSpec,
    manager: CheckpointManager,
    source_root: str | Path,
    count: int = 5,
) -> tuple[SnapshotResult, ...]:
    if count < 1:
        raise ValueError("count must be positive")
    root = Path(source_root)
    snapshots: list[SnapshotResult] = []
    for number in range(1, count + 1):
        checkpoint_id = f"checkpoint_{number:03d}"
        environment = TaskEnvironment(root / checkpoint_id, task)
        environment.reset()
        marker = environment.resolve_path("notes/checkpoint_marker.txt", must_exist=False)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"day6 smoke checkpoint {number}\n", encoding="utf-8")
        snapshots.append(
            manager.snapshot(
                checkpoint_id=checkpoint_id,
                environment=environment,
                runtime_state=build_day6_smoke_runtime(task, number),
            )
        )
    return tuple(snapshots)
