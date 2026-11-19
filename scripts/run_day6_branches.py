#!/usr/bin/env python3
"""Run the local, non-formal Day 6 checkpoint branch acceptance smoke test."""

from __future__ import annotations

import argparse
import json
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from risk_agent.benchmark import load_tasks
from risk_agent.benchmark.schema import validate_identifier
from risk_agent.branching import BranchRunner, create_day6_smoke_checkpoints
from risk_agent.checkpoint import CheckpointManager


def default_run_id(started_at: datetime) -> str:
    return f"day6_{started_at.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="configs/tasks.yaml")
    parser.add_argument("--output-root", default="runs/day06")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()

    started_at = datetime.now(timezone.utc)
    run_id = validate_identifier(args.run_id or default_run_id(started_at), "run_id")
    run_directory = Path(args.output_root) / run_id
    run_directory.mkdir(parents=True, exist_ok=False)
    task = next(
        task for task in load_tasks(args.tasks) if task.task_id == "api_ship_order"
    )
    manager = CheckpointManager(run_directory / "checkpoints")
    snapshots = create_day6_smoke_checkpoints(
        task=task,
        manager=manager,
        source_root=run_directory / "checkpoint_sources",
        count=5,
    )
    runner = BranchRunner(
        checkpoint_manager=manager,
        branch_root=run_directory / "branches",
        experiment_id="day6_branch_protocol_smoke_local",
        run_id=run_id,
    )
    checkpoint_results = [
        runner.run_checkpoint(checkpoint_id=snapshot.manifest.checkpoint_id, task=task)
        for snapshot in snapshots
    ]
    branches = [
        branch for checkpoint in checkpoint_results for branch in checkpoint.branches
    ]
    action_counts = Counter(branch.action.value for branch in branches)
    checks = {
        "five_checkpoints": len(checkpoint_results) >= 5,
        "fifteen_branches": len(branches) >= 15,
        "three_actions_per_checkpoint": all(
            len(checkpoint.branches) == 3 for checkpoint in checkpoint_results
        ),
        "five_of_each_action": action_counts
        == {"CONTINUE": 5, "RETRY": 5, "REPLAN": 5},
        "all_actions_applicable": all(branch.action_applicable for branch in branches),
        "all_initial_hash_sets_identical": all(
            checkpoint.checks["initial_state_hashes_identical"]
            for checkpoint in checkpoint_results
        ),
        "all_isolation_checks_passed": all(
            checkpoint.checks["workspace_paths_unique"]
            and checkpoint.checks["database_files_unique"]
            and checkpoint.checks["runtime_objects_unique"]
            and checkpoint.checks["cross_talk_probe_passed"]
            for checkpoint in checkpoint_results
        ),
        "all_deterministic_validations_passed": all(
            branch.validation_passed for branch in branches
        ),
        "local_tokens_are_measured_as_zero": all(
            branch.extra_tokens == 0 for branch in branches
        ),
        "tool_costs_match_action_protocol": all(
            branch.extra_tool_calls == (2 if branch.action.value == "REPLAN" else 1)
            for branch in branches
        ),
    }
    summary = {
        "schema_version": 1,
        "dataset_role": "day6_smoke",
        "formal_experiment": False,
        "experiment_id": "day6_branch_protocol_smoke_local",
        "run_id": run_id,
        "created_at": started_at.isoformat(),
        "task_id": task.task_id,
        "checkpoint_count": len(checkpoint_results),
        "branch_count": len(branches),
        "action_counts": dict(sorted(action_counts.items())),
        "checks": checks,
        "checkpoints": [result.to_dict() for result in checkpoint_results],
        "passed": all(checks.values()) and all(
            result.passed for result in checkpoint_results
        ),
    }
    output = run_directory / "branch_results.json"
    output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
