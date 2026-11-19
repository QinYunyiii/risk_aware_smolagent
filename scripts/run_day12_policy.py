#!/usr/bin/env python3
"""Run the Day 12 adaptive policy on 20 independently restored checkpoints."""

from __future__ import annotations

import argparse
import json
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

from risk_agent.benchmark import TaskEnvironment, generate_task_instances, load_task_templates
from risk_agent.benchmark.schema import validate_identifier
from risk_agent.checkpoint import CheckpointManager
from risk_agent.controller import AdaptiveInterventionPolicy, PolicyModelBundle
from risk_agent.faults.schema import Action
from risk_agent.intervention import InterventionBranchRunner, PreparedCheckpoint
from risk_agent.monitoring import JsonlTraceWriter, RuntimeState


class _ProbeModels:
    def __init__(self, *, risk: float = 0.9, fail_risk: bool = False) -> None:
        self.risk = risk
        self.fail_risk = fail_risk
        self.utility_lambda = 0.1

    def predict_risk(self, features: Mapping[str, float]) -> float:
        if self.fail_risk:
            raise RuntimeError("probe model unavailable")
        return self.risk

    def predict_action(
        self, action: str, features: Mapping[str, float]
    ) -> tuple[float, dict[str, float], float]:
        probability = {
            "CONTINUE": 0.10,
            "RETRY": 0.99,
            "REPLAN": 0.20,
        }[action]
        costs = {"tokens": 0.0, "tool_calls": 1.0, "latency_seconds": 0.0}
        return probability, costs, 1.0


def _default_run_id(started_at: datetime) -> str:
    return f"day12_{started_at.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _restore_runtime(
    *,
    manager: CheckpointManager,
    prepared: PreparedCheckpoint,
    task,
    destination: Path,
) -> RuntimeState:
    environment = TaskEnvironment(destination, task)
    restored = manager.restore(checkpoint_id=prepared.checkpoint_id, environment=environment)
    if restored.restored_state_hash != prepared.state_hash:
        raise RuntimeError(f"Restore hash mismatch for {prepared.checkpoint_id}")
    return restored.runtime_state


