"""Additive formal implementation binding and offline continuation review plan."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .budget_boundary_audit import BudgetBoundaryAgentFactory
from .day5_gate import sha256_file
from .formal_collection import FormalCollector
from .formal_contract import MANIFEST, load_formal_contract, read_records, write_once_json

REVISION = "budget-boundary-audit-v1"
REVISION_MANIFEST = "docs/phase2/formal_budget_audit_manifest_v1.json"


def load_revision(root: Path) -> dict[str, Any]:
    root = root.resolve()
    load_formal_contract(root)
    revision = json.loads((root / REVISION_MANIFEST).read_text())
    if (revision["implementation_revision"] != REVISION
            or revision["parent_formal_manifest_sha256"] != sha256_file(root / MANIFEST)):
        raise ValueError("Budget audit revision parent mismatch")
    for name, digest in revision["files"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or sha256_file(path) != digest:
            raise ValueError(f"Budget audit revision input changed: {name}")
    return revision


class BudgetAuditFormalCollector(FormalCollector):
    """Same producer and research protocol; explicitly bind the new Agent code."""

    def __init__(self, **kwargs: Any):
        root = Path(kwargs["root"]).resolve()
        revision = load_revision(root)
        digest = sha256_file(root / REVISION_MANIFEST)
        directory = Path(kwargs["run_directory"]).resolve()
        if kwargs.get("resume"):
            if not (directory / "implementation_binding.json").is_file():
                raise ValueError("Cannot resume an old or differently versioned Run")
            binding = json.loads((directory / "implementation_binding.json").read_text())
            if (binding["implementation_manifest_sha256"] != digest
                    or binding["run_id"] != kwargs["run_id"]):
                raise ValueError("Cannot resume an old or differently versioned Run")
        super().__init__(**kwargs)
        try:
            self.flags.update(implementation_revision=REVISION, implementation_manifest_sha256=digest)
            self.base_factory = BudgetBoundaryAgentFactory(self.base_factory.factory)
            if not kwargs.get("resume"):
                write_once_json(directory / "implementation_binding.json", {
                    "run_id": self.run_id, "implementation_revision": REVISION,
                    "implementation_manifest_sha256": digest,
                    "parent_formal_manifest_sha256": sha256_file(root / MANIFEST),
                    "run_manifest_sha256": sha256_file(directory / "run_manifest.json"),
                    "agent_class": "risk_agent.collection.budget_boundary_audit.CheckpointableToolCallingAgent"})
                for name in {REVISION_MANIFEST, *revision["files"]}:
                    path = directory / "frozen_revision_inputs" / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("xb") as handle:
                        handle.write((root / name).read_bytes())
        except BaseException:
            self.close()
            raise

    def run_task(self, task_id: str) -> dict[str, Any]:
        load_revision(self.root)
        return super().run_task(task_id)


def prepare_continuation_plan(*, root: Path, source_run: Path,
                              expected_manifest_sha256: str,
                              offline_fixture: bool = False) -> dict[str, Any]:
    """Verify immutable evidence and propose the exact unused schedule, no clients.

    The plan grants no execution authorization and cannot be consumed by the old
    --resume path. All attempted tasks, including a censored one, remain excluded.
    """
    root, source_run = root.resolve(), source_run.resolve()
    load_revision(root)
    _, plan, _ = load_formal_contract(root)
    manifests = list(source_run.glob("artifact_manifest_*.json"))
    summaries = list(source_run.glob("final_summary_*.json"))
    if len(manifests) != 1 or len(summaries) != 1:
        raise ValueError("Continuation requires one final, reviewed parent invocation")
    manifest = manifests[0]
    if sha256_file(manifest) != expected_manifest_sha256:
        raise ValueError("Parent Artifact Manifest does not match the trusted digest")
    files = json.loads(manifest.read_text())["files"]
    indexed: set[str] = set()
    for item in files:
        path = (source_run / item["path"]).resolve()
        if (item["path"] in indexed or not path.is_relative_to(source_run)
                or path.stat().st_size != item["size_bytes"] or sha256_file(path) != item["sha256"]):
            raise ValueError("Parent artifact drift or invalid manifest path")
        indexed.add(item["path"])
    required = {"run_manifest.json", "journal.jsonl", str(summaries[0].relative_to(source_run))}
    required.update(str(p.relative_to(source_run)) for p in (source_run/"records").glob("*.jsonl"))
    if not required <= indexed:
        raise ValueError("Parent manifest omits required identity or record evidence")
    identity = json.loads((source_run / "run_manifest.json").read_text())
    summary = json.loads(summaries[0].read_text())
    if (identity["formal_manifest_sha256"] != sha256_file(root / MANIFEST)
            or identity["offline_fixture"] != offline_fixture
            or identity["formal_experiment"] == offline_fixture
            or identity["authorized_split"] != "train"
            or summary["run_id"] != identity["run_id"]
            or not summary["batch_stopped"] or summary["complete"]):
        raise ValueError("Parent is not the expected stopped Train cohort")
    journal = read_records(source_run / "journal.jsonl")
    claimed = [r["task_id"] for r in journal if r["record_type"] == "task_claimed"]
    completed = [r for r in journal if r["record_type"] == "task_completed"]
    schedule = [c for c in plan["tasks"] if c["split"] == "train"]
    if (claimed != [c["task_id"] for c in schedule[:len(claimed)]]
            or len(set(claimed)) != len(claimed)
            or [r["task_id"] for r in completed] != claimed):
        raise ValueError("Parent must be a complete prefix without unresolved/replayed claims")
    for record in completed:
        for name, digest in record["artifacts"].items():
            path = (source_run / name).resolve()
            if name not in indexed or not path.is_relative_to(source_run) or sha256_file(path) != digest:
                raise ValueError("Parent task archive mismatch")
    for name in ("tasks", "checkpoints", "continues", "risk_candidates"):
        read_records(source_run / "records" / f"{name}.jsonl")
    results = [json.loads((source_run / "tasks" / tid / "task_result.json").read_text()) for tid in claimed]
    if any(f"tasks/{tid}/task_result.json" not in indexed for tid in claimed):
        raise ValueError("Parent task results are not manifest-bound")
    if not results or [r["batch_must_stop"] for r in results] != [False]*(len(results)-1)+[True]:
        raise ValueError("Parent stop must be the final attempted task")
    if (summary["attempted"] != len(claimed)
            or summary["valid_primary"] != sum(r["accepted_y_continue"] is not None for r in results)
            or summary["repeat_branches_observed"] != sum("repeat" in r for r in results)):
        raise ValueError("Parent summary does not match task evidence")
    pending = [c for c in schedule if c["task_id"] not in set(claimed)]
    return {"schema_version": "formal-continuation-review-v1", "status": "PENDING_EXECUTION_REVIEW",
        "execution_authorized": False, "qualification_only": offline_fixture,
        "parent_run_id": identity["run_id"], "parent_artifact_manifest_sha256": expected_manifest_sha256,
        "parent_artifact_files_verified": len(files), "parent_run_manifest_sha256": sha256_file(source_run/"run_manifest.json"),
        "implementation_revision": REVISION,
        "implementation_manifest_sha256": sha256_file(root / REVISION_MANIFEST),
        "parent_formal_manifest_sha256": sha256_file(root / MANIFEST),
        "plan_sha256": identity["plan_sha256"], "split": "train",
        "excluded_attempted_task_ids": claimed,
        "censored_task_ids_preserved": [r["task_id"] for r in results if r["accepted_y_continue"] is None],
        "remaining_tasks": pending, "remaining_primary": len(pending),
        "remaining_repeat_task_ids": [c["task_id"] for c in pending if c["scheduled_repeat"]],
        "parent_completed_repeats": sum("repeat" in r for r in results),
        "requirements": ["separate_review_of_explicit_parent_child_lineage", "fresh_worker_model_tls_preflight",
            "new_unique_child_run_id", "no_parent_writes_or_label_recalculation", "same_task_order_and_worker_mapping",
            "zero_source_retries_and_failover", "child_and_union_quality_audit_before_any_dataset_release"],
        "network_requests": 0}
