"""Approved formal plan and append-only canonical records; no inference here."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from risk_agent.agent.agent_state_adapter import canonical_hash
from risk_agent.collection.continue_acceptance_v2 import load_entry
from risk_agent.collection.day5_gate import sha256_file
from risk_agent.monitoring import JsonlTraceWriter

MANIFEST = "docs/phase2/formal_collection_manifest_v1.json"
PLAN = "configs/formal_collection_v1.yaml"


def write_once_json(path: Path, value: Any) -> None:
    from risk_agent.monitoring.provider_response_audit import sanitize_audit_value
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(sanitize_audit_value(value), handle, ensure_ascii=False, allow_nan=False, indent=2)
        handle.write("\n"); handle.flush(); os.fsync(handle.fileno())


def load_formal_contract(root: Path):
    root = root.resolve()
    profile, _ = load_entry(root)
    manifest = json.loads((root / MANIFEST).read_text())
    if manifest["parent_manifest_sha256"] != profile.manifest_sha256:
        raise ValueError("Formal plan parent changed")
    for name, expected in manifest["files"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or sha256_file(path) != expected:
            raise ValueError(f"Formal input changed: {name}")
    plan = yaml.safe_load((root / PLAN).read_text())
    if plan["status"] != "APPROVED_FROZEN" or plan["source_catalog_sha256"] != profile.audit_summary()["catalog_sha256"]:
        raise ValueError("Formal plan/catalog not frozen")
    if plan["split_sha256"] != profile.audit_summary()["split_manifest_sha256"]:
        raise ValueError("Formal split changed")
    return profile, plan, manifest


def read_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists(): return []
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    previous = None
    for row in rows:
        digest = row["record_sha256"]
        if row.get("previous_record_sha256") != previous or canonical_hash({k:v for k,v in row.items() if k != "record_sha256"}) != digest:
            raise ValueError("Append-only record chain corrupted")
        previous = digest
    return rows


class RecordLog:
    def __init__(self, path: Path, *, run_id: str, experiment_id: str):
        self.path, self.run_id, self.experiment_id = path, run_id, experiment_id
        rows = read_records(path)
        self.previous = rows[-1]["record_sha256"] if rows else None
        self.writer = JsonlTraceWriter(path)

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        value = {"schema_version": "risk-data-v2.0.0", "run_id": self.run_id,
            "experiment_id": self.experiment_id, "created_at": datetime.now(timezone.utc).isoformat(),
            **record, "previous_record_sha256": self.previous}
        value["record_sha256"] = canonical_hash(value)
        self.writer.append(value)
        self.previous = value["record_sha256"]
        return value
