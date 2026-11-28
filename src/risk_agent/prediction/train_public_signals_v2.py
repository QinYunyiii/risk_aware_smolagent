"""Day 2 candidate-contract checks only. No extraction, backfill or fitting API."""
from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

CONFIG = "configs/formal_decision_features_v2_candidate.json"
PROTOCOL = "docs/phase2/formal_decision_features_v2_protocol.md"
MANIFEST = "docs/phase2/formal_decision_features_v2_candidate_manifest.json"
BASE = "src/risk_agent/collection/formal_features.py"
STATUS = "PROPOSED_NOT_APPROVED_NOT_EXECUTABLE"
FEATURE = "public_completion_unknown"


@dataclass(frozen=True)
class CandidateContract:
    schema_version: str
    feature_names: tuple[str, ...]
    manifest_sha256: str
    config_sha256: str
    protocol_sha256: str
    status: str = STATUS
    execution_authorized: bool = False


def validate_diagnostic_value(value: int | str) -> None:
    if not (type(value) is int and value in (0, 1) or type(value) is str and value == "UNKNOWN"):
        raise ValueError("Candidate value must be integer 0/1 or explicit UNKNOWN")


def validate_publishable_value(value: int | str) -> None:
    validate_diagnostic_value(value)
    if value == "UNKNOWN":
        raise ValueError("UNKNOWN prevents complete qualified v2 release; no imputation")


def validate_definition(config: dict, base_names: tuple[str, ...]) -> None:
    if (config["status"] != STATUS or config["scope"] != "day2_candidate_contract_only"
            or config["schema_version"] != "formal-decision-features-v2"
            or config["base_schema_version"] != "formal-decision-features-v1"
            or config["base_feature_source"] != BASE or config["base_feature_count"] != 18
            or len(base_names) != 18 or tuple(config["feature_names"]) != (*base_names, FEATURE)):
        raise ValueError("Candidate must preserve exactly the ordered 18 v1 names and append one field")
    new = config["new_feature"]
    if (new["name"] != FEATURE or new["diagnostic_domain"] != [0, 1, "UNKNOWN"]
            or new["publishable_domain"] != [0, 1] or new["publishable_type"] != "integer_excluding_boolean"
            or new["unknown_policy"] != "block_complete_v2_dataset_release_no_imputation_no_label_change"
            or new["match_pattern"] != r"\bcompletion status is unknown\b"
            or new["match_flags"] != ["IGNORECASE"]
            or new["match_scope"] != "verified_single_line_public_error_body_only"
            or new["normal_observation_policy"] != "zero_only_if_wrapper_and_json_and_correspondence_valid"
            or new["other_error_policy"] != "zero_only_if_public_error_body_is_unambiguously_parsed"
            or new["source_visibility"] != "agent_visible_and_monitor_observable"
            or any(new[k] is not False for k in ("future_data_allowed", "hidden_metadata_allowed", "task_or_template_identity_as_feature"))):
        raise ValueError("Public signal or UNKNOWN boundary differs from candidate definition")
    auth = config["authorization"]
    if (any(auth[k] is not False for k in ("production_activation", "historical_backfill", "model_comparison", "new_collection"))
            or auth["day4_requires_separate_approval"] is not True
            or auth["approval_binding"] != "separate_explicit_approval_referencing_candidate_manifest_sha256"):
        raise ValueError("Day 2 does not authorize execution")
    if (config["reuse"]["module"] != "src/risk_agent/prediction/train_public_signals.py"
            or config["reuse"]["symbol"] != "review_public" or config["reuse"]["modify_existing_parser"] is not False):
        raise ValueError("Reuse the existing parser without modifying frozen code")
    if config["reuse"]["strategy"] != "future_day3_boundary_and_linkage_guards_then_reuse_existing_parser":
        raise ValueError("Only the proposed Day 3 boundary wrapper may reuse the parser")
    targets = {"qualified_train": 95, "ones": 4, "zeros": 91, "unknowns": 0,
        "original_v1_feature_changes": 0, "counts_are_not_extraction_rules_or_future_batch_requirements": True}
    if config["historical_backfill_targets_not_executed_today"] != targets:
        raise ValueError("Historical review targets changed")
    link = config["correspondence"]
    expected = {"source": "captured_action_memory_and_canonical_received_tool_responses",
        "cutoff": "after_tool_observation_before_next_model_generation",
        "explicit_response_call_id": "must_match_the_corresponding_memory_call_id",
        "single_call_without_response_id": "require_unambiguous_completed_action_correspondence",
        "multiple_calls": "require_explicit_unique_response_call_linkage_otherwise_UNKNOWN",
        "transport_ids": "provenance_only_never_predictor",
        "error_prefix": "must_match_public_tool_name_and_exact_arguments_repr",
        "framework_suffix": "only_the_frozen_known_suffix_or_empty_suffix_is_accepted",
        "multiline_or_unknown_error_envelope": "UNKNOWN"}
    if link != expected:
        raise ValueError("Public response correspondence boundary changed")


def _member(root: Path, name: str) -> Path:
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Candidate path traversal forbidden")
    path = root / relative
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.resolve().is_relative_to(root):
        raise ValueError("Candidate source symlink or escape")
    return path


def load_candidate(root: Path, *, expected_manifest_sha256: str) -> CandidateContract:
    root = root.resolve()
    manifest_bytes = _member(root, MANIFEST).read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest_sha256:
        raise ValueError("Untrusted candidate Manifest")
    lock = json.loads(manifest_bytes)
    if lock["status"] != STATUS or lock["execution_authorized"] is not False:
        raise ValueError("Candidate Manifest cannot activate v2")
    required = {CONFIG, PROTOCOL, BASE, "src/risk_agent/prediction/train_public_signals.py",
                "src/risk_agent/prediction/train_public_signals_v2.py"}
    if not required <= lock["files"].keys():
        raise ValueError("Candidate Manifest omits required protocol or implementation")
    for name, digest in lock["files"].items():
        if hashlib.sha256(_member(root, name).read_bytes()).hexdigest() != digest:
            raise ValueError("Candidate dependency Hash changed")
    tree = ast.parse(_member(root, BASE).read_text())
    assignments = [n for n in tree.body if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "NAMES" for t in n.targets)]
    if len(assignments) != 1:
        raise ValueError("Cannot establish frozen v1 Allowlist")
    base_names = tuple(ast.literal_eval(assignments[0].value))
    config = json.loads(_member(root, CONFIG).read_text())
    validate_definition(config, base_names)
    return CandidateContract(config["schema_version"], tuple(config["feature_names"]),
        expected_manifest_sha256, lock["files"][CONFIG], lock["files"][PROTOCOL])
