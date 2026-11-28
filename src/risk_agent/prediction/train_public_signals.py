"""Label-blind review candidates; never an active production feature extractor."""
from __future__ import annotations

from collections import Counter
import json
import re
from typing import Any

FIELDS = {"split", "task_text", "tool_registry", "calls", "responses", "cutoff_step"}


def review_public(view: dict[str, Any]) -> dict[str, Any]:
    """Accept a minimal public projection, rejecting labels and private metadata."""
    if set(view) != FIELDS or view["split"] != "train":
        raise ValueError("Only a Train public projection is accepted")
    if (type(view["cutoff_step"]) is not int or view["cutoff_step"] < 1
            or not isinstance(view["task_text"], str) or not view["task_text"].strip()
            or not view["calls"] or len(view["calls"]) != len(view["responses"])):
        raise ValueError("Incomplete decision-time public evidence")
    for call in view["calls"]:
        if set(call) != {"name", "arguments"} or not isinstance(call["arguments"], dict):
            raise ValueError("Calls must exclude transport IDs and private metadata")
    registry = {t["name"]: t for t in view["tool_registry"]}
    if len(registry) != len(view["tool_registry"]):
        raise ValueError("Duplicate public tool schema")
    missing, extra, identifier_pairs, mismatches = 0, 0, 0, 0
    task_sentence = view["task_text"].split("\n\n", 1)[0]
    literal_ids = set(re.findall(r"\b[A-Za-z][A-Za-z0-9]*-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\b", task_sentence))
    for call in view["calls"]:
        if call["name"] not in registry:
            raise ValueError("Called tool is absent from captured public schema")
        inputs = registry[call["name"]]["inputs"]
        args = call["arguments"]
        missing += sum(k not in args for k, spec in inputs.items() if not spec.get("nullable", False))
        extra += len(set(args) - set(inputs))
        for key, spec in inputs.items():
            description = spec.get("description", "").lower()
            if (len(literal_ids) == 1 and isinstance(args.get(key), str)
                    and re.search(r"\bexact\b", description) and re.search(r"\b(id|sku)\b", description)):
                identifier_pairs += 1
                mismatches += int(args[key] not in literal_ids)
    call, response = view["calls"][-1], view["responses"][-1]
    if not isinstance(response, str):
        raise ValueError("Canonical response must be text")
    result = {"missing_required_arguments": missing, "extra_arguments": extra,
        "literal_identifier_pairs": identifier_pairs, "literal_identifier_mismatches": mismatches,
        "public_unknown_resource": None, "public_completion_unknown": None,
        "error_parse_status": "UNKNOWN", "observation_json_type": None}
    if response.startswith("Observation:\n"):
        result.update(public_unknown_resource=0, public_completion_unknown=0, error_parse_status="NO_ERROR")
        try:
            value = json.loads(response[len("Observation:\n"):])
            result["observation_json_type"] = ("null" if value is None else "boolean" if type(value) is bool
                else "object" if isinstance(value, dict) else "array" if isinstance(value, list)
                else "string" if isinstance(value, str) else "number")
        except (ValueError, TypeError):
            result["observation_json_type"] = "unparseable"
        return result
    # Match the wrapper against the actual public call. Echoed arguments and the
    # subsequent framework retry advice must not become error-semantic evidence.
    lines = response.splitlines()
    offset = 1 if lines and lines[0].startswith("Call id: ") else 0
    if len(lines) <= offset + 1 or lines[offset] != "Error:":
        return result
    prefix = f"Error executing tool '{call['name']}' with arguments {call['arguments']}: "
    line = lines[offset + 1]
    if not line.startswith(prefix):
        return result
    match = re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*: (.*)", line[len(prefix):])
    if not match:
        return result
    message = match.group(1).lower()
    result.update(error_parse_status="PARSED_PUBLIC_ERROR",
        public_unknown_resource=int(bool(re.match(r"unknown (?:order|sku|file|resource):", message))),
        public_completion_unknown=int(bool(re.search(r"\bcompletion status is unknown\b", message))))
    return result


def summarize(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    if not reviews or len({r["task_id"] for r in reviews}) != len(reviews):
        raise ValueError("Unique nonempty Train primary cohort required")
    fields = ("public_unknown_resource", "public_completion_unknown", "missing_required_arguments",
              "extra_arguments", "literal_identifier_mismatches")
    support = {}
    for name in fields:
        positive = [r for r in reviews if r["signals"][name] is not None and r["signals"][name] > 0]
        support[name] = {"positive_rows": len(positive), "positive_templates": len({r["template_id"] for r in positive}),
            "missing_rows": sum(r["signals"][name] is None for r in reviews),
            "old_public_error_categories": dict(Counter(r["old_public_error_category"] for r in positive))}
    return {"status": "TRAIN_PUBLIC_SIGNAL_REVIEW_PASS", "scope": "availability_and_coverage_only",
        "train_rows": len(reviews), "train_templates": len({r["template_id"] for r in reviews}),
        "signals": support, "parse_status": dict(Counter(r["signals"]["error_parse_status"] for r in reviews)),
        "json_types": dict(Counter(r["signals"]["observation_json_type"] or "not_applicable_error" for r in reviews)),
        "identifier_comparable_rows": sum(r["signals"]["literal_identifier_pairs"] > 0 for r in reviews),
        "cutoff_steps": dict(Counter(r["cutoff_step"] for r in reviews)),
        "label_associations_computed": False, "production_features_modified": False,
        "model_fitted": False, "validation_used": False, "test_used": False,
        "candidate_status": "PROPOSED_REQUIRES_APPROVAL_NOT_EXECUTABLE"}
