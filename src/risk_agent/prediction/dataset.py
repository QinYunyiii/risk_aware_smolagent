"""Build risk examples without exposing post-checkpoint outcomes as features."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from risk_agent.benchmark.schema import Domain, TaskSpec
from risk_agent.monitoring import RuntimeState

from .schema import RiskExample


FAULT_TYPES = (
    "F1_TIMEOUT",
    "F2_API_503",
    "F3_INVALID_ARGUMENT",
    "F4_STALE_OBSERVATION",
    "F5_REPEATED_ACTION",
    "F6_PARTIAL_FAILURE",
)
DOMAINS = tuple(domain.value for domain in Domain)
FEATURE_NAMES = (
    "execution_progress_fraction",
    "tool_call_count",
    "observed_history_count",
    "error_count",
    "repeated_action_count",
    "no_progress_count",
    "recent_latency_mean_seconds",
    "recent_latency_max_seconds",
    "historical_tool_success_rate",
    "tokens_used",
    "token_budget_fraction",
    "tool_budget_fraction",
    "latency_budget_fraction",
    "pending_operation_read_only",
    "failure_present",
    "failure_response_received",
    *(f"fault_{fault.lower()}" for fault in FAULT_TYPES),
    *(f"domain_{domain}" for domain in DOMAINS),
)
_BANNED_FEATURE_TERMS = (
    "validation",
    "final",
    "outcome",
    "branch",
    "future",
    "result",
)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _signature(tool_name: str, arguments: Mapping[str, Any]) -> str:
    return f"{tool_name}:{json.dumps(arguments, sort_keys=True, default=str)}"


def extract_risk_features(runtime: RuntimeState, domain: Domain) -> dict[str, float]:
    """Extract only information available at the checkpoint decision time."""

    history = runtime.tool_history
    budget = runtime.budget
    errors = [entry for entry in history if entry.error_type is not None]
    signatures = [_signature(entry.tool_name, entry.tool_arguments) for entry in history]
    repeated = sum(
        current == previous for previous, current in zip(signatures, signatures[1:])
    )
    no_progress = sum(
        entry.tool_result is None and entry.error_type is not None for entry in history
    )
    recent_latencies = [entry.latency_seconds for entry in history[-3:]]
    successful_history = sum(
        entry.error_type is None and entry.tool_result is not None for entry in history
    )
    success_rate = successful_history / len(history) if history else 0.5
    pending = runtime.tool_state.get("pending_operation")
    pending_mapping = pending if isinstance(pending, Mapping) else {}
    failure = runtime.tool_state.get("last_failure")
    failure_mapping = failure if isinstance(failure, Mapping) else {}
    fault_type = str(failure_mapping.get("fault_type", ""))
    progress_denominator = max(1, budget.tool_calls_used + 1)

    values: dict[str, float] = {
        "execution_progress_fraction": budget.tool_calls_used / progress_denominator,
        "tool_call_count": float(budget.tool_calls_used),
        "observed_history_count": float(len(history)),
        "error_count": float(len(errors)),
        "repeated_action_count": float(repeated),
        "no_progress_count": float(no_progress),
        "recent_latency_mean_seconds": (
            sum(recent_latencies) / len(recent_latencies) if recent_latencies else 0.0
        ),
        "recent_latency_max_seconds": max(recent_latencies, default=0.0),
        "historical_tool_success_rate": float(success_rate),
        "tokens_used": float(budget.tokens_used),
        "token_budget_fraction": budget.tokens_used / budget.max_tokens,
        "tool_budget_fraction": budget.tool_calls_used / budget.max_tool_calls,
        "latency_budget_fraction": budget.latency_seconds_used / budget.max_latency_seconds,
        "pending_operation_read_only": float(bool(pending_mapping.get("read_only", False))),
        "failure_present": float(bool(failure_mapping)),
        "failure_response_received": float(bool(failure_mapping.get("response_received", False))),
    }
    values.update(
        {f"fault_{fault.lower()}": float(fault_type == fault) for fault in FAULT_TYPES}
    )
    values.update(
        {f"domain_{candidate}": float(domain.value == candidate) for candidate in DOMAINS}
    )
    if tuple(values) != FEATURE_NAMES:
        raise RuntimeError("Risk feature schema order changed unexpectedly")
    if any(term in name for name in values for term in _BANNED_FEATURE_TERMS):
        raise RuntimeError("Risk feature schema contains a post-decision field")
    if any(not isinstance(value, float) for value in values.values()):
        raise TypeError("Risk features must be numeric floats")
    return values


def _load_json_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def build_risk_dataset(
    *,
    source_run: str | Path,
    tasks_by_id: Mapping[str, TaskSpec],
    label_action: str = "CONTINUE",
) -> tuple[tuple[RiskExample, ...], dict[str, object]]:
    """Join checkpoint states to Continue outcomes while keeping the paths separate."""

    run = Path(source_run)
    manifest_path = run / "checkpoint_manifest.json"
    outcomes_path = run / "branch_outcomes.jsonl"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checkpoints = manifest.get("checkpoints")
    if not isinstance(checkpoints, list) or not checkpoints:
        raise ValueError("Day 9 checkpoint manifest is empty or malformed")
    outcomes = _load_json_lines(outcomes_path)
    labels: dict[str, dict[str, Any]] = {}
    for outcome in outcomes:
        if outcome.get("action") != label_action:
            continue
        checkpoint_id = str(outcome["checkpoint_id"])
        if checkpoint_id in labels:
            raise ValueError(f"Duplicate {label_action} outcome for {checkpoint_id}")
        if not outcome.get("action_applicable") or not outcome.get("branch_created"):
            raise ValueError(f"{label_action} was not executed for {checkpoint_id}")
        labels[checkpoint_id] = outcome
    if len(labels) != len(checkpoints):
        raise ValueError("Every checkpoint must have exactly one executed Continue outcome")

    examples: list[RiskExample] = []
    for checkpoint in checkpoints:
        checkpoint_id = str(checkpoint["checkpoint_id"])
        task_id = str(checkpoint["task_id"])
        task = tasks_by_id[task_id]
        if task.split is None or task.split.value != checkpoint.get("split"):
            raise ValueError(f"Checkpoint/task split mismatch for {checkpoint_id}")
        runtime_path = run / "checkpoints" / checkpoint_id / "runtime_state.json"
        runtime = RuntimeState.from_dict(json.loads(runtime_path.read_text(encoding="utf-8")))
        outcome = labels[checkpoint_id]
        if outcome.get("task_id") != task_id or outcome.get("split") != task.split.value:
            raise ValueError(f"Continue label metadata mismatch for {checkpoint_id}")
        examples.append(
            RiskExample(
                checkpoint_id=checkpoint_id,
                task_id=task_id,
                template_id=task.template_id,
                split=task.split.value,
                label_failure=int(not bool(outcome["success"])),
                features=extract_risk_features(runtime, task.domain),
            )
        )

    split_counts = {
        split: sum(example.split == split for example in examples)
        for split in sorted({example.split for example in examples})
    }
    audit: dict[str, object] = {
        "schema_version": 1,
        "feature_schema_version": 1,
        "feature_names": list(FEATURE_NAMES),
        "feature_count": len(FEATURE_NAMES),
        "sample_count": len(examples),
        "positive_failure_count": sum(example.label_failure for example in examples),
        "negative_success_count": sum(1 - example.label_failure for example in examples),
        "template_count": len({example.template_id for example in examples}),
        "split_counts": split_counts,
        "label_source": f"{label_action}.success inverted",
        "feature_source": "checkpoint runtime_state only",
        "outcome_fields_used_as_features": [],
        "source_sha256": {
            "checkpoint_manifest": sha256_file(manifest_path),
            "branch_outcomes": sha256_file(outcomes_path),
        },
        "checks": {
            "one_continue_label_per_checkpoint": len(labels) == len(checkpoints),
            "features_are_allowlisted": all(
                tuple(example.features) == FEATURE_NAMES for example in examples
            ),
            "no_post_decision_feature_names": not any(
                term in name for name in FEATURE_NAMES for term in _BANNED_FEATURE_TERMS
            ),
            "template_ids_unique_in_initial_dataset": len(
                {example.template_id for example in examples}
            )
            == len(examples),
            "official_test_set_not_present": all(example.split != "test" for example in examples),
        },
    }
    if not all(audit["checks"].values()):
        raise RuntimeError("Risk dataset leakage audit failed")
    return tuple(examples), audit
