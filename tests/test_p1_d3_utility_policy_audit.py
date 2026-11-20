from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_p1_d3_utility_policy.py"


def _module():
    spec = importlib.util.spec_from_file_location("p1_d3_audit", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_p1_d3_recomputes_action_coverage_and_model_limitations() -> None:
    module = _module()
    audit, costs, rows = module.run_audit(ROOT)

    coverage = audit["day9_action_coverage"]
    assert (coverage["CONTINUE"]["success_count"], coverage["CONTINUE"]["failure_count"]) == (11, 9)
    assert (coverage["RETRY"]["success_count"], coverage["RETRY"]["failure_count"]) == (6, 0)
    assert (coverage["REPLAN"]["success_count"], coverage["REPLAN"]["failure_count"]) == (10, 0)
    assert coverage["RETRY"]["not_applicable_count"] == 14
    assert coverage["REPLAN"]["not_applicable_count"] == 10

    day11 = audit["day11_utility_model_audit"]
    assert day11["not_applicable_labels_and_costs_are_null"]
    assert day11["actions"]["CONTINUE"]["outcome_model_kind_from_artifact"] == "logistic_regression_pipeline"
    assert day11["actions"]["RETRY"]["outcome_model_kind_from_artifact"] == "beta_bernoulli_constant"
    assert day11["actions"]["REPLAN"]["outcome_model_kind_from_artifact"] == "beta_bernoulli_constant"
    assert all(
        day11["actions"][action]["cost_models"]["tokens"]["all_zero_target"]
        for action in module.ACTIONS
    )
    assert costs["fields"]["prompt_tokens"]["observability_rate"] == 0.0
    assert costs["fields"]["tool_calls"]["observability_rate"] == 1.0
    assert costs["fields"]["tool_latency"]["observability_rate"] == 1.0
    assert costs["fields"]["model_latency"]["observability_rate"] == 0.0
    assert len(rows) == 20


def test_p1_d3_policy_equivalence_and_outputs_are_reproducible() -> None:
    module = _module()
    audit, costs, rows = module.run_audit(ROOT)
    policy = audit["day13_policy_equivalence"]

    assert policy["record_count"] == 180
    assert policy["full_vs_risk_plus_utility"]["exact_decision_payload_match_count"] == 20
    for candidate in (
        "risk_based",
        "risk_plus_utility",
        "without_risk",
        "without_utility",
        "without_cost",
    ):
        assert policy["pairwise_action_agreement_counts_out_of_20"]["full"][candidate] == 20
    assert policy["ablations"]["without_risk"]["risk_gate_changed_count"] == 11
    assert policy["ablations"]["without_utility"]["empty_action_scores_count"] == 20
    assert policy["ablations"]["without_cost"]["weighted_cost_numeric_change_checkpoint_count"] == 20
    assert policy["ablations"]["without_cost"]["ranking_change_count"] == 0
    assert policy["ablations"]["without_cost"]["selection_change_count"] == 0
    assert audit["gate_passed"]
    assert module.check_outputs(ROOT / "runs" / "phase2_audit", audit, costs, rows)
