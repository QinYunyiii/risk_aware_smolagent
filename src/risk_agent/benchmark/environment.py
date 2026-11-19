"""Isolated local task workspace with deterministic reset and state hashing."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .schema import TaskSpec, validate_relative_path


class WorkspaceSafetyError(RuntimeError):
    """Raised when a path would escape the dedicated task workspace."""


class TaskEnvironment:
    def __init__(self, base_directory: str | Path, task: TaskSpec) -> None:
        self.base_directory = Path(base_directory).expanduser().resolve()
        self.task = task
        self.workspace = self.base_directory / task.task_id
        self.database_path = self.workspace / "state" / "api.sqlite3"
        self.base_directory.mkdir(parents=True, exist_ok=True)
        if self.workspace.parent != self.base_directory:
            raise WorkspaceSafetyError("Task workspace must be a direct child of its base directory")

    def resolve_path(self, relative_path: str, *, must_exist: bool = True) -> Path:
        validated = validate_relative_path(relative_path)
        path = (self.workspace / validated).resolve()
        if path != self.workspace and self.workspace not in path.parents:
            raise WorkspaceSafetyError(f"Path escapes task workspace: {relative_path!r}")
        if must_exist and not path.exists():
            raise FileNotFoundError(relative_path)
        return path

    def reset(self) -> str:
        staging = Path(tempfile.mkdtemp(prefix=f".{self.task.task_id}-", dir=self.base_directory))
        try:
            self._materialize(staging)
            if self.workspace.exists():
                if self.workspace.is_symlink() or self.workspace.parent != self.base_directory:
                    raise WorkspaceSafetyError("Refusing to replace an unsafe workspace")
                shutil.rmtree(self.workspace)
            os.replace(staging, self.workspace)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise
        return self.state_hash()

    def _materialize(self, root: Path) -> None:
        for fixture in self.task.files:
            path = root / fixture.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(fixture.content, encoding="utf-8")
        database = root / "state" / "api.sqlite3"
        database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(database) as connection:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE inventory (
                    sku TEXT PRIMARY KEY,
                    quantity INTEGER NOT NULL CHECK(quantity >= 0)
                );
                CREATE TABLE orders (
                    order_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE idempotency_keys (
                    idempotency_key TEXT PRIMARY KEY,
                    request_hash TEXT NOT NULL,
                    response_json TEXT NOT NULL
                );
                """
            )
            connection.executemany(
                "INSERT INTO inventory(sku, quantity) VALUES (?, ?)",
                [(str(row["sku"]), int(row["quantity"])) for row in self.task.inventory],
            )
            connection.executemany(
                "INSERT INTO orders(order_id, status, version) VALUES (?, ?, ?)",
                [
                    (str(row["order_id"]), str(row["status"]), int(row.get("version", 1)))
                    for row in self.task.orders
                ],
            )

    @contextmanager
    def database(self) -> Iterator[sqlite3.Connection]:
        if not self.database_path.is_file():
            raise RuntimeError("Environment has not been reset")
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def state_manifest(self) -> dict[str, object]:
        files: dict[str, str] = {}
        for path in sorted(self.workspace.rglob("*")):
            if path.is_file():
                relative = path.relative_to(self.workspace).as_posix()
                if relative.startswith("state/api.sqlite3"):
                    continue
                files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.database() as connection:
            inventory = [dict(row) for row in connection.execute("SELECT * FROM inventory ORDER BY sku")]
            orders = [dict(row) for row in connection.execute("SELECT * FROM orders ORDER BY order_id")]
            idempotency = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM idempotency_keys ORDER BY idempotency_key"
                )
            ]
        return {
            "task_id": self.task.task_id,
            "files": files,
            "inventory": inventory,
            "orders": orders,
            "idempotency_keys": idempotency,
        }

    def state_hash(self) -> str:
        canonical = json.dumps(
            self.state_manifest(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
