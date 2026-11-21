"""Complete Checkpoint v2 artifact capture, verification, and isolated restore."""

from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, TYPE_CHECKING

from risk_agent.agent import AgentStateAdapter, AgentStateSnapshot
from risk_agent.agent.agent_state_adapter import canonical_hash
from risk_agent.benchmark.environment import TaskEnvironment, WorkspaceSafetyError
from risk_agent.monitoring.runtime_state import RuntimeState

from .metadata import (
    CaptureInvariants,
    CheckpointIdentityV2,
    EnvironmentCheckpointMetadata,
    RuntimeCheckpointMetadata,
)
from .schema import ArtifactRecordV2, CheckpointManifestV2, parse_checkpoint_manifest

if TYPE_CHECKING:
    from .manager import CheckpointManager


CHECKPOINT_SCHEMA_VERSION = "2.0.0"


@dataclass(frozen=True, slots=True)
class SnapshotV2Result:
    checkpoint_path: Path
    manifest: CheckpointManifestV2


@dataclass(frozen=True, slots=True)
class VerifyV2Result:
    checkpoint_path: Path
    manifest: CheckpointManifestV2
    agent_state_snapshot: AgentStateSnapshot
    runtime_state: RuntimeState


@dataclass(frozen=True, slots=True)
class RestoreV2Result:
    checkpoint_path: Path
    manifest: CheckpointManifestV2
    agent_state_snapshot: AgentStateSnapshot
    runtime_state: RuntimeState
    restored_environment_hash: str
    restored_runtime_hash: str
    restored_agent_state_hash: str
    restored_next_model_input_hash: str
    restored_combined_state_hash: str


@dataclass(frozen=True, slots=True)
class _VerifiedPayload:
    result: VerifyV2Result
    environment_document: dict[str, Any]
    runtime_document: dict[str, Any]
    identity_config: dict[str, Any]


def _integrity(message: str) -> Exception:
    from .manager import CheckpointIntegrityError

    return CheckpointIntegrityError(message)


def _mismatch(message: str) -> Exception:
    from .manager import CheckpointStateMismatchError

    return CheckpointStateMismatchError(message)


