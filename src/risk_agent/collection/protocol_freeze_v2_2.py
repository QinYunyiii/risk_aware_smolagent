"""Additive v2.2 output contract; v2.1 manifests and entry remain authoritative."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import yaml

from risk_agent.collection.continue_acceptance import (
    ENTRY_MANIFEST_PATH as LEGACY_ENTRY_MANIFEST, load_entry as load_legacy_entry,
)
from risk_agent.collection.day5_gate import canonical_task_catalog_hash, split_manifest_hash
from risk_agent.collection.protocol_freeze import FrozenCollectionProfile, file_sha256


PROTOCOL_ID = "risk-continue-v2.2.0"
REVISION = "output-contract-v1"
MANIFEST_PATH = "docs/phase2/protocol_freeze_manifest_v2_2.json"
PROFILE_PATH = "configs/phase2_risk_collection_v2_2.yaml"
CONTRACT_PATH = "configs/continue_output_contract_v1.txt"


def append_output_contract(prompt: str, contract: str) -> str:
    """Only public task text and a universal public contract enter the renderer."""
    return prompt + "\n\n" + contract.strip()


def load_frozen_collection(root: str | Path) -> FrozenCollectionProfile:
    root = Path(root).resolve()
    parent, _ = load_legacy_entry(root)
    manifest = json.loads((root / MANIFEST_PATH).read_text())
    if (manifest["protocol_id"] != PROTOCOL_ID
        or manifest["parent_manifest_sha256"] != parent.manifest_sha256
        or manifest["legacy_entry_manifest_sha256"] != file_sha256(root / LEGACY_ENTRY_MANIFEST)):
        raise ValueError("v2.2 requires its unchanged v2.1 parent and entry")
    for name, digest in manifest["files"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file() or file_sha256(path) != digest:
            raise ValueError(f"v2.2 frozen input changed: {name}")
    config = yaml.safe_load((root / PROFILE_PATH).read_text())
    if config["protocol_id"] != PROTOCOL_ID or config["implementation_revision"] != REVISION:
        raise ValueError("v2.2 profile identity mismatch")
    remote = yaml.safe_load((root / config["dependencies"]["remote_model_config"]).read_text())
    if (remote["prompt_version"] != manifest["prompt_version"]
        or remote["inference"]["max_tokens"] != config["limits"]["max_completion_tokens_per_call"]):
        raise ValueError("v2.2 prompt identity or completion budget mismatch")
    tasks = tuple(replace(task, prompt=append_output_contract(
        task.prompt, (root / CONTRACT_PATH).read_text())) for task in parent.tasks)
    if canonical_task_catalog_hash(tasks) != manifest["effective_catalog_sha256"]:
        raise ValueError("v2.2 effective catalog changed")
    if (split_manifest_hash(tasks) != manifest["split_manifest_sha256"]
        or split_manifest_hash(tasks) != parent.audit_summary()["split_manifest_sha256"]):
        raise ValueError("v2.2 split differs from parent")
    return FrozenCollectionProfile(config, tasks, file_sha256(root / MANIFEST_PATH))
