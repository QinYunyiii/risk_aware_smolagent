from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from smolagents import ToolCallingAgent
from smolagents.memory import ActionStep
from smolagents.models import ChatMessage, MessageRole

from risk_agent.agent import AgentStateAdapter
from risk_agent.benchmark import TaskEnvironment
from risk_agent.checkpoint import CheckpointManager
from risk_agent.monitoring import ToolHistoryEntry
from risk_agent.tools.calculation_tools import CalculatorTool
from tests import test_checkpoint_v2 as fixtures


def _file_inventory(root: Path) -> dict[str, dict[str, Any]]:
    return {
        path.relative_to(root).as_posix(): {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _workspace_bytes(environment: TaskEnvironment) -> dict[str, bytes]:
    return {
        path.relative_to(environment.workspace).as_posix(): path.read_bytes()
        for path in sorted(environment.workspace.rglob("*"))
        if path.is_file() and path != environment.database_path
    }


def _sqlite_state(environment: TaskEnvironment) -> tuple[str, ...]:
    with sqlite3.connect(environment.database_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        return tuple(connection.iterdump())


def _build_agent(snapshot) -> ToolCallingAgent:
    agent = ToolCallingAgent(
        tools=[CalculatorTool()],
        model=fixtures.StubModel(),
        max_steps=10,
        verbosity_level=0,
        name="v2_checkpoint_agent",
        description="Synthetic local Day 2 fixture",
    )
    return AgentStateAdapter.restore(agent, snapshot)


@dataclass(slots=True)
class IsolationFixture:
    manager: CheckpointManager
    checkpoint_path: Path
    manifest: Any
    before_inventory: dict[str, dict[str, Any]]
    environment_a: TaskEnvironment
    environment_b: TaskEnvironment
    restored_a: Any
    restored_b: Any
    agent_a: ToolCallingAgent
    agent_b: ToolCallingAgent


@pytest.fixture
def isolated_restores(tmp_path: Path) -> IsolationFixture:
    source = fixtures.prepared_environment(tmp_path / "source")
    removable = source.resolve_path("notes/delete_me.txt", must_exist=False)
    removable.write_text("delete independently\n", encoding="utf-8")
    manager = CheckpointManager(tmp_path / "checkpoints")
    snapshot = manager.snapshot_v2(
        checkpoint_id="day4_isolation_checkpoint",
        environment=source,
        runtime_state=fixtures.make_runtime(source.task),
        agent_state_snapshot=fixtures.make_agent_snapshot(),
        identity=fixtures.identity(),
        runtime_metadata=fixtures.runtime_metadata(),
        environment_metadata=fixtures.environment_metadata(),
        capture_invariants=fixtures.invariants(),
    )
    before = _file_inventory(snapshot.checkpoint_path)

    environment_a = TaskEnvironment(tmp_path / "branches" / "a", source.task)
    environment_b = TaskEnvironment(tmp_path / "branches" / "b", source.task)
    environment_a.reset()
    environment_b.reset()
    restored_a = manager.restore_v2(
        checkpoint_id="day4_isolation_checkpoint", environment=environment_a
    )
    restored_b = manager.restore_v2(
        checkpoint_id="day4_isolation_checkpoint", environment=environment_b
    )
    return IsolationFixture(
        manager=manager,
        checkpoint_path=snapshot.checkpoint_path,
        manifest=snapshot.manifest,
        before_inventory=before,
        environment_a=environment_a,
        environment_b=environment_b,
        restored_a=restored_a,
        restored_b=restored_b,
        agent_a=_build_agent(restored_a.agent_state_snapshot),
        agent_b=_build_agent(restored_b.agent_state_snapshot),
    )


def test_two_independent_restores_have_identical_component_hashes(
    isolated_restores: IsolationFixture,
) -> None:
    item = isolated_restores
    fields = (
        "restored_agent_state_hash",
        "restored_next_model_input_hash",
        "restored_environment_hash",
        "restored_runtime_hash",
        "restored_combined_state_hash",
    )
    for field in fields:
        assert getattr(item.restored_a, field) == getattr(item.restored_b, field)
    assert (
        item.restored_a.restored_combined_state_hash
        == item.manifest.hashes["combined_state_hash"]
    )


def test_two_restores_have_distinct_workspace_and_sqlite_files(
    isolated_restores: IsolationFixture,
) -> None:
    a = isolated_restores.environment_a
    b = isolated_restores.environment_b
    assert a.workspace.resolve() != b.workspace.resolve()
    assert a.database_path.resolve() != b.database_path.resolve()
    assert not os.path.samefile(a.workspace, b.workspace)
    assert not os.path.samefile(a.database_path, b.database_path)
    assert (a.database_path.stat().st_dev, a.database_path.stat().st_ino) != (
        b.database_path.stat().st_dev,
        b.database_path.stat().st_ino,
    )
    assert _workspace_bytes(a) == _workspace_bytes(b)
    assert _sqlite_state(a) == _sqlite_state(b)


def test_runtime_and_agent_state_objects_have_no_mutable_aliasing(
    isolated_restores: IsolationFixture,
) -> None:
    a = isolated_restores.restored_a
    b = isolated_restores.restored_b
    assert a.runtime_state is not b.runtime_state
    assert a.runtime_state.budget is not b.runtime_state.budget
    assert a.runtime_state.agent_memory is not b.runtime_state.agent_memory
    assert a.runtime_state.tool_history is not b.runtime_state.tool_history
    assert a.runtime_state.tool_state is not b.runtime_state.tool_state
    assert a.agent_state_snapshot is not b.agent_state_snapshot
    assert a.agent_state_snapshot.payload is not b.agent_state_snapshot.payload
    assert a.agent_state_snapshot.payload["memory"]["steps"] is not (
        b.agent_state_snapshot.payload["memory"]["steps"]
    )
    assert a.agent_state_snapshot.payload["canonical_next_model_input"] is not (
        b.agent_state_snapshot.payload["canonical_next_model_input"]
    )
    assert a.agent_state_snapshot.payload["model"]["generation"] is not (
        b.agent_state_snapshot.payload["model"]["generation"]
    )
    assert a.agent_state_snapshot.payload["memory"]["steps"][1]["tool_calls"] is not (
        b.agent_state_snapshot.payload["memory"]["steps"][1]["tool_calls"]
    )
    assert a.agent_state_snapshot.payload == b.agent_state_snapshot.payload
    assert isolated_restores.agent_a.memory is not isolated_restores.agent_b.memory
    assert isolated_restores.agent_a.memory.steps is not isolated_restores.agent_b.memory.steps
    assert isolated_restores.agent_a.state is not isolated_restores.agent_b.state


def test_branch_a_file_sqlite_runtime_and_agent_mutations_do_not_affect_b(
    isolated_restores: IsolationFixture,
) -> None:
    item = isolated_restores
    a, b = item.environment_a, item.environment_b
    b_files_before = _workspace_bytes(b)
    b_db_before = _sqlite_state(b)
    a.resolve_path("notes/progress.txt").write_text("branch A modified\n", encoding="utf-8")
    a.resolve_path("notes/delete_me.txt").unlink()
    a.resolve_path("probe/branch_a_only.txt", must_exist=False).parent.mkdir(parents=True)
    a.resolve_path("probe/branch_a_only.txt", must_exist=False).write_text("A\n", encoding="utf-8")
    with a.database() as connection:
        connection.execute(
            "UPDATE orders SET status='delivered', version=version+1 WHERE order_id='ord-200'"
        )
        connection.execute(
            "INSERT INTO idempotency_keys VALUES (?, ?, ?)",
            ("branch-a-key", "request-a", json.dumps({"branch": "a"})),
        )
        connection.commit()
    runtime_a = item.restored_a.runtime_state
    runtime_b = item.restored_b.runtime_state
    runtime_a.budget.tokens_used += 100
    runtime_a.budget.tool_calls_used += 1
    runtime_a.tool_state["branch"] = "A"
    runtime_a.agent_memory.append({"branch": "A"})
    runtime_a.tool_history.append(
        ToolHistoryEntry(
            step_id="branch-a",
            tool_name="probe",
            tool_arguments={},
            tool_result="A",
            error_type=None,
            timestamp="2026-11-21T00:00:00+00:00",
            token_usage={},
            latency_seconds=0.0,
            worker_id="local",
            environment_id="A",
            checkpoint_id="day4_isolation_checkpoint",
        )
    )
    action_a = next(
        step for step in item.agent_a.memory.steps if isinstance(step, ActionStep)
    )
    item.agent_a.state["branch"] = "A"
    action_a.observations = "A-only observation"
    assert action_a.model_input_messages is not None
    action_a.model_input_messages.append(
        ChatMessage(role=MessageRole.USER, content="A-only internal message")
    )
    assert action_a.tool_calls is not None
    action_a.tool_calls[0].arguments["expression"] = "A-only tool state"

    assert _workspace_bytes(b) == b_files_before
    assert _sqlite_state(b) == b_db_before
    assert not b.resolve_path("probe/branch_a_only.txt", must_exist=False).exists()
    assert runtime_b.budget.tokens_used == 9
    assert runtime_b.budget.tool_calls_used == 1
    assert "branch" not in runtime_b.tool_state
    assert {entry.step_id for entry in runtime_b.tool_history} == {"step_001"}
    assert "branch" not in item.agent_b.state
    action_b = next(
        step for step in item.agent_b.memory.steps if isinstance(step, ActionStep)
    )
    assert action_b.observations == "order shipped"
    assert len(action_b.model_input_messages or []) == 1
    assert action_b.tool_calls is not None
    assert action_b.tool_calls[0].arguments["expression"] == "40 + 2"


def test_branch_b_reverse_mutations_do_not_affect_a(
    isolated_restores: IsolationFixture,
) -> None:
    item = isolated_restores
    a, b = item.environment_a, item.environment_b
    a_files_before = _workspace_bytes(a)
    a_db_before = _sqlite_state(a)
    b.resolve_path("probe/branch_b_only.txt", must_exist=False).parent.mkdir(parents=True)
    b.resolve_path("probe/branch_b_only.txt", must_exist=False).write_text("B\n", encoding="utf-8")
    with b.database() as connection:
        connection.execute(
            "UPDATE orders SET status='cancelled', version=version+5 WHERE order_id='ord-200'"
        )
        connection.execute(
            "INSERT INTO idempotency_keys VALUES (?, ?, ?)",
            ("branch-b-key", "request-b", json.dumps({"branch": "b"})),
        )
        connection.commit()
    item.restored_b.runtime_state.tool_state["branch"] = "B"
    item.restored_b.runtime_state.agent_memory.append({"branch": "B"})
    item.agent_b.state["branch"] = "B"

    assert _workspace_bytes(a) == a_files_before
    assert _sqlite_state(a) == a_db_before
    assert not a.resolve_path("probe/branch_b_only.txt", must_exist=False).exists()
    assert "branch" not in item.restored_a.runtime_state.tool_state
    assert {entry.get("branch") for entry in item.restored_a.runtime_state.agent_memory} == {None}
    assert "branch" not in item.agent_a.state


def test_branch_mutations_do_not_change_original_checkpoint(
    isolated_restores: IsolationFixture,
) -> None:
    item = isolated_restores
    item.environment_a.resolve_path("probe/a.txt", must_exist=False).parent.mkdir(parents=True)
    item.environment_a.resolve_path("probe/a.txt", must_exist=False).write_text("A", encoding="utf-8")
    item.environment_b.resolve_path("probe/b.txt", must_exist=False).parent.mkdir(parents=True)
    item.environment_b.resolve_path("probe/b.txt", must_exist=False).write_text("B", encoding="utf-8")
    with item.environment_a.database() as connection:
        connection.execute(
            "UPDATE orders SET status='delivered', version=version+1 WHERE order_id='ord-200'"
        )
        connection.execute(
            "INSERT INTO idempotency_keys VALUES (?, ?, ?)",
            ("immutability-a", "request-a", json.dumps({"branch": "a"})),
        )
        connection.commit()
    with item.environment_b.database() as connection:
        connection.execute(
            "UPDATE orders SET status='cancelled', version=version+2 WHERE order_id='ord-200'"
        )
        connection.execute(
            "INSERT INTO idempotency_keys VALUES (?, ?, ?)",
            ("immutability-b", "request-b", json.dumps({"branch": "b"})),
        )
        connection.commit()
    item.restored_a.runtime_state.tool_state["changed"] = True
    item.restored_b.runtime_state.agent_memory.append({"changed": True})
    item.agent_a.state["changed"] = "A"
    item.restored_b.agent_state_snapshot.payload["agent"]["state"]["changed"] = True
    assert _file_inventory(item.checkpoint_path) == item.before_inventory
    assert len(item.before_inventory) == len(_file_inventory(item.checkpoint_path))
