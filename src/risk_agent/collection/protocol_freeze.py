"""Load the independently versioned formal profile, with offline drift checks.

This module never constructs a model client or runs an Agent. Passing its checks
establishes configuration integrity, not authorization or real-model acceptance.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, replace
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping

import yaml

from risk_agent.benchmark import generate_task_instances, load_task_templates
from risk_agent.benchmark.schema import TaskSpec, TaskTemplate
from risk_agent.collection.day5_gate import canonical_task_catalog_hash, split_manifest_hash


PROTOCOL_ID = "risk-continue-v2.1.0"
PROFILE_PATH = "configs/phase2_risk_collection_v2_1.yaml"
MANIFEST_PATH = "docs/phase2/protocol_freeze_manifest_v2_1.json"
SPLIT_PATH = "docs/phase2/task_split_manifest_v2_1.json"


class ProtocolFreezeError(ValueError):
    """A frozen input or its effective catalog has drifted."""


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render_exact_identifier_prompt(task: TaskSpec, template: TaskTemplate) -> str:
    """Render only public task inputs; do not inspect expected/reference/fault data."""
    if template.family != "multi_api" or task.template_id != template.template_id:
        raise ProtocolFreezeError("Exact identifier renderer requires a matching multi_api task")
    if len(task.orders) != 1:
        raise ProtocolFreezeError("Expected exactly one initial order")
    order_id = json.dumps(task.orders[0]["order_id"], ensure_ascii=False)
    new_status = json.dumps(template.parameters["new_status"], ensure_ascii=False)
    instance_number = int(task.instance_id.rsplit("_", 1)[1])
    key = json.dumps(f"{template.template_id}-{instance_number:03d}")
    return (
        f"Use the API tools to read the order whose exact order_id string is {order_id} "
        '(including the "order-" prefix). Use the status returned by get_order as '
        f"expected_status to update that same order to {new_status} with "
        f"idempotency_key {key}. Then query the same exact order_id again and return "
        "the observed final state. Do not shorten or normalize identifiers."
    )


def build_formal_catalog(root: Path) -> tuple[TaskSpec, ...]:
    """Derive a new catalog without changing either original template file."""
    root = Path(root)
    policy = yaml.safe_load((root / "configs/task_catalog_v2_1.yaml").read_text())
    if policy["prompt_policy"] != "p2-multi-api-exact-identifiers-v1":
        raise ProtocolFreezeError("Unsupported prompt policy")
    inherited = load_task_templates(root / "configs/task_templates.yaml")
    templates = load_task_templates(root / policy["source_templates"])
    if templates[: len(inherited)] != inherited:
        raise ProtocolFreezeError("Original template definitions or splits changed")
    old_ids = {template.template_id for template in inherited}
    by_id = {template.template_id: template for template in templates}
    tasks = generate_task_instances(templates, policy["instances_per_template"])
    return tuple(
        replace(task, prompt=render_exact_identifier_prompt(task, by_id[task.template_id]))
        if task.template_id not in old_ids and by_id[task.template_id].family == "multi_api"
        else task
        for task in tasks
    )


def split_records(tasks: tuple[TaskSpec, ...]) -> list[dict[str, Any]]:
    return [
        {
            "task_id": task.task_id,
            "template_id": task.template_id,
            "instance_id": task.instance_id,
            "seed": task.seed,
            "domain": task.domain.value,
            "split": task.split.value if task.split is not None else None,
        }
        for task in tasks
    ]


@dataclass(frozen=True)
class FrozenCollectionProfile:
    config: Mapping[str, Any]
    tasks: tuple[TaskSpec, ...]
    manifest_sha256: str

    def audit_summary(self) -> dict[str, Any]:
        return {
            "gate": "PROTOCOL_FREEZE_LOCAL_PASS",
            "protocol_id": self.config["protocol_id"],
            "manifest_sha256": self.manifest_sha256,
            "catalog_sha256": canonical_task_catalog_hash(self.tasks),
            "split_manifest_sha256": split_manifest_hash(self.tasks),
            "task_count": len(self.tasks),
            "template_count": len({task.template_id for task in self.tasks}),
            "tasks_by_split": dict(Counter(task.split.value for task in self.tasks)),
            "max_completion_tokens_per_call": self.config["limits"]["max_completion_tokens_per_call"],
            "capture_rule": self.config["capture"]["primary_rule"],
            "formal_collection_ready": False,
            "remaining_gate": "new-profile resume(stream=True) to terminal plus deterministic verifier",
            "network_operations": 0,
        }


def load_frozen_collection(root: str | Path) -> FrozenCollectionProfile:
    """Fail closed on local source, dependencies, Prompt, catalog or split drift."""
    root = Path(root).resolve()
    manifest_path = root / MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text())
    if manifest["protocol_id"] != PROTOCOL_ID:
        raise ProtocolFreezeError("Protocol identity mismatch")
    for relative, expected in manifest["files"].items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file() or file_sha256(path) != expected:
            raise ProtocolFreezeError(f"Frozen input changed: {relative}")
    for package, expected in manifest["package_versions"].items():
        if version(package) != expected:
            raise ProtocolFreezeError(f"Dependency version changed: {package}")
    prompt = files("smolagents").joinpath("prompts/toolcalling_agent.yaml").read_bytes()
    if hashlib.sha256(prompt).hexdigest() != manifest["system_prompt_resource_sha256"]:
        raise ProtocolFreezeError("smolagents system prompt resource changed")
    config = yaml.safe_load((root / PROFILE_PATH).read_text())
    remote = yaml.safe_load((root / config["dependencies"]["remote_model_config"]).read_text())
    if config["protocol_id"] != PROTOCOL_ID:
        raise ProtocolFreezeError("Collection profile identity mismatch")
    if remote["inference"]["max_tokens"] != config["limits"]["max_completion_tokens_per_call"]:
        raise ProtocolFreezeError("Remote and collection completion budgets disagree")
    tasks = build_formal_catalog(root)
    if canonical_task_catalog_hash(tasks) != manifest["effective_catalog_sha256"]:
        raise ProtocolFreezeError("Effective TaskSpec catalog changed")
    if split_manifest_hash(tasks) != manifest["split_manifest_sha256"]:
        raise ProtocolFreezeError("Effective split changed")
    if json.loads((root / SPLIT_PATH).read_text())["tasks"] != split_records(tasks):
        raise ProtocolFreezeError("Concrete split records disagree with catalog")
    return FrozenCollectionProfile(config, tasks, file_sha256(manifest_path))