def _run_safety_probes(
    *,
    policy_config: Mapping[str, Any],
    manager: CheckpointManager,
    prepared: tuple[PreparedCheckpoint, ...],
    tasks_by_id: Mapping[str, Any],
    root: Path,
) -> dict[str, Any]:
    by_scenario = {checkpoint.scenario.scenario_id: checkpoint for checkpoint in prepared}
    transient = by_scenario["transient_service_unavailable"]
    invalid = by_scenario["invalid_argument"]
    partial = by_scenario["partial_failure_not_committed"]

    unsafe_runtime = _restore_runtime(
        manager=manager,
        prepared=invalid,
        task=tasks_by_id[invalid.task_id],
        destination=root / "unsafe_retry",
    )
    probe_policy = AdaptiveInterventionPolicy(
        models=_ProbeModels(risk=0.9), config=policy_config
    )
    unsafe = probe_policy.decide(
        runtime=unsafe_runtime, domain=tasks_by_id[invalid.task_id].domain
    )

    loop_runtime = _restore_runtime(
        manager=manager,
        prepared=transient,
        task=tasks_by_id[transient.task_id],
        destination=root / "retry_loop",
    )
    first = probe_policy.decide(
        runtime=loop_runtime, domain=tasks_by_id[transient.task_id].domain
    )
    probe_policy.record_action(loop_runtime, first.action)
    second = probe_policy.decide(
        runtime=loop_runtime, domain=tasks_by_id[transient.task_id].domain
    )

    replan_runtime = _restore_runtime(
        manager=manager,
        prepared=invalid,
        task=tasks_by_id[invalid.task_id],
        destination=root / "replan_loop",
    )
    replan_first = probe_policy.decide(
        runtime=replan_runtime, domain=tasks_by_id[invalid.task_id].domain
    )
    probe_policy.record_action(replan_runtime, replan_first.action)
    replan_second = probe_policy.decide(
        runtime=replan_runtime, domain=tasks_by_id[invalid.task_id].domain
    )

    failure_policy = AdaptiveInterventionPolicy(
        models=_ProbeModels(fail_risk=True), config=policy_config
    )
    read_runtime = _restore_runtime(
        manager=manager,
        prepared=transient,
        task=tasks_by_id[transient.task_id],
        destination=root / "read_model_failure",
    )
    read_fallback = failure_policy.decide(
        runtime=read_runtime, domain=tasks_by_id[transient.task_id].domain
    )
    write_runtime = _restore_runtime(
        manager=manager,
        prepared=partial,
        task=tasks_by_id[partial.task_id],
        destination=root / "write_model_failure",
    )
    write_fallback = failure_policy.decide(
        runtime=write_runtime, domain=tasks_by_id[partial.task_id].domain
    )

    budget_runtime = _restore_runtime(
        manager=manager,
        prepared=transient,
        task=tasks_by_id[transient.task_id],
        destination=root / "budget_exhausted",
    )
    budget_runtime.budget.tokens_used = budget_runtime.budget.max_tokens
    exhausted = probe_policy.decide(
        runtime=budget_runtime, domain=tasks_by_id[transient.task_id].domain
    )
    checks = {
        "unsafe_retry_blocked": unsafe.action != Action.RETRY
        and Action.RETRY.value in unsafe.excluded_actions,
        "first_safe_retry_allowed": first.action == Action.RETRY,
        "retry_loop_prevented": second.action != Action.RETRY
        and Action.RETRY.value in second.excluded_actions,
        "first_safe_replan_allowed": replan_first.action == Action.REPLAN,
        "replan_loop_prevented": replan_second.action != Action.REPLAN
        and Action.REPLAN.value in replan_second.excluded_actions,
        "read_only_model_failure_falls_back_to_continue": read_fallback.action
        == Action.CONTINUE
        and read_fallback.fallback_used,
        "write_model_failure_falls_back_to_stop": write_fallback.action == Action.STOP
        and write_fallback.fallback_used,
        "budget_exhaustion_stops": exhausted.action == Action.STOP,
    }
    return {
        "checks": checks,
        "passed": all(checks.values()),
        "decisions": {
            "unsafe_retry": unsafe.to_dict(),
            "retry_first": first.to_dict(),
            "retry_second": second.to_dict(),
            "replan_first": replan_first.to_dict(),
            "replan_second": replan_second.to_dict(),
            "read_model_failure": read_fallback.to_dict(),
            "write_model_failure": write_fallback.to_dict(),
            "budget_exhausted": exhausted.to_dict(),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/day12_policy.yaml")
    parser.add_argument("--templates", default="configs/task_templates.yaml")
    parser.add_argument("--output-root", default="runs/day12")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()

    started_at = datetime.now(timezone.utc)
    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("Unsupported Day 12 configuration")
    run_id = validate_identifier(args.run_id or _default_run_id(started_at), "run_id")
    output_directory = Path(args.output_root) / run_id
    output_directory.mkdir(parents=True, exist_ok=False)

    source_run = Path(str(config["source_day9_run"]))
    source_summary = json.loads(
        (source_run / "intervention_summary.json").read_text(encoding="utf-8")
    )
    if not source_summary.get("passed"):
        raise RuntimeError("Day 9 source run did not pass")
    if source_summary.get("catalog_sha256") != config["catalog_sha256"]:
        raise RuntimeError("Day 9 catalog hash mismatch")
    manifest = json.loads(
        (source_run / "checkpoint_manifest.json").read_text(encoding="utf-8")
    )
    prepared = tuple(
        PreparedCheckpoint.from_dict(record) for record in manifest["checkpoints"]
    )
    if len(prepared) != int(config["expected_checkpoint_count"]):
        raise RuntimeError("Unexpected Day 12 checkpoint count")
    tasks = generate_task_instances(load_task_templates(args.templates))
    tasks_by_id = {task.task_id: task for task in tasks}
    manager = CheckpointManager(source_run / "checkpoints")
    models = PolicyModelBundle.load(
        risk_metadata_path=config["risk_model_metadata"],
        utility_metadata_path=config["utility_model_metadata"],
        risk_model_name=str(config["risk_model_name"]),
        expected_catalog_sha256=str(config["catalog_sha256"]),
    )
    policy = AdaptiveInterventionPolicy(models=models, config=config)
    decisions_writer = JsonlTraceWriter(output_directory / "policy_decisions.jsonl")
    branch_runner = InterventionBranchRunner(
        checkpoint_manager=manager,
        branch_root=output_directory / "selected_branches",
        outcome_writer=JsonlTraceWriter(output_directory / "selected_branch_outcomes.jsonl"),
        experiment_id=str(config["experiment_id"]),
        run_id=run_id,
        controller_type="adaptive_policy_day12",
        model_id="day10-risk-day11-utility",
        model_version="day12-v1",
        prompt_version="day12-v1",
        tool_version="day9-v1",
    )

    records: list[dict[str, Any]] = []
    for checkpoint in prepared:
        task = tasks_by_id[checkpoint.task_id]
        runtime = _restore_runtime(
            manager=manager,
            prepared=checkpoint,
            task=task,
            destination=output_directory / "decision_states" / checkpoint.checkpoint_id,
        )
        decision = policy.decide(runtime=runtime, domain=task.domain)
        policy.record_action(runtime, decision.action)
        decision_record = {
            "record_type": "adaptive_policy_decision",
            "experiment_id": config["experiment_id"],
            "run_id": run_id,
            "checkpoint_id": checkpoint.checkpoint_id,
            "task_id": task.task_id,
            "template_id": task.template_id,
            "split": checkpoint.split,
            "scenario_id": checkpoint.scenario.scenario_id,
            **decision.to_dict(),
        }
        decisions_writer.append(decision_record)
        if decision.action == Action.STOP:
            execution = {
                "action": Action.STOP.value,
                "success": False,
                "validation_passed": False,
                "termination_reason": "policy_stop",
                "initial_state_hash": checkpoint.state_hash,
                "restored_state_hash": checkpoint.state_hash,
            }
        else:
            execution = branch_runner.run_action(
                prepared=checkpoint, task=task, action=decision.action
            )
        records.append({"decision": decision_record, "execution": execution})

    probes = _run_safety_probes(
        policy_config=config,
        manager=manager,
        prepared=prepared,
        tasks_by_id=tasks_by_id,
        root=output_directory / "safety_probes",
    )
    _write_json(output_directory / "safety_probes.json", probes)
    _write_json(output_directory / "policy_e2e_records.json", {"records": records})

    action_counts = Counter(record["decision"]["action"] for record in records)
    risk_counts = Counter(record["decision"]["risk_level"] for record in records)
    low_risk = [record for record in records if record["decision"]["risk_level"] == "low"]
    high_risk = [record for record in records if record["decision"]["risk_level"] == "high"]
    checks = {
        "source_day9_passed": bool(source_summary.get("passed")),
        "twenty_end_to_end_tasks": len(records) >= 20,
        "all_end_to_end_tasks_passed": all(
            record["execution"]["success"]
            and record["execution"]["validation_passed"]
            for record in records
        ),
        "all_low_risk_continue": bool(low_risk)
        and all(record["decision"]["action"] == Action.CONTINUE.value for record in low_risk),
        "high_risk_compares_actions": bool(high_risk)
        and all(len(record["decision"]["action_scores"]) >= 2 for record in high_risk),
        "selected_actions_were_not_excluded": all(
            record["decision"]["action"]
            not in record["decision"]["excluded_actions"]
            for record in records
        ),
        "no_model_fallback_in_normal_e2e": all(
            not record["decision"]["fallback_used"] for record in records
        ),
        "no_stop_in_normal_e2e": all(
            record["decision"]["action"] != Action.STOP.value for record in records
        ),
        "all_selected_branch_restores_match": all(
            record["execution"]["initial_state_hash"]
            == record["execution"]["restored_state_hash"]
            for record in records
        ),
        "safety_probes_passed": bool(probes["passed"]),
    }
    summary = {
        "schema_version": 1,
        "experiment_id": config["experiment_id"],
        "run_id": run_id,
        "created_at": started_at.isoformat(),
        "source_day9_run": str(source_run),
        "risk_model_metadata": str(config["risk_model_metadata"]),
        "utility_model_metadata": str(config["utility_model_metadata"]),
        "catalog_sha256": config["catalog_sha256"],
        "task_count": len(records),
        "successful_task_count": sum(bool(record["execution"]["success"]) for record in records),
        "action_counts": dict(sorted(action_counts.items())),
        "risk_level_counts": dict(sorted(risk_counts.items())),
        "fallback_count": sum(bool(record["decision"]["fallback_used"]) for record in records),
        "stop_count": sum(record["decision"]["action"] == Action.STOP.value for record in records),
        "policy_config": {
            "low_risk_threshold": config["low_risk_threshold"],
            "minimum_utility_gain": config["minimum_utility_gain"],
            "max_retry_count": config["max_retry_count"],
            "max_replan_count": config["max_replan_count"],
            "max_same_action_streak": config["max_same_action_streak"],
            "threshold_selection": config["threshold_selection"],
        },
        "safety_probe_checks": probes["checks"],
        "checks": checks,
        "passed": all(checks.values()),
        "artifacts": {
            "decisions": str((output_directory / "policy_decisions.jsonl").resolve()),
            "selected_outcomes": str(
                (output_directory / "selected_branch_outcomes.jsonl").resolve()
            ),
            "e2e_records": str((output_directory / "policy_e2e_records.json").resolve()),
            "safety_probes": str((output_directory / "safety_probes.json").resolve()),
        },
    }
    _write_json(output_directory / "policy_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
