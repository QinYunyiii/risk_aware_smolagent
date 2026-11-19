#!/usr/bin/env python3
"""Train and evaluate Day 11 action-conditional outcome and cost models."""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from risk_agent.benchmark import generate_task_instances, load_task_templates
from risk_agent.benchmark.schema import validate_identifier
from risk_agent.utility import build_utility_dataset, train_and_evaluate_utility_models


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _default_run_id(started_at: datetime) -> str:
    return f"day11_{started_at.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/day11_utility.yaml")
    parser.add_argument("--templates", default="configs/task_templates.yaml")
    parser.add_argument("--output-root", default="runs/day11")
    parser.add_argument("--model-root", default="models/utility")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()

    started_at = datetime.now(timezone.utc)
    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("Unsupported Day 11 configuration")
    run_id = validate_identifier(args.run_id or _default_run_id(started_at), "run_id")
    output_directory = Path(args.output_root) / run_id
    output_directory.mkdir(parents=True, exist_ok=False)
    model_directory = Path(args.model_root) / run_id

    source_run = Path(str(config["source_day9_run"]))
    source_summary = json.loads(
        (source_run / "intervention_summary.json").read_text(encoding="utf-8")
    )
    if not source_summary.get("passed"):
        raise RuntimeError("Day 9 source run did not pass its acceptance checks")
    if source_summary.get("catalog_sha256") != config["catalog_sha256"]:
        raise RuntimeError("Day 9 catalog hash does not match Day 11 configuration")

    tasks = generate_task_instances(load_task_templates(args.templates))
    tasks_by_id = {task.task_id: task for task in tasks}
    examples, audit = build_utility_dataset(
        source_run=source_run,
        tasks_by_id=tasks_by_id,
    )
    trained = train_and_evaluate_utility_models(
        examples=examples,
        config=config,
        model_directory=model_directory,
    )
    _write_json(
        output_directory / "utility_training_dataset.json",
        {
            "schema_version": 1,
            "dataset_role": "day11_initial_utility",
            "run_id": run_id,
            "source_day9_run": str(source_run),
            "catalog_sha256": config["catalog_sha256"],
            "examples": [example.to_dict() for example in examples],
        },
    )
    _write_json(output_directory / "coverage_audit.json", audit)
    predictions_path = output_directory / "oof_utility_predictions.jsonl"
    predictions_path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False) + "\n"
            for record in trained["predictions"]
        ),
        encoding="utf-8",
    )
    _write_json(output_directory / "outcome_metrics.json", trained["outcome_metrics"])
    _write_json(output_directory / "cost_metrics.json", trained["cost_metrics"])
    _write_json(output_directory / "utility_metrics.json", trained["utility_metrics"])
    _write_json(output_directory / "offline_policy_records.json", trained["policy_records"])
    _write_json(
        model_directory / "model_metadata.json",
        {
            "schema_version": 1,
            "run_id": run_id,
            "source_day9_run": str(source_run),
            "catalog_sha256": config["catalog_sha256"],
            "feature_names": audit["feature_names"],
            "models": trained["models"],
            "cost_weights": trained["cost_weights"],
            "utility_lambda": trained["utility_lambda"],
            "weight_selection": config["weight_selection"],
        },
    )

    applicable_predictions = [
        record for record in trained["predictions"] if record["action_applicable"]
    ]
    inapplicable_predictions = [
        record for record in trained["predictions"] if not record["action_applicable"]
    ]
    checks = {
        "day9_source_passed": bool(source_summary.get("passed")),
        "catalog_hash_matches": source_summary.get("catalog_sha256") == config["catalog_sha256"],
        "coverage_audit_passed": all(audit["checks"].values()),
        "probabilities_in_unit_interval": all(
            0.0 <= record["predicted_success_probability"] <= 1.0
            for record in applicable_predictions
        ),
        "predicted_costs_nonnegative": all(
            all(value >= 0 for value in record["predicted_cost_components"].values())
            for record in applicable_predictions
        ),
        "inapplicable_actions_excluded": all(
            record["predicted_success_probability"] is None
            and record["predicted_cost_components"] is None
            and record["predicted_relative_utility"] is None
            for record in inapplicable_predictions
        ),
        "all_required_metrics_reported": all(
            key in trained["utility_metrics"]
            for key in ("utility_mae_non_continue", "policy_regret_mean")
        )
        and all(
            "brier" in trained["outcome_metrics"][action]
            and "mae" in trained["outcome_metrics"][action]
            and "weighted_cost_mae" in trained["cost_metrics"][action]
            for action in config["actions"]
        ),
        "official_test_not_accessed": all(example.split == "train" for example in examples),
        "no_hyperparameter_search": config["hyperparameter_search"] is False,
        "all_models_saved": all(
            Path(paths["outcome_model"]).is_file()
            and all(Path(path).is_file() for path in paths["cost_models"].values())
            for paths in trained["models"].values()
        ),
    }
    summary = {
        "schema_version": 1,
        "experiment_id": config["experiment_id"],
        "run_id": run_id,
        "created_at": started_at.isoformat(),
        "source_day9_run": str(source_run),
        "catalog_sha256": config["catalog_sha256"],
        "record_count": audit["record_count"],
        "checkpoint_count": audit["checkpoint_count"],
        "applicable_count": audit["applicable_count"],
        "inapplicable_count": audit["inapplicable_count"],
        "coverage": audit["coverage"],
        "controller_type": "deterministic_local_reference",
        "evaluation_scope": "train-only template-grouped out-of-fold",
        "official_validation_metrics_available": False,
        "official_test_metrics_available": False,
        "cost_weights": trained["cost_weights"],
        "utility_lambda": trained["utility_lambda"],
        "weight_selection": config["weight_selection"],
        "outcome_metrics": trained["outcome_metrics"],
        "cost_metrics": trained["cost_metrics"],
        "utility_metrics": trained["utility_metrics"],
        "checks": checks,
        "passed": all(checks.values()),
        "artifacts": {
            "dataset": str((output_directory / "utility_training_dataset.json").resolve()),
            "coverage_audit": str((output_directory / "coverage_audit.json").resolve()),
            "oof_predictions": str(predictions_path.resolve()),
            "outcome_metrics": str((output_directory / "outcome_metrics.json").resolve()),
            "cost_metrics": str((output_directory / "cost_metrics.json").resolve()),
            "utility_metrics": str((output_directory / "utility_metrics.json").resolve()),
            "offline_policy_records": str(
                (output_directory / "offline_policy_records.json").resolve()
            ),
            "model_directory": str(model_directory.resolve()),
        },
    }
    _write_json(output_directory / "training_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
