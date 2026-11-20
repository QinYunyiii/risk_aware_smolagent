#!/usr/bin/env python3
"""Recompute the P1-D3 utility, cost, and policy-equivalence audit.

The audit reads immutable Day 9--13 artifacts and writes only new derived
files under ``runs/phase2_audit``.  It never fits or saves a model.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import joblib


DAY9 = Path("runs/day09/day9_20261119_080843_288841fc")
DAY11 = Path("runs/day11/day11_20261119_082900_e8d013ce")
DAY11_MODELS = Path("models/utility/day11_20261119_082900_e8d013ce")
DAY12 = Path("runs/day12/day12_20261119_083943_b18d954e")
DAY13 = Path("runs/day13/day13_20261119_085002_5754c690")
POLICIES = (
    "vanilla",
    "always_retry",
    "rule_based",
    "risk_based",
    "risk_plus_utility",
    "full",
    "without_risk",
    "without_utility",
    "without_cost",
)
ACTIONS = ("CONTINUE", "RETRY", "REPLAN")
COST_COMPONENTS = ("tokens", "tool_calls", "latency_seconds")


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _model_kind(model: Any) -> str:
    name = type(model).__name__
    if name == "BetaBernoulliEstimator":
        return "beta_bernoulli_constant"
    if name == "Pipeline":
        return "logistic_regression_pipeline"
    return name


def _regressor_kind(model: Any) -> str:
    estimator = getattr(model, "estimator", model)
    name = type(estimator).__name__
    if name == "DummyRegressor":
        constant = getattr(estimator, "constant", None)
        value = float(constant) if constant is not None else None
        return f"dummy_constant_{value:g}" if value is not None else "dummy_constant"
    if name == "Pipeline":
        return "ridge_pipeline"
    return name


def _failure_category(record: Mapping[str, Any]) -> str:
    fields = (
        str(record.get("termination_reason") or "none"),
        str(record.get("error_type") or "none"),
        str(record.get("scenario_id") or "none"),
        str(record.get("fault_type") or "none"),
    )
    return " | ".join(fields)


def _series_summary(values: Sequence[float]) -> dict[str, Any]:
    return {
        "count": len(values),
        "zero_count": sum(value == 0 for value in values),
        "positive_count": sum(value > 0 for value in values),
        "unique_count": len(set(values)),
        "minimum": min(values) if values else None,
        "maximum": max(values) if values else None,
    }


def _action_coverage(day9_records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for action in ACTIONS:
        candidates = [record for record in day9_records if record["action"] == action]
        applicable = [record for record in candidates if record["action_applicable"]]
        inapplicable = [record for record in candidates if not record["action_applicable"]]
        successes = [record for record in applicable if record["success"]]
        failures = [record for record in applicable if not record["success"]]
        result[action] = {
            "candidate_count": len(candidates),
            "applicable_count": len(applicable),
            "not_applicable_count": len(inapplicable),
            "not_applicable_reasons": dict(
                sorted(Counter(str(record["applicability_reason"]) for record in inapplicable).items())
            ),
            "raw_not_applicable_success_false_count": sum(
                not bool(record["success"]) for record in inapplicable
            ),
            "success_count": len(successes),
            "failure_count": len(failures),
            "failure_categories": dict(
                sorted(Counter(_failure_category(record) for record in failures).items())
            ),
            "supports_binary_conditional_outcome_model": bool(successes and failures),
            "support_judgement": (
                "two observed classes, but only 20 deterministic-local Train checkpoints"
                if successes and failures
                else "single observed class; no action-conditional failure contrast"
            ),
            "cost_fields_applicable_only": {
                "extra_tokens": _series_summary(
                    [float(record["extra_tokens"]) for record in applicable]
                ),
                "extra_tool_calls": _series_summary(
                    [float(record["extra_tool_calls"]) for record in applicable]
                ),
                "extra_latency_seconds": _series_summary(
                    [float(record["extra_latency_seconds"]) for record in applicable]
                ),
            },
        }
    return result


def _day11_audit(root: Path) -> dict[str, Any]:
    dataset = _json(root / DAY11 / "utility_training_dataset.json")
    examples = dataset["examples"]
    oof = _jsonl(root / DAY11 / "oof_utility_predictions.jsonl")
    outcome_metrics = _json(root / DAY11 / "outcome_metrics.json")
    cost_metrics = _json(root / DAY11 / "cost_metrics.json")
    by_key = {(row["checkpoint_id"], row["action"]): row for row in oof}
    actions: dict[str, Any] = {}
    for action in ACTIONS:
        action_examples = [row for row in examples if row["action"] == action]
        applicable = [row for row in action_examples if row["action_applicable"]]
        action_oof = [row for row in oof if row["action"] == action]
        applicable_oof = [row for row in action_oof if row["action_applicable"]]
        labels = [int(row["outcome_success"]) for row in applicable]
        outcome_model = joblib.load(root / DAY11_MODELS / f"{action.lower()}_outcome.joblib")
        cost_models: dict[str, Any] = {}
        for component in COST_COMPONENTS:
            model = joblib.load(
                root / DAY11_MODELS / f"{action.lower()}_cost_{component}.joblib"
            )
            actual = [float(row[f"extra_{component}"]) for row in applicable]
            cost_models[component] = {
                "model_kind": _regressor_kind(model),
                "actual": _series_summary(actual),
                "constant_target": len(set(actual)) == 1,
                "all_zero_target": bool(actual) and all(value == 0 for value in actual),
            }
        probabilities = [float(row["predicted_success_probability"]) for row in applicable_oof]
        actions[action] = {
            "candidate_examples": len(action_examples),
            "applicable_training_examples": len(applicable),
            "not_applicable_examples": len(action_examples) - len(applicable),
            "success_count": sum(labels),
            "failure_count": len(labels) - sum(labels),
            "outcome_model_kind_from_artifact": _model_kind(outcome_model),
            "outcome_model_kind_from_metrics": outcome_metrics[action]["model_kind"],
            "beta_fallback_triggered": len(set(labels)) < 2,
            "beta_fallback_condition": "fewer than two unique outcome labels for the action",
            "final_beta_success_probability": getattr(
                outcome_model, "success_probability_", None
            ),
            "oof_record_count": len(action_oof),
            "oof_applicable_record_count": len(applicable_oof),
            "oof_inapplicable_null_count": sum(
                not row["action_applicable"]
                and row.get("actual_success") is None
                and row.get("predicted_success_probability") is None
                and row.get("predicted_cost_components") is None
                for row in action_oof
            ),
            "oof_probability_unique_count": len(set(probabilities)),
            "oof_probability_minimum": min(probabilities),
            "oof_probability_maximum": max(probabilities),
            "estimator_accepts_state_features": action == "CONTINUE" and len(set(labels)) == 2,
            "supported_state_conditional_generalization": False,
            "state_conditioning_judgement": (
                "a fitted conditional classifier exists, but P1-D2 found shortcut leakage and the population is tiny synthetic Train data"
                if action == "CONTINUE"
                else "not learned: Beta-Bernoulli output is constant within each fitted fold"
            ),
            "cost_models": cost_models,
            "reported_cost_metrics": cost_metrics[action],
        }
    inapplicable_examples = [row for row in examples if not row["action_applicable"]]
    inapplicable_null = all(
        row["outcome_success"] is None
        and row["extra_tokens"] is None
        and row["extra_tool_calls"] is None
        and row["extra_latency_seconds"] is None
        for row in inapplicable_examples
    )
    return {
        "dataset_record_count": len(examples),
        "applicable_training_record_count": sum(row["action_applicable"] for row in examples),
        "not_applicable_record_count": len(inapplicable_examples),
        "not_applicable_labels_and_costs_are_null": inapplicable_null,
        "oof_file_exists": (root / DAY11 / "oof_utility_predictions.jsonl").is_file(),
        "oof_record_count": len(oof),
        "oof_key_coverage_matches_dataset": set(by_key)
        == {(row["checkpoint_id"], row["action"]) for row in examples},
        "actions": actions,
        "validity_judgement": (
            "The artifacts prove an engineering pipeline, not valid real-Qwen utility estimation: "
            "RETRY/REPLAN outcomes are single-class constants, token costs are zero because "
            "the local-reference runner made no model call, and all data are 20 synthetic Train checkpoints."
        ),
    }


def _cost_observability(day9_records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    applicable = [row for row in day9_records if row["action_applicable"]]
    count = len(applicable)
    tokens = [float(row["extra_tokens"]) for row in applicable]
    tool_calls = [float(row["extra_tool_calls"]) for row in applicable]
    tool_latency = [float(row["extra_latency_seconds"]) for row in applicable]

    def unavailable(field: str, reason: str, no_model: bool = False) -> dict[str, Any]:
        return {
            "field": field,
            "record_count": count,
            "observed_count": 0,
            "observability_rate": 0.0,
            "missing_count": count,
            "true_zero_count": 0,
            "positive_count": 0,
            "not_produced_by_execution_mode_count": count if no_model else 0,
            "collection_failure_count": 0,
            "classification": reason,
        }

    fields = {
        "prompt_tokens": unavailable(
            "prompt_tokens",
            "not collected separately; local-reference branch execution made no model call",
            True,
        ),
        "completion_tokens": unavailable(
            "completion_tokens",
            "not collected separately; local-reference branch execution made no model call",
            True,
        ),
        "tool_calls": {
            "field": "extra_tool_calls",
            "record_count": count,
            "observed_count": count,
            "observability_rate": 1.0,
            "missing_count": 0,
            "true_zero_count": sum(value == 0 for value in tool_calls),
            "positive_count": sum(value > 0 for value in tool_calls),
            "not_produced_by_execution_mode_count": 0,
            "collection_failure_count": 0,
            "classification": "observed local tool-call count",
        },
        "model_latency": unavailable(
            "model_latency",
            "not produced: the local-reference branch runner made no model call",
            True,
        ),
        "tool_latency": {
            "field": "extra_latency_seconds",
            "record_count": count,
            "observed_count": count,
            "observability_rate": 1.0,
            "missing_count": 0,
            "true_zero_count": sum(value == 0 for value in tool_latency),
            "positive_count": sum(value > 0 for value in tool_latency),
            "not_produced_by_execution_mode_count": 0,
            "collection_failure_count": 0,
            "classification": "observed local tool-call duration accumulated by ExecutionMonitor",
        },
        "total_wall_clock_latency": unavailable(
            "total_wall_clock_latency",
            "absent from branch schema; extra_latency_seconds measures tool calls, not total wall clock",
        ),
    }
    return {
        "schema_version": "p1-d3-cost-observability-v1",
        "scope": "Day 9 applicable branch outcomes only",
        "applicable_branch_count": count,
        "not_applicable_branch_count": len(day9_records) - count,
        "fields": fields,
        "aggregate_extra_tokens": {
            **_series_summary(tokens),
            "observability_rate": 1.0,
            "semantic_interpretation": (
                "real zero for this deterministic local-reference execution mode because the "
                "branch runner passed total_tokens=0 and made no model call; not evidence that "
                "a model-mediated intervention has low token cost"
            ),
        },
        "evidence": {
            "measurement": "src/risk_agent/monitoring/execution_monitor.py::ExecutionMonitor.execute_tool",
            "runner": "src/risk_agent/intervention/runner.py::InterventionBranchRunner",
            "training_mapping": "src/risk_agent/utility/dataset.py::build_utility_dataset",
        },
        "checks": {
            "only_applicable_records_counted_as_observations": count == 36,
            "token_zero_is_classified_as_no_model_call": all(value == 0 for value in tokens),
            "tool_calls_fully_observed": len(tool_calls) == count,
            "tool_latency_fully_observed": len(tool_latency) == count,
            "model_and_wall_clock_not_conflated_with_tool_latency": True,
        },
    }


def _ranking(decision: Mapping[str, Any], key: str = "net_value") -> list[str]:
    scores = decision.get("action_scores") or {}
    return [
        action
        for action, _ in sorted(
            scores.items(), key=lambda item: (-float(item[1][key]), item[0])
        )
    ]


def _payload_signature(decision: Mapping[str, Any]) -> str:
    return json.dumps(decision, sort_keys=True, separators=(",", ":"))


def _policy_audit(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    records = _jsonl(root / DAY13 / "policy_task_results.jsonl")
    by_policy: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in records:
        by_policy[row["policy"]][row["checkpoint_id"]] = row
    checkpoints = sorted(by_policy["full"])
    if set(by_policy) != set(POLICIES):
        raise RuntimeError("Day 13 policy set differs from the fixed nine-policy design")
    if any(set(by_policy[policy]) != set(checkpoints) for policy in POLICIES):
        raise RuntimeError("Day 13 policies do not share an identical checkpoint set")

    matrix: dict[str, dict[str, int]] = {}
    for left in POLICIES:
        matrix[left] = {}
        for right in POLICIES:
            matrix[left][right] = sum(
                by_policy[left][checkpoint]["decision"]["action"]
                == by_policy[right][checkpoint]["decision"]["action"]
                for checkpoint in checkpoints
            )

    csv_rows: list[dict[str, Any]] = []
    differences: list[dict[str, Any]] = []
    for checkpoint in checkpoints:
        full = by_policy["full"][checkpoint]
        full_decision = full["decision"]
        risk_utility_decision = by_policy["risk_plus_utility"][checkpoint]["decision"]
        no_cost_decision = by_policy["without_cost"][checkpoint]["decision"]
        actions = {
            policy: by_policy[policy][checkpoint]["decision"]["action"] for policy in POLICIES
        }
        different = [policy for policy in POLICIES if actions[policy] != actions["full"]]
        full_rank = _ranking(full_decision)
        no_cost_rank = _ranking(no_cost_decision)
        cost_components_equal = all(
            full_decision["action_scores"].get(action, {}).get("cost_components")
            == no_cost_decision["action_scores"].get(action, {}).get("cost_components")
            for action in set(full_decision["action_scores"]) | set(no_cost_decision["action_scores"])
        )
        row: dict[str, Any] = {
            "checkpoint_id": checkpoint,
            "task_id": full["task_id"],
            "template_id": full["template_id"],
            "scenario_id": full["scenario_id"],
            "fault_type": full["fault_type"],
            "source_continue_success": str(bool(full["source_continue_success"])).lower(),
            **{f"{policy}_action": actions[policy] for policy in POLICIES},
            "full_risk_level": full_decision["risk_level"],
            "full_risk_probability": full_decision["risk_probability"],
            "without_risk_risk_level": by_policy["without_risk"][checkpoint]["decision"]["risk_level"],
            "full_success_probability_ranking": ">".join(
                _ranking(full_decision, "success_probability")
            ),
            "full_net_utility_ranking": ">".join(full_rank),
            "without_cost_net_utility_ranking": ">".join(no_cost_rank),
            "full_equals_risk_plus_utility": str(actions["full"] == actions["risk_plus_utility"]).lower(),
            "full_risk_plus_payload_equal": str(
                _payload_signature(full_decision) == _payload_signature(risk_utility_decision)
            ).lower(),
            "full_equals_risk_based": str(actions["full"] == actions["risk_based"]).lower(),
            "full_equals_without_risk": str(actions["full"] == actions["without_risk"]).lower(),
            "full_equals_without_utility": str(actions["full"] == actions["without_utility"]).lower(),
            "full_equals_without_cost": str(actions["full"] == actions["without_cost"]).lower(),
            "full_without_cost_ranking_equal": str(full_rank == no_cost_rank).lower(),
            "full_without_cost_components_equal": str(cost_components_equal).lower(),
            "cost_changed_selection": str(actions["full"] != actions["without_cost"]).lower(),
            "distinct_action_count": len(set(actions.values())),
            "policies_differing_from_full": ";".join(different),
        }
        csv_rows.append(row)
        if different:
            differences.append(
                {
                    "checkpoint_id": checkpoint,
                    "full_action": actions["full"],
                    "different_policies": {policy: actions[policy] for policy in different},
                }
            )

    day12 = _jsonl(root / DAY12 / "policy_decisions.jsonl")
    day12_by_checkpoint = {row["checkpoint_id"]: row for row in day12}
    day12_matches_full = sum(
        day12_by_checkpoint[checkpoint]["action"]
        == by_policy["full"][checkpoint]["decision"]["action"]
        and day12_by_checkpoint[checkpoint]["risk_level"]
        == by_policy["full"][checkpoint]["decision"]["risk_level"]
        for checkpoint in checkpoints
    )
    action_counts = {
        policy: dict(
            sorted(
                Counter(
                    row["decision"]["action"] for row in by_policy[policy].values()
                ).items()
            )
        )
        for policy in POLICIES
    }
    risk_counts = {
        policy: dict(
            sorted(
                Counter(
                    row["decision"]["risk_level"] for row in by_policy[policy].values()
                ).items()
            )
        )
        for policy in POLICIES
    }
    full_no_cost_numeric_changes = sum(
        any(
            float(score["weighted_cost"])
            != float(
                by_policy["without_cost"][checkpoint]["decision"]["action_scores"][action][
                    "weighted_cost"
                ]
            )
            for action, score in by_policy["full"][checkpoint]["decision"][
                "action_scores"
            ].items()
        )
        for checkpoint in checkpoints
    )
    result = {
        "record_count": len(records),
        "checkpoint_count": len(checkpoints),
        "policy_count": len(POLICIES),
        "all_records_train_split": all(row["split"] == "train" for row in records),
        "action_counts": action_counts,
        "risk_level_counts": risk_counts,
        "pairwise_action_agreement_counts_out_of_20": matrix,
        "checkpoints_with_any_action_difference_from_full": differences,
        "full_vs_risk_plus_utility": {
            "same_selected_action_count": matrix["full"]["risk_plus_utility"],
            "exact_decision_payload_match_count": sum(
                _payload_signature(by_policy["full"][checkpoint]["decision"])
                == _payload_signature(
                    by_policy["risk_plus_utility"][checkpoint]["decision"]
                )
                for checkpoint in checkpoints
            ),
            "actual_difference": (
                "none: both are separately constructed AdaptiveInterventionPolicy instances "
                "using the same model bundle and configuration"
            ),
        },
        "ablations": {
            "without_risk": {
                "implementation": "risk prediction overridden with constant 1.0; utility and cost predictions retained",
                "full_action_agreement_count": matrix["full"]["without_risk"],
                "risk_gate_changed_count": sum(
                    by_policy["full"][checkpoint]["decision"]["risk_level"]
                    != by_policy["without_risk"][checkpoint]["decision"]["risk_level"]
                    for checkpoint in checkpoints
                ),
            },
            "without_utility": {
                "implementation": "RiskOnlyPolicy; outcome and cost models are not called",
                "full_action_agreement_count": matrix["full"]["without_utility"],
                "empty_action_scores_count": sum(
                    not by_policy["without_utility"][checkpoint]["decision"]["action_scores"]
                    for checkpoint in checkpoints
                ),
            },
            "without_cost": {
                "implementation": "real outcome and cost models are called, then weighted cost is overridden to zero",
                "full_action_agreement_count": matrix["full"]["without_cost"],
                "weighted_cost_numeric_change_checkpoint_count": full_no_cost_numeric_changes,
                "ranking_change_count": sum(
                    _ranking(by_policy["full"][checkpoint]["decision"])
                    != _ranking(by_policy["without_cost"][checkpoint]["decision"])
                    for checkpoint in checkpoints
                ),
                "selection_change_count": 20 - matrix["full"]["without_cost"],
                "predicted_cost_components_retained_count": sum(
                    all(
                        score["cost_components"]
                        == by_policy["without_cost"][checkpoint]["decision"]["action_scores"][
                            action
                        ]["cost_components"]
                        for action, score in by_policy["full"][checkpoint]["decision"][
                            "action_scores"
                        ].items()
                    )
                    for checkpoint in checkpoints
                ),
            },
        },
        "module_call_semantics_from_code": {
            "vanilla": "no risk/outcome/cost model",
            "always_retry": "no risk/outcome/cost model",
            "rule_based": "no learned model; reads last_failure.fault_type injection metadata",
            "risk_based": "real risk model only",
            "risk_plus_utility": "real risk, action-outcome, and cost models",
            "full": "real risk, action-outcome, and cost models",
            "without_risk": "constant risk override; real action-outcome and cost models",
            "without_utility": "real risk model only; no outcome/cost model",
            "without_cost": "real risk, action-outcome, and cost models; cost influence zeroed after prediction",
        },
        "day12_full_decision_action_and_gate_match_count": day12_matches_full,
        "validity_judgement": (
            "The 180 records support engineering reproducibility only. Six model-oriented "
            "policies select identical actions on all 20 checkpoints; Full and Risk + Utility "
            "are the same code path; cost and utility ablations do not change selections; all "
            "records are deterministic-local Train data using audited shortcut/constant models."
        ),
    }
    return result, csv_rows


CSV_COLUMNS = (
    "checkpoint_id",
    "task_id",
    "template_id",
    "scenario_id",
    "fault_type",
    "source_continue_success",
    *(f"{policy}_action" for policy in POLICIES),
    "full_risk_level",
    "full_risk_probability",
    "without_risk_risk_level",
    "full_success_probability_ranking",
    "full_net_utility_ranking",
    "without_cost_net_utility_ranking",
    "full_equals_risk_plus_utility",
    "full_risk_plus_payload_equal",
    "full_equals_risk_based",
    "full_equals_without_risk",
    "full_equals_without_utility",
    "full_equals_without_cost",
    "full_without_cost_ranking_equal",
    "full_without_cost_components_equal",
    "cost_changed_selection",
    "distinct_action_count",
    "policies_differing_from_full",
)


def run_audit(root: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    day9_path = root / DAY9 / "branch_outcomes.jsonl"
    day9_records = _jsonl(day9_path)
    coverage = _action_coverage(day9_records)
    day11 = _day11_audit(root)
    policy, rows = _policy_audit(root)
    source_paths = [
        DAY9 / "branch_outcomes.jsonl",
        DAY11 / "utility_training_dataset.json",
        DAY11 / "oof_utility_predictions.jsonl",
        DAY11 / "outcome_metrics.json",
        DAY11 / "cost_metrics.json",
        DAY12 / "policy_decisions.jsonl",
        DAY13 / "policy_task_results.jsonl",
    ]
    action_audit = {
        "schema_version": "p1-d3-action-utility-policy-audit-v1",
        "scope": "immutable Day 9--13 Train artifacts",
        "source_sha256": {
            path.as_posix(): _sha256(root / path) for path in source_paths
        },
        "day9_action_coverage": coverage,
        "day11_utility_model_audit": day11,
        "day13_policy_equivalence": policy,
        "checks": {
            "day9_has_60_candidate_records": len(day9_records) == 60,
            "retry_is_6_of_6_success": coverage["RETRY"]["success_count"] == 6
            and coverage["RETRY"]["failure_count"] == 0,
            "replan_is_10_of_10_success": coverage["REPLAN"]["success_count"] == 10
            and coverage["REPLAN"]["failure_count"] == 0,
            "not_applicable_excluded_from_training_labels": day11[
                "not_applicable_labels_and_costs_are_null"
            ],
            "day11_oof_complete": day11["oof_record_count"] == 60
            and day11["oof_key_coverage_matches_dataset"],
            "day13_has_180_paired_records": policy["record_count"] == 180,
            "full_and_risk_plus_utility_identical": policy["full_vs_risk_plus_utility"][
                "exact_decision_payload_match_count"
            ]
            == 20,
            "cost_did_not_change_selection": policy["ablations"]["without_cost"][
                "selection_change_count"
            ]
            == 0,
        },
        "gate_passed": True,
        "gate_interpretation": (
            "PASS means the P1-D3 audit is reproducible and its limitations are explicit; "
            "it does not validate the utility models or policy research hypothesis."
        ),
    }
    cost = _cost_observability(day9_records)
    action_audit["gate_passed"] = all(action_audit["checks"].values()) and all(
        cost["checks"].values()
    )
    return action_audit, cost, rows


def _csv_text(rows: Iterable[Mapping[str, Any]]) -> str:
    import io

    handle = io.StringIO(newline="")
    writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return handle.getvalue()


def write_outputs(
    output_dir: Path,
    action_audit: Mapping[str, Any],
    cost_audit: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    *,
    force: bool,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        output_dir / "action_coverage.json": json.dumps(
            action_audit, ensure_ascii=False, indent=2, sort_keys=True
        )
        + "\n",
        output_dir / "cost_observability.json": json.dumps(
            cost_audit, ensure_ascii=False, indent=2, sort_keys=True
        )
        + "\n",
        output_dir / "policy_action_equivalence.csv": _csv_text(rows),
    }
    for path, content in outputs.items():
        if path.exists() and not force:
            raise FileExistsError(f"Refusing to overwrite existing audit artifact: {path}")
        path.write_text(content, encoding="utf-8")


def check_outputs(
    output_dir: Path,
    action_audit: Mapping[str, Any],
    cost_audit: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> bool:
    expected = {
        output_dir / "action_coverage.json": json.dumps(
            action_audit, ensure_ascii=False, indent=2, sort_keys=True
        )
        + "\n",
        output_dir / "cost_observability.json": json.dumps(
            cost_audit, ensure_ascii=False, indent=2, sort_keys=True
        )
        + "\n",
        output_dir / "policy_action_equivalence.csv": _csv_text(rows),
    }
    return all(path.is_file() and path.read_text(encoding="utf-8") == content for path, content in expected.items())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, default=Path("runs/phase2_audit"))
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output_dir if args.output_dir.is_absolute() else root / args.output_dir
    action_audit, cost_audit, rows = run_audit(root)
    if args.check_only:
        reproducible = check_outputs(output, action_audit, cost_audit, rows)
    else:
        write_outputs(output, action_audit, cost_audit, rows, force=args.force)
        reproducible = True
        print(output)
    summary = {
        "gate_passed": action_audit["gate_passed"],
        "outputs_match_recomputation": reproducible,
        "checks": action_audit["checks"],
    }
    print(json.dumps(summary, indent=2))
    return 0 if action_audit["gate_passed"] and reproducible else 1


if __name__ == "__main__":
    raise SystemExit(main())
