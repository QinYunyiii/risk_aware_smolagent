"""B2 Day 5 real-model checkpoint/restore acceptance orchestration.

The module contains no implicit network entry point.  Callers must supply an already
selected worker and explicitly invoke :class:`Day5GateRunner`.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from smolagents.memory import ActionStep

from risk_agent.agent import AgentStateSnapshot, ExpectedModelRequest
from risk_agent.agent.agent_factory import create_checkpointable_benchmark_agent
from risk_agent.agent.agent_state_adapter import (
    canonical_hash,
    canonical_json,
    serialize_chat_message,
)
from risk_agent.agent.worker_router import RemoteModelSettings, WorkerConfig
from risk_agent.benchmark import TaskEnvironment, task_to_dict
from risk_agent.benchmark.schema import DataSplit, TaskSpec
from risk_agent.checkpoint import (
    AdapterStateMetadata,
    AdapterStateStatus,
    CheckpointIdentityV2,
    CheckpointManager,
    EnvironmentCheckpointMetadata,
    RuntimeCheckpointMetadata,
)
from risk_agent.faults import FaultConfig, FaultInjector
from risk_agent.monitoring import BudgetState, ExecutionMonitor, JsonlTraceWriter, RuntimeState

from .benchmark_tools import (
    LocalBenchmarkTool,
    capture_local_tool_adapter_state,
    create_local_domain_tools,
    restore_local_tool_adapter_state,
)
from .decision_checkpoint import DecisionCheckpointCapture, DecisionCheckpointRejected


PROTOCOL_ID = "risk-continue-v2.0.0"
EXPERIMENT_ID = "b2_day5_real_qwen_gate"
TOOL_VERSION = "b2-day5-local-tools-v1"
VERIFIER_VERSION = "deterministic-verifier-day5-not-run"
RUN_RESEARCH_FLAGS = {
    "formal_experiment": False,
    "execution_mode": "model_backed_smoke",
    "controller_type": "qwen3_model",
    "research_dataset_eligible": False,
    "y_continue": None,
}


class NoEligibleCheckpointError(RuntimeError):
    """The fixed candidate's first ActionStep did not satisfy the capture rule."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_task_catalog_hash(tasks: Iterable[TaskSpec]) -> str:
    payload = json.dumps(
        [task_to_dict(task) for task in tasks],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def split_manifest_hash(tasks: Iterable[TaskSpec]) -> str:
    rows = [
        {
            "task_id": task.task_id,
            "template_id": task.template_id,
            "instance_id": task.instance_id,
            "split": task.split.value if task.split is not None else None,
        }
        for task in tasks
    ]
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def select_day5_candidates(tasks: Iterable[TaskSpec]) -> tuple[TaskSpec, ...]:
    """Return a fixed Train-only order covering four domains before the fifth task."""

    eligible = [
        task
        for task in tasks
        if task.split is DataSplit.TRAIN and task.instance_id.endswith("_001")
    ]
    selected: list[TaskSpec] = []
    seen_domains: set[str] = set()
    for task in eligible:
        if task.domain.value not in seen_domains:
            selected.append(task)
            seen_domains.add(task.domain.value)
    if len(seen_domains) != 4:
        raise ValueError("Day 5 candidates require all four Train domains")
    selected_ids = {task.task_id for task in selected}
    selected.extend(task for task in eligible if task.task_id not in selected_ids)
    if len(selected) < 5:
        raise ValueError("Day 5 requires at least five distinct Train candidates")
    return tuple(selected)


def create_unique_run_directory(output_root: str | Path, run_id: str) -> Path:
    root = Path(output_root).expanduser().resolve()
    path = root / run_id
    path.mkdir(parents=True, exist_ok=False)
    return path


def public_tool_schemas(snapshot: AgentStateSnapshot) -> list[dict[str, Any]]:
    """Derive RecordingModel's ordered public schemas from checkpoint authority."""

    return [
        {
            "name": item["name"],
            "description": item["description"],
            "inputs": item["inputs"],
            "output_type": item["output_type"],
            "output_schema": item.get("output_schema"),
        }
        for item in snapshot.payload["tool_registry"]
    ]


def expected_request_from_checkpoint(
    *,
    manifest: Any,
    snapshot: AgentStateSnapshot,
    runtime_state: RuntimeState,
) -> ExpectedModelRequest:
    """Build the one-shot contract only from frozen checkpoint fields."""

    budget = manifest.runtime_state["budget_state"]
    messages = snapshot.payload["canonical_next_model_input"]
    return ExpectedModelRequest(
        messages=messages,
        next_model_input_hash=manifest.hashes["next_model_input_hash"],
        public_tool_schemas=public_tool_schemas(snapshot),
        generation_config=snapshot.payload["model"]["generation"],
        model_id=manifest.identity["model_id"],
        step_number=int(manifest.agent_context["next_step_number"]),
        remaining_steps=int(manifest.agent_context["remaining_steps"]),
        model_calls_used=int(budget["model_calls_used"]),
        max_model_calls=int(budget["max_model_calls"]),
        tool_calls_used=int(budget["tool_calls_used"]),
        max_tool_calls=int(budget["max_tool_calls"]),
        tokens_used=runtime_state.budget.tokens_used,
        max_tokens=int(budget["max_tokens"]),
        latency_seconds_used=runtime_state.budget.latency_seconds_used,
        max_latency_seconds=float(budget["max_latency_seconds"]),
    )


def checkpoint_inventory(root: Path) -> dict[str, dict[str, Any]]:
    return {
        path.relative_to(root).as_posix(): {
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class Day5GateRunner:
    """Capture and independently restore one selected real-model task attempt."""

    def __init__(
        self,
        *,
        settings: RemoteModelSettings,
        run_directory: str | Path,
        run_id: str,
        catalog_hash: str,
        split_hash: str,
        verifier_content_hash: str,
        experiment_id: str = EXPERIMENT_ID,
        research_flags: Mapping[str, Any] = RUN_RESEARCH_FLAGS,
    ) -> None:
        self.settings = settings
        self.run_directory = Path(run_directory).resolve()
        self.run_id = run_id
        self.catalog_hash = catalog_hash
        self.split_hash = split_hash
        self.verifier_content_hash = verifier_content_hash
        self.experiment_id = experiment_id
        self.research_flags = dict(research_flags)
        self.tool_writer = JsonlTraceWriter(self.run_directory / "tool_trajectories.jsonl")
        self.manager = CheckpointManager(self.run_directory / "checkpoints")

    def _monitor(
        self,
        *,
        task: TaskSpec,
        runtime: RuntimeState,
        worker: WorkerConfig,
        task_run_id: str,
        environment_id: str,
    ) -> ExecutionMonitor:
        return ExecutionMonitor(
            writer=self.tool_writer,
            runtime_state=runtime,
            experiment_id=self.experiment_id,
            run_id=self.run_id,
            task_id=task.task_id,
            template_id=task.template_id,
            instance_id=task.instance_id,
            model_id=self.settings.model_id,
            model_version=self.settings.model_version,
            prompt_version=self.settings.prompt_version,
            tool_version=TOOL_VERSION,
            worker_id=worker.worker_id,
            environment_id=environment_id,
            additional_context={
                "protocol_id": PROTOCOL_ID,
                "task_run_id": task_run_id,
                "split": task.split.value if task.split is not None else None,
                **self.research_flags,
            },
        )

    def _identity(
        self,
        *,
        task_run_id: str,
        worker: WorkerConfig,
    ) -> CheckpointIdentityV2:
        return CheckpointIdentityV2(
            experiment_id=self.experiment_id,
            run_id=self.run_id,
            task_run_id=task_run_id,
            model_version=self.settings.model_version,
            worker_id=worker.worker_id,
            prompt_version=self.settings.prompt_version,
            tool_version=TOOL_VERSION,
            verifier_version=VERIFIER_VERSION,
            verifier_content_hash=self.verifier_content_hash,
            catalog_hash=self.catalog_hash,
            split_manifest_hash=self.split_hash,
            controller_type="qwen3_model",
            execution_mode="model_backed_smoke",
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _runtime_metadata(
        *,
        runtime: RuntimeState,
        model: Any,
        tools: list[LocalBenchmarkTool],
        fault_injector: FaultInjector | None = None,
    ) -> RuntimeCheckpointMetadata:
        return RuntimeCheckpointMetadata(
            max_model_calls=10,
            model_calls_used=model.model_calls_used,
            prompt_tokens_used=model.prompt_tokens_observed,
            completion_tokens_used=model.completion_tokens_observed,
            resource_usage={
                "wall_seconds": runtime.budget.latency_seconds_used,
                "gpu_used_for_local_execution": False,
            },
            tool_adapter_state=AdapterStateMetadata(
                AdapterStateStatus.STATEFUL,
                {"tools": capture_local_tool_adapter_state(tools)},
            ),
            fault_adapter_state=(
                AdapterStateMetadata(
                    AdapterStateStatus.STATEFUL,
                    fault_injector.checkpoint_state(),
                )
                if fault_injector is not None
                else AdapterStateMetadata(
                    AdapterStateStatus.NOT_INSTANTIATED,
                    None,
                )
            ),
            phase="after_tool_observation_before_model_generation",
            next_operation="model_generation",
        )

    @staticmethod
    def _environment_metadata(
        *, runtime: RuntimeState, event: ActionStep
    ) -> EnvironmentCheckpointMetadata:
        completed = [
            {
                "step_id": entry.step_id,
                "tool_name": entry.tool_name,
                "tool_arguments": entry.tool_arguments,
                "error_type": entry.error_type,
                "commit_state": "completed" if entry.error_type is None else "agent_visible_error",
            }
            for entry in runtime.tool_history
        ]
        return EnvironmentCheckpointMetadata(
            completed_side_effects=completed,
            latest_completed_operation={
                "kind": "tool_call_batch",
                "step_number": event.step_number,
                "call_ids": [call.id for call in event.tool_calls or []],
            },
        )

    def run_task(
        self,
        *,
        task: TaskSpec,
        worker: WorkerConfig,
        attempt_index: int,
        fault_config: FaultConfig | None = None,
    ) -> dict[str, Any]:
        if task.split is not DataSplit.TRAIN:
            raise ValueError("Day 5 acceptance candidates must be Train tasks")
        if attempt_index < 1:
            raise ValueError("attempt_index must be positive")
        wall_started = time.monotonic()
        task_run_id = f"{self.run_id}_{task.task_id}_attempt_{attempt_index:03d}"
        checkpoint_id = f"checkpoint_{task.task_id}_{attempt_index:03d}"
        source_environment = TaskEnvironment(
            self.run_directory / "source_environments" / task_run_id,
            task,
        )
        source_environment.reset()
        runtime = RuntimeState(
            seed=task.seed,
            agent_memory=[],
            tool_state={
                "protocol_id": PROTOCOL_ID,
                "fault_id": fault_config.fault_id if fault_config is not None else None,
                "fault_type": (
                    fault_config.fault_type.value if fault_config is not None else None
                ),
            },
            budget=BudgetState(50_000, 12, 900.0),
        )
        source_monitor = self._monitor(
            task=task,
            runtime=runtime,
            worker=worker,
            task_run_id=task_run_id,
            environment_id=f"source:{task_run_id}",
        )
        source_fault_injector = (
            FaultInjector(fault_config) if fault_config is not None else None
        )
        source_tools = create_local_domain_tools(
            environment=source_environment,
            monitor=source_monitor,
            fault_injector=source_fault_injector,
        )
        source_agent, source_model, source_underlying = create_checkpointable_benchmark_agent(
            self.settings,
            worker,
            source_tools,
            runtime,
            max_steps=10,
            max_model_calls=10,
            agent_name=f"b2_day5_{worker.worker_id.replace('-', '_')}",
        )
        source_stream = source_agent.run(task.prompt, stream=True)
        try:
            capture_event: ActionStep | None = None
            for event in source_stream:
                if isinstance(event, ActionStep):
                    capture_event = event
                    break
            if capture_event is None:
                raise RuntimeError("Source Agent ended without an ActionStep")
            try:
                captured = DecisionCheckpointCapture().capture(
                    event=capture_event,
                    agent=source_agent,
                    manager=self.manager,
                    checkpoint_id=checkpoint_id,
                    environment=source_environment,
                    runtime_state=runtime,
                    identity=self._identity(task_run_id=task_run_id, worker=worker),
                    runtime_metadata=self._runtime_metadata(
                        runtime=runtime,
                        model=source_model,
                        tools=source_tools,
                        fault_injector=source_fault_injector,
                    ),
                    environment_metadata=self._environment_metadata(
                        runtime=runtime,
                        event=capture_event,
                    ),
                )
            except DecisionCheckpointRejected as exc:
                raise NoEligibleCheckpointError(str(exc)) from exc
            verified = self.manager.verify_v2(
                checkpoint_id=checkpoint_id,
                environment=source_environment,
            )
            expected = expected_request_from_checkpoint(
                manifest=verified.manifest,
                snapshot=verified.agent_state_snapshot,
                runtime_state=verified.runtime_state,
            )
            source_model.expect(expected)
            source_model.intercept_valid_request = True
            source_history_count = len(runtime.tool_history)
            source_provider_calls = source_model.executed_calls
            next(source_stream, None)
            source_request = source_model.requests[-1]
            if source_request.status != "intercepted":
                raise RuntimeError("Source next-input check did not stop before provider execution")
            if source_model.executed_calls != source_provider_calls:
                raise RuntimeError("Source next-input check unexpectedly reached the provider")
            if len(runtime.tool_history) != source_history_count:
                raise RuntimeError("Source next-input check replayed a tool")
        finally:
            source_stream.close()
            source_underlying.client.close()
        if source_model.executed_calls != source_provider_calls:
            raise RuntimeError("Closing the source generator reached the provider")
        if len(runtime.tool_history) != source_history_count:
            raise RuntimeError("Closing the source generator replayed a tool")

        checkpoint_before = checkpoint_inventory(captured.snapshot.checkpoint_path)
        restored_environment = TaskEnvironment(
            self.run_directory / "branches" / task_run_id,
            task,
        )
        restored_environment.reset()
        restored = self.manager.restore_v2(
            checkpoint_id=checkpoint_id,
            environment=restored_environment,
        )
        restored_monitor = self._monitor(
            task=task,
            runtime=restored.runtime_state,
            worker=worker,
            task_run_id=task_run_id,
            environment_id=f"restore:{task_run_id}",
        )
        restored_fault_injector = (
            FaultInjector(fault_config) if fault_config is not None else None
        )
        if restored_fault_injector is not None:
            fault_document = json.loads(
                (captured.snapshot.checkpoint_path / "runtime/fault_adapter_state.json").read_text(
                    encoding="utf-8"
                )
            )
            if fault_document.get("status") != AdapterStateStatus.STATEFUL.value:
                raise RuntimeError("B4 fault adapter state is absent from Checkpoint")
            restored_fault_injector.restore_checkpoint_state(fault_document["state"])
        restored_tools = create_local_domain_tools(
            environment=restored_environment,
            monitor=restored_monitor,
            fault_injector=restored_fault_injector,
        )
        adapter_document = json.loads(
            (captured.snapshot.checkpoint_path / "runtime/tool_adapter_state.json").read_text(
                encoding="utf-8"
            )
        )
        restore_local_tool_adapter_state(restored_tools, adapter_document["state"]["tools"])
        restored_agent, restored_model, restored_underlying = (
            create_checkpointable_benchmark_agent(
                self.settings,
                worker,
                restored_tools,
                restored.runtime_state,
                max_steps=10,
                max_model_calls=10,
                agent_name=f"b2_day5_{worker.worker_id.replace('-', '_')}",
            )
        )
        try:
            if time.monotonic() - wall_started >= 900:
                raise RuntimeError("Task wall-clock budget exhausted before restored request")
            budget = restored.manifest.runtime_state["budget_state"]
            restored_model.restore_model_calls_used(int(budget["model_calls_used"]))
            restored_agent.restore_snapshot(restored.agent_state_snapshot)
            restored_expected = expected_request_from_checkpoint(
                manifest=restored.manifest,
                snapshot=restored.agent_state_snapshot,
                runtime_state=restored.runtime_state,
            )
            restored_model.expect(restored_expected)
            history_before = len(restored.runtime_state.tool_history)
            environment_hash_before = restored_environment.state_hash()
            controlled = restored_agent.request_next_model_response()
            actual_request = restored_model.requests[0]
            if len(restored.runtime_state.tool_history) != history_before:
                raise RuntimeError("Controlled restored request executed a task tool")
            if restored_environment.state_hash() != environment_hash_before:
                raise RuntimeError("Controlled restored request changed the environment")
            if checkpoint_inventory(captured.snapshot.checkpoint_path) != checkpoint_before:
                raise RuntimeError("Restore or controlled request mutated checkpoint artifacts")
        finally:
            restored_underlying.client.close()

        hashes_match = {
            "agent_state_hash": restored.restored_agent_state_hash
            == restored.manifest.hashes["agent_state_hash"],
            "next_model_input_hash": restored.restored_next_model_input_hash
            == restored.manifest.hashes["next_model_input_hash"],
            "environment_state_hash": restored.restored_environment_hash
            == restored.manifest.hashes["environment_state_hash"],
            "runtime_state_hash": restored.restored_runtime_hash
            == restored.manifest.hashes["runtime_state_hash"],
            "combined_state_hash": restored.restored_combined_state_hash
            == restored.manifest.hashes["combined_state_hash"],
            "identity_config_hash": canonical_hash(
                json.loads(
                    (
                        captured.snapshot.checkpoint_path
                        / "identity/identity_config.json"
                    ).read_text(encoding="utf-8")
                )
            )
            == restored.manifest.hashes["identity_config_hash"],
        }
        input_match = (
            source_request.input_hash
            == actual_request.input_hash
            == restored.manifest.hashes["next_model_input_hash"]
        )
        wall_seconds = time.monotonic() - wall_started
        if wall_seconds > 900:
            raise RuntimeError("Task wall-clock budget exceeded")
        return {
            "record_type": "b2_day5_attempt",
            "protocol_id": PROTOCOL_ID,
            "run_id": self.run_id,
            "task_run_id": task_run_id,
            "task_id": task.task_id,
            "template_id": task.template_id,
            "instance_id": task.instance_id,
            "domain": task.domain.value,
            "split": task.split.value,
            "checkpoint_id": checkpoint_id,
            "worker_id": worker.worker_id,
            "endpoint": worker.base_url,
            "model_id": self.settings.model_id,
            "model_version": self.settings.model_version,
            "capture_step": captured.snapshot.manifest.identity["step_index"],
            "source_model_calls": captured.model_calls_after,
            "restored_model_calls": restored_model.model_calls_used,
            "source_tool_calls": source_history_count,
            "restore_tool_calls_added": len(restored.runtime_state.tool_history) - history_before,
            "task_wall_seconds": wall_seconds,
            "source_next_input_status": source_request.status,
            "source_next_input_hash": source_request.input_hash,
            "source_next_input_canonical": source_request.canonical_bytes.decode("utf-8"),
            "checkpoint_next_input_hash": restored.manifest.hashes["next_model_input_hash"],
            "checkpoint_next_input_canonical": canonical_json(
                restored.agent_state_snapshot.payload["canonical_next_model_input"]
            ),
            "restored_actual_input_hash": actual_request.input_hash,
            "restored_actual_input_canonical": actual_request.canonical_bytes.decode("utf-8"),
            "input_match": input_match,
            "hashes_match": hashes_match,
            "checkpoint_hashes": dict(restored.manifest.hashes),
            "checkpoint_path": str(captured.snapshot.checkpoint_path),
            "source_request_usage": {
                "prompt_tokens": captured.snapshot.manifest.runtime_state["budget_state"][
                    "prompt_tokens_used"
                ],
                "completion_tokens": captured.snapshot.manifest.runtime_state[
                    "budget_state"
                ]["completion_tokens_used"],
                "latency_seconds": source_model.requests[0].latency_seconds,
            },
            "controlled_response": serialize_chat_message(controlled.message),
            "controlled_request_usage": {
                "prompt_tokens": actual_request.prompt_tokens,
                "completion_tokens": actual_request.completion_tokens,
                "latency_seconds": actual_request.latency_seconds,
            },
            "remaining_budget_at_checkpoint": {
                "model_calls": int(budget["max_model_calls"])
                - int(budget["model_calls_used"]),
                "tool_calls": int(budget["max_tool_calls"])
                - int(budget["tool_calls_used"]),
                "tokens": (
                    int(budget["max_tokens"])
                    - int(budget["prompt_tokens_used"])
                    - int(budget["completion_tokens_used"])
                    if budget["prompt_tokens_used"] is not None
                    and budget["completion_tokens_used"] is not None
                    else None
                ),
                "latency_seconds": float(budget["max_latency_seconds"])
                - float(budget["latency_seconds_used"]),
            },
            "outcome_status": "real_qwen_request_completed",
            "passed": input_match and all(hashes_match.values()),
            "fault_id": fault_config.fault_id if fault_config is not None else None,
            "fault_type": fault_config.fault_type.value if fault_config is not None else None,
            "fault_triggered": bool(
                source_fault_injector is not None
                and any(event.triggered for event in source_fault_injector.events)
            ),
            "fault_events": (
                [event.to_dict() for event in source_fault_injector.events]
                if source_fault_injector is not None
                else []
            ),
            **self.research_flags,
        }


__all__ = [
    "Day5GateRunner",
    "EXPERIMENT_ID",
    "PROTOCOL_ID",
    "RUN_RESEARCH_FLAGS",
    "NoEligibleCheckpointError",
    "TOOL_VERSION",
    "canonical_task_catalog_hash",
    "checkpoint_inventory",
    "create_unique_run_directory",
    "expected_request_from_checkpoint",
    "public_tool_schemas",
    "select_day5_candidates",
    "sha256_file",
    "split_manifest_hash",
]
