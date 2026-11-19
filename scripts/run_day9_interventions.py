#!/usr/bin/env python3
"""Create Day 9 checkpoints and collect local intervention branch outcomes."""

from __future__ import annotations

import argparse
import json
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

from risk_agent.benchmark import generate_task_instances, load_task_templates
from risk_agent.benchmark.schema import validate_identifier
from risk_agent.checkpoint import CheckpointManager
from risk_agent.intervention import (
    InterventionBranchRunner,
    eligible_day8_records,
    prepare_intervention_checkpoints,
)
from risk_agent.monitoring import JsonlTraceWriter


def default_run_id(started_at: datetime) -> str:
    return f"day9_{started_at.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/day9_interventions.yaml")
    parser.add_argument("--templates", default="configs/task_templates.yaml")
    parser.add_argument("--output-root", default="runs/day09")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()

    started_at = datetime.now(timezone.utc)
    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("Unsupported Day 9 configuration")
    if config.get("actions") != ["CONTINUE", "RETRY", "REPLAN"]:
        raise ValueError("Day 9 actions must be CONTINUE, RETRY, and REPLAN")
    if config.get("allow_infra_sources") is not False:
        raise ValueError("Day 9 must not admit infrastructure-failure sources")
    if config.get("controller_type") != "deterministic_local_reference":
        raise ValueError("This runner only supports the deterministic local reference controller")
    run_id = validate_identifier(args.run_id or default_run_id(started_at), "run_id")
    run_directory = Path(args.output_root) / run_id
    run_directory.mkdir(parents=True, exist_ok=False)

    source_path = Path(str(config["source_day8_results"]))
    source_document = json.loads(source_path.read_text(encoding="utf-8"))
    if source_document.get("catalog_sha256") != config["catalog_sha256"]:
        raise RuntimeError("Day 8 source catalog hash does not match Day 9 configuration")
    all_source_records = source_document.get("records", [])
    if len(all_source_records) != int(config["expected_source_tasks"]):
        raise RuntimeError("Unexpected Day 8 source task count")
    source_records = eligible_day8_records(source_path)
    if len(source_records) != int(config["expected_eligible_checkpoints"]):
        raise RuntimeError("Unexpected eligible Day 8 checkpoint source count")

    tasks = generate_task_instances(load_task_templates(args.templates))
    tasks_by_id = {task.task_id: task for task in tasks}
    checkpoint_manager = CheckpointManager(run_directory / "checkpoints")
    prepared = prepare_intervention_checkpoints(
        tasks_by_id=tasks_by_id,
        source_records=source_records,
        checkpoint_manager=checkpoint_manager,
        source_root=run_directory / "checkpoint_sources",
    )
    outcome_path = run_directory / "branch_outcomes.jsonl"
    branch_runner = InterventionBranchRunner(
        checkpoint_manager=checkpoint_manager,
        branch_root=run_directory / "branches",
        outcome_writer=JsonlTraceWriter(outcome_path),
        experiment_id=str(config["experiment_id"]),
        run_id=run_id,
    )
    records = [
        record
        for checkpoint in prepared
        for record in branch_runner.run_checkpoint(
            prepared=checkpoint, task=tasks_by_id[checkpoint.task_id]
        )
    ]

    by_checkpoint: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in records:
        by_checkpoint[str(record["checkpoint_id"])].append(record)
    applicable = [record for record in records if record["action_applicable"]]
    not_applicable = [record for record in records if not record["action_applicable"]]
    workspace_paths = [record["workspace_path"] for record in applicable]
    action_counts = Counter(str(record["action"]) for record in records)
    applicable_action_counts = Counter(str(record["action"]) for record in applicable)
    checks = {
        "twenty_eligible_checkpoints": len(prepared) == 20,
        "infra_sources_excluded": all(
            record.get("task_status") != "infra_failure" for record in source_records
        ),
        "three_action_records_per_checkpoint": len(records) == 60
        and all(len(group) == 3 for group in by_checkpoint.values()),
        "every_checkpoint_has_continue": all(
            any(record["action"] == "CONTINUE" for record in group)
            for group in by_checkpoint.values()
        ),
        "all_continue_actions_executed": all(
            record["action_applicable"] and record["branch_created"]
            for record in records
            if record["action"] == "CONTINUE"
        ),
        "all_applicable_actions_executed": all(record["branch_created"] for record in applicable),
        "not_applicable_actions_have_reasons": all(
            record["applicability_reason"]
            and record["termination_reason"] == "action_not_applicable"
            for record in not_applicable
        ),
        "all_applicable_restores_match_checkpoint": all(
            record["initial_state_hash"] == record["restored_state_hash"]
            for record in applicable
        ),
        "independent_applicable_workspaces": len(workspace_paths) == len(set(workspace_paths)),
        "all_applicable_results_verified": all(
            bool(record["validation_checks"]) for record in applicable
        ),
        "cost_fields_complete_nonnegative": all(
            isinstance(record["extra_tokens"], int)
            and record["extra_tokens"] >= 0
            and isinstance(record["extra_tool_calls"], int)
            and record["extra_tool_calls"] >= 0
            and isinstance(record["extra_latency_seconds"], (int, float))
            and record["extra_latency_seconds"] >= 0
            for record in records
        ),
        "checkpoint_branches_do_not_cross_split": all(
            len({record["split"] for record in group}) == 1
            for group in by_checkpoint.values()
        ),
        "source_and_branch_split_match": all(
            record["split"] == tasks_by_id[str(record["task_id"])].split.value
            for record in records
        ),
        "local_reference_tokens_measured_as_zero": all(
            record["extra_tokens"] == 0 for record in records
        ),
    }
    excluded = [
        {
            "task_id": record["task_id"],
            "task_run_id": record["task_run_id"],
            "reason": "day8_infrastructure_failure",
        }
        for record in all_source_records
        if record.get("task_status") == "infra_failure"
    ]
    eligibility = {
        "schema_version": 1,
        "source": str(source_path),
        "catalog_sha256": config["catalog_sha256"],
        "eligible_task_ids": [record["task_id"] for record in source_records],
        "excluded": excluded,
    }
    (run_directory / "eligibility_manifest.json").write_text(
        json.dumps(eligibility, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (run_directory / "checkpoint_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "checkpoints": [checkpoint.to_dict() for checkpoint in prepared],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    utility_document = {
        "schema_version": 1,
        "dataset_role": "day9_initial_intervention",
        "formal_remote_model_branches": False,
        "controller_type": config["controller_type"],
        "catalog_sha256": config["catalog_sha256"],
        "run_id": run_id,
        "records": records,
    }
    (run_directory / "utility_dataset.json").write_text(
        json.dumps(utility_document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = {
        "schema_version": 1,
        "experiment_id": config["experiment_id"],
        "run_id": run_id,
        "created_at": started_at.isoformat(),
        "source_day8_results": str(source_path),
        "catalog_sha256": config["catalog_sha256"],
        "checkpoint_count": len(prepared),
        "branch_record_count": len(records),
        "applicable_branch_count": len(applicable),
        "not_applicable_branch_count": len(not_applicable),
        "action_counts": dict(sorted(action_counts.items())),
        "applicable_action_counts": dict(sorted(applicable_action_counts.items())),
        "successful_applicable_branches": sum(
            bool(record["success"]) for record in applicable
        ),
        "validated_applicable_branches": sum(
            bool(record["validation_passed"]) for record in applicable
        ),
        "scenario_counts": dict(
            sorted(Counter(checkpoint.scenario.scenario_id for checkpoint in prepared).items())
        ),
        "split_counts": dict(sorted(Counter(checkpoint.split for checkpoint in prepared).items())),
        "checks": checks,
        "passed": all(checks.values()),
        "artifacts": {
            "eligibility": str((run_directory / "eligibility_manifest.json").resolve()),
            "checkpoints": str((run_directory / "checkpoint_manifest.json").resolve()),
            "branch_outcomes": str(outcome_path.resolve()),
            "utility_dataset": str((run_directory / "utility_dataset.json").resolve()),
        },
    }
    output = run_directory / "intervention_summary.json"
    output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
