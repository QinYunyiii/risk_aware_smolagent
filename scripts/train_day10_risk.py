#!/usr/bin/env python3
"""Build the Day 10 risk dataset and train fixed Logistic/LightGBM models."""

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
from risk_agent.prediction import build_risk_dataset, train_and_evaluate_risk_models


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _default_run_id(started_at: datetime) -> str:
    return f"day10_{started_at.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/day10_risk.yaml")
    parser.add_argument("--templates", default="configs/task_templates.yaml")
    parser.add_argument("--output-root", default="runs/day10")
    parser.add_argument("--model-root", default="models/risk")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()

    started_at = datetime.now(timezone.utc)
    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("Unsupported Day 10 configuration")
    if config.get("label_action") != "CONTINUE" or config.get("positive_label") != "continue_failure":
        raise ValueError("Day 10 risk label must be failure under Continue")
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
        raise RuntimeError("Day 9 catalog hash does not match Day 10 configuration")

    tasks = generate_task_instances(load_task_templates(args.templates))
    tasks_by_id = {task.task_id: task for task in tasks}
    examples, leakage_audit = build_risk_dataset(
        source_run=source_run,
        tasks_by_id=tasks_by_id,
        label_action=str(config["label_action"]),
    )
    training = train_and_evaluate_risk_models(
        examples=examples,
        config=config,
        model_directory=model_directory,
    )

    dataset_document = {
        "schema_version": 1,
        "dataset_role": "day10_initial_risk",
        "run_id": run_id,
        "source_day9_run": str(source_run),
        "catalog_sha256": config["catalog_sha256"],
        "examples": [example.to_dict() for example in examples],
    }
    _write_json(output_directory / "risk_dataset.json", dataset_document)
    _write_json(output_directory / "leakage_audit.json", leakage_audit)
    predictions_path = output_directory / "oof_predictions.jsonl"
    predictions_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in training["predictions"]),
        encoding="utf-8",
    )
    metrics = {
        model_name: {
            key: value
            for key, value in result.items()
            if key not in {"calibration_curve", "feature_importance"}
        }
        for model_name, result in training["models"].items()
    }
    calibration = {
        model_name: result["calibration_curve"]
        for model_name, result in training["models"].items()
    }
    feature_importance = {
        model_name: {
            "kind": result["feature_importance_kind"],
            "values": result["feature_importance"],
        }
        for model_name, result in training["models"].items()
    }
    _write_json(output_directory / "metrics.json", metrics)
    _write_json(output_directory / "calibration_curves.json", calibration)
    _write_json(output_directory / "feature_importance.json", feature_importance)

    checks = {
        "day9_source_passed": bool(source_summary.get("passed")),
        "catalog_hash_matches": source_summary.get("catalog_sha256") == config["catalog_sha256"],
        "both_label_classes_present": {example.label_failure for example in examples} == {0, 1},
        "features_use_checkpoint_state_only": leakage_audit["feature_source"] == "checkpoint runtime_state only",
        "no_outcome_fields_used_as_features": leakage_audit["outcome_fields_used_as_features"] == [],
        "template_group_isolation": bool(training["cv"]["all_fold_template_overlaps_empty"]),
        "official_test_set_not_accessed": training["cv"]["official_test_set_accessed"] is False,
        "no_hyperparameter_search": training["cv"]["hyperparameter_search"] is False,
        "probabilities_in_unit_interval": all(
            0.0 <= record["oof_failure_probability"] <= 1.0
            for record in training["predictions"]
        ),
        "required_metrics_reported": all(
            all(
                result["oof_metrics"][name] is not None
                for name in ("auroc", "auprc", "brier", "precision", "recall")
            )
            for result in training["models"].values()
        ),
        "both_models_saved": all(
            Path(result["model_path"]).is_file() for result in training["models"].values()
        ),
    }
    summary = {
        "schema_version": 1,
        "experiment_id": config["experiment_id"],
        "run_id": run_id,
        "created_at": started_at.isoformat(),
        "source_day9_run": str(source_run),
        "catalog_sha256": config["catalog_sha256"],
        "sample_count": len(examples),
        "failure_count": sum(example.label_failure for example in examples),
        "success_count": sum(1 - example.label_failure for example in examples),
        "template_count": len({example.template_id for example in examples}),
        "official_split_counts": leakage_audit["split_counts"],
        "evaluation_scope": "train-only template-grouped out-of-fold",
        "official_validation_metrics_available": False,
        "official_test_metrics_available": False,
        "cv": training["cv"],
        "model_metrics": {
            name: result["oof_metrics"] for name, result in training["models"].items()
        },
        "checks": checks,
        "passed": all(checks.values()),
        "artifacts": {
            "risk_dataset": str((output_directory / "risk_dataset.json").resolve()),
            "leakage_audit": str((output_directory / "leakage_audit.json").resolve()),
            "oof_predictions": str(predictions_path.resolve()),
            "metrics": str((output_directory / "metrics.json").resolve()),
            "calibration_curves": str((output_directory / "calibration_curves.json").resolve()),
            "feature_importance": str((output_directory / "feature_importance.json").resolve()),
            "model_directory": str(model_directory.resolve()),
        },
    }
    _write_json(output_directory / "training_summary.json", summary)
    _write_json(
        model_directory / "model_metadata.json",
        {
            "schema_version": 1,
            "run_id": run_id,
            "feature_names": leakage_audit["feature_names"],
            "catalog_sha256": config["catalog_sha256"],
            "source_day9_run": str(source_run),
            "label": config["positive_label"],
            "models": {
                name: result["model_path"] for name, result in training["models"].items()
            },
        },
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
