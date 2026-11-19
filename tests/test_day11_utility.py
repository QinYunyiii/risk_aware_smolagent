from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import joblib

from risk_agent.benchmark import generate_task_instances, load_task_templates
from risk_agent.monitoring import BudgetState, RuntimeState
from risk_agent.prediction import extract_risk_features
from risk_agent.utility import (
    BetaBernoulliEstimator,
    UtilityExample,
    build_utility_dataset,
    train_and_evaluate_utility_models,
)


ROOT = Path(__file__).parents[1]
TEMPLATES = ROOT / "configs/task_templates.yaml"


def _train_tasks(limit: int):
    tasks = generate_task_instances(load_task_templates(TEMPLATES))
    return [
        task
        for task in tasks
        if task.instance_id.endswith("_001") and task.split.value == "train"
    ][:limit]


def _features(task, failed: bool):
    state = RuntimeState(
        seed=task.seed,
        tool_state={
            "pending_operation": {
                "tool_name": task.reference_steps[-1].tool,
                "arguments": dict(task.reference_steps[-1].arguments),
                "read_only": True,
            },
            **(
                {
                    "last_failure": {
                        "fault_type": "F3_INVALID_ARGUMENT",
                        "response_received": True,
                    }
                }
                if failed
                else {}
            ),
        },
        budget=BudgetState(
            max_tokens=1000,
            max_tool_calls=10,
            max_latency_seconds=10,
            tokens_used=100 if failed else 0,
            tool_calls_used=1 if failed else 0,
        ),
    )
    return extract_risk_features(state, task.domain)


def _examples() -> list[UtilityExample]:
    examples = []
    for index, task in enumerate(_train_tasks(20), start=1):
        failed = index % 2 == 0
        features = _features(task, failed)
        applicability = {
            "CONTINUE": True,
            "RETRY": index <= 6,
            "REPLAN": index <= 10,
        }
        for action in ("CONTINUE", "RETRY", "REPLAN"):
            applicable = applicability[action]
            success = (not failed) if action == "CONTINUE" else True
            examples.append(
                UtilityExample(
                    checkpoint_id=f"cp_{index:03d}",
                    task_id=task.task_id,
                    template_id=task.template_id,
                    split="train",
                    action=action,
                    action_applicable=applicable,
                    applicability_reason="fixture applicable" if applicable else "fixture excluded",
                    outcome_success=int(success) if applicable else None,
                    extra_tokens=0.0 if applicable else None,
                    extra_tool_calls=(
                        float(2 if action == "REPLAN" and index == 1 else 1)
                        if applicable
                        else None
                    ),
                    extra_latency_seconds=float(index) / 1000 if applicable else None,
                    features=features,
                )
            )
    return examples


def _config() -> dict:
    return {
        "actions": ["CONTINUE", "RETRY", "REPLAN"],
        "random_seed": 11,
        "cv_folds": 5,
        "hyperparameter_search": False,
        "outcome_model": {
            "C": 1.0,
            "max_iter": 500,
            "class_weight": "balanced",
            "beta_prior_alpha": 1.0,
            "beta_prior_beta": 1.0,
        },
        "cost_model": {"ridge_alpha": 1.0},
        "cost_weights": {"tokens": 0.001, "tool_calls": 1.0, "latency_seconds": 1.0},
        "utility_lambda": 0.1,
    }


def test_beta_bernoulli_smooths_single_class_probability() -> None:
    estimator = BetaBernoulliEstimator(alpha=1, beta=1)
    estimator.fit(np.zeros((6, 2)), np.ones(6, dtype=int))
    probabilities = estimator.predict_proba(np.zeros((2, 2)))[:, 1]
    assert np.allclose(probabilities, 7 / 8)
    assert np.all((probabilities >= 0) & (probabilities <= 1))


