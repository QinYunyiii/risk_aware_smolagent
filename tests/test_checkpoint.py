from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from risk_agent.benchmark import TaskEnvironment, load_tasks
from risk_agent.checkpoint import (
    CheckpointIntegrityError,
    CheckpointManager,
    CheckpointStateMismatchError,
    normalized_state_hash,
)
from risk_agent.monitoring import (
    BudgetState,
    ExecutionMonitor,
    JsonlTraceWriter,
    RuntimeState,
)
from risk_agent.tools.task_tool_registry import TaskToolRegistry


ROOT = Path(__file__).parents[1]
TASK_CONFIG = ROOT / "configs" / "tasks.yaml"


def api_task():
    return next(task for task in load_tasks(TASK_CONFIG) if task.task_id == "api_ship_order")


def runtime_for_task() -> RuntimeState:
    task = api_task()
    return RuntimeState(
        seed=task.seed,
        agent_memory=[{"role": "user", "content": task.prompt}],
        tool_state={"cursor": 1, "fault_state": {"calls": 2}},
        budget=BudgetState(max_tokens=100, max_tool_calls=10, max_latency_seconds=30.0),
    )


def test_execution_monitor_records_success_error_tokens_latency_and_redaction(tmp_path) -> None:
    task = api_task()
    runtime = runtime_for_task()
    trace = tmp_path / "trajectory.jsonl"
    monitor = ExecutionMonitor(
        writer=JsonlTraceWriter(trace),
        runtime_state=runtime,
        experiment_id="experiment_001",
        run_id="run_001",
        task_id=task.task_id,
        template_id=task.template_id,
        instance_id=task.instance_id,
        model_id="local",
        model_version="test",
        prompt_version="test",
        tool_version="test",
        worker_id="local-wsl",
        environment_id="environment_001",
    )
    assert monitor.execute_tool(
        step_id="step_001",
        tool_name="safe_tool",
        arguments={"value": 2, "api_key": "must-not-leak"},
        token_usage={"input_tokens": 3, "output_tokens": 2},
        checkpoint_id="checkpoint_001",
        call=lambda: {"answer": 4, "password": "also-secret"},
    ) == {"answer": 4, "password": "also-secret"}
    with pytest.raises(RuntimeError):
        monitor.execute_tool(
            step_id="step_002",
            tool_name="failing_tool",
            arguments={"value": 0},
            token_usage={"total_tokens": 7},
            checkpoint_id="checkpoint_001",
            call=lambda: (_ for _ in ()).throw(RuntimeError("expected")),
        )
    records = [json.loads(line) for line in trace.read_text().splitlines()]
    assert len(records) == 2
    assert records[0]["tool_arguments"]["api_key"] == "[REDACTED]"
    assert records[0]["tool_result"]["password"] == "[REDACTED]"
    assert "must-not-leak" not in trace.read_text()
    assert records[1]["error_type"] == "RuntimeError"
    assert all(record["latency_seconds"] >= 0 for record in records)
    assert runtime.budget.tokens_used == 12
    assert runtime.budget.tool_calls_used == 2
    assert len(runtime.tool_history) == 2


def test_execution_monitor_rejects_negative_tokens_before_tool_call(tmp_path) -> None:
    task = api_task()
    runtime = runtime_for_task()
    monitor = ExecutionMonitor(
        writer=JsonlTraceWriter(tmp_path / "trajectory.jsonl"),
        runtime_state=runtime,
        experiment_id="experiment_001",
        run_id="run_001",
        task_id=task.task_id,
        template_id=task.template_id,
        instance_id=task.instance_id,
        model_id="local",
        model_version="test",
        prompt_version="test",
        tool_version="test",
        worker_id="local-wsl",
        environment_id="environment_001",
    )
    called = False

    def tool() -> None:
        nonlocal called
        called = True

    with pytest.raises(ValueError):
        monitor.execute_tool(
            step_id="step_001",
            tool_name="tool",
            arguments={},
            token_usage={"total_tokens": -1},
            checkpoint_id=None,
            call=tool,
        )
    assert called is False
    assert runtime.tool_history == []


