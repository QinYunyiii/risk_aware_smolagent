"""Version-dispatched checkpoint manifests with strict v2 validation."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Mapping


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CheckpointSchemaError(ValueError):
    """Raised when a checkpoint manifest violates its declared schema."""


@dataclass(frozen=True, slots=True)
class CheckpointManifest:
    """Historical environment/runtime-only v1 manifest."""

    schema_version: int
    checkpoint_id: str
    created_at: str
    task_id: str
    state_hash: str
    environment_hash: str
    runtime_hash: str
    task_config_hash: str
    mock_api_state_hash: str
    artifact_checksums: Mapping[str, str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CheckpointManifest:
        if int(raw["schema_version"]) != 1:
            raise CheckpointSchemaError("Unsupported checkpoint schema version")
        checksums = raw.get("artifact_checksums")
        if not isinstance(checksums, dict):
            raise CheckpointSchemaError("artifact_checksums must be a mapping")
        return cls(
            schema_version=1,
            checkpoint_id=str(raw["checkpoint_id"]),
            created_at=str(raw["created_at"]),
            task_id=str(raw["task_id"]),
            state_hash=str(raw["state_hash"]),
            environment_hash=str(raw["environment_hash"]),
            runtime_hash=str(raw["runtime_hash"]),
            task_config_hash=str(raw["task_config_hash"]),
            mock_api_state_hash=str(raw["mock_api_state_hash"]),
            artifact_checksums={str(key): str(value) for key, value in checksums.items()},
        )


@dataclass(frozen=True, slots=True)
class ArtifactRecordV2:
    path: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CheckpointManifestV2:
    schema_version: str
    identity: Mapping[str, Any]
    agent_context: Mapping[str, Any]
    environment_state: Mapping[str, Any]
    runtime_state: Mapping[str, Any]
    hashes: Mapping[str, str]
    artifacts: tuple[ArtifactRecordV2, ...]
    capture_invariants: Mapping[str, bool]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "identity": _json_copy(self.identity),
            "agent_context": _json_copy(self.agent_context),
            "environment_state": _json_copy(self.environment_state),
            "runtime_state": _json_copy(self.runtime_state),
            "hashes": dict(self.hashes),
            "artifacts": [item.to_dict() for item in self.artifacts],
            "capture_invariants": dict(self.capture_invariants),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CheckpointManifestV2:
        document = _json_copy(raw)
        _validate_v2_manifest(document)
        return cls(
            schema_version="2.0.0",
            identity=document["identity"],
            agent_context=document["agent_context"],
            environment_state=document["environment_state"],
            runtime_state=document["runtime_state"],
            hashes=document["hashes"],
            artifacts=tuple(ArtifactRecordV2(**item) for item in document["artifacts"]),
            capture_invariants=document["capture_invariants"],
        )


def _json_copy(value: Any) -> Any:
    try:
        return json.loads(
            json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
        )
    except (TypeError, ValueError) as exc:
        raise CheckpointSchemaError("Manifest must contain finite JSON values") from exc


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CheckpointSchemaError(f"{field} must be an object")
    return value


def _exact_keys(
    value: Mapping[str, Any], required: set[str], optional: set[str], field: str
) -> None:
    keys = set(value)
    missing = required - keys
    extra = keys - required - optional
    if missing:
        raise CheckpointSchemaError(f"{field} missing required fields: {sorted(missing)}")
    if extra:
        raise CheckpointSchemaError(f"{field} has unsupported fields: {sorted(extra)}")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CheckpointSchemaError(f"{field} must be a non-empty string")
    return value


def _integer(value: Any, field: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise CheckpointSchemaError(f"{field} must be an integer >= {minimum}")
    return value


def _number(value: Any, field: str, minimum: float, *, exclusive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CheckpointSchemaError(f"{field} must be a number")
    if (exclusive and value <= minimum) or (not exclusive and value < minimum):
        comparator = ">" if exclusive else ">="
        raise CheckpointSchemaError(f"{field} must be {comparator} {minimum}")
    return float(value)


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise CheckpointSchemaError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _relative_artifact(value: Any, field: str) -> str:
    text = _text(value, field)
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or text.startswith("/")
        or "\\" in text
        or path.as_posix() != text
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise CheckpointSchemaError(f"{field} must be a safe relative artifact path")
    return text


def _validate_identity(value: Any) -> None:
    identity = _object(value, "identity")
    required = {
        "experiment_id", "run_id", "task_run_id", "template_id", "task_id",
        "instance_id", "split", "checkpoint_id", "step_index", "model_id",
        "model_version", "worker_id", "prompt_version", "tool_version",
        "verifier_version", "controller_type", "execution_mode", "created_at",
    }
    _exact_keys(
        identity,
        required,
        {"catalog_hash", "split_manifest_hash", "seed"},
        "identity",
    )
    for field in required - {"step_index", "split"}:
        _text(identity[field], f"identity.{field}")
    _integer(identity["step_index"], "identity.step_index", 1)
    if "seed" in identity:
        _integer(identity["seed"], "identity.seed", 0)
    if identity["split"] not in {"train", "validation", "test"}:
        raise CheckpointSchemaError("identity.split is invalid")
    if identity["controller_type"] != "qwen3_model":
        raise CheckpointSchemaError("identity.controller_type is invalid")
    if identity["execution_mode"] not in {"model_backed_smoke", "model_backed_formal"}:
        raise CheckpointSchemaError("identity.execution_mode is invalid")
    try:
        created = datetime.fromisoformat(identity["created_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise CheckpointSchemaError("identity.created_at must be a date-time") from exc
    if created.tzinfo is None:
        raise CheckpointSchemaError("identity.created_at must include a timezone")
    for field in ("catalog_hash", "split_manifest_hash"):
        if field in identity:
            _sha256(identity[field], f"identity.{field}")


def _validate_agent_context(value: Any) -> None:
    context = _object(value, "agent_context")
    required = {
        "framework", "framework_version", "agent_class", "messages_ref",
        "memory_steps_ref", "latest_action_step_ref", "system_prompt_ref",
        "task_prompt_ref", "assistant_tool_calls_ref", "tool_observations_ref",
        "step_number", "next_step_number", "max_steps", "remaining_steps",
        "tool_registry", "generation_config", "agent_state", "next_model_input_ref",
    }
    optional = {"planning_interval", "stream_outputs", "capture_granularity"}
    _exact_keys(context, required, optional, "agent_context")
    constants = {
        "framework": "smolagents",
        "framework_version": "1.26.0",
        "agent_class": "ToolCallingAgent",
    }
    for field, expected in constants.items():
        if context[field] != expected:
            raise CheckpointSchemaError(f"agent_context.{field} must be {expected!r}")
    for field in (
        "messages_ref", "memory_steps_ref", "latest_action_step_ref", "system_prompt_ref",
        "task_prompt_ref", "assistant_tool_calls_ref", "tool_observations_ref",
        "next_model_input_ref",
    ):
        _relative_artifact(context[field], f"agent_context.{field}")
    _integer(context["step_number"], "agent_context.step_number", 1)
    _integer(context["next_step_number"], "agent_context.next_step_number", 2)
    _integer(context["max_steps"], "agent_context.max_steps", 2)
    _integer(context["remaining_steps"], "agent_context.remaining_steps", 1)
    if "planning_interval" in context and context["planning_interval"] is not None:
        _integer(context["planning_interval"], "agent_context.planning_interval", 1)
    if "stream_outputs" in context and not isinstance(context["stream_outputs"], bool):
        raise CheckpointSchemaError("agent_context.stream_outputs must be boolean")
    if "capture_granularity" in context and context["capture_granularity"] != "action_step_observation_batch":
        raise CheckpointSchemaError("agent_context.capture_granularity is invalid")
    if not isinstance(context["agent_state"], dict):
        raise CheckpointSchemaError("agent_context.agent_state must be an object")

    registry = _object(context["tool_registry"], "agent_context.tool_registry")
    _exact_keys(
        registry,
        {"version", "schema_ref", "schema_hash", "ordered_tool_names"},
        set(),
        "agent_context.tool_registry",
    )
    _text(registry["version"], "agent_context.tool_registry.version")
    _relative_artifact(registry["schema_ref"], "agent_context.tool_registry.schema_ref")
    _sha256(registry["schema_hash"], "agent_context.tool_registry.schema_hash")
    names = registry["ordered_tool_names"]
    if not isinstance(names, list) or not names:
        raise CheckpointSchemaError("ordered_tool_names must be a non-empty array")
    for index, name in enumerate(names):
        _text(name, f"ordered_tool_names[{index}]")

    generation = _object(context["generation_config"], "agent_context.generation_config")
    required_generation = {"seed", "temperature", "max_tokens", "chat_template_kwargs"}
    if not required_generation.issubset(generation):
        raise CheckpointSchemaError("generation_config is missing required fields")
    _integer(generation["seed"], "generation_config.seed", 0)
    _number(generation["temperature"], "generation_config.temperature", 0)
    _integer(generation["max_tokens"], "generation_config.max_tokens", 1)
    _object(generation["chat_template_kwargs"], "generation_config.chat_template_kwargs")


def _validate_environment_state(value: Any) -> None:
    state = _object(value, "environment_state")
    required = {
        "workspace_manifest_ref", "sqlite_backup_ref", "sqlite_logical_dump_ref",
        "mock_api_state_ref", "file_state_ref", "database_state_ref",
        "side_effects_ref", "pending_operation",
    }
    _exact_keys(state, required, {"latest_completed_operation_ref"}, "environment_state")
    for field in required - {"pending_operation"}:
        _relative_artifact(state[field], f"environment_state.{field}")
    if "latest_completed_operation_ref" in state:
        _relative_artifact(
            state["latest_completed_operation_ref"],
            "environment_state.latest_completed_operation_ref",
        )
    pending = _object(state["pending_operation"], "environment_state.pending_operation")
    _exact_keys(pending, {"kind", "next_step_number"}, set(), "pending_operation")
    if pending["kind"] != "model_generation":
        raise CheckpointSchemaError("pending_operation.kind is invalid")
    _integer(pending["next_step_number"], "pending_operation.next_step_number", 2)


def _validate_runtime_state(value: Any) -> None:
    state = _object(value, "runtime_state")
    required = {
        "runtime_ref", "tool_history_ref", "tool_adapter_state_ref",
        "fault_adapter_state_ref", "budget_state", "resource_usage", "execution_position",
    }
    _exact_keys(state, required, set(), "runtime_state")
    for field in ("runtime_ref", "tool_history_ref", "tool_adapter_state_ref", "fault_adapter_state_ref"):
        _relative_artifact(state[field], f"runtime_state.{field}")
    budget = _object(state["budget_state"], "runtime_state.budget_state")
    budget_fields = {
        "max_tokens", "max_tool_calls", "max_model_calls", "max_latency_seconds",
        "prompt_tokens_used", "completion_tokens_used", "tool_calls_used",
        "model_calls_used", "latency_seconds_used",
    }
    _exact_keys(budget, budget_fields, set(), "runtime_state.budget_state")
    for field in ("max_tokens", "max_tool_calls", "max_model_calls"):
        _integer(budget[field], f"budget_state.{field}", 1)
    _number(budget["max_latency_seconds"], "budget_state.max_latency_seconds", 0, exclusive=True)
    for field in ("prompt_tokens_used", "completion_tokens_used"):
        if budget[field] is not None:
            _integer(budget[field], f"budget_state.{field}", 0)
    _integer(budget["tool_calls_used"], "budget_state.tool_calls_used", 0)
    _integer(budget["model_calls_used"], "budget_state.model_calls_used", 1)
    _number(budget["latency_seconds_used"], "budget_state.latency_seconds_used", 0)
    _object(state["resource_usage"], "runtime_state.resource_usage")
    position = _object(state["execution_position"], "runtime_state.execution_position")
    _exact_keys(position, {"phase", "next_operation"}, set(), "execution_position")
    if position != {
        "phase": "after_tool_observation_before_model_generation",
        "next_operation": "model_generation",
    }:
        raise CheckpointSchemaError("runtime_state.execution_position is invalid")


def _validate_v2_manifest(raw: Any) -> None:
    document = _object(raw, "manifest")
    top_fields = {
        "schema_version", "identity", "agent_context", "environment_state",
        "runtime_state", "hashes", "artifacts", "capture_invariants",
    }
    _exact_keys(document, top_fields, set(), "manifest")
    if document["schema_version"] != "2.0.0":
        raise CheckpointSchemaError("schema_version must be '2.0.0'")
    _validate_identity(document["identity"])
    _validate_agent_context(document["agent_context"])
    _validate_environment_state(document["environment_state"])
    _validate_runtime_state(document["runtime_state"])

    hashes = _object(document["hashes"], "hashes")
    hash_fields = {
        "agent_state_hash", "next_model_input_hash", "environment_state_hash",
        "runtime_state_hash", "identity_config_hash", "combined_state_hash",
    }
    _exact_keys(hashes, hash_fields, set(), "hashes")
    for field in hash_fields:
        _sha256(hashes[field], f"hashes.{field}")

    artifacts = document["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) < 10:
        raise CheckpointSchemaError("artifacts must contain at least 10 entries")
    for index, item_value in enumerate(artifacts):
        item = _object(item_value, f"artifacts[{index}]")
        _exact_keys(item, {"path", "sha256", "size_bytes"}, set(), f"artifacts[{index}]")
        _relative_artifact(item["path"], f"artifacts[{index}].path")
        _sha256(item["sha256"], f"artifacts[{index}].sha256")
        _integer(item["size_bytes"], f"artifacts[{index}].size_bytes", 0)

    invariants = _object(document["capture_invariants"], "capture_invariants")
    invariant_fields = {
        "observation_in_memory", "next_model_request_not_started", "non_terminal",
        "remaining_step_available", "snapshot_synchronous", "verifier_not_run",
    }
    _exact_keys(invariants, invariant_fields, set(), "capture_invariants")
    if any(invariants[field] is not True for field in invariant_fields):
        raise CheckpointSchemaError("All capture invariants must be true")


def parse_checkpoint_manifest(
    raw: Mapping[str, Any],
) -> CheckpointManifest | CheckpointManifestV2:
    if "schema_version" not in raw:
        raise CheckpointSchemaError("Checkpoint manifest is missing schema_version")
    version = raw["schema_version"]
    if not isinstance(version, bool) and (version == 1 or version == "1"):
        return CheckpointManifest.from_dict(raw)
    if version == "2.0.0":
        return CheckpointManifestV2.from_dict(raw)
    raise CheckpointSchemaError(f"Unsupported checkpoint schema version: {version!r}")


__all__ = [
    "ArtifactRecordV2",
    "CheckpointManifest",
    "CheckpointManifestV2",
    "CheckpointSchemaError",
    "parse_checkpoint_manifest",
]
