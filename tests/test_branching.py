from __future__ import annotations

import json
from pathlib import Path

from risk_agent.benchmark import load_tasks
from risk_agent.branching import (
    BranchApplicabilityEvaluator,
    BranchRunner,
    build_day6_smoke_runtime,
    create_day6_smoke_checkpoints,
)
from risk_agent.checkpoint import CheckpointManager
from risk_agent.faults.schema import Action


ROOT = Path(__file__).parents[1]
TASK_CONFIG = ROOT / "configs" / "tasks.yaml"


def api_task():
    return next(task for task in load_tasks(TASK_CONFIG) if task.task_id == "api_ship_order")


def test_all_three_actions_are_applicable_only_with_safe_retry_metadata() -> None:
    task = api_task()
    runtime = build_day6_smoke_runtime(task, 1)
    evaluator = BranchApplicabilityEvaluator()
    assert all(
        evaluator.evaluate(action, runtime).applicable
        for action in (Action.CONTINUE, Action.RETRY, Action.REPLAN)
    )

    runtime.tool_state["last_failure"]["commit_status"] = "committed"
    retry = evaluator.evaluate(Action.RETRY, runtime)
    assert retry.applicable is False
    assert "committed" in retry.reason.lower() or "uncertain" in retry.reason.lower()

    runtime.tool_state["last_failure"]["commit_status"] = "not_committed"
    del runtime.tool_state["pending_operation"]["arguments"]["idempotency_key"]
    retry_without_key = evaluator.evaluate(Action.RETRY, runtime)
    assert retry_without_key.applicable is False
    assert "idempotency" in retry_without_key.reason


def test_five_checkpoints_execute_three_isolated_validated_branches(tmp_path) -> None:
    task = api_task()
    manager = CheckpointManager(tmp_path / "checkpoints")
    snapshots = create_day6_smoke_checkpoints(
        task=task,
        manager=manager,
        source_root=tmp_path / "sources",
        count=5,
    )
    runner = BranchRunner(
        checkpoint_manager=manager,
        branch_root=tmp_path / "branches",
        experiment_id="day6_test",
        run_id="day6_test_run",
    )
    results = [
        runner.run_checkpoint(checkpoint_id=snapshot.manifest.checkpoint_id, task=task)
        for snapshot in snapshots
    ]
    assert len(results) == 5
    assert all(result.passed for result in results)
    assert all(len(result.branches) == 3 for result in results)
    assert all(all(result.checks.values()) for result in results)

    for checkpoint in results:
        assert {branch.action for branch in checkpoint.branches} == {
            Action.CONTINUE,
            Action.RETRY,
            Action.REPLAN,
        }
        assert len({branch.initial_state_hash for branch in checkpoint.branches}) == 1
        assert all(
            branch.initial_state_hash == branch.restored_state_hash
            for branch in checkpoint.branches
        )
        assert all(branch.validation_passed for branch in checkpoint.branches)
        assert all(branch.extra_tokens == 0 for branch in checkpoint.branches)
        assert {
            branch.action: branch.extra_tool_calls for branch in checkpoint.branches
        } == {Action.CONTINUE: 1, Action.RETRY: 1, Action.REPLAN: 2}
        assert len({branch.workspace_path for branch in checkpoint.branches}) == 3
        assert len({branch.database_path for branch in checkpoint.branches}) == 3

    records = [
        json.loads(line)
        for line in results[0].branches[0].trajectory_path.read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert records[0]["branch_id"] == "checkpoint_001_continue"
    assert records[0]["action"] == "CONTINUE"
    assert records[0]["action_applicable"] is True
    assert records[0]["token_usage"] == {"total_tokens": 0}
