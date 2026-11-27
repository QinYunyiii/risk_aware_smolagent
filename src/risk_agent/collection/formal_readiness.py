"""Offline audit of the transition from the frozen smoke entry to formal collection.

This does not promote smoke records, assign missing faults, activate features, or
construct a model client. It distinguishes a passing engineering Gate from the
unimplemented formal producer and its unregistered collection controls.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

from risk_agent.benchmark import load_task_templates
from .continue_acceptance_v2 import FLAGS, load_entry


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_engineering_gate(run: Path, manifest_hash: str, task_ids: list[str]) -> dict[str, Any]:
    """Verify the actual immutable Gate, not just its report or passed flag."""
    run = run.resolve()
    try:
        summary = json.loads((run / "gate_summary_final_audit.json").read_text())
        original = json.loads((run / "run_manifest.json").read_text())
        manifest = json.loads((run / "artifact_manifest.json").read_text())
        actual = {str(p.relative_to(run)) for p in run.rglob("*")
                  if p.is_file() and p.name != "artifact_manifest.json"}
        if actual != {f["path"] for f in manifest["files"]}:
            raise ValueError("Gate file set mismatch")
        for item in manifest["files"]:
            p = (run / item["path"]).resolve()
            if (not p.is_relative_to(run) or p.stat().st_size != item["size_bytes"]
                or _sha(p) != item["sha256"]):
                raise ValueError("Gate artifact integrity mismatch")
        if (summary.get("gate") != "CONTINUE_ENGINEERING_GATE_PASS"
            or summary.get("passed") is not True or summary.get("offline_fixture") is not False
            or summary.get("connection_resources_released") is not True
            or original.get("freeze_manifest_sha256") != manifest_hash
            or [t["task_id"] for t in summary["tasks"]] != task_ids
            or not summary.get("real_model_calls", 0)):
            raise ValueError("Gate provenance does not match the frozen real entry")
        for row in summary["tasks"]:
            c = row["component"]
            if not (row["engineering_passed"] and c["complete_chain"]
                    and c["first_input_exact"] and len(c["restore_hashes"]) == 6
                    and all(c["restore_hashes"].values()) and c["checkpoint_immutable"]
                    and row["provider_audit"]["complete"] and row["resources_released"]):
                raise ValueError("Gate task evidence is incomplete")
        return {"verified": True, "run_id": summary["run_id"],
                "artifact_files_verified": len(manifest["files"]),
                "smoke_data_eligible_for_formal_dataset": False}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"verified": False, "error_type": type(exc).__name__,
                "smoke_data_eligible_for_formal_dataset": False}


def audit_formal_readiness(root: Path, gate_run: Path) -> dict[str, Any]:
    root = root.resolve()
    profile, entry = load_entry(root)
    templates = {t.template_id: t for t in load_task_templates(root / "configs/task_templates_v2.yaml")}
    inventory = []
    for task in profile.tasks:
        plan = templates[task.template_id].parameters.get("fault_plan")
        inventory.append({"task_id": task.task_id, "template_id": task.template_id,
            "domain": task.domain.value, "split": task.split.value,
            "template_fault_control": deepcopy(dict(plan)) if plan is not None else None,
            "formal_instance_schedule_registered": False})
    allowlist = json.loads((root / profile.config["dependencies"]["feature_allowlist"]).read_text())
    features = allowlist["features"]
    enabled = [f["feature_name"] for f in features if f["allowed_for_deployment"]]
    gate = verify_engineering_gate(gate_run, profile.manifest_sha256, entry["task_ids"])
    split_counts = Counter(t["split"] for t in inventory)
    f6 = {}
    for split in ("train", "validation", "test"):
        f6[split] = dict(Counter(
            t["template_fault_control"]["parameters"].get("commit_status", "UNSPECIFIED")
            for t in inventory if t["split"] == split and t["template_fault_control"]
            and t["template_fault_control"]["fault_type"] == "F6_PARTIAL_FAILURE"))
    required_train = {"committed", "not_committed", "unknown"}
    f6_checks = {
        "train_all_three_commit_states": required_train <= set(f6["train"]),
        "validation_at_least_two_including_unknown": sum(f6["validation"].values()) >= 2
            and f6["validation"].get("unknown", 0) >= 1,
        "test_at_least_two_including_unknown": sum(f6["test"].values()) >= 2
            and f6["test"].get("unknown", 0) >= 1,
    }
    domain_fault_cells = []
    for split in ("train", "validation", "test"):
        for domain in sorted({t["domain"] for t in inventory}):
            counts = Counter(t["template_fault_control"]["fault_type"]
                for t in inventory if t["split"] == split and t["domain"] == domain
                and t["template_fault_control"])
            domain_fault_cells.append({"split": split, "domain": domain,
                                       "template_planned_counts": dict(counts)})
    blockers = []
    if not gate["verified"]:
        blockers.append("REAL_ENGINEERING_GATE_PROOF_MISSING_OR_INVALID")
    # These are facts about the audited entry. A future formal producer needs its
    # own frozen, tested entry; changing the booleans below cannot qualify it.
    if len(entry["task_ids"]) == 5 and FLAGS["formal_experiment"] is False:
        blockers.append("AUDITED_ENTRY_IS_FIVE_TRAIN_TASK_SMOKE_NOT_FORMAL_BATCH")
    if any(not t["formal_instance_schedule_registered"] for t in inventory):
        blockers.append("FORMAL_PER_INSTANCE_FAULT_SCHEDULE_NOT_REGISTERED")
    if not all(f6_checks.values()):
        blockers.append("TEMPLATE_F6_ASSIGNMENTS_DO_NOT_MEET_PROTOCOL_SPLIT_REQUIREMENTS")
    if not enabled:
        blockers.append("V2_FEATURE_PROVENANCE_EXTRACTOR_AND_ACTIVATION_PENDING")
    return {"gate": "FORMAL_COLLECTION_BLOCKED" if blockers else "FORMAL_READINESS_REVIEW_REQUIRED",
        "formal_collection_ready": False, "formal_collection_started": False,
        "model_requests": 0, "remote_connections": 0, "blockers": blockers,
        "engineering_gate": gate, "protocol_id": profile.config["protocol_id"],
        "manifest_sha256": profile.manifest_sha256, "task_count": len(inventory),
        "tasks_by_split": dict(split_counts), "templates": len(templates),
        "tasks_without_template_fault_control": sum(t["template_fault_control"] is None for t in inventory),
        "f6_planned_commit_states": f6, "f6_protocol_checks": f6_checks,
        "candidate_feature_count": len(features), "enabled_features": enabled,
        "feature_extractors_still_pending": sum(str(f.get("computation_code_location", "")).startswith("PENDING") for f in features),
        "domain_fault_cells": domain_fault_cells, "task_fault_control_inventory": inventory,
        "inventory_is_control_audit_not_model_input_or_training_examples": True}
