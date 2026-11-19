from __future__ import annotations

import json
from pathlib import Path

from risk_agent.benchmark import generate_task_instances, load_task_templates
from risk_agent.monitoring import BudgetState, RuntimeState, ToolHistoryEntry
from risk_agent.prediction import (
    FEATURE_NAMES,
    RiskExample,
    build_risk_dataset,
    extract_risk_features,
    train_and_evaluate_risk_models,
)


ROOT = Path(__file__).parents[1]
TEMPLATES = ROOT / "configs/task_templates.yaml"


def _one_instance_per_template(limit: int):
    tasks = generate_task_instances(load_task_templates(TEMPLATES))
    return [
        task
        for task in tasks
        if task.instance_id.endswith("_001") and task.split.value == "train"
    ][:limit]


def _runtime(*, failed: bool, seed: int) -> RuntimeState:
    history = []
    tool_state = {
        "pending_operation": {
            "tool_name": "filter_data",
            "arguments": {"path": "data/input.csv"},
            "read_only": True,
        }
    }
    if failed:
        history.append(
            ToolHistoryEntry(
                step_id="failure_001",
                tool_name="filter_data",
                tool_arguments={"path": "data/input.csv"},
                tool_result=None,
                error_type="F3_INVALID_ARGUMENT_checkpoint",
                timestamp="2026-01-01T00:00:00+00:00",
                token_usage={"total_tokens": 100},
                latency_seconds=0.2,
                worker_id="worker-0",
                environment_id="fixture",
                checkpoint_id="fixture",
            )
        )
        tool_state["last_failure"] = {
            "fault_type": "F3_INVALID_ARGUMENT",
            "response_received": True,
        }
    return RuntimeState(
        seed=seed,
        tool_history=history,
        tool_state=tool_state,
        budget=BudgetState(
            max_tokens=1000,
            max_tool_calls=10,
            max_latency_seconds=10.0,
            tokens_used=100 if failed else 0,
            tool_calls_used=1 if failed else 0,
            latency_seconds_used=0.2 if failed else 0.0,
        ),
    )


def test_feature_extractor_has_fixed_numeric_leakage_safe_schema() -> None:
    tasks = generate_task_instances(load_task_templates(TEMPLATES))
    features = extract_risk_features(_runtime(failed=True, seed=1), tasks[0].domain)
    assert tuple(features) == FEATURE_NAMES
    assert all(isinstance(value, float) for value in features.values())
    assert not any(
        term in name
        for name in features
        for term in ("validation", "final", "outcome", "result", "branch")
    )


def test_dataset_joins_only_continue_label_to_checkpoint_features(tmp_path) -> None:
    tasks = _one_instance_per_template(10)
    tasks_by_id = {task.task_id: task for task in tasks}
    source = tmp_path / "day9"
    checkpoints = []
    outcomes = []
    for index, task in enumerate(tasks, start=1):
        checkpoint_id = f"fixture_cp_{index:03d}"
        checkpoints.append(
            {
                "checkpoint_id": checkpoint_id,
                "task_id": task.task_id,
                "split": task.split.value,
            }
        )
        runtime_path = source / "checkpoints" / checkpoint_id / "runtime_state.json"
        runtime_path.parent.mkdir(parents=True)
        runtime_path.write_text(
            json.dumps(_runtime(failed=index % 2 == 0, seed=task.seed).to_dict()),
            encoding="utf-8",
        )
        outcomes.append(
            {
                "checkpoint_id": checkpoint_id,
                "task_id": task.task_id,
                "split": task.split.value,
                "action": "CONTINUE",
                "action_applicable": True,
                "branch_created": True,
                "success": index % 2 == 1,
                "validation_passed": index % 2 == 1,
                "result": "future value that must not enter features",
            }
        )
    (source / "checkpoint_manifest.json").write_text(
        json.dumps({"checkpoints": checkpoints}), encoding="utf-8"
    )
    (source / "branch_outcomes.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in outcomes), encoding="utf-8"
    )
    examples, audit = build_risk_dataset(source_run=source, tasks_by_id=tasks_by_id)
    assert len(examples) == 10
    assert sum(example.label_failure for example in examples) == 5
    assert audit["outcome_fields_used_as_features"] == []
    assert all(audit["checks"].values())


def test_models_produce_grouped_oof_metrics_and_saved_artifacts(tmp_path) -> None:
    tasks = _one_instance_per_template(20)
    examples = []
    for index, task in enumerate(tasks):
        failed = index % 2 == 0
        examples.append(
            RiskExample(
                checkpoint_id=f"cp_{index:03d}",
                task_id=task.task_id,
                template_id=task.template_id,
                split=task.split.value,
                label_failure=int(failed),
                features=extract_risk_features(
                    _runtime(failed=failed, seed=task.seed), task.domain
                ),
            )
        )
    config = {
        "random_seed": 7,
        "cv_folds": 5,
        "decision_threshold": 0.5,
        "hyperparameter_search": False,
        "logistic_regression": {"C": 1.0, "max_iter": 500, "class_weight": "balanced"},
        "lightgbm": {
            "n_estimators": 20,
            "learning_rate": 0.1,
            "num_leaves": 7,
            "max_depth": 3,
            "min_child_samples": 2,
            "reg_lambda": 1.0,
            "class_weight": "balanced",
        },
    }
    result = train_and_evaluate_risk_models(
        examples=examples,
        config=config,
        model_directory=tmp_path / "models",
    )
    assert result["cv"]["all_fold_template_overlaps_empty"]
    assert result["cv"]["official_test_set_accessed"] is False
    assert len(result["predictions"]) == 40
    for model in result["models"].values():
        assert Path(model["model_path"]).is_file()
        assert set(model["oof_metrics"]) == {
            "auroc",
            "auprc",
            "brier",
            "precision",
            "recall",
        }
