"""Durable causal order for decision features and provider delegation boundaries."""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from risk_agent.agent.agent_state_adapter import canonical_hash
from risk_agent.monitoring import AuditPersistenceError
from .formal_contract import RecordLog, read_records
from .day5_gate import sha256_file


class DecisionOrderLedger:
    def __init__(self, directory: Path, *, run_id: str, experiment_id: str,
                 clock_ns: Callable[[], int] = time.monotonic_ns):
        self.directory = directory.resolve()
        self.log = RecordLog(directory/"decision_order.jsonl", run_id=run_id, experiment_id=experiment_id)
        self.run_id = run_id
        self.epoch = uuid.uuid4().hex
        self.clock_ns = clock_ns
        self.lock = threading.RLock()
        self.rows = read_records(self.log.path)
        self.cutoffs = {r["task_id"]:r for r in self.rows if r["record_type"]=="feature_cutoff"}
        self.last_ns: int | None = None
        self.failures: dict[str,str] = {}

    def _append(self, value: dict[str, Any]) -> dict[str, Any]:
        now = self.clock_ns()
        if self.last_ns is not None and now < self.last_ns:
            raise AuditPersistenceError("Monotonic decision clock regressed")
        try:
            record = self.log.append({**value, "event_seq":len(self.rows)+1,
                "process_epoch":self.epoch,"monotonic_ns":now,"clock_basis":"process_monotonic_ns"})
        except Exception as exc:
            raise AuditPersistenceError("Decision order persistence failure") from exc
        self.rows.append(record)
        self.last_ns = now
        return record

    def freeze(self, *, task_id: str, checkpoint_id: str, features_path: Path) -> dict[str, Any]:
        with self.lock:
            if task_id in self.cutoffs:
                raise AuditPersistenceError("Decision features already frozen")
            path = features_path.resolve()
            if not path.is_relative_to(self.directory):
                raise AuditPersistenceError("Feature path outside owned Run")
            packet = json.loads(path.read_text())
            if (packet["task_id"]!=task_id or packet["checkpoint_id"]!=checkpoint_id
                    or canonical_hash(packet["values"])!=packet["features_sha256"]):
                raise AuditPersistenceError("Feature identity/hash mismatch")
            record = self._append({"record_type":"feature_cutoff","task_id":task_id,
                "checkpoint_id":checkpoint_id,"features_sha256":packet["features_sha256"],
                "features_file_sha256":sha256_file(path),"features_ref":str(path.relative_to(self.directory))})
            self.cutoffs[task_id]=record
            return record

    def request_started(self, context: Any, request_id: str) -> None:
        try:
            self._record_request(context,request_id)
        except Exception as exc:
            with self.lock:self.failures[context.task_id]=type(exc).__name__
            if isinstance(exc,AuditPersistenceError):raise
            raise AuditPersistenceError("Decision delegation boundary could not be verified")from exc

    def _record_request(self, context: Any, request_id: str) -> None:
        with self.lock:
            if context.task_id in self.failures:
                raise AuditPersistenceError("Decision order failure is latched; automatic retry refused")
            if context.run_id!=self.run_id or context.phase not in {"source","continue","repeat"}:
                raise AuditPersistenceError("Unexpected causal request identity")
            if any(r.get("audit_request_id")==request_id for r in self.rows):
                raise AuditPersistenceError("Duplicate causal request ID")
            cutoff = self.cutoffs.get(context.task_id)
            if context.phase=="source":
                if cutoff:raise AuditPersistenceError("Source cannot resume after feature cutoff")
            else:
                if not cutoff or cutoff["process_epoch"]!=self.epoch:
                    raise AuditPersistenceError("CONTINUE requires a current-process durable feature cutoff")
                try:
                    current_hash=sha256_file(self.directory/cutoff["features_ref"])
                except OSError as exc:
                    raise AuditPersistenceError("Frozen decision feature file is unreadable")from exc
                if current_hash!=cutoff["features_file_sha256"]:
                    raise AuditPersistenceError("Frozen decision feature file changed before delegation")
            self._append({"record_type":"provider_request_boundary","task_id":context.task_id,
                "phase":context.phase,"worker_id":context.worker_id,"audit_request_id":request_id,
                "cutoff_record_sha256":cutoff["record_sha256"] if cutoff else None,
                "cutoff_event_seq":cutoff["event_seq"] if cutoff else None})

    def verify(self, task_id: str, providers: list[dict[str, Any]]) -> bool:
        try:
            if task_id in self.failures:return False
            rows = read_records(self.log.path)
            if [r["event_seq"]for r in rows]!=list(range(1,len(rows)+1)):
                return False
            selected=[r for r in rows if r.get("task_id")==task_id]
            cutoffs=[r for r in selected if r["record_type"]=="feature_cutoff"]
            if len(cutoffs)!=1:return False
            cutoff=cutoffs[0]
            path=(self.directory/cutoff["features_ref"]).resolve()
            if not path.is_relative_to(self.directory) or sha256_file(path)!=cutoff["features_file_sha256"]:
                return False
            requests=[r for r in selected if r["record_type"]=="provider_request_boundary"]
            if not any(r["phase"]=="continue"for r in requests):return False
            by_id={r["audit_request_id"]:r for r in requests}
            if len(by_id)!=len(requests) or set(by_id)!={p["audit_request_id"]for p in providers}:
                return False
            epochs={r["process_epoch"]for r in selected}
            if epochs!={cutoff["process_epoch"]}:return False
            if any(b["monotonic_ns"]<a["monotonic_ns"]for a,b in zip(selected,selected[1:])):
                return False
            for p in providers:
                boundary=by_id[p["audit_request_id"]]
                if boundary["phase"]!=p["phase"] or boundary["worker_id"]!=p["worker_id"]:return False
                if p["phase"]=="source":
                    if boundary["event_seq"]>=cutoff["event_seq"]:return False
                elif (boundary["event_seq"]<=cutoff["event_seq"]
                      or boundary["cutoff_record_sha256"]!=cutoff["record_sha256"]
                      or boundary["cutoff_event_seq"]!=cutoff["event_seq"]):return False
            return True
        except (OSError,ValueError,KeyError,TypeError):
            return False
