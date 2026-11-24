"""P2-D2 real-Qwen multi-step checkpoint and independent-restore gate."""

from __future__ import annotations

import copy
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from smolagents.memory import ActionStep

from risk_agent.agent import AgentStateAdapter, RecordingModel
from risk_agent.agent.agent_factory import (
    ModelServiceError,
    create_checkpointable_benchmark_agent,
)
from risk_agent.agent.agent_state_adapter import (
    canonical_hash,
    canonical_json,
    serialize_chat_message,
    serialize_memory_step,
)
from risk_agent.agent.worker_router import RemoteModelSettings, WorkerConfig
from risk_agent.benchmark import TaskEnvironment
from risk_agent.benchmark.schema import DataSplit, TaskSpec
from risk_agent.benchmark.step_references import resolve_step_value
from risk_agent.checkpoint import (
    AdapterStateMetadata,
    AdapterStateStatus,
    CheckpointIdentityV2,
    CheckpointManager,
    EnvironmentCheckpointMetadata,
    RuntimeCheckpointMetadata,
)
from risk_agent.monitoring import (
    AuditPersistenceError,
    BudgetState,
    ExecutionMonitor,
    JsonlTraceWriter,
    ProviderAuditContext,
    ProviderResponseAudit,
    RuntimeState,
)

from .benchmark_tools import (
    CompletedToolReplayError,
    LocalBenchmarkTool,
    canonical_tool_call_signature,
    capture_local_tool_adapter_state,
    create_local_domain_tools,
    restore_local_tool_adapter_state,
)
from .day5_gate import (
    PROTOCOL_ID,
    checkpoint_inventory,
    expected_request_from_checkpoint,
)
from .decision_checkpoint import DecisionCheckpointCapture


EXPERIMENT_ID = "p2_d2_real_qwen_gate"
TOOL_VERSION = "p2-d2-local-tools-v1"
VERIFIER_VERSION = "deterministic-verifier-p2-d2-not-run"
FIXED_TASK_IDS = (
    "p2_data_region_units_sum_001",
    "p2_file_report_control_001",
    "p2_api_process_control_001",
    "p2_logic_chain_control_001",
    "p2_data_region_units_sum_002",
)
RESEARCH_FLAGS = {
    "formal_experiment": False,
    "execution_mode": "model_backed_smoke",
    "controller_type": "qwen3_model",
    "research_dataset_eligible": False,
    "y_continue": None,
    "y_retry": None,
    "y_replan": None,
}
REQUIRED_AUDIT_FILES = (
    "task_attempts.jsonl",
    "tool_trajectories.jsonl",
    "decision_checkpoints.jsonl",
    "restore_audit.jsonl",
    "model_request_audit.jsonl",
    "budget_audit.jsonl",
    "checkpoint_inventory.jsonl",
    "replay_guard_audit.jsonl",
    "branch_isolation_audit.jsonl",
)


