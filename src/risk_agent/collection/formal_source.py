"""Formal Source capture: same v2.2 lifecycle, formal identities and fault state."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from dataclasses import asdict
from typing import Any
from smolagents.memory import ActionStep
from risk_agent.benchmark import TaskEnvironment
from risk_agent.agent import AgentStateAdapter
from risk_agent.monitoring import RuntimeState, BudgetState, ExecutionMonitor, JsonlTraceWriter
from risk_agent.checkpoint import CheckpointIdentityV2
from .continue_acceptance_v2 import ContinueAcceptanceRunner
from .formal_contract import write_once_json as write_json
from .continue_gate import CheckpointAuditRecord
from .day5_gate import Day5GateRunner, TOOL_VERSION, checkpoint_inventory, sha256_file
from .decision_checkpoint import DecisionCheckpointCapture, DecisionCheckpointRejected
from .formal_faults import create_formal_tools

class NoFormalCheckpoint(RuntimeError):
    pass

class FormalSource(ContinueAcceptanceRunner):
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
            experiment_id=self.experiment_id, run_id=self.run_id, task_id=task.task_id,
            template_id=task.template_id, instance_id=task.instance_id, worker_id=worker.worker_id,
            model_id=self.settings.model_id, model_version=self.settings.model_version,
            prompt_version=self.settings.prompt_version, tool_version=TOOL_VERSION,
            environment_id=f"source:{task.task_id}",
            additional_context={"protocol_id": self.profile.config["protocol_id"],
                                "task_run_id": task_run_id, **self.flags})
        fault_injector = self.fault_injector(task.task_id, "source")
        tools = create_formal_tools(environment=environment, monitor=monitor, fault_injector=fault_injector)
        factory = self._factory(task, "source", started, clients)
        agent, model, underlying = factory(self.settings, worker, tools, runtime,
            max_steps=limits["max_agent_steps"], max_model_calls=limits["max_model_calls"],
            agent_name="frozen_continue_source")
        checkpoint_id = "cp_" + hashlib.sha256(f"{self.run_id}:{task.task_id}".encode()).hexdigest()[:40]
        identity = CheckpointIdentityV2(
            experiment_id=self.experiment_id, run_id=self.run_id, task_run_id=task_run_id,
            model_version=self.settings.model_version, worker_id=worker.worker_id,
            prompt_version=self.settings.prompt_version, tool_version=TOOL_VERSION,
            verifier_version="deterministic-verifier-v2.1", verifier_content_hash=sha256_file(
                self.root / "src/risk_agent/benchmark/verifier.py"),
            catalog_hash=self.profile.audit_summary()["catalog_sha256"],
            split_manifest_hash=self.profile.audit_summary()["split_manifest_sha256"],
            controller_type="qwen3_model", execution_mode=self.flags["execution_mode"],
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
                        runtime_metadata=Day5GateRunner._runtime_metadata(runtime=runtime, model=model, tools=tools, fault_injector=fault_injector),
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
            write_json(task_root / "source_agent_state.json", AgentStateAdapter.capture(agent).to_dict())
            write_json(self.directory / "internal" / task.task_id / "source_runtime.json", runtime.to_dict())
            write_json(task_root / "source_phase_ended.json", {"ended_at": datetime.now(timezone.utc).isoformat(), **self.flags})
            write_json(task_root / "source_model_requests.json", [{
                "input_hash": r.input_hash, "status": r.status,
                "prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens,
                "latency_seconds": r.latency_seconds} for r in model.requests])
        if snapshot is None:
            raise NoFormalCheckpoint("No eligible nonterminal decision checkpoint; Source not retried")
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
            "source_model_calls": model.executed_calls, "source_budget": runtime.budget.to_dict(), **self.flags})
        return record, environment, runtime, model
