from __future__ import annotations

import json
from pathlib import Path

import pytest

from risk_agent.benchmark import generate_task_instances, load_task_templates
from risk_agent.checkpoint import CheckpointManager
from risk_agent.intervention import (
    InterventionBranchRunner,
    build_intervention_scenario,
    eligible_day8_records,
    prepare_intervention_checkpoints,
)
from risk_agent.monitoring import JsonlTraceWriter


ROOT = Path(__file__).parents[1]
TEMPLATES = ROOT / "configs/task_templates.yaml"


def _write_day8_fixture(path: Path) -> Path:
    tasks = generate_task_instances(load_task_templates(TEMPLATES))[:24]
    infra_positions = {5, 11, 17, 23}
    records = []
    for position, task in enumerate(tasks, start=1):
        task_status = "infra_failure" if position in infra_positions else "success"
        if position == 19:
            task_status = "task_failure"
        records.append(
            {
                "run_id": "day8_fixture",
                "task_run_id": f"fixture_attempt_{position:03d}",
                "task_id": task.task_id,
                "split": task.split.value,
                "worker_id": f"worker_{position % 2}",
                "task_status": task_status,
                "agent_steps": [
                    {
                        "tool_calls": [{"name": task.reference_steps[-1].tool}],
                        "token_usage": {"total_tokens": 100 + position},
                    }
                ],
            }
        )
    path.write_text(json.dumps({"records": records}), encoding="utf-8")
    return path


def test_day8_infrastructure_failures_are_excluded(tmp_path) -> None:
    source = _write_day8_fixture(tmp_path / "logical_task_results.json")
    all_records = json.loads(source.read_text(encoding="utf-8"))["records"]
    eligible = eligible_day8_records(source)
    assert len(all_records) == 24
    assert len(eligible) == 20
    assert all(record["task_status"] != "infra_failure" for record in eligible)
    assert {record["task_status"] for record in eligible} == {"success", "task_failure"}


def test_unknown_day8_status_is_rejected(tmp_path) -> None:
    source = _write_day8_fixture(tmp_path / "logical_task_results.json")
    document = json.loads(source.read_text(encoding="utf-8"))
    document["records"][0]["task_status"] = "unknown"
    source.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown Day 8 task statuses"):
        eligible_day8_records(source)


def test_scenarios_make_retry_and_replan_applicability_explicit(tmp_path) -> None:
    tasks = generate_task_instances(load_task_templates(TEMPLATES))
    read_task = next(task for task in tasks if task.task_id == "data_filter_amount_gt_001")
    transient = build_intervention_scenario(read_task, 1)
    invalid = build_intervention_scenario(read_task, 2)
    assert transient.last_failure["fault_type"] == "F2_API_503"
    assert invalid.last_failure["fault_type"] == "F3_INVALID_ARGUMENT"
    update_task = next(task for task in tasks if task.task_id == "api_order_start_processing_001")
    update = build_intervention_scenario(update_task, 99)
    assert update.scenario_id == "partial_failure_not_committed"
    assert len(update.replan_steps) == 2


def test_representative_checkpoints_execute_only_applicable_actions(tmp_path) -> None:
    tasks = generate_task_instances(load_task_templates(TEMPLATES))
    tasks_by_id = {task.task_id: task for task in tasks}
    source = _write_day8_fixture(tmp_path / "logical_task_results.json")
    eligible = eligible_day8_records(source)[:4]
    manager = CheckpointManager(tmp_path / "checkpoints")
    prepared = prepare_intervention_checkpoints(
        tasks_by_id=tasks_by_id,
        source_records=eligible,
        checkpoint_manager=manager,
        source_root=tmp_path / "sources",
    )
    runner = InterventionBranchRunner(
        checkpoint_manager=manager,
        branch_root=tmp_path / "branches",
        outcome_writer=JsonlTraceWriter(tmp_path / "outcomes.jsonl"),
        experiment_id="day9_test",
        run_id="day9_test_run",
    )
    records = [
        record
        for checkpoint in prepared
        for record in runner.run_checkpoint(
            prepared=checkpoint, task=tasks_by_id[checkpoint.task_id]
        )
    ]
    assert len(records) == 12
    assert all(
        record["action_applicable"] and record["branch_created"]
        for record in records
        if record["action"] == "CONTINUE"
    )
    assert all(
        record["branch_created"]
        for record in records
        if record["action_applicable"]
    )
    assert all(
        record["termination_reason"] == "action_not_applicable"
        for record in records
        if not record["action_applicable"]
    )
    assert all(
        record["initial_state_hash"] == record["restored_state_hash"]
        for record in records
    )
    assert all(record["extra_tokens"] == 0 for record in records)
    assert all(record["split"] == "train" for record in records)