def _file_hash(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Artifact is not finite JSON: {path.name}") from exc
    path.write_text(payload + "\n", encoding="utf-8")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _integrity(f"Invalid JSON artifact: {path.name}") from exc


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sqlite_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__sqlite_type__": "blob", "base64": base64.b64encode(value).decode("ascii")}
    if value is None or isinstance(value, (str, int, float)):
        return value
    raise _integrity(f"Unsupported SQLite value type: {type(value).__name__}")


def _sqlite_logical_dump(database_path: Path) -> dict[str, Any]:
    try:
        with sqlite3.connect(database_path) as connection:
            connection.row_factory = sqlite3.Row
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise _integrity("SQLite integrity_check failed")
            table_names = [
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            tables: list[dict[str, Any]] = []
            for table_name in table_names:
                quoted = _quote_identifier(table_name)
                columns = [
                    {
                        "cid": int(row[0]),
                        "name": str(row[1]),
                        "type": str(row[2]),
                        "not_null": bool(row[3]),
                        "default": row[4],
                        "primary_key_position": int(row[5]),
                    }
                    for row in connection.execute(f"PRAGMA table_info({quoted})")
                ]
                primary_key = [
                    item["name"]
                    for item in sorted(
                        (item for item in columns if item["primary_key_position"] > 0),
                        key=lambda item: item["primary_key_position"],
                    )
                ]
                stable_columns = primary_key or [item["name"] for item in columns]
                order_clause = ""
                if stable_columns:
                    order_clause = " ORDER BY " + ", ".join(
                        _quote_identifier(item) for item in stable_columns
                    )
                rows = [
                    {key: _sqlite_value(row[key]) for key in row.keys()}
                    for row in connection.execute(f"SELECT * FROM {quoted}{order_clause}")
                ]
                if not primary_key:
                    rows.sort(key=canonical_hash)
                tables.append(
                    {
                        "name": table_name,
                        "columns": columns,
                        "primary_key": primary_key,
                        "rows": rows,
                    }
                )
            return {"integrity_check": "ok", "tables": tables}
    except sqlite3.DatabaseError as exc:
        raise _integrity("SQLite database is corrupt or unreadable") from exc


def _mock_api_state(logical_dump: Mapping[str, Any]) -> dict[str, Any]:
    by_name = {table["name"]: table["rows"] for table in logical_dump["tables"]}
    required = {"inventory", "orders", "idempotency_keys"}
    if not required.issubset(by_name):
        raise _integrity("SQLite backup is missing required Mock API tables")
    return {name: by_name[name] for name in sorted(required)}


def _workspace_manifest(root: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise WorkspaceSafetyError("Checkpoint refuses workspace symlinks")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in {
            "state/api.sqlite3",
            "state/api.sqlite3-wal",
            "state/api.sqlite3-shm",
        }:
            continue
        files.append(
            {"path": relative, "sha256": _file_hash(path), "size_bytes": path.stat().st_size}
        )
    return {"files": files}


def _copy_workspace_files(source_root: Path, destination_root: Path) -> None:
    for source in sorted(source_root.rglob("*")):
        if source.is_symlink():
            raise WorkspaceSafetyError("Checkpoint refuses workspace symlinks")
        if not source.is_file():
            continue
        relative = source.relative_to(source_root)
        if relative.as_posix() in {
            "state/api.sqlite3",
            "state/api.sqlite3-wal",
            "state/api.sqlite3-shm",
        }:
            continue
        target = destination_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _backup_database(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as destination_db:
            source_db.backup(destination_db)
            result = destination_db.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                raise _integrity("SQLite backup failed integrity_check")
    except sqlite3.DatabaseError as exc:
        raise _integrity("SQLite backup failed") from exc


def _restore_database(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as destination_db:
            source_db.backup(destination_db)
            result = destination_db.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                raise _integrity("Restored SQLite database failed integrity_check")
    except sqlite3.DatabaseError as exc:
        raise _integrity("SQLite restore failed") from exc


def _artifact_records(root: Path) -> tuple[ArtifactRecordV2, ...]:
    records: list[ArtifactRecordV2] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise _integrity("Checkpoint payload must not contain symlinks")
        if path.is_file() and path.name != "manifest.json":
            records.append(
                ArtifactRecordV2(
                    path=path.relative_to(root).as_posix(),
                    sha256=_file_hash(path),
                    size_bytes=path.stat().st_size,
                )
            )
    return tuple(records)


def _artifact_map(manifest: CheckpointManifestV2) -> dict[str, ArtifactRecordV2]:
    result: dict[str, ArtifactRecordV2] = {}
    for item in manifest.artifacts:
        if item.path in result:
            raise _integrity(f"Duplicate artifact registration: {item.path}")
        result[item.path] = item
    return result


def _referenced_artifacts(value: Any) -> set[str]:
    references: set[str] = set()
    if isinstance(value, list):
        for item in value:
            references.update(_referenced_artifacts(item))
    elif isinstance(value, dict):
        for key, item in value.items():
            if key.endswith("_ref"):
                references.add(str(item))
            else:
                references.update(_referenced_artifacts(item))
    return references


def _generation_config(agent_snapshot: AgentStateSnapshot) -> dict[str, Any]:
    generation = dict(agent_snapshot.payload["model"]["generation"])
    if "chat_template_kwargs" not in generation:
        extra_body = generation.get("extra_body")
        if isinstance(extra_body, dict) and isinstance(extra_body.get("chat_template_kwargs"), dict):
            generation["chat_template_kwargs"] = extra_body["chat_template_kwargs"]
    required = {"seed", "temperature", "max_tokens", "chat_template_kwargs"}
    if not required.issubset(generation):
        raise ValueError(f"Agent generation config is missing {sorted(required - set(generation))}")
    return generation


def _extract_agent_payload(agent_snapshot: AgentStateSnapshot) -> dict[str, Any]:
    AgentStateAdapter.verify_state_hash(agent_snapshot)
    payload = agent_snapshot.payload
    agent = payload.get("agent")
    memory = payload.get("memory")
    if not isinstance(agent, dict) or not isinstance(memory, dict):
        raise ValueError("AgentStateSnapshot is missing agent or memory state")
    steps = memory.get("steps")
    messages = payload.get("canonical_next_model_input")
    tools = payload.get("tool_registry")
    if not isinstance(steps, list) or not isinstance(messages, list) or not isinstance(tools, list):
        raise ValueError("AgentStateSnapshot is missing messages, memory steps, or tools")
    actions = [item for item in steps if item.get("type") == "action"]
    if not actions:
        raise ValueError("Complete v2 checkpoint requires an ActionStep")
    latest = actions[-1]
    if latest.get("is_final_answer") is True:
        raise ValueError("Complete v2 checkpoint must be non-terminal")
    if latest.get("observations") is None and latest.get("error") is None:
        raise ValueError("Latest ActionStep is missing a Tool Observation or Agent-visible error")
    if not latest.get("tool_calls"):
        raise ValueError("Latest ActionStep is missing Tool Calls")
    step_number = agent.get("step_number")
    max_steps = agent.get("max_steps")
    remaining_steps = agent.get("remaining_steps")
    counters = (step_number, max_steps, remaining_steps)
    if not all(isinstance(item, int) and not isinstance(item, bool) for item in counters):
        raise ValueError("Agent step counters are invalid")
    if step_number < 1 or max_steps < 2 or remaining_steps < 1:
        raise ValueError("Complete v2 checkpoint requires a remaining Agent step")
    if remaining_steps != max(0, max_steps - step_number):
        raise ValueError("Agent remaining step budget is inconsistent")
    if int(latest.get("step_number", -1)) != step_number:
        raise ValueError("Latest ActionStep does not match Agent step_number")
    if not isinstance(agent.get("task"), str) or not agent["task"]:
        raise ValueError("Agent task prompt is missing")
    if not tools:
        raise ValueError("Complete v2 checkpoint requires a non-empty Tool Registry")
    model_id = payload.get("model", {}).get("model_id")
    if not isinstance(model_id, str) or not model_id:
        raise ValueError("Agent model_id is missing")
    if str(agent.get("class", "")).rsplit(".", 1)[-1] not in {
        "ToolCallingAgent",
        "CheckpointableToolCallingAgent",
    }:
        raise ValueError("Frozen Checkpoint v2 requires ToolCallingAgent state")
    latest_assistant_calls = (latest.get("model_output_message") or {}).get("tool_calls")
    if not latest_assistant_calls:
        raise ValueError("Latest ActionStep is missing Assistant Tool Calls")
    executed_by_id = {item["id"]: item for item in latest["tool_calls"]}
    assistant_by_id = {item["id"]: item for item in latest_assistant_calls}
    if set(executed_by_id) != set(assistant_by_id):
        raise ValueError("Assistant and executed Tool Call IDs differ")
    for call_id, executed in executed_by_id.items():
        function = assistant_by_id[call_id]["function"]
        if executed["name"] != function["name"] or executed["arguments"] != function["arguments"]:
            raise ValueError(f"Assistant and executed Tool Call differ: {call_id}")
    tool_calls = [
        {
            "step_number": item["step_number"],
            "executed_tool_calls": item.get("tool_calls"),
            "assistant_tool_calls": (item.get("model_output_message") or {}).get("tool_calls"),
        }
        for item in actions
        if item.get("tool_calls")
    ]
    observations = [
        {
            "step_number": item["step_number"],
            "call_ids": sorted(call["id"] for call in item.get("tool_calls") or []),
            "observation": item.get("observations"),
        }
        for item in actions
        if item.get("observations") is not None
    ]
    observations.extend(
        {
            "step_number": item["step_number"],
            "call_ids": sorted(call["id"] for call in item.get("tool_calls") or []),
            "observation": None,
            "error": item["error"],
        }
        for item in actions
        if item.get("observations") is None and item.get("error") is not None
    )
    return {
        "agent": agent,
        "memory": memory,
        "steps": steps,
        "messages": messages,
        "tools": tools,
        "latest_action": latest,
        "tool_calls": tool_calls,
        "observations": observations,
        "generation": _generation_config(agent_snapshot),
        "model_id": model_id,
    }


def _identity_config(
    *,
    identity: Mapping[str, Any],
    agent_snapshot: AgentStateSnapshot,
    identity_metadata: CheckpointIdentityV2,
    tool_schema_hash: str,
) -> dict[str, Any]:
    prompt_content = {
        "system_prompt": agent_snapshot.payload["memory"]["system_prompt"],
        "prompt_templates": agent_snapshot.payload["prompt_templates"],
    }
    return {
        "model_id": identity["model_id"],
        "model_version": identity["model_version"],
        "worker_id": identity["worker_id"],
        "prompt_version": identity["prompt_version"],
        "prompt_content_hash": canonical_hash(prompt_content),
        "tool_version": identity["tool_version"],
        "tool_schema_hash": tool_schema_hash,
        "verifier_version": identity["verifier_version"],
        "verifier_content_hash": identity_metadata.verifier_content_hash,
        "catalog_hash": identity_metadata.catalog_hash,
        "split_manifest_hash": identity_metadata.split_manifest_hash,
        "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
    }


def _combined_hash(hashes: Mapping[str, str]) -> str:
    return canonical_hash(
        {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "agent_state_hash": hashes["agent_state_hash"],
            "next_model_input_hash": hashes["next_model_input_hash"],
            "environment_state_hash": hashes["environment_state_hash"],
            "runtime_state_hash": hashes["runtime_state_hash"],
            "identity_config_hash": hashes["identity_config_hash"],
        }
    )


def _environment_document(
    *,
    workspace_manifest: Any,
    sqlite_logical_dump: Any,
    sqlite_backup_hash: str,
    mock_api_state: Any,
    side_effects: Any,
    pending_operation: Any,
    latest_completed_operation: Any,
) -> dict[str, Any]:
    return {
        "workspace": workspace_manifest,
        "sqlite_logical_dump": sqlite_logical_dump,
        "sqlite_backup_sha256": sqlite_backup_hash,
        "mock_api_state": mock_api_state,
        "side_effects": side_effects,
        "pending_operation": pending_operation,
        "latest_completed_operation": latest_completed_operation,
    }


def _runtime_hash_document(
    *,
    runtime_state: Any,
    tool_history: Any,
    tool_adapter_state: Any,
    fault_adapter_state: Any,
    budget_state: Any,
    resource_usage: Any,
    execution_position: Any,
) -> dict[str, Any]:
    return {
        "runtime_state": runtime_state,
        "tool_history": tool_history,
        "tool_adapter_state": tool_adapter_state,
        "fault_adapter_state": fault_adapter_state,
        "budget_state": budget_state,
        "resource_usage": resource_usage,
        "execution_position": execution_position,
    }


def _continue_budget_error(
    *,
    runtime_state: RuntimeState,
    max_model_calls: int,
    model_calls_used: int,
    prompt_tokens_used: int | None,
    completion_tokens_used: int | None,
    agent_state: Mapping[str, Any],
    next_step_number: int,
) -> str | None:
    """Return a semantic inconsistency that would prevent the declared next generation."""

    if model_calls_used >= max_model_calls:
        return "No model call budget remains for next model_generation"
    budget = runtime_state.budget
    if budget.tokens_used >= budget.max_tokens:
        return "No cumulative Token budget remains for next model_generation"
    if budget.latency_seconds_used >= budget.max_latency_seconds:
        return "No latency budget remains for next model_generation"
    if budget.tool_calls_used > budget.max_tool_calls:
        return "Tool Call usage exceeds the configured limit"
    if (
        prompt_tokens_used is not None
        and completion_tokens_used is not None
        and prompt_tokens_used + completion_tokens_used != budget.tokens_used
    ):
        return "Observed Prompt/Completion Tokens do not match cumulative Token usage"
    step_number = agent_state.get("step_number")
    max_steps = agent_state.get("max_steps")
    remaining_steps = agent_state.get("remaining_steps")
    if not all(
        isinstance(value, int) and not isinstance(value, bool)
        for value in (step_number, max_steps, remaining_steps, next_step_number)
    ):
        return "Agent step budget contains invalid counters"
    if (
        remaining_steps <= 0
        or remaining_steps != max_steps - step_number
        or next_step_number != step_number + 1
        or next_step_number > max_steps
    ):
        return "No Agent step budget remains for next model_generation"
    return None


def snapshot_v2(
    manager: CheckpointManager,
    *,
    checkpoint_id: str,
    environment: TaskEnvironment,
    runtime_state: RuntimeState,
    agent_state_snapshot: AgentStateSnapshot,
    identity: CheckpointIdentityV2,
    runtime_metadata: RuntimeCheckpointMetadata,
    environment_metadata: EnvironmentCheckpointMetadata,
    capture_invariants: CaptureInvariants,
) -> SnapshotV2Result:
    target = manager._checkpoint_path(checkpoint_id)
    if target.exists():
        raise FileExistsError(f"Checkpoint already exists: {target}")
    if environment.task.split is None:
        raise _mismatch("Complete v2 checkpoint requires a real TaskSpec split")
    if runtime_state.seed != environment.task.seed:
        raise _mismatch("Runtime seed does not match task seed")
    if (
        runtime_state.budget.max_tokens != 50_000
        or runtime_state.budget.max_tool_calls != 12
        or runtime_state.budget.max_latency_seconds != 900
        or runtime_metadata.max_model_calls != 10
    ):
        raise _mismatch("Runtime limits do not match frozen Protocol v2 budgets")
    extracted = _extract_agent_payload(agent_state_snapshot)
    agent = extracted["agent"]
    if agent["task"] != environment.task.prompt:
        raise _mismatch("Agent task prompt does not match TaskSpec prompt")
    next_step_number = int(agent["step_number"]) + 1
    budget_error = _continue_budget_error(
        runtime_state=runtime_state,
        max_model_calls=runtime_metadata.max_model_calls,
        model_calls_used=runtime_metadata.model_calls_used,
        prompt_tokens_used=runtime_metadata.prompt_tokens_used,
        completion_tokens_used=runtime_metadata.completion_tokens_used,
        agent_state=agent,
        next_step_number=next_step_number,
    )
    if budget_error is not None:
        raise _mismatch(budget_error)
    pending_operation = {"kind": "model_generation", "next_step_number": next_step_number}
    staging = Path(tempfile.mkdtemp(prefix=f".{checkpoint_id}-v2-", dir=manager.checkpoint_root))
    try:
        agent_paths = {
            "state": "agent/agent_state.json",
            "messages": "agent/messages.json",
            "steps": "agent/memory_steps.json",
            "latest": "agent/latest_action_step.json",
            "system": "agent/system_prompt.json",
            "task": "agent/task_prompt.json",
            "calls": "agent/tool_calls.json",
            "observations": "agent/tool_observations.json",
            "tools": "agent/tool_registry.json",
            "next_input": "agent/next_model_input.json",
        }
        _write_json(staging / agent_paths["state"], agent_state_snapshot.to_dict())
        _write_json(staging / agent_paths["messages"], extracted["messages"])
        _write_json(staging / agent_paths["steps"], extracted["steps"])
        _write_json(staging / agent_paths["latest"], extracted["latest_action"])
        _write_json(
            staging / agent_paths["system"],
            {"system_prompt": extracted["memory"]["system_prompt"]},
        )
        _write_json(staging / agent_paths["task"], {"task_prompt": agent["task"]})
        _write_json(staging / agent_paths["calls"], extracted["tool_calls"])
        _write_json(staging / agent_paths["observations"], extracted["observations"])
        _write_json(staging / agent_paths["tools"], extracted["tools"])
        _write_json(staging / agent_paths["next_input"], extracted["messages"])

        source_workspace_before = _workspace_manifest(environment.workspace)
        files_root = staging / "environment" / "files"
        _copy_workspace_files(environment.workspace, files_root)
        workspace_manifest = _workspace_manifest(files_root)
        if source_workspace_before != workspace_manifest:
            raise _mismatch("Workspace changed during checkpoint capture")
        sqlite_backup_path = staging / "environment" / "sqlite_backup.sqlite3"
        _backup_database(environment.database_path, sqlite_backup_path)
        sqlite_dump = _sqlite_logical_dump(sqlite_backup_path)
        if _sqlite_logical_dump(environment.database_path) != sqlite_dump:
            raise _mismatch("SQLite state changed during checkpoint capture")
        if _workspace_manifest(environment.workspace) != source_workspace_before:
            raise _mismatch("Workspace changed during checkpoint capture")
        mock_api = _mock_api_state(sqlite_dump)
        side_effects = environment_metadata.side_effects_dict()
        latest_operation = environment_metadata.latest_operation_value()
        environment_paths = {
            "workspace": "environment/workspace_manifest.json",
            "sqlite": "environment/sqlite_backup.sqlite3",
            "logical": "environment/sqlite_logical_dump.json",
            "mock": "environment/mock_api_state.json",
            "files": "environment/file_state.json",
            "database": "environment/database_state.json",
            "side_effects": "environment/side_effects.json",
            "pending": "environment/pending_operation.json",
        }
        _write_json(staging / environment_paths["workspace"], workspace_manifest)
        _write_json(staging / environment_paths["logical"], sqlite_dump)
        _write_json(staging / environment_paths["mock"], mock_api)
        _write_json(staging / environment_paths["files"], workspace_manifest)
        _write_json(staging / environment_paths["database"], sqlite_dump)
        _write_json(staging / environment_paths["side_effects"], side_effects)
        _write_json(staging / environment_paths["pending"], pending_operation)
        latest_operation_ref: str | None = None
        if latest_operation is not None:
            latest_operation_ref = "environment/latest_completed_operation.json"
            _write_json(staging / latest_operation_ref, latest_operation)

        runtime_document = runtime_state.to_dict()
        tool_history = runtime_document["tool_history"]
        tool_adapter = runtime_metadata.tool_adapter_state.to_dict()
        fault_adapter = runtime_metadata.fault_adapter_state.to_dict()
        budget_state = {
            "max_tokens": runtime_state.budget.max_tokens,
            "max_tool_calls": runtime_state.budget.max_tool_calls,
            "max_model_calls": runtime_metadata.max_model_calls,
            "max_latency_seconds": runtime_state.budget.max_latency_seconds,
            "prompt_tokens_used": runtime_metadata.prompt_tokens_used,
            "completion_tokens_used": runtime_metadata.completion_tokens_used,
            "tool_calls_used": runtime_state.budget.tool_calls_used,
            "model_calls_used": runtime_metadata.model_calls_used,
            "latency_seconds_used": runtime_state.budget.latency_seconds_used,
        }
        resource_usage = runtime_metadata.resource_usage_dict()
        execution_position = {
            "phase": runtime_metadata.phase,
            "next_operation": runtime_metadata.next_operation,
        }
        runtime_paths = {
            "runtime": "runtime/runtime_state.json",
            "history": "runtime/tool_history.json",
            "tool": "runtime/tool_adapter_state.json",
            "fault": "runtime/fault_adapter_state.json",
            "budget": "runtime/budget_state.json",
            "resources": "runtime/resource_usage.json",
            "position": "runtime/execution_position.json",
        }
        _write_json(staging / runtime_paths["runtime"], runtime_document)
        _write_json(staging / runtime_paths["history"], tool_history)
        _write_json(staging / runtime_paths["tool"], tool_adapter)
        _write_json(staging / runtime_paths["fault"], fault_adapter)
        _write_json(staging / runtime_paths["budget"], budget_state)
        _write_json(staging / runtime_paths["resources"], resource_usage)
        _write_json(staging / runtime_paths["position"], execution_position)

        task = environment.task
        identity_document = {
            "experiment_id": identity.experiment_id,
            "run_id": identity.run_id,
            "task_run_id": identity.task_run_id,
            "template_id": task.template_id,
            "task_id": task.task_id,
            "instance_id": task.instance_id,
            "split": task.split.value,
            "checkpoint_id": checkpoint_id,
            "step_index": agent["step_number"],
            "model_id": extracted["model_id"],
            "model_version": identity.model_version,
            "worker_id": identity.worker_id,
            "prompt_version": identity.prompt_version,
            "tool_version": identity.tool_version,
            "verifier_version": identity.verifier_version,
            "controller_type": identity.controller_type,
            "execution_mode": identity.execution_mode,
            "created_at": identity.created_at,
            "catalog_hash": identity.catalog_hash,
            "split_manifest_hash": identity.split_manifest_hash,
        }
        tool_schema_hash = canonical_hash(extracted["tools"])
        identity_config = _identity_config(
            identity=identity_document,
            agent_snapshot=agent_state_snapshot,
            identity_metadata=identity,
            tool_schema_hash=tool_schema_hash,
        )
        identity_path = "identity/identity_config.json"
        _write_json(staging / identity_path, identity_config)

        environment_document = _environment_document(
            workspace_manifest=workspace_manifest,
            sqlite_logical_dump=sqlite_dump,
            sqlite_backup_hash=_file_hash(sqlite_backup_path),
            mock_api_state=mock_api,
            side_effects=side_effects,
            pending_operation=pending_operation,
            latest_completed_operation=latest_operation,
        )
        runtime_hash_document = _runtime_hash_document(
            runtime_state=runtime_document,
            tool_history=tool_history,
            tool_adapter_state=tool_adapter,
            fault_adapter_state=fault_adapter,
            budget_state=budget_state,
            resource_usage=resource_usage,
            execution_position=execution_position,
        )
        hashes = {
            "agent_state_hash": agent_state_snapshot.agent_state_hash,
            "next_model_input_hash": agent_state_snapshot.payload["next_model_input_hash"],
            "environment_state_hash": canonical_hash(environment_document),
            "runtime_state_hash": canonical_hash(runtime_hash_document),
            "identity_config_hash": canonical_hash(identity_config),
        }
        hashes["combined_state_hash"] = _combined_hash(hashes)
        agent_context = {
            "framework": agent_state_snapshot.payload["framework"]["name"],
            "framework_version": agent_state_snapshot.payload["framework"]["version"],
            "agent_class": "ToolCallingAgent",
            "messages_ref": agent_paths["messages"],
            "memory_steps_ref": agent_paths["steps"],
            "latest_action_step_ref": agent_paths["latest"],
            "system_prompt_ref": agent_paths["system"],
            "task_prompt_ref": agent_paths["task"],
            "assistant_tool_calls_ref": agent_paths["calls"],
            "tool_observations_ref": agent_paths["observations"],
            "step_number": agent["step_number"],
            "next_step_number": next_step_number,
            "max_steps": agent["max_steps"],
            "remaining_steps": agent["remaining_steps"],
            "tool_registry": {
                "version": identity.tool_version,
                "schema_ref": agent_paths["tools"],
                "schema_hash": tool_schema_hash,
                "ordered_tool_names": [item["name"] for item in extracted["tools"]],
            },
            "generation_config": extracted["generation"],
            "agent_state": agent["state"],
            "next_model_input_ref": agent_paths["next_input"],
            "planning_interval": agent["planning_interval"],
            "stream_outputs": agent["stream_outputs"],
            "capture_granularity": "action_step_observation_batch",
        }
        environment_state = {
            "workspace_manifest_ref": environment_paths["workspace"],
            "sqlite_backup_ref": environment_paths["sqlite"],
            "sqlite_logical_dump_ref": environment_paths["logical"],
            "mock_api_state_ref": environment_paths["mock"],
            "file_state_ref": environment_paths["files"],
            "database_state_ref": environment_paths["database"],
            "side_effects_ref": environment_paths["side_effects"],
            "pending_operation": pending_operation,
        }
        if latest_operation_ref is not None:
            environment_state["latest_completed_operation_ref"] = latest_operation_ref
        manifest_runtime = {
            "runtime_ref": runtime_paths["runtime"],
            "tool_history_ref": runtime_paths["history"],
            "tool_adapter_state_ref": runtime_paths["tool"],
            "fault_adapter_state_ref": runtime_paths["fault"],
            "budget_state": budget_state,
            "resource_usage": resource_usage,
            "execution_position": execution_position,
        }
        artifacts = _artifact_records(staging)
        raw_manifest = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "identity": identity_document,
            "agent_context": agent_context,
            "environment_state": environment_state,
            "runtime_state": manifest_runtime,
            "hashes": hashes,
            "artifacts": [item.to_dict() for item in artifacts],
            "capture_invariants": capture_invariants.to_dict(),
        }
        manifest = CheckpointManifestV2.from_dict(raw_manifest)
        registered = set(_artifact_map(manifest))
        missing_refs = _referenced_artifacts(raw_manifest) - registered
        if missing_refs:
            raise _integrity(f"Manifest references unregistered artifacts: {sorted(missing_refs)}")
        _write_json(staging / "manifest.json", manifest.to_dict())
        os.replace(staging, target)
        return SnapshotV2Result(checkpoint_path=target, manifest=manifest)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def _verify_artifacts(root: Path, manifest: CheckpointManifestV2) -> dict[str, ArtifactRecordV2]:
    if root.is_symlink():
        raise _integrity("Checkpoint root must not be a symlink")
    expected = _artifact_map(manifest)
    actual_files: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise _integrity("Checkpoint contains a symlink")
        if path.is_file() and path.name != "manifest.json":
            actual_files[path.relative_to(root).as_posix()] = path
    if set(actual_files) != set(expected):
        missing = sorted(set(expected) - set(actual_files))
        unregistered = sorted(set(actual_files) - set(expected))
        raise _integrity(
            f"Checkpoint artifact set mismatch; missing={missing}, unregistered={unregistered}"
        )
    for relative, record in expected.items():
        path = actual_files[relative]
        if path.stat().st_size != record.size_bytes or _file_hash(path) != record.sha256:
            raise _integrity(f"Checkpoint artifact checksum mismatch: {relative}")
    references = _referenced_artifacts(manifest.to_dict())
    if not references.issubset(expected):
        raise _integrity(f"Manifest contains unresolved artifact references: {sorted(references - set(expected))}")
    return expected


def _expected_identity_config(
    manifest: CheckpointManifestV2,
    agent_snapshot: AgentStateSnapshot,
    stored: Mapping[str, Any],
) -> dict[str, Any]:
    identity = manifest.identity
    prompt_content = {
        "system_prompt": agent_snapshot.payload["memory"]["system_prompt"],
        "prompt_templates": agent_snapshot.payload["prompt_templates"],
    }
    return {
        "model_id": identity["model_id"],
        "model_version": identity["model_version"],
        "worker_id": identity["worker_id"],
        "prompt_version": identity["prompt_version"],
        "prompt_content_hash": canonical_hash(prompt_content),
        "tool_version": identity["tool_version"],
        "tool_schema_hash": manifest.agent_context["tool_registry"]["schema_hash"],
        "verifier_version": identity["verifier_version"],
        "verifier_content_hash": stored.get("verifier_content_hash"),
        "catalog_hash": identity.get("catalog_hash"),
        "split_manifest_hash": identity.get("split_manifest_hash"),
        "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
    }


def _verify_v2_payload(
    manager: CheckpointManager,
    *,
    checkpoint_id: str,
    task_environment: TaskEnvironment,
) -> _VerifiedPayload:
    root = manager._checkpoint_path(checkpoint_id)
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise _integrity("Checkpoint manifest is missing or unsafe")
    try:
        parsed = parse_checkpoint_manifest(_read_json(manifest_path))
    except ValueError as exc:
        raise _integrity("Checkpoint v2 manifest validation failed") from exc
    if not isinstance(parsed, CheckpointManifestV2):
        raise _integrity("Checkpoint is not a complete v2 checkpoint")
    manifest = parsed
    task = task_environment.task
    if (
        manifest.identity["checkpoint_id"] != checkpoint_id
        or manifest.identity["task_id"] != task.task_id
        or manifest.identity["template_id"] != task.template_id
        or manifest.identity["instance_id"] != task.instance_id
        or task.split is None
        or manifest.identity["split"] != task.split.value
    ):
        raise _mismatch("Checkpoint identity does not match restore target")
    artifact_map = _verify_artifacts(root, manifest)

    def artifact(ref: str) -> Any:
        if ref not in artifact_map:
            raise _integrity(f"Unregistered artifact reference: {ref}")
        return _read_json(root / ref)

    agent_context = manifest.agent_context
    agent_snapshot = AgentStateSnapshot.from_dict(artifact("agent/agent_state.json"))
    extracted = _extract_agent_payload(agent_snapshot)
    if (
        manifest.identity["model_id"] != extracted["model_id"]
        or manifest.identity["step_index"] != extracted["agent"]["step_number"]
        or extracted["agent"]["task"] != task.prompt
    ):
        raise _integrity("Agent identity, step, or Task prompt differs from Manifest")
    if manifest.hashes["agent_state_hash"] != agent_snapshot.agent_state_hash:
        raise _integrity("Agent state hash mismatch")
    if manifest.hashes["next_model_input_hash"] != agent_snapshot.payload["next_model_input_hash"]:
        raise _integrity("Next model input hash mismatch")
    agent_expectations = {
        agent_context["messages_ref"]: extracted["messages"],
        agent_context["memory_steps_ref"]: extracted["steps"],
        agent_context["latest_action_step_ref"]: extracted["latest_action"],
        agent_context["system_prompt_ref"]: {"system_prompt": extracted["memory"]["system_prompt"]},
        agent_context["task_prompt_ref"]: {"task_prompt": extracted["agent"]["task"]},
        agent_context["assistant_tool_calls_ref"]: extracted["tool_calls"],
        agent_context["tool_observations_ref"]: extracted["observations"],
        agent_context["tool_registry"]["schema_ref"]: extracted["tools"],
        agent_context["next_model_input_ref"]: extracted["messages"],
    }
    for ref, expected in agent_expectations.items():
        if artifact(ref) != expected:
            raise _integrity(f"Agent artifact does not match AgentStateSnapshot: {ref}")
    if not extracted["observations"]:
        raise _integrity("Checkpoint has no Tool Observation")
    if canonical_hash(extracted["messages"]) != manifest.hashes["next_model_input_hash"]:
        raise _integrity("Canonical next model input content mismatch")
    if canonical_hash(extracted["tools"]) != agent_context["tool_registry"]["schema_hash"]:
        raise _integrity("Tool Registry schema hash mismatch")
    if agent_context["generation_config"] != extracted["generation"]:
        raise _integrity("Generation config differs from AgentStateSnapshot")
    agent = extracted["agent"]
    if (
        agent_context["step_number"] != agent["step_number"]
        or agent_context["next_step_number"] != agent["step_number"] + 1
        or agent_context["max_steps"] != agent["max_steps"]
        or agent_context["remaining_steps"] != agent["remaining_steps"]
        or agent_context["agent_state"] != agent["state"]
    ):
        raise _integrity("Agent context counters or state are inconsistent")

    environment_state = manifest.environment_state
    workspace_manifest = artifact(environment_state["workspace_manifest_ref"])
    if artifact(environment_state["file_state_ref"]) != workspace_manifest:
        raise _integrity("Workspace file-state artifacts disagree")
    sqlite_path = root / environment_state["sqlite_backup_ref"]
    sqlite_dump = artifact(environment_state["sqlite_logical_dump_ref"])
    if artifact(environment_state["database_state_ref"]) != sqlite_dump:
        raise _integrity("SQLite logical-state artifacts disagree")
    actual_dump = _sqlite_logical_dump(sqlite_path)
    if actual_dump != sqlite_dump:
        raise _integrity("SQLite backup differs from logical dump")
    mock_api = artifact(environment_state["mock_api_state_ref"])
    if mock_api != _mock_api_state(sqlite_dump):
        raise _integrity("Mock API state differs from SQLite logical dump")
    side_effects = artifact(environment_state["side_effects_ref"])
    pending = artifact("environment/pending_operation.json")
    if pending != environment_state["pending_operation"]:
        raise _integrity("Pending operation artifact differs from Manifest")
    latest_ref = environment_state.get("latest_completed_operation_ref")
    latest = artifact(latest_ref) if latest_ref is not None else None
    stored_files_root = root / "environment" / "files"
    if _workspace_manifest(stored_files_root) != workspace_manifest:
        raise _integrity("Workspace payload differs from workspace manifest")
    environment_document = _environment_document(
        workspace_manifest=workspace_manifest,
        sqlite_logical_dump=sqlite_dump,
        sqlite_backup_hash=_file_hash(sqlite_path),
        mock_api_state=mock_api,
        side_effects=side_effects,
        pending_operation=pending,
        latest_completed_operation=latest,
    )
    if canonical_hash(environment_document) != manifest.hashes["environment_state_hash"]:
        raise _integrity("Environment state hash mismatch")

    runtime_manifest = manifest.runtime_state
    runtime_document = artifact(runtime_manifest["runtime_ref"])
    runtime_state = RuntimeState.from_dict(runtime_document)
    if runtime_state.seed != task.seed:
        raise _mismatch("Checkpoint Runtime seed does not match TaskSpec")
    tool_history = artifact(runtime_manifest["tool_history_ref"])
    tool_adapter = artifact(runtime_manifest["tool_adapter_state_ref"])
    fault_adapter = artifact(runtime_manifest["fault_adapter_state_ref"])
    budget = artifact("runtime/budget_state.json")
    resources = artifact("runtime/resource_usage.json")
    position = artifact("runtime/execution_position.json")
    if tool_history != runtime_document["tool_history"]:
        raise _integrity("Tool history artifact differs from RuntimeState")
    if budget != runtime_manifest["budget_state"] or resources != runtime_manifest["resource_usage"]:
        raise _integrity("Runtime budget or resource artifact differs from Manifest")
    if position != runtime_manifest["execution_position"]:
        raise _integrity("Execution position artifact differs from Manifest")
    if (
        budget["max_tokens"] != runtime_state.budget.max_tokens
        or budget["max_tool_calls"] != runtime_state.budget.max_tool_calls
        or budget["max_latency_seconds"] != runtime_state.budget.max_latency_seconds
        or budget["tool_calls_used"] != runtime_state.budget.tool_calls_used
        or budget["latency_seconds_used"] != runtime_state.budget.latency_seconds_used
    ):
        raise _integrity("v2 budget state differs from RuntimeState")
    budget_error = _continue_budget_error(
        runtime_state=runtime_state,
        max_model_calls=budget["max_model_calls"],
        model_calls_used=budget["model_calls_used"],
        prompt_tokens_used=budget["prompt_tokens_used"],
        completion_tokens_used=budget["completion_tokens_used"],
        agent_state=agent,
        next_step_number=agent_context["next_step_number"],
    )
    if budget_error is not None:
        raise _integrity(budget_error)
    runtime_hash_document = _runtime_hash_document(
        runtime_state=runtime_document,
        tool_history=tool_history,
        tool_adapter_state=tool_adapter,
        fault_adapter_state=fault_adapter,
        budget_state=budget,
        resource_usage=resources,
        execution_position=position,
    )
    if canonical_hash(runtime_hash_document) != manifest.hashes["runtime_state_hash"]:
        raise _integrity("Runtime state hash mismatch")

    identity_config = artifact("identity/identity_config.json")
    if identity_config != _expected_identity_config(manifest, agent_snapshot, identity_config):
        raise _integrity("Identity config fields are inconsistent")
    if canonical_hash(identity_config) != manifest.hashes["identity_config_hash"]:
        raise _integrity("Identity config hash mismatch")
    if _combined_hash(manifest.hashes) != manifest.hashes["combined_state_hash"]:
        raise _integrity("Combined state hash mismatch")
    return _VerifiedPayload(
        result=VerifyV2Result(
            checkpoint_path=root,
            manifest=manifest,
            agent_state_snapshot=agent_snapshot,
            runtime_state=runtime_state,
        ),
        environment_document=environment_document,
        runtime_document=runtime_hash_document,
        identity_config=identity_config,
    )


def verify_v2(
    manager: CheckpointManager,
    *,
    checkpoint_id: str,
    environment: TaskEnvironment,
) -> VerifyV2Result:
    return _verify_v2_payload(
        manager, checkpoint_id=checkpoint_id, task_environment=environment
    ).result


def restore_v2(
    manager: CheckpointManager,
    *,
    checkpoint_id: str,
    environment: TaskEnvironment,
) -> RestoreV2Result:
    verified = _verify_v2_payload(
        manager, checkpoint_id=checkpoint_id, task_environment=environment
    )
    result = verified.result
    root = result.checkpoint_path
    manifest = result.manifest
    staging = Path(
        tempfile.mkdtemp(prefix=f".{checkpoint_id}-v2-restore-", dir=environment.base_directory)
    )
    old_workspace = environment.base_directory / (
        f".{environment.task.task_id}-pre-v2-restore-{uuid.uuid4().hex}"
    )
    moved_old = False
    installed_new = False
    try:
        files_root = root / "environment" / "files"
        _copy_workspace_files(files_root, staging)
        sqlite_ref = manifest.environment_state["sqlite_backup_ref"]
        _restore_database(root / sqlite_ref, staging / "state" / "api.sqlite3")
        staged_workspace = _workspace_manifest(staging)
        staged_dump = _sqlite_logical_dump(staging / "state" / "api.sqlite3")
        expected_workspace = verified.environment_document["workspace"]
        expected_dump = verified.environment_document["sqlite_logical_dump"]
        if staged_workspace != expected_workspace or staged_dump != expected_dump:
            raise _mismatch("Restored staging environment differs from checkpoint")
        restored_environment_document = dict(verified.environment_document)
        restored_environment_document["workspace"] = staged_workspace
        restored_environment_document["sqlite_logical_dump"] = staged_dump
        restored_environment_hash = canonical_hash(restored_environment_document)
        if restored_environment_hash != manifest.hashes["environment_state_hash"]:
            raise _mismatch("Restored environment hash mismatch")

        if environment.workspace.exists():
            if environment.workspace.is_symlink() or environment.workspace.parent != environment.base_directory:
                raise WorkspaceSafetyError("Refusing to replace unsafe restore target")
            os.replace(environment.workspace, old_workspace)
            moved_old = True
        os.replace(staging, environment.workspace)
        installed_new = True
        if _workspace_manifest(environment.workspace) != expected_workspace:
            raise _mismatch("Installed workspace differs from checkpoint")
        if _sqlite_logical_dump(environment.database_path) != expected_dump:
            raise _mismatch("Installed SQLite state differs from checkpoint")
        if moved_old:
            shutil.rmtree(old_workspace)
        return RestoreV2Result(
            checkpoint_path=root,
            manifest=manifest,
            agent_state_snapshot=result.agent_state_snapshot,
            runtime_state=result.runtime_state,
            restored_environment_hash=restored_environment_hash,
            restored_runtime_hash=canonical_hash(verified.runtime_document),
            restored_agent_state_hash=result.agent_state_snapshot.agent_state_hash,
            restored_next_model_input_hash=result.agent_state_snapshot.payload["next_model_input_hash"],
            restored_combined_state_hash=_combined_hash(manifest.hashes),
        )
    except Exception:
        if installed_new and environment.workspace.exists():
            shutil.rmtree(environment.workspace)
        if moved_old and old_workspace.exists():
            os.replace(old_workspace, environment.workspace)
        if staging.exists():
            shutil.rmtree(staging)
        raise


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "RestoreV2Result",
    "SnapshotV2Result",
    "VerifyV2Result",
    "restore_v2",
    "snapshot_v2",
    "verify_v2",
]
