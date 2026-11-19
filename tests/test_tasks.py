from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from risk_agent.benchmark import DeterministicVerifier, ReferenceTaskRunner, TaskEnvironment, load_tasks
from risk_agent.benchmark.schema import Domain, TaskDefinitionError
from risk_agent.tools.api_workflow_tools import (
    APIWorkflowError,
    APIWorkflowTools,
    IdempotencyConflictError,
    StateConflictError,
)
from risk_agent.tools.task_tool_registry import TaskToolRegistry, ToolRegistryError


TASK_CONFIG = Path(__file__).parents[1] / "configs" / "tasks.yaml"


def test_loads_two_tasks_per_domain() -> None:
    tasks = load_tasks(TASK_CONFIG)
    assert len(tasks) == 8
    assert Counter(task.domain for task in tasks) == {
        Domain.DATA_ANALYSIS: 2,
        Domain.FILE_RETRIEVAL: 2,
        Domain.API_WORKFLOW: 2,
        Domain.CALCULATION_LOGIC: 2,
    }
    assert all(task.dataset_role == "day3_smoke" for task in tasks)


def test_all_eight_tasks_run_validate_and_reset(tmp_path) -> None:
    runner = ReferenceTaskRunner(tmp_path / "environments")
    results = [runner.run(task) for task in load_tasks(TASK_CONFIG)]
    assert len(results) == 8
    assert all(result.passed for result in results)
    assert all(result.reset_verified for result in results)
    assert all(result.initial_state_hash == result.restored_state_hash for result in results)


def test_reset_restores_database_and_clears_idempotency(tmp_path) -> None:
    task = next(task for task in load_tasks(TASK_CONFIG) if task.task_id == "api_ship_order")
    environment = TaskEnvironment(tmp_path, task)
    initial_hash = environment.reset()
    api = APIWorkflowTools(environment)
    first = api.update_order("ord-100", "processing", "shipped", "safe-key")
    replay = api.update_order("ord-100", "processing", "shipped", "safe-key")
    assert first == {
        "order_id": "ord-100",
        "status": "shipped",
        "version": 2,
        "idempotent_replay": False,
    }
    assert replay["idempotent_replay"] is True
    assert api.get_order("ord-100")["version"] == 2
    assert environment.state_hash() != initial_hash
    assert environment.reset() == initial_hash
    assert APIWorkflowTools(environment).get_order("ord-100") == {
        "order_id": "ord-100",
        "status": "processing",
        "version": 1,
    }
    assert environment.state_manifest()["idempotency_keys"] == []


def test_reset_restores_modified_fixture_file(tmp_path) -> None:
    task = next(task for task in load_tasks(TASK_CONFIG) if task.task_id == "file_read_owner")
    environment = TaskEnvironment(tmp_path, task)
    initial_hash = environment.reset()
    environment.resolve_path("notes/owner.txt").write_text("corrupted\n", encoding="utf-8")
    assert environment.state_hash() != initial_hash
    assert environment.reset() == initial_hash
    assert environment.resolve_path("notes/owner.txt").read_text(encoding="utf-8") == (
        "Owner: Lin\nTeam: Reliability\n"
    )


def test_order_write_rejects_state_and_idempotency_conflicts(tmp_path) -> None:
    task = next(task for task in load_tasks(TASK_CONFIG) if task.task_id == "api_ship_order")
    environment = TaskEnvironment(tmp_path, task)
    environment.reset()
    api = APIWorkflowTools(environment)
    with pytest.raises(StateConflictError):
        api.update_order("ord-100", "pending", "processing", "wrong-state")
    api.update_order("ord-100", "processing", "shipped", "same-key")
    with pytest.raises(IdempotencyConflictError):
        api.update_order("ord-100", "processing", "cancelled", "same-key")
    assert api.get_order("ord-100") == {
        "order_id": "ord-100",
        "status": "shipped",
        "version": 2,
    }
    with pytest.raises(APIWorkflowError):
        api.update_order("ord-100", "shipped", "processing", "unsafe-transition")


def test_workspace_rejects_path_traversal(tmp_path) -> None:
    task = load_tasks(TASK_CONFIG)[0]
    environment = TaskEnvironment(tmp_path, task)
    environment.reset()
    with pytest.raises(TaskDefinitionError):
        environment.resolve_path("../outside.txt", must_exist=False)


def test_loader_rejects_unsafe_fixture_path(tmp_path) -> None:
    config = tmp_path / "unsafe.yaml"
    config.write_text(
        """version: 1
tasks:
  - task_id: bad_task
    template_id: bad_template
    instance_id: bad_instance
    domain: file_retrieval
    seed: 1
    prompt: unsafe
    initial_state:
      files:
        - {path: ../escape.txt, content: bad}
    reference_steps:
      - {tool: read_file, arguments: {path: ../escape.txt}}
    expected: {result: bad}
""",
        encoding="utf-8",
    )
    with pytest.raises(TaskDefinitionError):
        load_tasks(config)


def test_registry_prevents_cross_domain_tool_use(tmp_path) -> None:
    task = load_tasks(TASK_CONFIG)[0]
    environment = TaskEnvironment(tmp_path, task)
    environment.reset()
    registry = TaskToolRegistry(environment)
    with pytest.raises(ToolRegistryError):
        registry.execute(task.domain, "update_order", {})


def test_verifier_rejects_wrong_result(tmp_path) -> None:
    task = load_tasks(TASK_CONFIG)[0]
    environment = TaskEnvironment(tmp_path, task)
    environment.reset()
    result = DeterministicVerifier().verify(task, environment, [{"wrong": "value"}])
    assert result.passed is False
    assert result.checks == {"result": False}
