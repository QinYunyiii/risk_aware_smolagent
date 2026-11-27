"""Pinned five-Train-task Source/capture/full-CONTINUE acceptance orchestration.

This is a smoke entry, never a formal data collector. Transport ownership stays
with ConnectionLifecycle; the existing checkpoint and CONTINUE implementations
remain authoritative for state, replay, exact input and deterministic outcomes.
"""

from __future__ import annotations

import json
import subprocess
import time
import traceback
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml
from smolagents.memory import ActionStep

from risk_agent.agent import ModelRequestRejected, RecordingModel
from risk_agent.agent.agent_factory import create_checkpointable_benchmark_agent
from risk_agent.agent.agent_state_adapter import canonical_hash
from risk_agent.agent.worker_router import RemoteModelSettings
from risk_agent.benchmark import TaskEnvironment
from risk_agent.benchmark.schema import DataSplit, validate_identifier
from risk_agent.checkpoint import CheckpointIdentityV2, CheckpointManager
from risk_agent.monitoring import (
    BudgetState, ExecutionMonitor, JsonlTraceWriter,
    ProviderAuditContext, ProviderResponseAudit, RuntimeState,
)
from risk_agent.monitoring.provider_response_audit import sanitize_audit_value

from .continue_gate import CheckpointAuditRecord, ContinueGateRunner
from .day5_gate import Day5GateRunner, TOOL_VERSION, checkpoint_inventory, sha256_file
from .decision_checkpoint import DecisionCheckpointCapture, DecisionCheckpointRejected
from .protocol_freeze import CURRENT_MANIFEST_PATH, load_frozen_collection


