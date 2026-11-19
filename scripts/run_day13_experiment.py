#!/usr/bin/env python3
"""Run Day 13 baselines and ablations on independent checkpoint restores."""

from __future__ import annotations

import argparse
import json
import statistics
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from risk_agent.benchmark import TaskEnvironment, generate_task_instances, load_task_templates
from risk_agent.benchmark.schema import validate_identifier
from risk_agent.checkpoint import CheckpointManager
from risk_agent.controller import PolicyModelBundle
from risk_agent.evaluation import build_experiment_policies
from risk_agent.faults.schema import Action
from risk_agent.intervention import InterventionBranchRunner, PreparedCheckpoint
from risk_agent.monitoring import JsonlTraceWriter


BASELINES = ("vanilla", "always_retry", "rule_based", "risk_based", "risk_plus_utility")
ABLATIONS = ("full", "without_risk", "without_utility", "without_cost")


def _default_run_id(started_at: datetime, smoke: bool) -> str:
    prefix = "day13_smoke" if smoke else "day13"
    return f"{prefix}_{started_at.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _mean(values: Iterable[float]) -> float:
    materialized = list(values)
    return float(statistics.mean(materialized)) if materialized else 0.0


def _std(values: Iterable[float]) -> float:
    materialized = list(values)
    return float(statistics.pstdev(materialized)) if len(materialized) > 1 else 0.0