def test_inapplicable_example_rejects_labels() -> None:
    task = _train_tasks(1)[0]
    try:
        UtilityExample(
            checkpoint_id="cp_bad",
            task_id=task.task_id,
            template_id=task.template_id,
            split="train",
            action="RETRY",
            action_applicable=False,
            applicability_reason="not safe",
            outcome_success=0,
            extra_tokens=0,
            extra_tool_calls=0,
            extra_latency_seconds=0,
            features=_features(task, False),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Inapplicable action accepted outcome labels")


def test_dataset_keeps_inapplicable_actions_unlabelled(tmp_path) -> None:
    tasks = _train_tasks(2)
    tasks_by_id = {task.task_id: task for task in tasks}
    source = tmp_path / "day9"
    checkpoints = []
    outcomes = []
    for index, task in enumerate(tasks, start=1):
        checkpoint_id = f"cp_{index:03d}"
        checkpoints.append(
            {"checkpoint_id": checkpoint_id, "task_id": task.task_id, "split": "train"}
        )
        runtime = RuntimeState(
            seed=task.seed,
            tool_state={
                "pending_operation": {
                    "tool_name": task.reference_steps[-1].tool,
                    "arguments": dict(task.reference_steps[-1].arguments),
                    "read_only": True,
                }
            },
        )
        runtime_path = source / "checkpoints" / checkpoint_id / "runtime_state.json"
        runtime_path.parent.mkdir(parents=True)
        runtime_path.write_text(json.dumps(runtime.to_dict()), encoding="utf-8")
        for action in ("CONTINUE", "RETRY", "REPLAN"):
            applicable = action == "CONTINUE" or (index == 1 and action == "RETRY") or (
                index == 2 and action == "REPLAN"
            )
            outcomes.append(
                {
                    "checkpoint_id": checkpoint_id,
                    "task_id": task.task_id,
                    "split": "train",
                    "action": action,
                    "action_applicable": applicable,
                    "applicability_reason": "fixture applicable" if applicable else "fixture excluded",
                    "branch_created": applicable,
                    "success": applicable,
                    "extra_tokens": 0,
                    "extra_tool_calls": 1 if applicable else 0,
                    "extra_latency_seconds": 0.01 if applicable else 0,
                }
            )
    source.mkdir(exist_ok=True)
    (source / "checkpoint_manifest.json").write_text(
        json.dumps({"checkpoints": checkpoints}), encoding="utf-8"
    )
    (source / "branch_outcomes.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in outcomes), encoding="utf-8"
    )
    examples, audit = build_utility_dataset(source_run=source, tasks_by_id=tasks_by_id)
    assert len(examples) == 6
    assert audit["applicable_count"] == 4
    assert audit["inapplicable_count"] == 2
    assert all(audit["checks"].values())
    assert all(
        example.outcome_success is None
        and example.extra_tokens is None
        and example.extra_tool_calls is None
        and example.extra_latency_seconds is None
        for example in examples
        if not example.action_applicable
    )


def test_t_learner_outputs_probabilities_costs_and_regret(tmp_path) -> None:
    result = train_and_evaluate_utility_models(
        examples=_examples(),
        config=_config(),
        model_directory=tmp_path / "models",
    )
    applicable = [record for record in result["predictions"] if record["action_applicable"]]
    excluded = [record for record in result["predictions"] if not record["action_applicable"]]
    assert len(applicable) == 36
    assert len(excluded) == 24
    assert all(0 <= record["predicted_success_probability"] <= 1 for record in applicable)
    assert all(
        all(value >= 0 for value in record["predicted_cost_components"].values())
        for record in applicable
    )
    assert all(record["predicted_success_probability"] is None for record in excluded)
    assert result["outcome_metrics"]["RETRY"]["model_kind"] == "beta_bernoulli_constant"
    assert result["outcome_metrics"]["REPLAN"]["model_kind"] == "beta_bernoulli_constant"
    assert result["utility_metrics"]["policy_regret_mean"] >= 0
    assert result["utility_metrics"]["outcome_brier_all_applicable"] >= 0
    assert result["utility_metrics"]["weighted_cost_mae_all_applicable"] >= 0
    assert len(result["policy_records"]) == 20
    assert all(Path(paths["outcome_model"]).is_file() for paths in result["models"].values())
    sample = np.zeros((2, len(next(iter(_examples())).features)))
    for paths in result["models"].values():
        for path in paths["cost_models"].values():
            model = joblib.load(path)
            assert np.all(model.predict(sample) >= 0)
