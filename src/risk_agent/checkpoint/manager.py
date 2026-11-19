"""Atomic task snapshot/restore with semantic hashes and SQLite Backup API."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from risk_agent.benchmark.environment import TaskEnvironment, WorkspaceSafetyError
from risk_agent.benchmark.schema import TaskSpec, validate_identifier
from risk_agent.monitoring.runtime_state import RuntimeState

from .schema import CheckpointManifest


class CheckpointError(RuntimeError):
    pass


class CheckpointIntegrityError(CheckpointError):
    pass


class CheckpointStateMismatchError(CheckpointError):
    pass


@dataclass(frozen=True, slots=True)
class SnapshotResult:
    checkpoint_path: Path
    manifest: CheckpointManifest


@dataclass(frozen=True, slots=True)
class RestoreResult:
    checkpoint_path: Path
    manifest: CheckpointManifest
    runtime_state: RuntimeState
    restored_state_hash: str


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash_value(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def task_config_dict(task: TaskSpec) -> dict[str, object]:
    raw = asdict(task)
    raw["domain"] = task.domain.value
    return json.loads(_canonical_json(raw))


def normalized_state_document(
    environment: TaskEnvironment,
    runtime_state: RuntimeState,
    task: TaskSpec,
) -> dict[str, object]:
    return {
        "environment": environment.state_manifest(),
        "runtime": runtime_state.to_dict(),
        "task_config": task_config_dict(task),
    }


def normalized_state_hash(
    environment: TaskEnvironment,
    runtime_state: RuntimeState,
    task: TaskSpec,
) -> str:
    return _hash_value(normalized_state_document(environment, runtime_state, task))


def _database_state(database_path: Path) -> dict[str, list[dict[str, object]]]:
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise CheckpointIntegrityError("SQLite integrity_check failed")
        return {
            "inventory": [
                dict(row) for row in connection.execute("SELECT * FROM inventory ORDER BY sku")
            ],
            "orders": [
                dict(row)
                for row in connection.execute("SELECT * FROM orders ORDER BY order_id")
            ],
            "idempotency_keys": [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM idempotency_keys ORDER BY idempotency_key"
                )
            ],
        }


class CheckpointManager:
    def __init__(self, checkpoint_root: str | Path) -> None:
        self.checkpoint_root = Path(checkpoint_root).expanduser().resolve()
        self.checkpoint_root.mkdir(parents=True, exist_ok=True)

    def _checkpoint_path(self, checkpoint_id: str) -> Path:
        validate_identifier(checkpoint_id, "checkpoint_id")
        path = self.checkpoint_root / checkpoint_id
        if path.parent != self.checkpoint_root:
            raise CheckpointError("Checkpoint must be a direct child of checkpoint_root")
        return path

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @staticmethod
    def _backup_database(source_path: Path, destination_path: Path) -> None:
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(source_path) as source, sqlite3.connect(destination_path) as destination:
            source.backup(destination)
            integrity = destination.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise CheckpointIntegrityError("SQLite backup failed integrity_check")

    @staticmethod
    def _copy_environment_files(environment: TaskEnvironment, destination: Path) -> None:
        for source in sorted(environment.workspace.rglob("*")):
            if source.is_symlink():
                raise WorkspaceSafetyError("Checkpoint refuses symlinks in task workspace")
            if not source.is_file():
                continue
            relative = source.relative_to(environment.workspace)
            if relative.as_posix().startswith("state/api.sqlite3"):
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    @staticmethod
    def _artifact_checksums(root: Path) -> dict[str, str]:
        checksums: dict[str, str] = {}
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise CheckpointIntegrityError("Checkpoint payload must not contain symlinks")
            if path.is_file() and path.name != "manifest.json":
                checksums[path.relative_to(root).as_posix()] = _file_hash(path)
        return checksums

    @staticmethod
    def _environment_manifest_from_checkpoint(root: Path, task_id: str) -> dict[str, object]:
        files_root = root / "files"
        files = {
            path.relative_to(files_root).as_posix(): _file_hash(path)
            for path in sorted(files_root.rglob("*"))
            if path.is_file()
        }
        database = _database_state(root / "database" / "api.sqlite3")
        return {"task_id": task_id, "files": files, **database}

    def snapshot(
        self,
        *,
        checkpoint_id: str,
        environment: TaskEnvironment,
        runtime_state: RuntimeState,
    ) -> SnapshotResult:
        target = self._checkpoint_path(checkpoint_id)
        if target.exists():
            raise FileExistsError(f"Checkpoint already exists: {target}")
        if runtime_state.seed != environment.task.seed:
            raise CheckpointStateMismatchError("Runtime seed does not match task seed")
        staging = Path(
            tempfile.mkdtemp(prefix=f".{checkpoint_id}-", dir=self.checkpoint_root)
        )
        try:
            task_config = task_config_dict(environment.task)
            runtime_document = runtime_state.to_dict()
            source_environment = environment.state_manifest()
            mock_api_state = {
                "inventory": source_environment["inventory"],
                "orders": source_environment["orders"],
                "idempotency_keys": source_environment["idempotency_keys"],
            }
            self._copy_environment_files(environment, staging / "files")
            self._backup_database(
                environment.database_path, staging / "database" / "api.sqlite3"
            )
            self._write_json(staging / "runtime_state.json", runtime_document)
            self._write_json(staging / "task_config.json", task_config)
            self._write_json(staging / "mock_api_state.json", mock_api_state)
            captured_environment = self._environment_manifest_from_checkpoint(
                staging, environment.task.task_id
            )
            if captured_environment != source_environment:
                raise CheckpointStateMismatchError("Environment changed during checkpoint capture")
            state_hash = _hash_value(
                {
                    "environment": captured_environment,
                    "runtime": runtime_document,
                    "task_config": task_config,
                }
            )
            manifest = CheckpointManifest(
                schema_version=1,
                checkpoint_id=checkpoint_id,
                created_at=datetime.now(timezone.utc).isoformat(),
                task_id=environment.task.task_id,
                state_hash=state_hash,
                environment_hash=_hash_value(captured_environment),
                runtime_hash=_hash_value(runtime_document),
                task_config_hash=_hash_value(task_config),
                mock_api_state_hash=_hash_value(mock_api_state),
                artifact_checksums=self._artifact_checksums(staging),
            )
            self._write_json(staging / "manifest.json", manifest.to_dict())
            os.replace(staging, target)
            return SnapshotResult(checkpoint_path=target, manifest=manifest)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise

    def _load_and_verify(
        self, checkpoint_id: str, task: TaskSpec
    ) -> tuple[Path, CheckpointManifest, RuntimeState, dict[str, object]]:
        root = self._checkpoint_path(checkpoint_id)
        if root.is_symlink():
            raise CheckpointIntegrityError("Checkpoint root must not be a symlink")
        manifest_path = root / "manifest.json"
        if not manifest_path.is_file():
            raise CheckpointIntegrityError("Checkpoint manifest is missing")
        manifest = CheckpointManifest.from_dict(json.loads(manifest_path.read_text(encoding="utf-8")))
        if manifest.checkpoint_id != checkpoint_id or manifest.task_id != task.task_id:
            raise CheckpointIntegrityError("Checkpoint identity does not match restore target")
        actual_checksums = self._artifact_checksums(root)
        if actual_checksums != dict(manifest.artifact_checksums):
            raise CheckpointIntegrityError("Checkpoint artifact checksum mismatch")
        task_config = json.loads((root / "task_config.json").read_text(encoding="utf-8"))
        if task_config != task_config_dict(task) or _hash_value(task_config) != manifest.task_config_hash:
            raise CheckpointStateMismatchError("Task configuration does not match checkpoint")
        runtime_document = json.loads((root / "runtime_state.json").read_text(encoding="utf-8"))
        if _hash_value(runtime_document) != manifest.runtime_hash:
            raise CheckpointIntegrityError("Runtime state hash mismatch")
        runtime_state = RuntimeState.from_dict(runtime_document)
        if runtime_state.seed != task.seed:
            raise CheckpointStateMismatchError("Checkpoint seed does not match task")
        captured_environment = self._environment_manifest_from_checkpoint(root, task.task_id)
        if _hash_value(captured_environment) != manifest.environment_hash:
            raise CheckpointIntegrityError("Environment state hash mismatch")
        mock_api_state = json.loads((root / "mock_api_state.json").read_text(encoding="utf-8"))
        captured_api = {
            "inventory": captured_environment["inventory"],
            "orders": captured_environment["orders"],
            "idempotency_keys": captured_environment["idempotency_keys"],
        }
        if mock_api_state != captured_api or _hash_value(mock_api_state) != manifest.mock_api_state_hash:
            raise CheckpointIntegrityError("Mock API export does not match SQLite backup")
        captured_hash = _hash_value(
            {
                "environment": captured_environment,
                "runtime": runtime_document,
                "task_config": task_config,
            }
        )
        if captured_hash != manifest.state_hash:
            raise CheckpointIntegrityError("Combined checkpoint state hash mismatch")
        return root, manifest, runtime_state, captured_environment

    @staticmethod
    def _restore_database(source_path: Path, destination_path: Path) -> None:
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(source_path) as source, sqlite3.connect(destination_path) as destination:
            source.backup(destination)
            integrity = destination.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise CheckpointIntegrityError("Restored SQLite database failed integrity_check")

    def restore(
        self,
        *,
        checkpoint_id: str,
        environment: TaskEnvironment,
    ) -> RestoreResult:
        root, manifest, runtime_state, _ = self._load_and_verify(
            checkpoint_id, environment.task
        )
        staging = Path(
            tempfile.mkdtemp(prefix=f".{checkpoint_id}-restore-", dir=environment.base_directory)
        )
        old_workspace = environment.base_directory / (
            f".{environment.task.task_id}-pre-restore-{uuid.uuid4().hex}"
        )
        moved_old = False
        installed_new = False
        try:
            files_root = root / "files"
            for source in sorted(files_root.rglob("*")):
                if source.is_symlink():
                    raise CheckpointIntegrityError("Checkpoint contains a symlink")
                if source.is_file():
                    relative = source.relative_to(files_root)
                    target = staging / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
            self._restore_database(
                root / "database" / "api.sqlite3", staging / "state" / "api.sqlite3"
            )
            if environment.workspace.exists():
                if environment.workspace.is_symlink() or environment.workspace.parent != environment.base_directory:
                    raise WorkspaceSafetyError("Refusing to replace unsafe restore target")
                os.replace(environment.workspace, old_workspace)
                moved_old = True
            os.replace(staging, environment.workspace)
            installed_new = True
            restored_hash = normalized_state_hash(
                environment, runtime_state, environment.task
            )
            if restored_hash != manifest.state_hash:
                raise CheckpointStateMismatchError("Restored normalized state hash mismatch")
            if moved_old:
                shutil.rmtree(old_workspace)
            return RestoreResult(
                checkpoint_path=root,
                manifest=manifest,
                runtime_state=runtime_state,
                restored_state_hash=restored_hash,
            )
        except Exception:
            if installed_new and environment.workspace.exists():
                shutil.rmtree(environment.workspace)
            if moved_old and old_workspace.exists():
                os.replace(old_workspace, environment.workspace)
            if staging.exists():
                shutil.rmtree(staging)
            raise