def test_checkpoint_round_trip_restores_files_database_memory_tools_and_budget(tmp_path) -> None:
    task = api_task()
    environment = TaskEnvironment(tmp_path / "environments", task)
    environment.reset()
    note = environment.resolve_path("notes/progress.txt", must_exist=False)
    note.parent.mkdir(parents=True)
    note.write_text("before\n", encoding="utf-8")
    registry = TaskToolRegistry(environment)
    arguments = {
        "order_id": "ord-100",
        "expected_status": "processing",
        "new_status": "shipped",
        "idempotency_key": "checkpoint-test",
    }
    registry.execute(task.domain, "update_order", arguments)
    runtime = runtime_for_task()
    runtime.agent_memory.append({"role": "tool", "content": "shipped"})
    runtime.budget.record(tokens=9, tool_calls=1, latency_seconds=0.25)
    manager = CheckpointManager(tmp_path / "checkpoints")
    expected_runtime = runtime.to_dict()
    snapshot = manager.snapshot(
        checkpoint_id="checkpoint_001", environment=environment, runtime_state=runtime
    )
    assert snapshot.manifest.state_hash == normalized_state_hash(environment, runtime, task)

    note.write_text("after\n", encoding="utf-8")
    environment.resolve_path("notes/extra.txt", must_exist=False).write_text("extra", encoding="utf-8")
    with environment.database() as connection:
        connection.execute("UPDATE orders SET status='cancelled', version=10")
        connection.commit()
    runtime.agent_memory.append({"role": "assistant", "content": "mutated"})
    runtime.budget.record(tokens=50, tool_calls=3, latency_seconds=2.0)
    assert normalized_state_hash(environment, runtime, task) != snapshot.manifest.state_hash

    restored = manager.restore(checkpoint_id="checkpoint_001", environment=environment)
    assert restored.restored_state_hash == snapshot.manifest.state_hash
    assert restored.runtime_state.to_dict() == expected_runtime
    assert environment.resolve_path("notes/progress.txt").read_text() == "before\n"
    assert not environment.resolve_path("notes/extra.txt", must_exist=False).exists()
    assert registry.execute(task.domain, "get_order", {"order_id": "ord-100"}) == {
        "order_id": "ord-100",
        "status": "shipped",
        "version": 2,
    }


def test_sqlite_backup_and_mock_api_export_are_consistent(tmp_path) -> None:
    task = api_task()
    environment = TaskEnvironment(tmp_path / "environments", task)
    environment.reset()
    manager = CheckpointManager(tmp_path / "checkpoints")
    snapshot = manager.snapshot(
        checkpoint_id="checkpoint_001",
        environment=environment,
        runtime_state=runtime_for_task(),
    )
    database = snapshot.checkpoint_path / "database" / "api.sqlite3"
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        orders = connection.execute(
            "SELECT order_id, status, version FROM orders ORDER BY order_id"
        ).fetchall()
    exported = json.loads(
        (snapshot.checkpoint_path / "mock_api_state.json").read_text(encoding="utf-8")
    )
    assert orders == [("ord-100", "processing", 1)]
    assert exported["orders"] == [
        {"order_id": "ord-100", "status": "processing", "version": 1}
    ]


def test_corrupt_checkpoint_is_rejected_before_workspace_changes(tmp_path) -> None:
    task = api_task()
    environment = TaskEnvironment(tmp_path / "environments", task)
    environment.reset()
    runtime = runtime_for_task()
    manager = CheckpointManager(tmp_path / "checkpoints")
    snapshot = manager.snapshot(
        checkpoint_id="checkpoint_001", environment=environment, runtime_state=runtime
    )
    with environment.database() as connection:
        connection.execute("UPDATE orders SET status='cancelled', version=9")
        connection.commit()
    before_restore_attempt = environment.state_hash()
    (snapshot.checkpoint_path / "runtime_state.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(CheckpointIntegrityError):
        manager.restore(checkpoint_id="checkpoint_001", environment=environment)
    assert environment.state_hash() == before_restore_attempt


def test_checkpoint_payload_symlink_is_rejected(tmp_path) -> None:
    task = api_task()
    environment = TaskEnvironment(tmp_path / "environments", task)
    environment.reset()
    manager = CheckpointManager(tmp_path / "checkpoints")
    snapshot = manager.snapshot(
        checkpoint_id="checkpoint_001",
        environment=environment,
        runtime_state=runtime_for_task(),
    )
    runtime_path = snapshot.checkpoint_path / "runtime_state.json"
    runtime_path.unlink()
    runtime_path.symlink_to(tmp_path / "outside.json")
    with pytest.raises(CheckpointIntegrityError):
        manager.restore(checkpoint_id="checkpoint_001", environment=environment)


def test_checkpoint_refuses_overwrite_seed_mismatch_and_wrong_task(tmp_path) -> None:
    task = api_task()
    environment = TaskEnvironment(tmp_path / "environments", task)
    environment.reset()
    manager = CheckpointManager(tmp_path / "checkpoints")
    runtime = runtime_for_task()
    manager.snapshot(checkpoint_id="checkpoint_001", environment=environment, runtime_state=runtime)
    with pytest.raises(FileExistsError):
        manager.snapshot(
            checkpoint_id="checkpoint_001", environment=environment, runtime_state=runtime
        )
    wrong_seed = runtime_for_task()
    wrong_seed.seed += 1
    with pytest.raises(CheckpointStateMismatchError):
        manager.snapshot(
            checkpoint_id="checkpoint_002", environment=environment, runtime_state=wrong_seed
        )
    other_task = load_tasks(TASK_CONFIG)[0]
    other_environment = TaskEnvironment(tmp_path / "other", other_task)
    other_environment.reset()
    with pytest.raises(CheckpointIntegrityError):
        manager.restore(checkpoint_id="checkpoint_001", environment=other_environment)