CONFIG_PATH = "configs/continue_acceptance_v2_1.yaml"
ENTRY_MANIFEST_PATH = "docs/phase2/continue_acceptance_entry_manifest_v1.json"
FLAGS = dict(formal_experiment=False, research_dataset_eligible=False,
             execution_mode="model_backed_smoke", controller_type="qwen3_model")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(sanitize_audit_value(value), handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def load_entry(root: Path):
    profile = load_frozen_collection(root)
    manifest = json.loads((root / ENTRY_MANIFEST_PATH).read_text())
    if manifest["parent_manifest_sha256"] != profile.manifest_sha256:
        raise ValueError("Acceptance entry requires its pinned parent implementation")
    for name, digest in manifest["files"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file() or sha256_file(path) != digest:
            raise ValueError(f"Acceptance entry input changed: {name}")
    config = yaml.safe_load((root / CONFIG_PATH).read_text())
    selected = {task.task_id: task for task in profile.tasks}
    ids = config["task_ids"]
    if len(ids) != 5 or len(set(ids)) != 5 or any(selected[t].split is not DataSplit.TRAIN for t in ids):
        raise ValueError("Acceptance requires five distinct pinned Train tasks")
    return profile, config


class AcceptanceProviderAudit(ProviderResponseAudit):
    """Balance an interrupted request only after its owned clients are closed."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.pending: dict[str, ProviderAuditContext] = {}

    def request_started(self, context: ProviderAuditContext, request_id: str) -> None:
        self.pending[request_id] = context
        try:
            super().request_started(context, request_id)
        except BaseException:
            self.pending.pop(request_id, None)
            raise

    def request_finished(self, context: ProviderAuditContext, request_id: str, exception=None) -> None:
        try:
            super().request_finished(context, request_id, exception)
        finally:
            self.pending.pop(request_id, None)

    def release_aborted(self, task_id: str) -> int:
        abandoned = [(key, ctx) for key, ctx in self.pending.items() if ctx.task_id == task_id]
        for key, context in abandoned:
            self.request_finished(context, key, RuntimeError("owned_client_closed_after_abort"))
        return len(abandoned)


class AcceptanceRecordingModel(RecordingModel):
    """Apply the frozen context and one shared wall budget before tools execute."""

    def __init__(self, *args: Any, started: float, clock: Callable[[], float],
                 max_context: int, runtime: RuntimeState, **kwargs: Any) -> None:
        super().__init__(*args, runtime_state=runtime, **kwargs)
        self.task_started = started
        self.task_clock = clock
        self.max_context = max_context
        self.live_runtime = runtime

    def sync_wall(self) -> None:
        budget = self.live_runtime.budget
        budget.latency_seconds_used = max(budget.latency_seconds_used, self.task_clock() - self.task_started)

    def generate(self, *args: Any, **kwargs: Any):
        remaining = self.live_runtime.budget.max_latency_seconds - (self.task_clock() - self.task_started)
        if remaining <= 0:
            raise ModelRequestRejected("task_wall_clock_budget")
        if not self.resume_contract_pending:
            self.sync_wall()
        # RecordingModel further caps this to the pinned client and live budgets.
        kwargs["timeout"] = min(float(kwargs.get("timeout", remaining)), remaining)
        result = super().generate(*args, **kwargs)
        self.sync_wall()
        if self.task_clock() - self.task_started >= self.live_runtime.budget.max_latency_seconds:
            raise ModelRequestRejected("task_wall_clock_budget")
        request = self.requests[-1]
        if request.prompt_tokens is not None and request.completion_tokens is not None:
            if request.prompt_tokens + request.completion_tokens > self.max_context:
                raise ModelRequestRejected("context_token_budget")
        return result


class ContinueAcceptanceRunner:
    def __init__(self, *, root: str | Path, run_directory: str | Path, run_id: str,
                 offline_fixture: bool = False,
                 authorize_remote: bool = False,
                 agent_factory: Callable[..., Any] = create_checkpointable_benchmark_agent,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.root = Path(root).resolve()
        self.profile, self.config = load_entry(self.root)
        if not offline_fixture and not authorize_remote:
            raise ValueError("Real execution requires explicit authorize_remote")
        if not offline_fixture and agent_factory is not create_checkpointable_benchmark_agent:
            raise ValueError("Injected backends must be explicitly marked offline_fixture")
        self.run_id = validate_identifier(run_id, "run_id")
        self.directory = Path(run_directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        self.offline_fixture = offline_fixture
        self.base_factory = agent_factory
        self.clock = clock
        self.settings = RemoteModelSettings.from_yaml(
            self.root / self.profile.config["dependencies"]["remote_model_config"])
        self.tasks = {task.task_id: task for task in self.profile.tasks}
        self.workers = {worker.worker_id: worker for worker in self.settings.workers}
        self.audit = AcceptanceProviderAudit(self.directory / "audit")
        self.manager = CheckpointManager(self.directory / "source/checkpoints")
        self.attempted: set[str] = set()
        manifest_bytes = (self.root / ENTRY_MANIFEST_PATH).read_bytes()
        with (self.directory / "entry_manifest_snapshot.json").open("xb") as handle:
            handle.write(manifest_bytes)
        for name in json.loads(manifest_bytes)["files"]:
            target = self.directory / "entry_inputs" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write((self.root / name).read_bytes())
        write_json(self.directory / "run_manifest.json", {
            "run_id": run_id, "protocol_id": self.profile.config["protocol_id"],
            "implementation_revision": self.profile.config["implementation_revision"],
            "freeze_manifest_sha256": self.profile.manifest_sha256,
            "entry_manifest_sha256": sha256_file(self.root / ENTRY_MANIFEST_PATH),
            "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip(),
            "git_worktree_dirty": bool(subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=self.root, text=True).strip()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "offline_fixture": offline_fixture, "scope": self.config,
            "model_id": self.settings.model_id, "inference": self.settings.inference,
            "catalog_sha256": self.profile.audit_summary()["catalog_sha256"],
            "split_sha256": self.profile.audit_summary()["split_manifest_sha256"], **FLAGS,
        })

    def _factory(self, task: Any, phase: str, started: float, clients: list[Any]):
        def create(settings: Any, worker: Any, tools: Any, runtime: RuntimeState, **kwargs: Any):
            runtime.budget.latency_seconds_used = max(runtime.budget.latency_seconds_used, self.clock() - started)
            context = ProviderAuditContext(
                run_id=self.run_id, task_id=task.task_id, attempt_index=1,
                worker_id=worker.worker_id, model_id=settings.model_id,
                model_version=settings.model_version, endpoint=worker.base_url, phase=phase)
            agent, _, underlying = self.base_factory(settings, worker, tools, runtime, **kwargs)
            clients.append((phase, underlying.client))
            recorder = AcceptanceRecordingModel(
                underlying, runtime=runtime, max_model_calls=kwargs["max_model_calls"],
                provider_audit=self.audit, provider_audit_context=context,
                started=started, clock=self.clock,
                max_context=self.profile.config["limits"]["max_context_tokens"])
            agent.model = recorder
            recorder.bind_step_state_provider(lambda: (agent.step_number, agent.max_steps))
            return agent, recorder, underlying
        return create

    def _capture(self, task: Any, worker: Any, task_root: Path, started: float, clients: list[Any]):
        limits = self.profile.config["limits"]
        task_run_id = f"{self.run_id}_{task.task_id}_source"
        environment = TaskEnvironment(self.directory / "source/environments" / task.task_id, task)
        environment.reset()
        runtime = RuntimeState(seed=task.seed, budget=BudgetState(
            limits["max_total_tokens"], limits["max_tool_calls"], limits["task_wall_clock_timeout_seconds"]),
            tool_state={"protocol_id": self.profile.config["protocol_id"]})
        monitor = ExecutionMonitor(
            writer=JsonlTraceWriter(task_root / "source_tools.jsonl"), runtime_state=runtime,
            experiment_id="frozen_continue_acceptance", run_id=self.run_id, task_id=task.task_id,
            template_id=task.template_id, instance_id=task.instance_id, worker_id=worker.worker_id,
            model_id=self.settings.model_id, model_version=self.settings.model_version,
            prompt_version=self.settings.prompt_version, tool_version=TOOL_VERSION,
            environment_id=f"source:{task.task_id}",
            additional_context={"protocol_id": self.profile.config["protocol_id"],
                                "task_run_id": task_run_id, **FLAGS})
        from .benchmark_tools import create_local_domain_tools
        tools = create_local_domain_tools(environment=environment, monitor=monitor)
        factory = self._factory(task, "source", started, clients)
        agent, model, underlying = factory(self.settings, worker, tools, runtime,
            max_steps=limits["max_agent_steps"], max_model_calls=limits["max_model_calls"],
            agent_name="frozen_continue_source")
        checkpoint_id = f"checkpoint_{task.task_id}"
        identity = CheckpointIdentityV2(
            experiment_id="frozen_continue_acceptance", run_id=self.run_id, task_run_id=task_run_id,
            model_version=self.settings.model_version, worker_id=worker.worker_id,
            prompt_version=self.settings.prompt_version, tool_version=TOOL_VERSION,
            verifier_version="deterministic-verifier-v2.1", verifier_content_hash=sha256_file(
                self.root / "src/risk_agent/benchmark/verifier.py"),
            catalog_hash=self.profile.audit_summary()["catalog_sha256"],
            split_manifest_hash=self.profile.audit_summary()["split_manifest_sha256"],
            controller_type="qwen3_model", execution_mode="model_backed_smoke",
            created_at=datetime.now(timezone.utc).isoformat())
        capture = DecisionCheckpointCapture()
        snapshot = None
        stream = agent.run(task.prompt, stream=True)
        decisions = JsonlTraceWriter(task_root / "capture_decisions.jsonl")
        try:
            for event in stream:
                if not isinstance(event, ActionStep):
                    continue
                model.sync_wall()
                if model.model_calls_used >= limits["max_model_calls"]:
                    decisions.append({"step_number": event.step_number, "accepted": False,
                                      "reason": "no_remaining_model_call"})
                    break
                try:
                    snapshot = capture.capture(
                        event=event, agent=agent, manager=self.manager, checkpoint_id=checkpoint_id,
                        environment=environment, runtime_state=runtime, identity=identity,
                        runtime_metadata=Day5GateRunner._runtime_metadata(runtime=runtime, model=model, tools=tools),
                        environment_metadata=Day5GateRunner._environment_metadata(runtime=runtime, event=event),
                    ).snapshot
                except DecisionCheckpointRejected as exc:
                    decisions.append(asdict(capture.decisions[-1]))
                    if event.is_final_answer or str(exc) in {
                        "agent_step_budget_exhausted", "token_budget_exhausted", "latency_budget_exhausted",
                        "final_answer_tool_call_is_terminal"}:
                        break
                    if str(exc) not in {"assistant_tool_call_missing", "tool_observation_missing",
                        "assistant_model_tool_calls_missing", "canonical_messages_do_not_end_in_tool_response"}:
                        raise
                    continue
                decisions.append(asdict(capture.decisions[-1]))
                break
        finally:
            stream.close()
            underlying.client.close()
            write_json(task_root / "source_model_requests.json", [{
                "input_hash": r.input_hash, "status": r.status,
                "prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens,
                "latency_seconds": r.latency_seconds} for r in model.requests])
        if snapshot is None:
            raise RuntimeError("No eligible nonterminal decision checkpoint; Source not retried")
        self.manager.verify_v2(checkpoint_id=checkpoint_id, environment=environment)
        inventory = checkpoint_inventory(snapshot.checkpoint_path)
        document = json.loads((snapshot.checkpoint_path / "manifest.json").read_text())
        step = int(snapshot.manifest.identity["step_index"])
        record = CheckpointAuditRecord(
            checkpoint_id=checkpoint_id, checkpoint_path=str(snapshot.checkpoint_path),
            task_id=task.task_id, task_run_id=task_run_id, worker_id=worker.worker_id,
            schema_version=snapshot.manifest.schema_version,
            manifest_sha256=sha256_file(snapshot.checkpoint_path / "manifest.json"),
            artifact_count=len(document["artifacts"]), inventory_file_count=len(inventory),
            step_number=step, next_step_number=step + 1, model_calls_used=model.model_calls_used,
            tool_calls_used=runtime.budget.tool_calls_used, hashes=snapshot.manifest.hashes,
            all_hashes_verified=True, checkpoint_inventory=inventory)
        write_json(task_root / "source_result.json", {
            "status": "checkpoint_captured", "checkpoint": record.to_dict(),
            "source_model_calls": model.executed_calls, "source_budget": runtime.budget.to_dict(), **FLAGS})
        return record, environment, runtime, model

    def _audit_complete(self, task_id: str) -> dict[str, Any]:
        rows = [r for r in read_jsonl(self.audit.response_path) if r["task_id"] == task_id]
        ids = {r["audit_request_id"] for r in rows}
        parses = [r for r in read_jsonl(self.audit.parsing_path) if r["audit_request_id"] in ids]
        complete = (bool(rows) and len(ids) == len(rows) and len(parses) == len(rows)
                    and ids == {r["audit_request_id"] for r in parses}
                    and not any(ctx.task_id == task_id for ctx in self.audit.pending.values()))
        complete = complete and all(
            r.get("request_status") == "response_received" and r.get("usage_completeness") == "complete"
            and r.get("served_model_id") == self.settings.model_id and r.get("finish_reason") is not None
            and r.get("raw_response_sha256") == canonical_hash(r.get("raw_response_snapshot")) for r in rows)
        return {"complete": complete, "provider_events": len(rows), "parsing_events": len(parses),
                "source_events": sum(r["phase"] == "source" for r in rows),
                "continue_events": sum(r["phase"] == "continue" for r in rows)}

    def run_task(self, task_id: str, worker_id: str) -> dict[str, Any]:
        if task_id not in self.config["task_ids"] or task_id in self.attempted:
            raise ValueError("Unselected or already attempted task; retries/replacements forbidden")
        expected = self.config["worker_schedule"][self.config["task_ids"].index(task_id)]
        if worker_id != expected:
            raise ValueError("Frozen worker assignment differs; failover forbidden")
        load_entry(self.root)
        self.attempted.add(task_id)
        task, worker = self.tasks[task_id], self.workers[worker_id]
        task_root = self.directory / "tasks" / task_id
        task_root.mkdir(parents=True, exist_ok=False)
        started = self.clock()
        clients: list[Any] = []
        result = {"task_id": task_id, "worker_id": worker_id, "accepted_y_continue": None,
                  "passed": False, "offline_fixture": self.offline_fixture, **FLAGS}
        try:
            record, environment, runtime, model = self._capture(task, worker, task_root, started, clients)
            source_hash = environment.state_hash()
            source_runtime_hash = canonical_hash(runtime.to_dict())
            source_calls = model.executed_calls
            continuation = ContinueGateRunner.from_frozen_collection(
                root=self.root, run_directory=self.directory / "continue", checkpoint_run=self.directory / "source",
                run_id=self.run_id, agent_factory=self._factory(task, "continue", started, clients), clock=self.clock)
            component = continuation.run_checkpoint(audit=record, worker=worker, branch_attempt_index=1)
            unchanged = (environment.state_hash() == source_hash and canonical_hash(runtime.to_dict()) == source_runtime_hash
                         and model.executed_calls == source_calls)
            audit = self._audit_complete(task_id)
            audit["complete"] = bool(audit["complete"] and audit["source_events"] == source_calls
                and audit["continue_events"] == component["model_requests_added"])
            eligible = component["complete_chain"] and unchanged and audit["complete"]
            result.update(checkpoint_id=record.checkpoint_id, capture_step=record.step_number,
                source_model_calls=source_calls, source_unchanged=unchanged,
                component=component, provider_audit=audit,
                accepted_y_continue=component["y_continue"] if eligible else None,
                passed=bool(eligible and component["verifier_passed"]),
                wall_seconds=self.clock() - started)
        except (Exception, KeyboardInterrupt) as exc:
            result.update(error_type=type(exc).__name__, exception_chain=sanitize_audit_value(
                "".join(traceback.format_exception(exc))))
        finally:
            errors = []
            for phase, client in clients:
                try:
                    client.close()
                    closed = client.is_closed() if callable(client.is_closed) else client.is_closed
                    if not closed:
                        raise RuntimeError("Owned client did not close")
                except Exception as exc:
                    errors.append({"phase": phase, "error_type": type(exc).__name__})
            abandoned = 0
            if not errors:
                try:
                    abandoned = self.audit.release_aborted(task_id)
                except Exception as exc:
                    errors.append({"phase": "request_release", "error_type": type(exc).__name__})
            release = {"owned_clients": len(clients), "close_errors": errors,
                       "aborted_requests_released_after_client_close": abandoned,
                       "all_closed": not errors, "external_processes_terminated": 0}
            write_json(task_root / "resource_release.json", release)
            result["resources_released"] = release["all_closed"]
            if errors:
                result.update(passed=False, accepted_y_continue=None)
        write_json(task_root / "task_result.json", result)
        return result

    def run_fixed_gate(self) -> dict[str, Any]:
        rows = []
        for task_id, worker_id in zip(self.config["task_ids"], self.config["worker_schedule"], strict=True):
            rows.append(self.run_task(task_id, worker_id))
            if not rows[-1]["passed"]:
                break
        passed = len(rows) == 5 and all(r["passed"] for r in rows)
        summary = {"run_id": self.run_id, "passed": passed,
            "gate": ("CONTINUE_ENTRY_OFFLINE_PASS" if self.offline_fixture else "REAL_CONTINUE_GATE_PASS")
                    if passed else "CONTINUE_ACCEPTANCE_BLOCKED",
            "offline_fixture": self.offline_fixture, "tasks": rows,
            "task_attempts": len(rows), "automatic_retries": 0, "worker_failover": False,
            "real_model_calls": 0 if self.offline_fixture else len(read_jsonl(self.audit.response_path)),
            "formal_collection_ready": False, **FLAGS}
        write_json(self.directory / "gate_summary.json", summary)
        return summary