def _source_values(
    source_run: Path, cost_weights: Mapping[str, float], utility_lambda: float
) -> dict[str, dict[str, dict[str, Any]]]:
    records = [
        json.loads(line)
        for line in (source_run / "branch_outcomes.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    by_checkpoint: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for record in records:
        if not record["action_applicable"]:
            continue
        cost = (
            float(cost_weights["tokens"]) * float(record["extra_tokens"])
            + float(cost_weights["tool_calls"]) * float(record["extra_tool_calls"])
            + float(cost_weights["latency_seconds"])
            * float(record["extra_latency_seconds"])
        )
        record = dict(record)
        record["weighted_cost"] = cost
        record["observed_value"] = float(bool(record["success"])) - utility_lambda * cost
        by_checkpoint[str(record["checkpoint_id"])][str(record["action"])] = record
    return dict(by_checkpoint)


def _summarize_subset(records: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(records)
    successes = sum(bool(record["execution"]["success"]) for record in records)
    validations = sum(bool(record["execution"]["validation_passed"]) for record in records)
    continue_failures = [record for record in records if not record["source_continue_success"]]
    recovered = sum(bool(record["execution"]["success"]) for record in continue_failures)
    interventions = [
        record for record in records if record["decision"]["action"] != Action.CONTINUE.value
    ]
    unnecessary = [record for record in interventions if record["source_continue_success"]]
    action_counts = Counter(record["decision"]["action"] for record in records)
    tokens = [float(record["execution"]["extra_tokens"]) for record in records]
    tools = [float(record["execution"]["extra_tool_calls"]) for record in records]
    latencies = [float(record["execution"]["extra_latency_seconds"]) for record in records]
    regrets = [float(record["regret"]) for record in records]
    return {
        "task_count": count,
        "task_success_count": successes,
        "success_rate": successes / count if count else 0.0,
        "validation_pass_rate": validations / count if count else 0.0,
        "continue_failure_count": len(continue_failures),
        "recovered_count": recovered,
        "recovery_rate": recovered / len(continue_failures) if continue_failures else 0.0,
        "intervention_count": len(interventions),
        "intervention_rate": len(interventions) / count if count else 0.0,
        "unnecessary_intervention_count": len(unnecessary),
        "unnecessary_intervention_rate_among_interventions": (
            len(unnecessary) / len(interventions) if interventions else 0.0
        ),
        "action_counts": dict(sorted(action_counts.items())),
        "action_rates": {
            action: action_counts.get(action, 0) / count if count else 0.0
            for action in ("CONTINUE", "RETRY", "REPLAN", "STOP")
        },
        "average_tokens": _mean(tokens),
        "std_tokens": _std(tokens),
        "average_tool_calls": _mean(tools),
        "std_tool_calls": _std(tools),
        "average_latency_seconds": _mean(latencies),
        "std_latency_seconds": _std(latencies),
        "policy_regret_mean": _mean(regrets),
        "policy_regret_std": _std(regrets),
        "policy_regret_max": max(regrets, default=0.0),
    }


def _policy_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(records)
    successes = sum(bool(record["execution"]["success"]) for record in records)
    validations = sum(bool(record["execution"]["validation_passed"]) for record in records)
    continue_failures = [record for record in records if not record["source_continue_success"]]
    recovered = sum(bool(record["execution"]["success"]) for record in continue_failures)
    interventions = [
        record for record in records if record["decision"]["action"] != Action.CONTINUE.value
    ]
    unnecessary = [record for record in interventions if record["source_continue_success"]]
    action_counts = Counter(record["decision"]["action"] for record in records)
    tokens = [float(record["execution"]["extra_tokens"]) for record in records]
    tools = [float(record["execution"]["extra_tool_calls"]) for record in records]
    latencies = [float(record["execution"]["extra_latency_seconds"]) for record in records]
    regrets = [float(record["regret"]) for record in records]
    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_fault: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_domain[str(record["domain"])].append(record)
        by_fault[str(record["fault_type"])].append(record)
    return {
        "task_count": count,
        "task_success_count": successes,
        "task_success_rate": successes / count,
        "validation_pass_rate": validations / count,
        "continue_failure_count": len(continue_failures),
        "recovered_count": recovered,
        "recovery_rate": recovered / len(continue_failures) if continue_failures else 0.0,
        "intervention_count": len(interventions),
        "intervention_rate": len(interventions) / count,
        "unnecessary_intervention_count": len(unnecessary),
        "unnecessary_intervention_rate_among_interventions": (
            len(unnecessary) / len(interventions) if interventions else 0.0
        ),
        "action_counts": dict(sorted(action_counts.items())),
        "action_rates": {
            action: action_counts.get(action, 0) / count
            for action in ("CONTINUE", "RETRY", "REPLAN", "STOP")
        },
        "average_tokens": _mean(tokens),
        "std_tokens": _std(tokens),
        "average_tool_calls": _mean(tools),
        "std_tool_calls": _std(tools),
        "average_latency_seconds": _mean(latencies),
        "std_latency_seconds": _std(latencies),
        "policy_regret_mean": _mean(regrets),
        "policy_regret_std": _std(regrets),
        "policy_regret_max": max(regrets, default=0.0),
        "fallback_count": sum(bool(record["decision"]["fallback_used"]) for record in records),
        "stop_count": action_counts.get(Action.STOP.value, 0),
        "by_domain": {
            key: _summarize_subset(group) for key, group in sorted(by_domain.items())
        },
        "by_fault_type": {
            key: _summarize_subset(group) for key, group in sorted(by_fault.items())
        },
    }


def _comparison(
    reference: list[dict[str, Any]], candidate: list[dict[str, Any]]
) -> dict[str, float]:
    ref = {record["checkpoint_id"]: record for record in reference}
    cand = {record["checkpoint_id"]: record for record in candidate}
    if set(ref) != set(cand):
        raise RuntimeError("Paired policy comparison checkpoint mismatch")
    keys = sorted(ref)
    return {
        "success_rate_delta": _mean(
            float(ref[key]["execution"]["success"])
            - float(cand[key]["execution"]["success"])
            for key in keys
        ),
        "intervention_rate_delta": _mean(
            float(ref[key]["decision"]["action"] != "CONTINUE")
            - float(cand[key]["decision"]["action"] != "CONTINUE")
            for key in keys
        ),
        "average_tool_calls_delta": _mean(
            float(ref[key]["execution"]["extra_tool_calls"])
            - float(cand[key]["execution"]["extra_tool_calls"])
            for key in keys
        ),
        "average_latency_delta": _mean(
            float(ref[key]["execution"]["extra_latency_seconds"])
            - float(cand[key]["execution"]["extra_latency_seconds"])
            for key in keys
        ),
        "policy_regret_delta": _mean(
            float(ref[key]["regret"]) - float(cand[key]["regret"]) for key in keys
        ),
        "average_tokens_delta": _mean(
            float(ref[key]["execution"]["extra_tokens"])
            - float(cand[key]["execution"]["extra_tokens"])
            for key in keys
        ),
        "recovery_rate_delta": _mean(
            float(ref[key]["execution"]["success"])
            - float(cand[key]["execution"]["success"])
            for key in keys
            if not ref[key]["source_continue_success"]
        ),
        "unnecessary_intervention_indicator_delta": _mean(
            float(
                ref[key]["decision"]["action"] != "CONTINUE"
                and ref[key]["source_continue_success"]
            )
            - float(
                cand[key]["decision"]["action"] != "CONTINUE"
                and cand[key]["source_continue_success"]
            )
            for key in keys
        ),
    }


def _stratified_metrics_complete(metrics: Mapping[str, Any]) -> bool:
    required = {
        "task_count",
        "success_rate",
        "recovery_rate",
        "intervention_rate",
        "unnecessary_intervention_rate_among_interventions",
        "average_tokens",
        "std_tokens",
        "average_latency_seconds",
        "std_latency_seconds",
        "average_tool_calls",
        "std_tool_calls",
        "policy_regret_mean",
        "policy_regret_std",
    }
    return all(
        required.issubset(subset)
        for policy_metrics in metrics.values()
        for grouping in ("by_domain", "by_fault_type")
        for subset in policy_metrics[grouping].values()
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/day13_experiment.yaml")
    parser.add_argument("--templates", default="configs/task_templates.yaml")
    parser.add_argument("--output-root", default="runs/day13")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()

    started_at = datetime.now(timezone.utc)
    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("Unsupported Day 13 configuration")
    configured_policies = tuple(str(name) for name in config["policies"])
    if configured_policies != BASELINES + ABLATIONS:
        raise ValueError("Day 13 policy set does not match the fixed baselines and ablations")
    run_id = validate_identifier(
        args.run_id or _default_run_id(started_at, args.smoke), "run_id"
    )
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
        raise RuntimeError("Unexpected Day 13 checkpoint count")
    policy_names = configured_policies
    if args.smoke:
        prepared = prepared[:2]
        policy_names = ("vanilla", "full")

    tasks = generate_task_instances(load_task_templates(args.templates))
    tasks_by_id = {task.task_id: task for task in tasks}
    manager = CheckpointManager(source_run / "checkpoints")
    models = PolicyModelBundle.load(
        risk_metadata_path=config["risk_model_metadata"],
        utility_metadata_path=config["utility_model_metadata"],
        risk_model_name=str(config["risk_model_name"]),
        expected_catalog_sha256=str(config["catalog_sha256"]),
    )
    policies = build_experiment_policies(models=models, policy_config=config)
    source_values = _source_values(source_run, models.cost_weights, models.utility_lambda)
    results_writer = JsonlTraceWriter(output_directory / "policy_task_results.jsonl")
    branch_writer = JsonlTraceWriter(output_directory / "selected_branch_outcomes.jsonl")
    records_by_policy: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for policy_name in policy_names:
        policy = policies[policy_name]
        runner = InterventionBranchRunner(
            checkpoint_manager=manager,
            branch_root=output_directory / "policy_branches" / policy_name,
            outcome_writer=branch_writer,
            experiment_id=str(config["experiment_id"]),
            run_id=run_id,
            controller_type=f"day13_{policy_name}",
            model_id="day13-baseline-ablation",
            model_version="day13-v1",
            prompt_version="day13-v1",
            tool_version="day9-v1",
            branch_id_prefix=policy_name,
        )
        for checkpoint in prepared:
            task = tasks_by_id[checkpoint.task_id]
            decision_environment = TaskEnvironment(
                output_directory
                / "decision_states"
                / policy_name
                / checkpoint.checkpoint_id,
                task,
            )
            restored = manager.restore(
                checkpoint_id=checkpoint.checkpoint_id,
                environment=decision_environment,
            )
            if restored.restored_state_hash != checkpoint.state_hash:
                raise RuntimeError(f"Restore hash mismatch for {policy_name}/{checkpoint.checkpoint_id}")
            decision = policy.decide(runtime=restored.runtime_state, domain=task.domain)
            if decision.action == Action.STOP:
                execution = {
                    "action": "STOP",
                    "success": False,
                    "validation_passed": False,
                    "termination_reason": "policy_stop",
                    "extra_tokens": 0,
                    "extra_tool_calls": 0,
                    "extra_latency_seconds": 0.0,
                    "initial_state_hash": checkpoint.state_hash,
                    "restored_state_hash": checkpoint.state_hash,
                    "branch_id": f"{policy_name}_{checkpoint.checkpoint_id}_stop",
                }
            else:
                execution = runner.run_action(
                    prepared=checkpoint, task=task, action=decision.action
                )
            paired = source_values[checkpoint.checkpoint_id]
            continue_source = paired[Action.CONTINUE.value]
            if decision.action == Action.STOP:
                selected_value = 0.0
            else:
                if decision.action.value not in paired:
                    raise RuntimeError(
                        f"Policy selected inapplicable action {decision.action.value}"
                    )
                selected_value = float(paired[decision.action.value]["observed_value"])
            oracle_value = max(float(value["observed_value"]) for value in paired.values())
            record = {
                "record_type": "day13_policy_task",
                "experiment_id": config["experiment_id"],
                "run_id": run_id,
                "policy": policy_name,
                "checkpoint_id": checkpoint.checkpoint_id,
                "task_id": task.task_id,
                "template_id": task.template_id,
                "split": checkpoint.split,
                "domain": task.domain.value,
                "fault_type": (
                    checkpoint.scenario.fault_type.value
                    if checkpoint.scenario.fault_type is not None
                    else "F0_NO_FAULT"
                ),
                "scenario_id": checkpoint.scenario.scenario_id,
                "decision": decision.to_dict(),
                "execution": execution,
                "source_continue_success": bool(continue_source["success"]),
                "oracle_observed_value": oracle_value,
                "selected_observed_value": selected_value,
                "regret": max(0.0, oracle_value - selected_value),
                "regret_source": "day9_paired_applicable_branch_outcomes",
            }
            results_writer.append(record)
            records_by_policy[policy_name].append(record)

    metrics = {
        policy_name: _policy_metrics(records_by_policy[policy_name])
        for policy_name in policy_names
    }
    _write_json(output_directory / "experiment_metrics.json", metrics)
    if all(name in records_by_policy for name in BASELINES + ABLATIONS):
        comparisons = {
            "full_vs_baselines": {
                name: _comparison(records_by_policy["full"], records_by_policy[name])
                for name in BASELINES
            },
            "full_vs_ablations": {
                name: _comparison(records_by_policy["full"], records_by_policy[name])
                for name in ABLATIONS
                if name != "full"
            },
        }
    else:
        comparisons = {
            "full_vs_smoke_vanilla": _comparison(
                records_by_policy["full"], records_by_policy["vanilla"]
            )
        }
    _write_json(output_directory / "paired_comparisons.json", comparisons)

    all_records = [record for group in records_by_policy.values() for record in group]
    expected_records = len(policy_names) * len(prepared)
    branch_ids = [str(record["execution"]["branch_id"]) for record in all_records]
    checks = {
        "source_day9_passed": bool(source_summary.get("passed")),
        "expected_policy_task_records": len(all_records) == expected_records,
        "unique_policy_checkpoint_pairs": len(
            {(record["policy"], record["checkpoint_id"]) for record in all_records}
        )
        == expected_records,
        "unique_branch_ids": len(branch_ids) == len(set(branch_ids)),
        "selected_actions_not_excluded": all(
            record["decision"]["action"] not in record["decision"]["excluded_actions"]
            for record in all_records
        ),
        "selected_actions_applicable": all(
            record["decision"]["action"] == "STOP"
            or record["decision"]["action"] in source_values[record["checkpoint_id"]]
            for record in all_records
        ),
        "all_restores_match": all(
            record["execution"]["initial_state_hash"]
            == record["execution"]["restored_state_hash"]
            for record in all_records
        ),
        "all_records_train_split": all(record["split"] == "train" for record in all_records),
        "metrics_complete": all(
            all(
                key in metrics[name]
                for key in (
                    "task_success_rate",
                    "recovery_rate",
                    "intervention_rate",
                    "unnecessary_intervention_rate_among_interventions",
                    "average_tokens",
                    "average_latency_seconds",
                    "average_tool_calls",
                    "policy_regret_mean",
                    "by_domain",
                    "by_fault_type",
                )
            )
            for name in policy_names
        ),
        "stratified_metrics_complete": _stratified_metrics_complete(metrics),
        "full_policy_all_tasks_passed": all(
            record["execution"]["success"] for record in records_by_policy["full"]
        ),
    }
    if not args.smoke:
        checks.update(
            {
                "five_baselines_present": all(name in metrics for name in BASELINES),
                "four_ablations_present": all(name in metrics for name in ABLATIONS),
                "twenty_checkpoints_per_policy": all(
                    metrics[name]["task_count"] == 20 for name in policy_names
                ),
                "paired_comparisons_present": bool(comparisons["full_vs_baselines"])
                and bool(comparisons["full_vs_ablations"]),
            }
        )
    summary = {
        "schema_version": 1,
        "experiment_id": config["experiment_id"],
        "run_id": run_id,
        "created_at": started_at.isoformat(),
        "smoke": args.smoke,
        "evaluation_scope": config["evaluation_scope"],
        "source_day9_run": str(source_run),
        "catalog_sha256": config["catalog_sha256"],
        "checkpoint_count": len(prepared),
        "policy_count": len(policy_names),
        "policy_task_record_count": len(all_records),
        "policies": list(policy_names),
        "official_validation_metrics_available": False,
        "official_test_metrics_available": False,
        "regret_source": "day9_paired_applicable_branch_outcomes",
        "metrics": metrics,
        "checks": checks,
        "passed": all(checks.values()),
        "artifacts": {
            "task_results": str((output_directory / "policy_task_results.jsonl").resolve()),
            "branch_outcomes": str(
                (output_directory / "selected_branch_outcomes.jsonl").resolve()
            ),
            "metrics": str((output_directory / "experiment_metrics.json").resolve()),
            "paired_comparisons": str(
                (output_directory / "paired_comparisons.json").resolve()
            ),
        },
    }
    _write_json(output_directory / "main_experiment_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
