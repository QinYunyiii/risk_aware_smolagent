"""Build action-conditional utility examples from Day 9 branch records."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from risk_agent.benchmark.schema import TaskSpec
from risk_agent.monitoring import RuntimeState
from risk_agent.prediction.dataset import FEATURE_NAMES, extract_risk_features, sha256_file

from .schema import UtilityExample


ACTIONS = ("CONTINUE", "RETRY", "REPLAN")


def build_utility_dataset(
    *, source_run: str | Path, tasks_by_id: Mapping[str, TaskSpec]
) -> tuple[tuple[UtilityExample, ...], dict[str, object]]:
    run = Path(source_run)
    manifest_path = run / "checkpoint_manifest.json"
    outcomes_path = run / "branch_outcomes.jsonl"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checkpoints = manifest.get("checkpoints")
    if not isinstance(checkpoints, list) or not checkpoints:
        raise ValueError("Day 9 checkpoint manifest is empty or malformed")
    outcomes = [
        json.loads(line)
        for line in outcomes_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    outcomes_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for outcome in outcomes:
        key = (str(outcome["checkpoint_id"]), str(outcome["action"]))
        if key in outcomes_by_key:
            raise ValueError(f"Duplicate branch outcome: {key}")
        outcomes_by_key[key] = outcome

    examples: list[UtilityExample] = []
    for checkpoint in checkpoints:
        checkpoint_id = str(checkpoint["checkpoint_id"])
        task_id = str(checkpoint["task_id"])
        task = tasks_by_id[task_id]
        if task.split is None or task.split.value != checkpoint.get("split"):
            raise ValueError(f"Checkpoint/task split mismatch for {checkpoint_id}")
        runtime_path = run / "checkpoints" / checkpoint_id / "runtime_state.json"
        runtime = RuntimeState.from_dict(json.loads(runtime_path.read_text(encoding="utf-8")))
        features = extract_risk_features(runtime, task.domain)
        for action in ACTIONS:
            outcome = outcomes_by_key.get((checkpoint_id, action))
            if outcome is None:
                raise ValueError(f"Missing {action} record for {checkpoint_id}")
            applicable = bool(outcome["action_applicable"])
            if outcome.get("task_id") != task_id or outcome.get("split") != task.split.value:
                raise ValueError(f"Branch metadata mismatch for {checkpoint_id}/{action}")
            if applicable and not outcome.get("branch_created"):
                raise ValueError(f"Applicable branch was not executed: {checkpoint_id}/{action}")
            if not applicable and outcome.get("branch_created"):
                raise ValueError(f"Inapplicable branch was unexpectedly executed: {checkpoint_id}/{action}")
            examples.append(
                UtilityExample(
                    checkpoint_id=checkpoint_id,
                    task_id=task_id,
                    template_id=task.template_id,
                    split=task.split.value,
                    action=action,
                    action_applicable=applicable,
                    applicability_reason=str(outcome["applicability_reason"]),
                    outcome_success=int(bool(outcome["success"])) if applicable else None,
                    extra_tokens=float(outcome["extra_tokens"]) if applicable else None,
                    extra_tool_calls=float(outcome["extra_tool_calls"]) if applicable else None,
                    extra_latency_seconds=(
                        float(outcome["extra_latency_seconds"]) if applicable else None
                    ),
                    features=features,
                )
            )

    applicable = [example for example in examples if example.action_applicable]
    coverage = {
        action: {
            "total": sum(example.action == action for example in examples),
            "applicable": sum(
                example.action == action and example.action_applicable for example in examples
            ),
            "success": sum(
                example.action == action and example.outcome_success == 1 for example in examples
            ),
            "failure": sum(
                example.action == action and example.outcome_success == 0 for example in examples
            ),
        }
        for action in ACTIONS
    }
    reason_counts = Counter(
        example.applicability_reason for example in examples if not example.action_applicable
    )
    audit: dict[str, object] = {
        "schema_version": 1,
        "feature_schema_version": 1,
        "feature_names": list(FEATURE_NAMES),
        "record_count": len(examples),
        "checkpoint_count": len(checkpoints),
        "applicable_count": len(applicable),
        "inapplicable_count": len(examples) - len(applicable),
        "coverage": coverage,
        "inapplicable_reason_counts": dict(sorted(reason_counts.items())),
        "feature_source": "checkpoint runtime_state only",
        "outcome_and_cost_source": "applicable Day 9 branch outcome only",
        "source_sha256": {
            "checkpoint_manifest": sha256_file(manifest_path),
            "branch_outcomes": sha256_file(outcomes_path),
        },
        "checks": {
            "three_action_records_per_checkpoint": len(examples) == len(checkpoints) * 3,
            "features_are_allowlisted": all(
                tuple(example.features) == FEATURE_NAMES for example in examples
            ),
            "inapplicable_labels_are_null": all(
                example.outcome_success is None
                and example.extra_tokens is None
                and example.extra_tool_calls is None
                and example.extra_latency_seconds is None
                for example in examples
                if not example.action_applicable
            ),
            "applicable_costs_nonnegative": all(
                float(example.extra_tokens) >= 0
                and float(example.extra_tool_calls) >= 0
                and float(example.extra_latency_seconds) >= 0
                for example in applicable
            ),
            "all_records_train_split": all(example.split == "train" for example in examples),
        },
    }
    if not all(audit["checks"].values()):
        raise RuntimeError("Utility dataset audit failed")
    return tuple(examples), audit
