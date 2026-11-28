"""Validation entry freeze binds the unchanged approved schedule and parent code."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import yaml

from .causal_collection import CAUSAL_MANIFEST,load_causal_contract
from .formal_contract import PLAN,load_formal_contract
from .day5_gate import sha256_file

VALIDATION_MANIFEST="docs/phase2/formal_validation_manifest_v1.json"
VALIDATION_CONFIG="configs/formal_validation_v1.yaml"


def load_validation_contract(root:Path):
    root=root.resolve();load_causal_contract(root)
    profile,plan,_=load_formal_contract(root)
    manifest=json.loads((root/VALIDATION_MANIFEST).read_text())
    if (manifest["authorized_split"]!="validation"
            or manifest["parent_causal_manifest_sha256"]!=sha256_file(root/CAUSAL_MANIFEST)):
        raise ValueError("Validation entry parent/split mismatch")
    for name,digest in manifest["files"].items():
        path=(root/name).resolve()
        if not path.is_relative_to(root)or sha256_file(path)!=digest:
            raise ValueError(f"Validation entry input changed: {name}")
    config=yaml.safe_load((root/VALIDATION_CONFIG).read_text())
    cases=[c for c in plan["tasks"]if c["split"]=="validation"]
    ids=[c["task_id"]for c in cases];repeats=[c["task_id"]for c in cases if c["scheduled_repeat"]]
    if (config["authorized_split"]!="validation"or config["primary_target"]!=32 or config["repeat_target"]!=7
            or len(ids)!=32 or len(repeats)!=7 or config["task_ids"]!=ids
            or config["repeat_task_ids"]!=repeats or config["minimum_valid_primary"]!=24
            or config["source_plan_sha256"]!=sha256_file(root/PLAN)):
        raise ValueError("Validation must use exactly the approved 32/7 schedule")
    templates={split:{t.template_id for t in profile.tasks if t.split.value==split}
               for split in("train","validation","test")}
    if templates["validation"]& (templates["train"]|templates["test"]):
        raise ValueError("Validation template split leakage")
    return profile,plan,manifest,config
