"""Approved historical sidecars only: guard frozen envelopes then reuse parser."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any

from .train_public_signals import review_public
from .train_public_signals_v2 import validate_diagnostic_value

EXTRACTOR_VERSION = "p2-d4-public-completion-backfill-v2.0"
CANDIDATE_SHA256 = "2517ef42fde46f1a55ba32e6278678dfd170bdd0b44047960fb27c2174f4cd26"
FRAMEWORK_SUFFIX = ["Please try again or use another tool",
    "Now let's retry: take care not to repeat previous errors! If you have retried several times, try a completely different approach."]
VIEW_FIELDS = {"split", "task_text", "tool_registry", "calls", "responses", "cutoff_step",
               "cutoff_verified", "last_role"}


def check_approval(approval: dict, candidate_sha256: str) -> None:
    if (candidate_sha256 != CANDIDATE_SHA256 or approval["candidate_manifest_sha256"] != candidate_sha256
            or approval["status"] != "APPROVED_OFFLINE_TRAIN_BACKFILL_ONLY"
            or approval["allowed_operation"] != "day3_historical_qualified_train_v2_sidecar_backfill"
            or approval["stop_after_day3"] is not True
            or any(approval[k] is not False for k in ("weight_fitting_authorized", "model_comparison_authorized",
                "production_activation_authorized", "new_collection_authorized", "remote_access_authorized"))):
        raise ValueError("Only separately approved Day 3 historical Train backfill is allowed")


def public_value(view: dict[str, Any]) -> dict[str, Any]:
    """No identity, label, fault or future fields may enter the signal interface."""
    if set(view) != VIEW_FIELDS or view["split"] != "train" or view["cutoff_verified"] is not True:
        raise ValueError("Only verified Train public decision-time projection accepted")
    if type(view["cutoff_step"]) is not int or view["cutoff_step"] < 1:
        raise ValueError("Invalid cutoff step")
    def unknown(reason):
        return {"value": "UNKNOWN", "parse_status": "UNKNOWN", "reason": reason}
    if view["last_role"] != "tool-response":
        return unknown("canonical_input_not_at_received_response")
    calls, responses = view["calls"], view["responses"]
    if not calls or len(calls) != len(responses):
        return unknown("call_response_count_mismatch")
    if any(set(c) != {"id", "name", "arguments"} or not isinstance(c["arguments"], dict) for c in calls):
        raise ValueError("Invalid public call projection")
    if any(set(r) != {"text", "call_id"} or not isinstance(r["text"], str) for r in responses):
        raise ValueError("Invalid public response projection")
    ids = [c["id"] for c in calls]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        return unknown("missing_or_duplicate_call_identity")
    linked = []
    for response in responses:
        explicit = response["call_id"]
        lines = response["text"].splitlines()
        embedded = lines[0][len("Call id: "):] if lines and lines[0].startswith("Call id: ") else None
        if explicit is not None and (not isinstance(explicit, str) or not explicit):
            return unknown("invalid_response_call_identity")
        if explicit is not None and embedded is not None and explicit != embedded:
            return unknown("conflicting_response_call_identities")
        identity = explicit if explicit is not None else embedded
        if identity is None:
            if len(calls) != 1:
                return unknown("multiple_calls_without_explicit_linkage")
            identity = ids[0]
        if identity not in ids:
            return unknown("response_call_id_mismatch")
        linked.append(identity)
    if len(set(linked)) != len(linked):
        return unknown("duplicate_response_linkage")
    # Received order determines the last response; explicit IDs select its call.
    by_id = {c["id"]: c for c in calls}
    ordered = [by_id[i] for i in linked]
    last, text = ordered[-1], responses[-1]["text"]
    if text.startswith("Observation:\n"):
        def invalid_constant(value): raise ValueError("Non-JSON numeric constant")
        try:
            json.loads(text[len("Observation:\n"):], parse_constant=invalid_constant)
        except (ValueError, TypeError):
            return unknown("normal_observation_invalid_json")
    else:
        lines = text.splitlines()
        offset = 1 if lines and lines[0].startswith("Call id: ") else 0
        if len(lines) < offset + 2 or lines[offset] != "Error:":
            return unknown("unrecognized_error_envelope")
        prefix = f"Error executing tool '{last['name']}' with arguments {last['arguments']}: "
        body = lines[offset + 1]
        if not body.startswith(prefix) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*: .+", body[len(prefix):]):
            return unknown("error_call_prefix_or_body_mismatch")
        suffix = lines[offset + 2:]
        if suffix not in ([], FRAMEWORK_SUFFIX):
            return unknown("unknown_suffix_or_multiline_body")
    parser_view = {"split": "train", "task_text": view["task_text"], "tool_registry": view["tool_registry"],
        "calls": [{"name": c["name"], "arguments": c["arguments"]} for c in ordered],
        "responses": [r["text"] for r in responses], "cutoff_step": view["cutoff_step"]}
    try:
        parsed = review_public(parser_view)
    except (ValueError, TypeError, KeyError):
        return unknown("public_schema_or_parser_projection_invalid")
    value = parsed["public_completion_unknown"]
    if parsed["error_parse_status"] == "UNKNOWN" or type(value) is not int or value not in (0, 1):
        return unknown("existing_parser_cannot_establish_value")
    return {"value": value, "parse_status": parsed["error_parse_status"], "reason": "verified_frozen_protocol"}


def public_projection(row: dict) -> dict:
    if row.get("split") != "train":
        raise ValueError("Only qualified Train rows may be projected")
    payload = row["payload"]
    calls = [{k: c[k] for k in ("id", "name", "arguments")} for s in payload["memory"]["steps"]
             if s["type"] == "action" for c in (s.get("tool_calls") or [])]
    responses = []
    for message in payload["canonical_next_model_input"]:
        if message["role"] != "tool-response":
            continue
        content = message["content"]
        text = content if isinstance(content, str) else "".join(c.get("text", "") for c in content)
        # The current serializer has no separate response ID; the explicit error
        # Call id line is handled above. Accept an optional public ID in fixtures.
        if message.get("call_id") is not None and message.get("tool_call_id") is not None and message["call_id"] != message["tool_call_id"]:
            raise ValueError("Conflicting canonical response ID fields")
        identity = message["tool_call_id"] if message.get("tool_call_id") is not None else message.get("call_id")
        responses.append({"text": text, "call_id": identity})
    temporal = row["temporal"]
    if temporal["tool_events"] != len(calls) or temporal["cutoff_step"] != payload["agent"]["step_number"]:
        raise ValueError("Public projection differs from verified cutoff")
    return {"split": "train", "task_text": payload["agent"]["task"], "tool_registry": [
        {k: t[k] for k in ("name", "description", "inputs", "output_type")} for t in payload["tool_registry"]],
        "calls": calls, "responses": responses, "cutoff_step": temporal["cutoff_step"],
        "cutoff_verified": True, "last_role": payload["canonical_next_model_input"][-1]["role"]}


def build_sidecars(rows: list[dict], *, contract, extractor_sha256: str,
                   execution_manifest_sha256: str, approval_sha256: str, targets: dict) -> tuple[list[dict], dict]:
    from .train_evidence import canonical
    if (not rows or len({r["task_id"] for r in rows}) != len(rows)
            or len({r["checkpoint_id"] for r in rows}) != len(rows)):
        raise ValueError("Unique qualified primary Train cohort required")
    sidecars = []
    created = datetime.now(timezone.utc).isoformat()
    for row in rows:
        view = public_projection(row)
        result = public_value(view)
        value = result["value"]
        validate_diagnostic_value(value)
        original = dict(row["features"])
        if tuple(original) != contract.feature_names[:-1]:
            raise ValueError("v1 feature order differs from approved candidate")
        features = {**original, "public_completion_unknown": value}
        if tuple(features) != contract.feature_names or any(features[n] != original[n] for n in original):
            raise ValueError("v1 features changed during backfill")
        record = {"schema_version": contract.schema_version, "source_schema_version": "formal-decision-features-v1",
            "split": "train", "task_id": row["task_id"], "template_id": row["template_id"],
            "checkpoint_id": row["checkpoint_id"], "source_record_sha256": row["risk_record_sha256"],
            "checkpoint_record_sha256": row["checkpoint_record_sha256"],
            "original_v1_features_sha256": canonical(original), "public_projection_sha256": canonical(view),
            "extractor_version": EXTRACTOR_VERSION, "extractor_sha256": extractor_sha256,
            "candidate_manifest_sha256": contract.manifest_sha256,
            "execution_manifest_sha256": execution_manifest_sha256, "approval_sha256": approval_sha256,
            "public_completion_unknown": value, "cutoff_step": view["cutoff_step"], "created_at": created,
            "feature_names": list(contract.feature_names), "features": features,
            "sources": row["sources"], "parse_audit": result,
            "future_data_excluded": True, "identity_fields_are_provenance_only": True}
        record["sidecar_record_sha256"] = canonical(record)
        sidecars.append(record)
    counts = {"qualified_train": len(sidecars),
        "ones": sum(s["public_completion_unknown"] == 1 for s in sidecars),
        "zeros": sum(s["public_completion_unknown"] == 0 for s in sidecars),
        "unknowns": sum(s["public_completion_unknown"] == "UNKNOWN" for s in sidecars),
        "original_v1_feature_changes": 0}
    expected = {k: targets[k] for k in counts}
    eligible = counts == expected and counts["unknowns"] == 0
    return sidecars, {"gate": "PASS" if eligible else "BLOCKED", "observed": counts, "expected": expected,
        "counts_used_as_parsing_rules": False, "complete_v2_dataset_eligible": eligible,
        "model_comparison_authorized": False, "production_activation_authorized": False}
