"""Independent policy revision; all frozen parent inputs are still verified."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import yaml

from .aggregate_target_policy import POLICY, MEMBERS, applicable
from .day5_gate import sha256_file
from .formal_contract import PLAN, write_once_json
from .validation_contract import VALIDATION_MANIFEST, load_validation_contract

TARGET_MANIFEST = "docs/phase2/aggregate_fault_target_manifest_v1.json"
TARGET_CONFIG = "configs/aggregate_fault_target_v1.yaml"


def load_aggregate_contract(root: Path):
    root = root.resolve()
    profile, original, parent, validation = load_validation_contract(root)
    manifest = json.loads((root / TARGET_MANIFEST).read_text())
    if (manifest["revision"] != POLICY or manifest["authorized_split"] != "validation"
            or manifest["parent_validation_manifest_sha256"] != sha256_file(root / VALIDATION_MANIFEST)):
        raise ValueError("Aggregate revision parent/split mismatch")
    for name, digest in manifest["files"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or sha256_file(path) != digest:
            raise ValueError(f"Aggregate revision input changed: {name}")
    policy = yaml.safe_load((root / TARGET_CONFIG).read_text())
    if (policy["revision"] != POLICY or policy["domain"] != "data_analysis"
            or policy["members"] != sorted(MEMBERS)
            or policy["fault_types"] != ["F2_API_503", "F5_REPEATED_ACTION"]
            or policy["trigger_indices"] != [1]
            or policy["parent_validation_manifest_sha256"] != sha256_file(root / VALIDATION_MANIFEST)
            or policy["source_plan_sha256"] != sha256_file(root / PLAN)
            or policy["authorized_execution_split"] != "validation"
            or (policy["primary_target"], policy["repeat_target"], policy["minimum_valid_primary"]) != (32, 7, 24)):
        raise ValueError("Aggregate target policy differs from approved scope")
    plan = deepcopy(original)
    selected = [case for case in plan["tasks"] if applicable(case)]
    if dict(Counter(c["split"] for c in selected)) != {"validation": 2, "test": 2}:
        raise ValueError("Aggregate target metadata scope changed")
    if policy["scope_counts"] != {"train": 0, "validation": 2, "test": 2}:
        raise ValueError("Aggregate target scope counts changed")
    for case in selected:
        if case["call_indices"] != [1] or "target_match_policy" in case["parameters"]:
            raise ValueError("Unexpected pre-existing target policy or trigger indices")
        case["parameters"]["target_match_policy"] = POLICY
    plan["formal_experiment_id"] = policy["experiment_id"]
    if not policy["experiment_id"] or policy["experiment_id"] == original["formal_experiment_id"]:
        raise ValueError("Aggregate revision requires a separate experiment identity")
    return profile, plan, parent, validation, manifest, policy


def claim_aggregate_cohort(root: Path, run_id: str) -> Path:
    """One claim per frozen policy, even if a later code manifest has changed."""
    load_aggregate_contract(root)
    from risk_agent.benchmark.schema import validate_identifier
    validate_identifier(run_id, "run_id")
    path = (root / "runs/phase2_validation_target_control" / sha256_file(root / PLAN)
            / sha256_file(root / TARGET_CONFIG) / "launch_claim.json")
    write_once_json(path, {"run_id": run_id, "revision": POLICY,
                          "aggregate_manifest_sha256": sha256_file(root / TARGET_MANIFEST),
                          "policy_sha256": sha256_file(root / TARGET_CONFIG), "scope": "validation-32-7"})
    return path
