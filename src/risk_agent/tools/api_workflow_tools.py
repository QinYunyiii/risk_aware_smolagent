"""Transactional SQLite-backed mock API tools with idempotent writes."""

from __future__ import annotations

import hashlib
import json

from risk_agent.benchmark.environment import TaskEnvironment


class APIWorkflowError(RuntimeError):
    pass


class StateConflictError(APIWorkflowError):
    pass


class IdempotencyConflictError(APIWorkflowError):
    pass


class APIWorkflowTools:
    _ALLOWED_STATUSES = {"pending", "processing", "shipped", "cancelled"}
    _ALLOWED_TRANSITIONS = {
        "pending": {"processing", "cancelled"},
        "processing": {"shipped", "cancelled"},
        "shipped": set(),
        "cancelled": set(),
    }

    def __init__(self, environment: TaskEnvironment) -> None:
        self.environment = environment

    def query_inventory(self, sku: str) -> dict[str, object]:
        with self.environment.database() as connection:
            row = connection.execute(
                "SELECT sku, quantity FROM inventory WHERE sku = ?", (sku,)
            ).fetchone()
        if row is None:
            raise APIWorkflowError(f"Unknown SKU: {sku}")
        return dict(row)

    def get_order(self, order_id: str) -> dict[str, object]:
        with self.environment.database() as connection:
            row = connection.execute(
                "SELECT order_id, status, version FROM orders WHERE order_id = ?", (order_id,)
            ).fetchone()
        if row is None:
            raise APIWorkflowError(f"Unknown order: {order_id}")
        return dict(row)

    def update_order(
        self,
        order_id: str,
        expected_status: str,
        new_status: str,
        idempotency_key: str,
    ) -> dict[str, object]:
        if expected_status not in self._ALLOWED_STATUSES or new_status not in self._ALLOWED_STATUSES:
            raise APIWorkflowError("Unsupported order status")
        if new_status not in self._ALLOWED_TRANSITIONS[expected_status]:
            raise APIWorkflowError(
                f"Unsafe order transition: {expected_status!r} -> {new_status!r}"
            )
        if not idempotency_key or len(idempotency_key) > 128:
            raise APIWorkflowError("A bounded idempotency_key is required")
        request = {
            "order_id": order_id,
            "expected_status": expected_status,
            "new_status": new_status,
        }
        request_hash = hashlib.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        with self.environment.database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                "SELECT request_hash, response_json FROM idempotency_keys WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if prior is not None:
                if prior["request_hash"] != request_hash:
                    raise IdempotencyConflictError("Idempotency key was used for a different request")
                connection.rollback()
                replay = dict(json.loads(prior["response_json"]))
                replay["idempotent_replay"] = True
                return replay
            current = connection.execute(
                "SELECT status, version FROM orders WHERE order_id = ?", (order_id,)
            ).fetchone()
            if current is None:
                raise APIWorkflowError(f"Unknown order: {order_id}")
            if current["status"] != expected_status:
                raise StateConflictError(
                    f"Expected status {expected_status!r}, observed {current['status']!r}"
                )
            next_version = int(current["version"]) + 1
            cursor = connection.execute(
                """
                UPDATE orders SET status = ?, version = ?
                WHERE order_id = ? AND status = ? AND version = ?
                """,
                (new_status, next_version, order_id, expected_status, current["version"]),
            )
            if cursor.rowcount != 1:
                raise StateConflictError("Order changed concurrently")
            response: dict[str, object] = {
                "order_id": order_id,
                "status": new_status,
                "version": next_version,
                "idempotent_replay": False,
            }
            connection.execute(
                "INSERT INTO idempotency_keys VALUES (?, ?, ?)",
                (idempotency_key, request_hash, json.dumps(response, sort_keys=True)),
            )
            connection.commit()
            return response
