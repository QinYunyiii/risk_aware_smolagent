"""Formal event identity is attached before persistence, not to historical files."""
from __future__ import annotations

from .continue_acceptance import AcceptanceProviderAudit
from risk_agent.monitoring import JsonlTraceWriter
from risk_agent.monitoring.provider_response_audit import AuditPersistenceError


class ContextWriter:
    def __init__(self, writer, context):
        self.writer, self.context = writer, context
    def append(self, record):
        context = self.context(record) if callable(self.context) else self.context
        self.writer.append({**record, **context})


class FormalProviderAudit(AcceptanceProviderAudit):
    def __init__(self, directory, *, flags, experiment_id, tasks):
        self.contexts = {}
        def context(record):
            ctx = self.contexts.get(record.get("audit_request_id"))
            if ctx is None:
                raise AuditPersistenceError("Formal Provider event has no registered request context")
            return {"experiment_id": experiment_id, "run_id": ctx.run_id,
                    "task_id": ctx.task_id, "split": tasks[ctx.task_id].split.value, **flags}
        super().__init__(directory,
            response_writer=ContextWriter(JsonlTraceWriter(directory / "provider_response_audit.jsonl"), context),
            parsing_writer=ContextWriter(JsonlTraceWriter(directory / "parsing_outcome_audit.jsonl"), context))
    def request_started(self, context, request_id):
        self.contexts[request_id] = context
        super().request_started(context, request_id)
