"""Deterministic result and environment-state validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .environment import TaskEnvironment
from .schema import TaskSpec


@dataclass(frozen=True, slots=True)
class ValidationResult:
    passed: bool
    checks: Mapping[str, bool]


class DeterministicVerifier:
    def verify(
        self,
        task: TaskSpec,
        environment: TaskEnvironment,
        result: Any,
    ) -> ValidationResult:
        expected = task.expected
        checks: dict[str, bool] = {}
        if "result" in expected:
            checks["result"] = result == expected["result"]
        expected_files = expected.get("files", {})
        if not isinstance(expected_files, dict):
            raise ValueError("expected.files must be a mapping")
        for relative_path, content in expected_files.items():
            path = environment.resolve_path(str(relative_path))
            checks[f"file:{relative_path}"] = path.read_text(encoding="utf-8") == content
        database = expected.get("database", {})
        if not isinstance(database, dict):
            raise ValueError("expected.database must be a mapping")
        orders = database.get("orders", {})
        inventory = database.get("inventory", {})
        if not isinstance(orders, dict) or not isinstance(inventory, dict):
            raise ValueError("expected database tables must be mappings")
        with environment.database() as connection:
            for order_id, fields in orders.items():
                row = connection.execute(
                    "SELECT order_id, status, version FROM orders WHERE order_id = ?",
                    (order_id,),
                ).fetchone()
                checks[f"order:{order_id}"] = row is not None and all(
                    row[key] == value for key, value in dict(fields).items()
                )
            for sku, fields in inventory.items():
                row = connection.execute(
                    "SELECT sku, quantity FROM inventory WHERE sku = ?", (sku,)
                ).fetchone()
                checks[f"inventory:{sku}"] = row is not None and all(
                    row[key] == value for key, value in dict(fields).items()
                )
        if not checks:
            raise ValueError(f"Task {task.task_id!r} has no deterministic checks")
        return ValidationResult(passed=all(checks.values()), checks=checks)