class P2D2GateError(RuntimeError):
    """A real-Qwen Gate invariant failed without changing the frozen protocol."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


def select_p2_d2_tasks(tasks: Iterable[TaskSpec]) -> tuple[TaskSpec, ...]:
    """Select exactly the frozen five new Train tasks in their required order."""

    by_id = {task.task_id: task for task in tasks}
    missing = [task_id for task_id in FIXED_TASK_IDS if task_id not in by_id]
    if missing:
        raise P2D2GateError("configuration_failure", f"Missing fixed tasks: {missing}")
    selected = tuple(by_id[task_id] for task_id in FIXED_TASK_IDS)
    if len({task.task_id for task in selected}) != 5:
        raise P2D2GateError("configuration_failure", "Fixed task IDs are not unique")
    if any(task.split is not DataSplit.TRAIN for task in selected):
        raise P2D2GateError("configuration_failure", "Every fixed task must be Train")
    if any(not task.template_id.startswith("p2_") for task in selected):
        raise P2D2GateError(
            "configuration_failure", "Every fixed task must use a new P2-D1 template"
        )
    if {task.domain.value for task in selected} != {
        "data_analysis",
        "file_retrieval",
        "api_workflow",
        "calculation_logic",
    }:
        raise P2D2GateError("configuration_failure", "Fixed tasks do not cover four domains")
    if any(len(task.reference_steps) < 3 for task in selected):
        raise P2D2GateError("configuration_failure", "Fixed tasks must be multi-step")
    return selected


def validate_first_dependency(
    task: TaskSpec, history: list[Any]
) -> dict[str, Any]:
    """Prove the first two actual calls form the frozen sequential dependency."""

    if len(history) != 2:
        raise P2D2GateError(
            "model_behavior_failure", "Capture requires exactly two completed tool calls"
        )
    first, second = history
    if first.error_type is not None or second.error_type is not None:
        raise P2D2GateError(
            "task_execution_failure", "The first two dependent tool calls must succeed"
        )
    first_reference, second_reference = task.reference_steps[:2]
    expected_first = copy.deepcopy(dict(first_reference.arguments))
    expected_second = resolve_step_value(
        second_reference.arguments,
        {"step_1": first.tool_result},
    )
    if first.tool_name != first_reference.tool or dict(first.tool_arguments) != expected_first:
        raise P2D2GateError(
            "model_behavior_failure", "First real tool call differs from the fixed first step"
        )
    if second.tool_name != second_reference.tool:
        raise P2D2GateError(
            "model_behavior_failure", "Second real tool name differs from the dependent step"
        )
    if dict(second.tool_arguments) != expected_second:
        raise P2D2GateError(
            "model_behavior_failure",
            "Second real tool arguments were not derived from the first Observation",
        )
    return {
        "first_tool": first.tool_name,
        "first_arguments": dict(first.tool_arguments),
        "first_result_hash": canonical_hash(first.tool_result),
        "second_tool": second.tool_name,
        "second_arguments": dict(second.tool_arguments),
        "expected_second_arguments": expected_second,
        "dependency_verified": True,
        "separate_action_steps": True,
    }


def _inventory_hash(inventory: Mapping[str, Any]) -> str:
    return canonical_hash(dict(inventory))


def _response_type(message: Any) -> str:
    if getattr(message, "tool_calls", None):
        return "tool_call"
    return "text"


def _error_category(exc: BaseException) -> str:
    if isinstance(exc, AuditPersistenceError):
        return "audit_persistence_failure"
    if isinstance(exc, P2D2GateError):
        return exc.category
    if isinstance(exc, ModelServiceError):
        return "infrastructure_failure"
    if isinstance(exc, CompletedToolReplayError):
        return "replay_failure"
    name = type(exc).__name__.casefold()
    if any(token in name for token in ("connection", "timeout", "ssl", "rate")):
        return "infrastructure_failure"
    return "checkpoint_failure"


def classify_action_error(error: BaseException) -> str:
    """Separate malformed model output from a local task-tool failure."""

    if isinstance(error, AuditPersistenceError):
        return "audit_persistence_failure"
    if type(error).__name__ in {"AgentParsingError", "AgentGenerationError"}:
        return "model_behavior_failure"
    return "task_execution_failure"


class P2D2RealQwenGateRunner:
    """Run the fixed canary-first five-task Gate without provider retries."""

    def __init__(
        self,
        *,
        settings: RemoteModelSettings,
        run_directory: str | Path,
        run_id: str,
        catalog_hash: str,
        split_hash: str,
        verifier_content_hash: str,
        agent_factory: Callable[..., tuple[Any, RecordingModel, Any]] = (
            create_checkpointable_benchmark_agent
        ),
        clock: Callable[[], float] = time.monotonic,
        experiment_id: str = EXPERIMENT_ID,
        experiment_config_version: str | None = None,
        diagnostic_mode: str = "single-t1-diagnostic",
        gate_mode: str = "gate",
    ) -> None:
        if not experiment_id or not diagnostic_mode or not gate_mode:
            raise ValueError("Experiment ID and execution modes must be non-empty")
        if experiment_config_version is not None and not experiment_config_version:
            raise ValueError("Experiment config version must be non-empty when supplied")
        self.settings = settings
        self.run_directory = Path(run_directory).expanduser().resolve()
        self.run_id = run_id
        self.catalog_hash = catalog_hash
        self.split_hash = split_hash
        self.verifier_content_hash = verifier_content_hash
        self.agent_factory = agent_factory
        self.clock = clock
        self.experiment_id = experiment_id
        self.experiment_config_version = experiment_config_version
        self.diagnostic_mode = diagnostic_mode
        self.gate_mode = gate_mode
        self.manager = CheckpointManager(self.run_directory / "checkpoints")
        self.tool_writer = JsonlTraceWriter(self.run_directory / "tool_trajectories.jsonl")
        self.provider_audit = ProviderResponseAudit(self.run_directory / "audit")
        self.writers = {
            name: JsonlTraceWriter(self.run_directory / name)
            for name in REQUIRED_AUDIT_FILES
            if name != "tool_trajectories.jsonl"
        }

    def _append(self, name: str, value: Mapping[str, Any]) -> None:
        self.writers[name].append(dict(value))

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
                "split": task.split.value,
                **(
                    {"experiment_config_version": self.experiment_config_version}
                    if self.experiment_config_version is not None
                    else {}
                ),
                **RESEARCH_FLAGS,
            },
        )

    def _identity(
        self, *, task_run_id: str, worker: WorkerConfig
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

    def _provider_audit_context(
        self,
        *,
        task: TaskSpec,
        attempt_index: int,
        worker: WorkerConfig,
        phase: str,
    ) -> ProviderAuditContext:
        return ProviderAuditContext(
            run_id=self.run_id,
            task_id=task.task_id,
            attempt_index=attempt_index,
            worker_id=worker.worker_id,
            model_id=self.settings.model_id,
            model_version=self.settings.model_version,
            endpoint=worker.base_url,
            phase=phase,
        )

    @staticmethod
    def _runtime_metadata(
        runtime: RuntimeState,
        model: RecordingModel,
        tools: list[LocalBenchmarkTool],
    ) -> RuntimeCheckpointMetadata:
        return RuntimeCheckpointMetadata(
            max_model_calls=10,
            model_calls_used=model.model_calls_used,
            prompt_tokens_used=model.prompt_tokens_observed,
            completion_tokens_used=model.completion_tokens_observed,
            resource_usage={
                "model_latency_seconds": model.model_latency_seconds_observed,
                "tool_latency_seconds": sum(
                    entry.latency_seconds for entry in runtime.tool_history
                ),
                "gpu_used_for_local_execution": False,
                "unobserved": None,
            },
            tool_adapter_state=AdapterStateMetadata(
                AdapterStateStatus.STATEFUL,
                {"tools": capture_local_tool_adapter_state(tools)},
            ),
            fault_adapter_state=AdapterStateMetadata(
                AdapterStateStatus.NOT_INSTANTIATED,
                None,
            ),
            phase="after_tool_observation_before_model_generation",
            next_operation="model_generation",
        )

    @staticmethod
    def _environment_metadata(
        runtime: RuntimeState, event: ActionStep
    ) -> EnvironmentCheckpointMetadata:
        completed = [
            {
                "step_id": entry.step_id,
                "tool_name": entry.tool_name,
                "tool_arguments": entry.tool_arguments,
                "error_type": entry.error_type,
                "commit_state": (
                    "committed"
                    if entry.error_type is None and entry.tool_name == "update_order"
                    else "completed"
                    if entry.error_type is None
                    else "agent_visible_error"
                ),
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

    @staticmethod
    def _assert_usage(requests: list[Any], phase: str) -> None:
        missing = [
            index
            for index, request in enumerate(requests, 1)
            if request.status == "succeeded"
            and (request.prompt_tokens is None or request.completion_tokens is None)
        ]
        if missing:
            raise P2D2GateError(
                "budget_failure", f"Provider Usage missing in {phase} requests: {missing}"
            )

    def run_task(
        self, *, task: TaskSpec, worker: WorkerConfig, attempt_index: int
    ) -> dict[str, Any]:
        if task.task_id != FIXED_TASK_IDS[attempt_index - 1]:
            raise P2D2GateError("configuration_failure", "Task order differs from frozen Gate")
        wall_started = self.clock()
        task_run_id = f"{self.run_id}_{task.task_id}_attempt_{attempt_index:03d}"
        checkpoint_id = f"checkpoint_{task.task_id}_{attempt_index:03d}"
        source_environment = TaskEnvironment(
            self.run_directory / "source_environments" / task_run_id,
            task,
        )
        source_environment.reset()
        runtime = RuntimeState(
            seed=task.seed,
            tool_state={
                "protocol_id": PROTOCOL_ID,
                "fault_injection_enabled": False,
                **(
                    {"experiment_config_version": self.experiment_config_version}
                    if self.experiment_config_version is not None
                    else {}
                ),
            },
            budget=BudgetState(50_000, 12, 900.0),
        )
        source_tools = create_local_domain_tools(
            environment=source_environment,
            monitor=self._monitor(
                task=task,
                runtime=runtime,
                worker=worker,
                task_run_id=task_run_id,
                environment_id=f"source:{task_run_id}",
            ),
        )
        source_agent = source_model = source_underlying = source_stream = None
        try:
            source_agent, source_model, source_underlying = self.agent_factory(
                self.settings,
                worker,
                source_tools,
                runtime,
                max_steps=10,
                max_model_calls=10,
                agent_name=f"p2_d2_source_{worker.worker_id.replace('-', '_')}",
                provider_audit=self.provider_audit,
                provider_audit_context=self._provider_audit_context(
                    task=task,
                    attempt_index=attempt_index,
                    worker=worker,
                    phase="source",
                ),
            )
            source_stream = source_agent.run(task.prompt, stream=True)
            action_events: list[ActionStep] = []
            while len(action_events) < 2:
                event = next(source_stream, None)
                if event is None:
                    raise P2D2GateError(
                        "model_behavior_failure",
                        "Source Agent ended before two dependent ActionSteps",
                    )
                if not isinstance(event, ActionStep):
                    continue
                action_events.append(event)
                if event.error is not None:
                    error_type = type(event.error).__name__
                    self._append(
                        "model_request_audit.jsonl",
                        {
                            "task_id": task.task_id,
                            "task_run_id": task_run_id,
                            "worker_id": worker.worker_id,
                            "phase": "source_failure",
                            "request_count": len(source_model.requests),
                            "last_request": (
                                {
                                    "input_hash": source_model.requests[-1].input_hash,
                                    "status": source_model.requests[-1].status,
                                    "prompt_tokens": source_model.requests[-1].prompt_tokens,
                                    "completion_tokens": source_model.requests[-1].completion_tokens,
                                    "latency_seconds": source_model.requests[-1].latency_seconds,
                                }
                                if source_model.requests
                                else None
                            ),
                            "action_step": serialize_memory_step(event),
                            "error_type": error_type,
                            "error_message": str(event.error),
                            **RESEARCH_FLAGS,
                        },
                    )
                    raise P2D2GateError(
                        classify_action_error(event.error),
                        f"Source ActionStep {event.step_number} failed: {error_type}",
                    )
                if len(runtime.tool_history) != len(action_events):
                    if (
                        source_model.requests
                        and source_model.requests[-1].status
                        == "audit_persistence_failure"
                    ):
                        raise P2D2GateError(
                            "audit_persistence_failure",
                            "Provider response audit failed before tool execution",
                        )
                    raise P2D2GateError(
                        "model_behavior_failure",
                        "Dependent calls must occur in separate single-call ActionSteps",
                    )
            capture_event = action_events[-1]
            dependency = validate_first_dependency(task, runtime.tool_history)
            if source_agent.memory.steps[-1] is not capture_event:
                raise P2D2GateError(
                    "checkpoint_failure", "Capture event is not latest Agent Memory"
                )
            captured = DecisionCheckpointCapture().capture(
                event=capture_event,
                agent=source_agent,
                manager=self.manager,
                checkpoint_id=checkpoint_id,
                environment=source_environment,
                runtime_state=runtime,
                identity=self._identity(task_run_id=task_run_id, worker=worker),
                runtime_metadata=self._runtime_metadata(runtime, source_model, source_tools),
                environment_metadata=self._environment_metadata(runtime, capture_event),
            )
            verified = self.manager.verify_v2(
                checkpoint_id=checkpoint_id,
                environment=source_environment,
            )
            if runtime.agent_memory != verified.agent_state_snapshot.payload["memory"]["steps"]:
                raise P2D2GateError(
                    "checkpoint_failure", "Runtime and Agent Memory differ at capture"
                )
            expected = expected_request_from_checkpoint(
                manifest=verified.manifest,
                snapshot=verified.agent_state_snapshot,
                runtime_state=verified.runtime_state,
            )
            source_model.expect(expected)
            source_model.intercept_valid_request = True
            source_provider_calls = source_model.executed_calls
            source_history_count = len(runtime.tool_history)
            source_state_hash = source_environment.state_hash()
            next(source_stream, None)
            source_request = source_model.requests[-1]
            if source_request.status != "intercepted":
                raise P2D2GateError(
                    "input_mismatch", "Source next request was not intercepted"
                )
            if source_model.executed_calls != source_provider_calls:
                raise P2D2GateError(
                    "input_mismatch", "Source input check reached the provider"
                )
            if len(runtime.tool_history) != source_history_count:
                raise P2D2GateError("replay_failure", "Source input check ran another tool")
            if source_environment.state_hash() != source_state_hash:
                raise P2D2GateError("replay_failure", "Source input check changed state")
            self._assert_usage(source_model.requests, "source")
        finally:
            if source_stream is not None:
                source_stream.close()
            if source_underlying is not None:
                source_underlying.client.close()
        if source_model.executed_calls != source_provider_calls:
            raise P2D2GateError("input_mismatch", "Closing Source reached provider")
        if len(runtime.tool_history) != source_history_count:
            raise P2D2GateError("replay_failure", "Closing Source ran another tool")
        if source_environment.state_hash() != source_state_hash:
            raise P2D2GateError("replay_failure", "Closing Source changed state")

        checkpoint_before = checkpoint_inventory(captured.snapshot.checkpoint_path)
        checkpoint_inventory_hash = _inventory_hash(checkpoint_before)
        source_manifest = source_environment.state_manifest()
        restored_environment = TaskEnvironment(
            self.run_directory / "restored_environments" / task_run_id,
            task,
        )
        restored_environment.reset()
        restored = self.manager.restore_v2(
            checkpoint_id=checkpoint_id,
            environment=restored_environment,
        )
        with restored_environment.database() as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise P2D2GateError("restore_failure", "Restored SQLite integrity failed")
        if os.path.samefile(source_environment.database_path, restored_environment.database_path):
            raise P2D2GateError("restore_failure", "Source and Restore share SQLite")
        if restored_environment.state_manifest() != source_manifest:
            raise P2D2GateError("restore_failure", "Restored environment differs from Source")

        hashes = restored.manifest.hashes
        identity_config = json.loads(
            (captured.snapshot.checkpoint_path / "identity/identity_config.json").read_text(
                encoding="utf-8"
            )
        )
        hash_matches = {
            "agent_state_hash": restored.restored_agent_state_hash
            == hashes["agent_state_hash"],
            "next_model_input_hash": restored.restored_next_model_input_hash
            == hashes["next_model_input_hash"],
            "environment_state_hash": restored.restored_environment_hash
            == hashes["environment_state_hash"],
            "runtime_state_hash": restored.restored_runtime_hash
            == hashes["runtime_state_hash"],
            "identity_config_hash": canonical_hash(identity_config)
            == hashes["identity_config_hash"],
            "combined_state_hash": restored.restored_combined_state_hash
            == hashes["combined_state_hash"],
        }
        if not all(hash_matches.values()):
            raise P2D2GateError("restore_failure", "One or more restored hashes differ")

        prohibited = frozenset(
            canonical_tool_call_signature(entry.tool_name, entry.tool_arguments)
            for entry in restored.runtime_state.tool_history
            if entry.error_type is None
        )
        restored_tools = create_local_domain_tools(
            environment=restored_environment,
            monitor=self._monitor(
                task=task,
                runtime=restored.runtime_state,
                worker=worker,
                task_run_id=task_run_id,
                environment_id=f"restore:{task_run_id}",
            ),
            checkpoint_id=checkpoint_id,
            prohibited_call_signatures=prohibited,
        )
        adapter_document = json.loads(
            (captured.snapshot.checkpoint_path / "runtime/tool_adapter_state.json").read_text(
                encoding="utf-8"
            )
        )
        restore_local_tool_adapter_state(restored_tools, adapter_document["state"]["tools"])

        replay_entry = restored.runtime_state.tool_history[-1]
        replay_tool = next(tool for tool in restored_tools if tool.name == replay_entry.tool_name)
        replay_environment_hash = restored_environment.state_hash()
        replay_history_count = len(restored.runtime_state.tool_history)
        replay_tool_budget = restored.runtime_state.budget.tool_calls_used
        replay_blocked = False
        try:
            replay_tool.forward(**copy.deepcopy(dict(replay_entry.tool_arguments)))
        except CompletedToolReplayError:
            replay_blocked = True
        if not replay_blocked:
            raise P2D2GateError("replay_failure", "Completed tool replay was not blocked")
        if (
            restored_environment.state_hash() != replay_environment_hash
            or len(restored.runtime_state.tool_history) != replay_history_count
            or restored.runtime_state.budget.tool_calls_used != replay_tool_budget
        ):
            raise P2D2GateError("replay_failure", "Replay guard allowed state mutation")

        restored_agent = restored_model = restored_underlying = None
        try:
            restored_agent, restored_model, restored_underlying = self.agent_factory(
                self.settings,
                worker,
                restored_tools,
                restored.runtime_state,
                max_steps=10,
                max_model_calls=10,
                agent_name=f"p2_d2_restore_{worker.worker_id.replace('-', '_')}",
                provider_audit=self.provider_audit,
                provider_audit_context=self._provider_audit_context(
                    task=task,
                    attempt_index=attempt_index,
                    worker=worker,
                    phase="restore",
                ),
            )
            budget = restored.manifest.runtime_state["budget_state"]
            restored_model.restore_model_calls_used(int(budget["model_calls_used"]))
            restored_agent.restore_snapshot(restored.agent_state_snapshot)
            restored_expected = expected_request_from_checkpoint(
                manifest=restored.manifest,
                snapshot=restored.agent_state_snapshot,
                runtime_state=restored.runtime_state,
            )
            restored_model.expect(restored_expected)
            history_before_request = len(restored.runtime_state.tool_history)
            environment_before_request = restored_environment.state_hash()
            tokens_before_request = restored.runtime_state.budget.tokens_used
            model_calls_before_request = restored_model.model_calls_used
            controlled = restored_agent.request_next_model_response()
            actual_request = restored_model.requests[-1]
            self._assert_usage(restored_model.requests, "restore")
            if restored_model.model_calls_used != model_calls_before_request + 1:
                raise P2D2GateError("budget_failure", "Restore Model Call continuity failed")
            if len(restored.runtime_state.tool_history) != history_before_request:
                raise P2D2GateError("replay_failure", "Restore request executed a task tool")
            if restored_environment.state_hash() != environment_before_request:
                raise P2D2GateError("replay_failure", "Restore request changed environment")
        finally:
            if restored_underlying is not None:
                restored_underlying.client.close()

        checkpoint_after = checkpoint_inventory(captured.snapshot.checkpoint_path)
        if checkpoint_after != checkpoint_before:
            raise P2D2GateError("restore_failure", "Checkpoint artifacts were mutated")
        checkpoint_canonical = canonical_json(
            restored.agent_state_snapshot.payload["canonical_next_model_input"]
        )
        input_match = (
            source_request.canonical_bytes.decode("utf-8")
            == checkpoint_canonical
            == actual_request.canonical_bytes.decode("utf-8")
            and source_request.input_hash
            == hashes["next_model_input_hash"]
            == actual_request.input_hash
            and actual_request.public_tool_schemas == restored_expected.public_tool_schemas
        )
        if not input_match:
            raise P2D2GateError("input_mismatch", "Source/Checkpoint/Restore input differs")
        if restored_model.model_fingerprint["optional"].get("worker_id") != worker.worker_id:
            raise P2D2GateError("input_mismatch", "Restore Worker identity differs")
        wall_seconds = self.clock() - wall_started
        if wall_seconds > 900:
            raise P2D2GateError("budget_failure", "Task wall-clock budget exceeded")
        if restored.runtime_state.budget.tokens_used > 50_000:
            raise P2D2GateError("budget_failure", "Task Token budget exceeded")

        restored_manifest = restored_environment.state_manifest()
        api_write_audit: dict[str, Any] | None = None
        if task.domain.value == "api_workflow":
            api_write_audit = {
                "idempotency_key_count": len(restored_manifest["idempotency_keys"]),
                "orders": restored_manifest["orders"],
                "update_order_history_count": sum(
                    entry.tool_name == "update_order"
                    for entry in restored.runtime_state.tool_history
                ),
            }
            if (
                api_write_audit["idempotency_key_count"] != 1
                or api_write_audit["update_order_history_count"] != 1
            ):
                raise P2D2GateError("replay_failure", "API write did not occur exactly once")

        result = {
            "record_type": "p2_d2_real_qwen_attempt",
            **(
                {
                    "experiment_id": self.experiment_id,
                    "experiment_config_version": self.experiment_config_version,
                }
                if self.experiment_config_version is not None
                else {}
            ),
            "run_id": self.run_id,
            "task_run_id": task_run_id,
            "checkpoint_id": checkpoint_id,
            "task_id": task.task_id,
            "template_id": task.template_id,
            "instance_id": task.instance_id,
            "domain": task.domain.value,
            "split": task.split.value,
            "seed": task.seed,
            "worker_id": worker.worker_id,
            "endpoint": worker.base_url,
            "model_id": self.settings.model_id,
            "model_version": self.settings.model_version,
            "capture_step": captured.snapshot.manifest.identity["step_index"],
            "dependency": dependency,
            "source_model_calls": source_provider_calls,
            "source_tool_calls": source_history_count,
            "restore_model_calls": restored_model.model_calls_used,
            "restore_tool_calls_added": len(restored.runtime_state.tool_history)
            - history_before_request,
            "six_hashes": dict(hashes),
            "six_hash_matches": hash_matches,
            "exact_input_match": input_match,
            "source_next_input_status": source_request.status,
            "source_next_input_hash": source_request.input_hash,
            "checkpoint_next_input_hash": hashes["next_model_input_hash"],
            "restored_actual_input_hash": actual_request.input_hash,
            "restored_response": serialize_chat_message(controlled.message),
            "restored_response_type": _response_type(controlled.message),
            "prompt_tokens": {
                "source": source_model.prompt_tokens_observed,
                "restore": actual_request.prompt_tokens,
            },
            "completion_tokens": {
                "source": source_model.completion_tokens_observed,
                "restore": actual_request.completion_tokens,
            },
            "model_latency_seconds": {
                "source": source_model.model_latency_seconds_observed,
                "restore": actual_request.latency_seconds,
            },
            "tool_latency_seconds": sum(
                entry.latency_seconds for entry in restored.runtime_state.tool_history
            ),
            "wall_clock_seconds": wall_seconds,
            "tokens_before_restore_request": tokens_before_request,
            "tokens_after_restore_request": restored.runtime_state.budget.tokens_used,
            "checkpoint_inventory_hash": checkpoint_inventory_hash,
            "replay_guard_result": "blocked_before_execution",
            "checkpoint_immutable": checkpoint_after == checkpoint_before,
            "sqlite_integrity": integrity,
            "sqlite_independent": not os.path.samefile(
                source_environment.database_path, restored_environment.database_path
            ),
            "api_write_audit": api_write_audit,
            "fault_injection_enabled": False,
            "passed": True,
            **RESEARCH_FLAGS,
        }
        self._append(
            "decision_checkpoints.jsonl",
            {
                "task_id": task.task_id,
                "task_run_id": task_run_id,
                "checkpoint_id": checkpoint_id,
                "capture_step": result["capture_step"],
                "dependency": dependency,
                "hashes": dict(hashes),
                **RESEARCH_FLAGS,
            },
        )
        self._append(
            "restore_audit.jsonl",
            {
                "task_id": task.task_id,
                "checkpoint_id": checkpoint_id,
                "hash_matches": hash_matches,
                "sqlite_integrity": integrity,
                "sqlite_independent": result["sqlite_independent"],
                "agent_memory_restored": True,
                "runtime_restored": True,
                "environment_restored": True,
            },
        )
        self._append(
            "model_request_audit.jsonl",
            {
                "task_id": task.task_id,
                "checkpoint_id": checkpoint_id,
                "worker_id": worker.worker_id,
                "source_input_hash": source_request.input_hash,
                "checkpoint_input_hash": hashes["next_model_input_hash"],
                "restore_input_hash": actual_request.input_hash,
                "canonical_bytes_match": input_match,
                "tool_schema_match": actual_request.public_tool_schemas
                == restored_expected.public_tool_schemas,
                "generation_config_match": True,
                "model_id_match": True,
                "response": serialize_chat_message(controlled.message),
                "response_type": result["restored_response_type"],
                "prompt_tokens": actual_request.prompt_tokens,
                "completion_tokens": actual_request.completion_tokens,
                "latency_seconds": actual_request.latency_seconds,
            },
        )
        self._append(
            "budget_audit.jsonl",
            {
                "task_id": task.task_id,
                "checkpoint_budget": dict(budget),
                "source_model_calls": source_provider_calls,
                "restore_model_calls": restored_model.model_calls_used,
                "tokens_before_restore_request": tokens_before_request,
                "tokens_after_restore_request": restored.runtime_state.budget.tokens_used,
                "tool_calls_after_restore_request": restored.runtime_state.budget.tool_calls_used,
                "wall_clock_seconds": wall_seconds,
                "usage_complete": True,
                "automatic_model_retries": 0,
            },
        )
        self._append(
            "checkpoint_inventory.jsonl",
            {
                "task_id": task.task_id,
                "checkpoint_id": checkpoint_id,
                "inventory_hash": checkpoint_inventory_hash,
                "file_count": len(checkpoint_before),
                "before": checkpoint_before,
                "unchanged": True,
            },
        )
        self._append(
            "replay_guard_audit.jsonl",
            {
                "task_id": task.task_id,
                "checkpoint_id": checkpoint_id,
                "replayed_tool": replay_entry.tool_name,
                "replayed_arguments_hash": canonical_hash(replay_entry.tool_arguments),
                "blocked": replay_blocked,
                "environment_unchanged": True,
                "history_unchanged": True,
                "tool_budget_unchanged": True,
                "api_write_audit": api_write_audit,
            },
        )
        self._append(
            "branch_isolation_audit.jsonl",
            {
                "task_id": task.task_id,
                "source_workspace": str(source_environment.workspace),
                "restored_workspace": str(restored_environment.workspace),
                "source_database": str(source_environment.database_path),
                "restored_database": str(restored_environment.database_path),
                "workspace_distinct": source_environment.workspace
                != restored_environment.workspace,
                "sqlite_distinct": result["sqlite_independent"],
                "logical_state_equal_at_restore": True,
                "checkpoint_immutable": True,
            },
        )
        return result

    def run_single_t1(
        self,
        *,
        tasks: Iterable[TaskSpec],
        worker: WorkerConfig,
    ) -> dict[str, Any]:
        """Run only the frozen T1 diagnostic and stop after its one attempt.

        A successful T1 is allowed to complete the existing checkpoint/restore path in
        :meth:`run_task`; a failed T1 is recorded and returned without replacement,
        retry, or advancement to T2. The five-task Gate remains a separate entry point.
        """

        selected = select_p2_d2_tasks(tasks)
        task = selected[0]
        if task.task_id != FIXED_TASK_IDS[0]:
            raise P2D2GateError(
                "configuration_failure", "Single diagnostic selection is not frozen T1"
            )
        if worker.worker_id != "worker-0":
            raise P2D2GateError(
                "configuration_failure", "T1 diagnostic requires worker-0"
            )
        result: dict[str, Any] | None = None
        failure: dict[str, Any] | None = None
        try:
            result = self.run_task(task=task, worker=worker, attempt_index=1)
            self._append("task_attempts.jsonl", result)
        except Exception as exc:
            failure = {
                "record_type": "p2_d2_t1_diagnostic_attempt",
                **(
                    {
                        "experiment_id": self.experiment_id,
                        "experiment_config_version": self.experiment_config_version,
                    }
                    if self.experiment_config_version is not None
                    else {}
                ),
                "run_id": self.run_id,
                "task_id": task.task_id,
                "attempt_index": 1,
                "worker_id": worker.worker_id,
                "passed": False,
                "failure_category": _error_category(exc),
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "automatic_retry_performed": False,
                "replacement_task_used": False,
                **RESEARCH_FLAGS,
            }
            self._append("task_attempts.jsonl", failure)
            for name in REQUIRED_AUDIT_FILES:
                path = self.run_directory / name
                if name in {"task_attempts.jsonl", "tool_trajectories.jsonl"}:
                    continue
                if not path.exists():
                    self._append(
                        name,
                        {
                            "run_id": self.run_id,
                            "task_id": task.task_id,
                            "attempt_index": 1,
                            "status": "not_reached",
                            "reason": "single_t1_diagnostic_failed",
                            "failure_category": failure["failure_category"],
                            **RESEARCH_FLAGS,
                        },
                    )
        passed = result is not None and failure is None
        summary = {
            "run_id": self.run_id,
            "mode": self.diagnostic_mode,
            **(
                {
                    "experiment_id": self.experiment_id,
                    "experiment_config_version": self.experiment_config_version,
                }
                if self.experiment_config_version is not None
                else {}
            ),
            "status": "PASS" if passed else "BLOCKED",
            "passed": passed,
            "diagnostic_task_count": 1,
            "attempted_task_ids": [task.task_id],
            "worker_ids": [worker.worker_id],
            "attempt_count": 1,
            "automatic_model_retries": 0,
            "replacement_tasks": 0,
            "t2_t5_started": False,
            "checkpoint_created": bool(result),
            "real_restore_requests": 1 if result is not None else 0,
            "failure": failure,
            "result": result,
            **RESEARCH_FLAGS,
        }
        (self.run_directory / "gate_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return summary

    def run_fixed_gate(
        self,
        *,
        tasks: Iterable[TaskSpec],
        workers: tuple[WorkerConfig, ...],
    ) -> dict[str, Any]:
        selected = select_p2_d2_tasks(tasks)
        if self.experiment_config_version is None and len(workers) < 2:
            raise P2D2GateError("configuration_failure", "Two Workers are required")
        if self.experiment_config_version is not None and tuple(
            worker.worker_id for worker in workers
        ) != ("worker-0", "worker-1"):
            raise P2D2GateError(
                "configuration_failure",
                "Frozen Gate requires exactly worker-0 then worker-1",
            )
        results: list[dict[str, Any]] = []
        failure: dict[str, Any] | None = None
        for index, task in enumerate(selected, 1):
            worker = workers[(index - 1) % len(workers)]
            try:
                result = self.run_task(task=task, worker=worker, attempt_index=index)
                results.append(result)
                self._append("task_attempts.jsonl", result)
            except Exception as exc:
                failure = {
                    "record_type": "p2_d2_real_qwen_attempt",
                    **(
                        {
                            "experiment_id": self.experiment_id,
                            "experiment_config_version": self.experiment_config_version,
                        }
                        if self.experiment_config_version is not None
                        else {}
                    ),
                    "run_id": self.run_id,
                    "task_id": task.task_id,
                    "attempt_index": index,
                    "worker_id": worker.worker_id,
                    "passed": False,
                    "failure_category": _error_category(exc),
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    **(
                        {
                            "automatic_retry_performed": False,
                            "replacement_task_used": False,
                            "worker_failover_performed": False,
                        }
                        if self.experiment_config_version is not None
                        else {}
                    ),
                    **RESEARCH_FLAGS,
                }
                self._append("task_attempts.jsonl", failure)
                for name in REQUIRED_AUDIT_FILES:
                    path = self.run_directory / name
                    if name in {"task_attempts.jsonl", "tool_trajectories.jsonl"}:
                        continue
                    if not path.exists():
                        self._append(
                            name,
                            {
                                "run_id": self.run_id,
                                "task_id": task.task_id,
                                "attempt_index": index,
                                "status": "not_reached",
                                "reason": "canary_or_prior_task_failed",
                                "failure_category": failure["failure_category"],
                                **RESEARCH_FLAGS,
                            },
                        )
                break
        passed = len(results) == 5 and failure is None
        versioned_summary: dict[str, Any] = {}
        if self.experiment_config_version is not None:
            task_costs = [
                {
                    "task_id": item["task_id"],
                    "worker_id": item["worker_id"],
                    "prompt_tokens": sum(item["prompt_tokens"].values()),
                    "completion_tokens": sum(item["completion_tokens"].values()),
                    "model_latency_seconds": sum(
                        item["model_latency_seconds"].values()
                    ),
                    "tool_latency_seconds": item["tool_latency_seconds"],
                    "wall_clock_seconds": item["wall_clock_seconds"],
                }
                for item in results
            ]
            versioned_summary = {
                "mode": self.gate_mode,
                "experiment_id": self.experiment_id,
                "experiment_config_version": self.experiment_config_version,
                "attempted_task_ids": [item["task_id"] for item in results]
                + ([failure["task_id"]] if failure is not None else []),
                "attempt_count": len(results) + (1 if failure is not None else 0),
                "worker_schedule": [
                    workers[(index - 1) % len(workers)].worker_id
                    for index in range(1, len(results) + (1 if failure else 0) + 1)
                ],
                "automatic_model_retries": 0,
                "replacement_tasks": 0,
                "worker_failovers": 0,
                "stop_on_first_failure": True,
                "task_costs": task_costs,
                "gate_cost_totals": {
                    key: sum(item[key] for item in task_costs)
                    for key in (
                        "prompt_tokens",
                        "completion_tokens",
                        "model_latency_seconds",
                        "tool_latency_seconds",
                        "wall_clock_seconds",
                    )
                },
            }
        summary = {
            "run_id": self.run_id,
            **versioned_summary,
            "status": "PASS" if passed else "BLOCKED",
            "passed": passed,
            "fixed_task_count": 5,
            "completed_task_count": len(results),
            "task_ids": [item["task_id"] for item in results],
            "domains": sorted({item["domain"] for item in results}),
            "checkpoints": len(results),
            "six_hash_matches": sum(
                all(item["six_hash_matches"].values()) for item in results
            ),
            "exact_input_matches": sum(item["exact_input_match"] for item in results),
            "real_restore_requests": len(results),
            "replay_failures": sum(
                item["replay_guard_result"] != "blocked_before_execution"
                for item in results
            ),
            "usage_missing_requests": sum(
                item["prompt_tokens"]["restore"] is None
                or item["completion_tokens"]["restore"] is None
                for item in results
            ),
            "failure": failure,
            "canary_passed": bool(results and results[0]["task_id"] == FIXED_TASK_IDS[0]),
            **RESEARCH_FLAGS,
        }
        (self.run_directory / "gate_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return summary


__all__ = [
    "EXPERIMENT_ID",
    "FIXED_TASK_IDS",
    "P2D2GateError",
    "P2D2RealQwenGateRunner",
    "RESEARCH_FLAGS",
    "REQUIRED_AUDIT_FILES",
    "classify_action_error",
    "select_p2_d2_tasks",
    "validate_first_dependency",
]
