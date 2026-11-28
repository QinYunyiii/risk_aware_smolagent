from copy import deepcopy

import pytest

from risk_agent.prediction.train_public_signals import review_public, summarize


def public():
    return {"split": "train", "task_text": "Return state of order-42.", "cutoff_step": 1,
        "tool_registry": [{"name": "get_order", "inputs": {"order_id": {"description": "Exact order ID."}}}],
        "calls": [{"name": "get_order", "arguments": {"order_id": "42"}}],
        "responses": ['Observation:\n{"status":"pending"}']}


def error(view, message):
    c = view["calls"][-1]
    view["responses"] = [f"Call id: transport-only\nError:\nError executing tool '{c['name']}' with arguments {c['arguments']}: VisibleToolError: {message}\nFramework advice: Unknown order: fake"]
    return view


@pytest.mark.parametrize("message,unknown,uncertain", [
    ("Unknown order: 42", 1, 0),
    ("Tool request failed; completion status is unknown. Query before write.", 0, 1),
    ("Timeout: request timed out; completion status is unknown.", 0, 1),
    ("503 Service Unavailable: operation was not started.", 0, 0),
])
def test_public_error_semantics_cross_cut_existing_categories(message, unknown, uncertain):
    result = review_public(error(public(), message))
    assert result["public_unknown_resource"] == unknown
    assert result["public_completion_unknown"] == uncertain


def test_echoed_arguments_and_framework_advice_are_not_error_messages():
    v = public()
    v["calls"][0]["arguments"]["order_id"] = "Unknown order: completion status is unknown"
    r = review_public(error(v, "Operation was not started."))
    assert r["public_unknown_resource"] == r["public_completion_unknown"] == 0


def test_regular_json_text_is_not_an_exception_signal():
    v = public()
    v["responses"] = ['Observation:\n"Unknown order: 42; completion status is unknown"']
    r = review_public(v)
    assert r["observation_json_type"] == "string"
    assert r["public_unknown_resource"] == r["public_completion_unknown"] == 0


def test_literal_mismatch_is_an_audit_observation_not_a_failure_label():
    v = public(); before = deepcopy(v)
    r = review_public(v)
    assert r["literal_identifier_pairs"] == 1 and r["literal_identifier_mismatches"] == 1
    assert v == before and "y_failure" not in r


def test_schema_required_and_extra_arguments():
    v = public(); v["calls"][0]["arguments"] = {"bad": "value"}
    r = review_public(v)
    assert r["missing_required_arguments"] == r["extra_arguments"] == 1


def test_unrecognized_error_format_stays_unknown():
    v = public(); v["responses"] = ["Error:\nUnknown order: 42"]
    r = review_public(v)
    assert r["error_parse_status"] == "UNKNOWN" and r["public_unknown_resource"] is None


def test_error_without_json_is_distinct_from_a_literal_json_null():
    first = public(); first["responses"] = ["Observation:\nnull"]
    second = error(public(), "Unknown order: 42")
    rows = [{"task_id": str(i), "template_id": "t", "cutoff_step": 1,
        "old_public_error_category": "none", "signals": review_public(v)} for i, v in enumerate((first, second))]
    assert summarize(rows)["json_types"] == {"null": 1, "not_applicable_error": 1}


@pytest.mark.parametrize("forbidden", ["label", "fault_type", "expected_answer", "future_outcome", "task_id"])
def test_private_or_identity_metadata_rejected(forbidden):
    v = public(); v[forbidden] = "hidden"
    with pytest.raises(ValueError): review_public(v)


@pytest.mark.parametrize("split", ["validation", "test"])
def test_other_splits_rejected(split):
    v = public(); v["split"] = split
    with pytest.raises(ValueError): review_public(v)


def test_source_boundary_mismatch_rejected():
    v = public(); v["responses"] = []
    with pytest.raises(ValueError): review_public(v)


def test_summary_never_groups_by_outcomes_and_rejects_duplicate_primaries():
    r = {"task_id": "one", "template_id": "t", "cutoff_step": 1,
        "old_public_error_category": "other", "signals": review_public(error(public(), "Unknown order: 42"))}
    result = summarize([r])
    assert result["signals"]["public_unknown_resource"]["positive_templates"] == 1
    assert not result["label_associations_computed"] and not result["validation_used"]
    with pytest.raises(ValueError): summarize([r, r])
