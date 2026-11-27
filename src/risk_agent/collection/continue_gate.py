"""B3 model-backed CONTINUE orchestration over immutable B2 Checkpoint v2 inputs."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from smolagents.agents import AgentGenerationError
from smolagents.memory import ActionStep, FinalAnswerStep, PlanningStep

from risk_agent.agent import (
    AgentStateAdapter,
    CheckpointResumeError,
    ModelRequestRejected,
    RecordingModel,
)
from risk_agent.agent.agent_factory import (
    ModelErrorCategory,
    classify_model_exception,
    create_checkpointable_benchmark_agent,
)
from risk_agent.agent.agent_state_adapter import canonical_hash, serialize_memory_step
from risk_agent.agent.worker_router import RemoteModelSettings, WorkerConfig
from risk_agent.benchmark import TaskEnvironment
from risk_agent.benchmark.schema import TaskSpec
from risk_agent.checkpoint import CheckpointManager, RestoreV2Result, VerifyV2Result
from risk_agent.faults import CommitStatus, FaultInjector, describe_operation
from risk_agent.monitoring import ExecutionMonitor, JsonlTraceWriter, RuntimeState

from .benchmark_tools import (
    CompletedToolReplayError,
    canonical_tool_call_signature,
    create_local_domain_tools,
    restore_local_tool_adapter_state,
)
from .continue_outcome import (
    ContinueLineage,
    ContinueOutcome,
    ContinueOutcomeEvaluator,
    ContinueTermination,
)
from .day5_gate import (
    PROTOCOL_ID,
    TOOL_VERSION,
    checkpoint_inventory,
    expected_request_from_checkpoint,
    sha256_file,
)


EXPERIMENT_ID = "b3_model_backed_continue_gate"
LEGACY_REPLAY_POLICY = "historical-signatures-v1"
READONLY_REFRESH_POLICY = "new-readonly-observations-v1"
B3_RESEARCH_FLAGS = {
    "formal_experiment": False,
    "execution_mode": "model_backed_smoke",
    "controller_type": "qwen3_model",
    "research_dataset_eligible": False,
}


@dataclass(frozen=True, slots=True)
class CheckpointAuditRecord:
    checkpoint_id: str
    checkpoint_path: str
    task_id: str
    task_run_id: str
    worker_id: str
    schema_version: str
    manifest_sha256: str
    artifact_count: int
    inventory_file_count: int
    step_number: int
    next_step_number: int
    model_calls_used: int
    tool_calls_used: int
    hashes: Mapping[str, str]
    all_hashes_verified: bool
    checkpoint_inventory: Mapping[str, Mapping[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CheckpointRunAudit:
    checkpoint_run: str
    source_run_id: str
    records: tuple[CheckpointAuditRecord, ...]
    gate_status: str
    controller_type: str
    execution_mode: str
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ContinueGateError(RuntimeError):
    """A frozen B3 prerequisite or complete-chain invariant failed."""


def _prohibited_source_signatures(
    runtime_state: RuntimeState, *, replay_policy: str = LEGACY_REPLAY_POLICY
) -> frozenset[str]:
    """Protect completed/unsafe source calls without treating every error as success.

    A failed read-only operation, or a write proven not to have started/committed, is
    not an already completed source call.  Writes with missing, committed, or unknown
    commit evidence remain prohibited conservatively.
    """

    if replay_policy not in {LEGACY_REPLAY_POLICY, READONLY_REFRESH_POLICY}:
        raise ValueError("Unknown CONTINUE replay policy")
    signatures: set[str] = set()
    last_failure = runtime_state.tool_state.get("last_failure")
    for entry in runtime_state.tool_history:
        if replay_policy == READONLY_REFRESH_POLICY and describe_operation(
            entry.tool_name, entry.tool_arguments
        ).read_only:
            continue
        signature = canonical_tool_call_signature(
            entry.tool_name, entry.tool_arguments
        )
        if entry.error_type is None:
            signatures.add(signature)
            continue
        operation = describe_operation(entry.tool_name, entry.tool_arguments)
        if operation.read_only:
            continue
        commit_status: str | None = None
        if isinstance(last_failure, Mapping) and (
            last_failure.get("tool_name") == entry.tool_name
        ):
            raw_status = last_failure.get("commit_status")
            if isinstance(raw_status, str):
                commit_status = raw_status
        if commit_status not in {
            CommitStatus.NOT_STARTED.value,
            CommitStatus.NOT_COMMITTED.value,
        }:
            signatures.add(signature)
    return frozenset(signatures)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContinueGateError(f"Expected JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ContinueGateError(f"Expected JSON object at {path}:{line_number}")
        rows.append(value)
    return rows


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def audit_checkpoint_run(
    *,
    checkpoint_run: str | Path,
    tasks: Iterable[TaskSpec],
    audit_workspace: str | Path,
) -> CheckpointRunAudit:
    """Verify the fixed five B2 Checkpoints without restoring or mutating them."""

    run = Path(checkpoint_run).expanduser().resolve()
    required = (
        "run_manifest.json",
        "checkpoint_index.jsonl",
        "source_attempts.jsonl",
        "restore_audit.jsonl",
        "model_request_audit.jsonl",
        "budget_audit.jsonl",
        "gate_summary.json",
    )
    missing = [name for name in required if not (run / name).is_file()]
    if missing:
        raise ContinueGateError(f"B2 checkpoint run is missing: {missing}")
    manifest = _read_json(run / "run_manifest.json")
    summary = _read_json(run / "gate_summary.json")
    index = _read_jsonl(run / "checkpoint_index.jsonl")
    attempts = _read_jsonl(run / "source_attempts.jsonl")
    if len(index) != 5:
        raise ContinueGateError(f"B3 requires exactly five indexed Checkpoints; found {len(index)}")
    for field in ("task_id", "task_run_id", "checkpoint_id"):
        values = [str(row[field]) for row in index]
        if len(values) != len(set(values)):
            raise ContinueGateError(f"Duplicate {field} in checkpoint index")
    if summary.get("status") != "PASS" or summary.get("passed_count") != 5:
        raise ContinueGateError("B2 five-Checkpoint Gate is not PASS")
    if manifest.get("execution_mode") != "model_backed_smoke":
        raise ContinueGateError("B2 execution_mode is not model_backed_smoke")
    if manifest.get("controller_type") != "qwen3_model":
        raise ContinueGateError("B2 controller_type is not qwen3_model")
    passed_attempts = {
        str(row.get("checkpoint_id")): row
        for row in attempts
        if row.get("passed") is True
    }
    task_map = {task.task_id: task for task in tasks}
    workspace = Path(audit_workspace).expanduser().resolve()
    records: list[CheckpointAuditRecord] = []
    checkpoint_root = (run / "checkpoints").resolve()
    manager = CheckpointManager(checkpoint_root)
    for row in index:
        checkpoint_id = str(row["checkpoint_id"])
        task_id = str(row["task_id"])
        task = task_map.get(task_id)
        if task is None:
            raise ContinueGateError(f"Checkpoint task is absent from frozen catalog: {task_id}")
        checkpoint_path = Path(str(row["checkpoint_path"])).expanduser().resolve()
        if checkpoint_path != checkpoint_root / checkpoint_id:
            raise ContinueGateError(f"Checkpoint path escapes fixed B2 run: {checkpoint_id}")
        if checkpoint_id not in passed_attempts:
            raise ContinueGateError(f"Checkpoint has no passed B2 source attempt: {checkpoint_id}")
        environment = TaskEnvironment(workspace / checkpoint_id, task)
        verified = manager.verify_v2(
            checkpoint_id=checkpoint_id,
            environment=environment,
        )
        if verified.manifest.schema_version != "2.0.0":
            raise ContinueGateError(f"Checkpoint is not schema v2.0.0: {checkpoint_id}")
        identity = verified.manifest.identity
        if identity["controller_type"] != "qwen3_model":
            raise ContinueGateError(f"Checkpoint controller mismatch: {checkpoint_id}")
        if identity["execution_mode"] != "model_backed_smoke":
            raise ContinueGateError(f"Checkpoint mode mismatch: {checkpoint_id}")
        inventory = checkpoint_inventory(checkpoint_path)
        hashes = dict(verified.manifest.hashes)
        all_hashes_verified = hashes == dict(row["hashes"])
        if not all_hashes_verified:
            raise ContinueGateError(f"Checkpoint index Hash mismatch: {checkpoint_id}")
        records.append(
            CheckpointAuditRecord(
                checkpoint_id=checkpoint_id,
                checkpoint_path=str(checkpoint_path),
                task_id=task_id,
                task_run_id=str(row["task_run_id"]),
                worker_id=str(identity["worker_id"]),
                schema_version=verified.manifest.schema_version,
                manifest_sha256=sha256_file(checkpoint_path / "manifest.json"),
                artifact_count=len(verified.manifest.artifacts),
                inventory_file_count=len(inventory),
                step_number=int(verified.manifest.agent_context["step_number"]),
                next_step_number=int(verified.manifest.agent_context["next_step_number"]),
                model_calls_used=int(
                    verified.manifest.runtime_state["budget_state"]["model_calls_used"]
                ),
                tool_calls_used=int(
                    verified.manifest.runtime_state["budget_state"]["tool_calls_used"]
                ),
                hashes=hashes,
                all_hashes_verified=True,
                checkpoint_inventory=inventory,
            )
        )
    return CheckpointRunAudit(
        checkpoint_run=str(run),
        source_run_id=str(manifest["run_id"]),
        records=tuple(records),
        gate_status=str(summary["status"]),
        controller_type=str(manifest["controller_type"]),
        execution_mode=str(manifest["execution_mode"]),
        passed=True,
    )


class _TeeWriter:
    def __init__(self, *writers: JsonlTraceWriter) -> None:
        self.writers = writers

    def append(self, record: Mapping[str, object]) -> None:
        for writer in self.writers:
            writer.append(record)


class ContinueGateRunner:
    """Restore one B2 Checkpoint and consume its full unassisted Agent stream."""

    def __init__(
        self,
        *,
        settings: RemoteModelSettings,
        run_directory: str | Path,
        checkpoint_run: str | Path,
        run_id: str,
        tasks: Iterable[TaskSpec],
        agent_factory: Callable[..., tuple[Any, RecordingModel, Any]] = (
            create_checkpointable_benchmark_agent
        ),
        outcome_evaluator: ContinueOutcomeEvaluator | None = None,
        fault_injector_factory: (
            Callable[[CheckpointAuditRecord, Path], FaultInjector | None] | None
        ) = None,
        experiment_id: str = EXPERIMENT_ID,
        clock: Callable[[], float] = time.monotonic,
        replay_policy: str = LEGACY_REPLAY_POLICY,
        protocol_id: str = PROTOCOL_ID,
    ) -> None:
        if replay_policy not in {LEGACY_REPLAY_POLICY, READONLY_REFRESH_POLICY}:
            raise ValueError("Unknown CONTINUE replay policy")
        self.replay_policy = replay_policy
        self.protocol_id = protocol_id
        self.settings = settings
        self.run_directory = Path(run_directory).expanduser().resolve()
        self.checkpoint_run = Path(checkpoint_run).expanduser().resolve()
        self.run_id = run_id
        self.tasks = {task.task_id: task for task in tasks}
        self.agent_factory = agent_factory
        self.outcome_evaluator = outcome_evaluator or ContinueOutcomeEvaluator()
        self.fault_injector_factory = fault_injector_factory
        self.experiment_id = experiment_id
        self.clock = clock
        self.manager = CheckpointManager(self.checkpoint_run / "checkpoints")
        self.model_writer = JsonlTraceWriter(self.run_directory / "model_events.jsonl")
        self.tool_writer = JsonlTraceWriter(self.run_directory / "tool_events.jsonl")
        self.visible_writer = JsonlTraceWriter(
            self.run_directory / "agent_visible_events.jsonl"
        )
        self.outcome_writer = JsonlTraceWriter(
            self.run_directory / "continue_outcomes.jsonl"
        )
        self.verifier_writer = JsonlTraceWriter(
            self.run_directory / "verifier_outcomes.jsonl"
        )
        self.budget_writer = JsonlTraceWriter(self.run_directory / "budget_audit.jsonl")

    @classmethod
    def from_frozen_collection(
        cls, *, root: str | Path, run_directory: str | Path,
        checkpoint_run: str | Path, run_id: str, **kwargs: Any,
    ) -> ContinueGateRunner:
        """Load the pinned revision before constructing any model client."""
        from .protocol_freeze import load_frozen_collection

        root = Path(root).resolve()
        profile = load_frozen_collection(root)
        return cls(
            settings=RemoteModelSettings.from_yaml(
                root / profile.config["dependencies"]["remote_model_config"]
            ),
            tasks=profile.tasks, run_directory=run_directory,
            checkpoint_run=checkpoint_run, run_id=run_id,
            replay_policy=profile.config["continue"]["replay_policy"],
            protocol_id=profile.config["protocol_id"], **kwargs,
        )

    def _validate_identity(
        self, verified: VerifyV2Result, worker: WorkerConfig
    ) -> None:
        identity = verified.manifest.identity
        if worker.worker_id != identity["worker_id"]:
            raise ContinueGateError("Original Checkpoint Worker is required; failover is forbidden")
        if self.settings.model_id != identity["model_id"]:
            raise ContinueGateError("Configured Model ID differs from Checkpoint")
        if self.settings.model_version != identity["model_version"]:
            raise ContinueGateError("Configured Model Version differs from Checkpoint")
        if self.settings.prompt_version != identity["prompt_version"]:
            raise ContinueGateError("Configured Prompt Version differs from Checkpoint")
        if identity["tool_version"] != TOOL_VERSION:
            raise ContinueGateError("Tool Version differs from Checkpoint")
        identity_config = _read_json(
            verified.checkpoint_path / "identity/identity_config.json"
        )
        if identity_config.get("verifier_content_hash") != sha256_file(
            "src/risk_agent/benchmark/verifier.py"
        ):
            raise ContinueGateError("Verifier code Hash differs from Checkpoint identity")

    def _monitor(
        self,
        *,
        task: TaskSpec,
        restored: RestoreV2Result,
        worker: WorkerConfig,
        audit: CheckpointAuditRecord,
        branch_id: str,
        branch_attempt_id: str,
        branch_writer: JsonlTraceWriter,
    ) -> ExecutionMonitor:
        return ExecutionMonitor(
            writer=_TeeWriter(branch_writer),  # type: ignore[arg-type]
            runtime_state=restored.runtime_state,
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
            environment_id=f"b3:{branch_attempt_id}",
            additional_context={
                "protocol_id": self.protocol_id,
                "replay_policy": self.replay_policy,
                "source_run_id": audit.task_run_id,
                "checkpoint_id": audit.checkpoint_id,
                "branch_id": branch_id,
                "branch_attempt_id": branch_attempt_id,
                **B3_RESEARCH_FLAGS,
            },
        )

    @staticmethod
    def _termination_for_exception(
        exc: BaseException, model: RecordingModel
    ) -> ContinueTermination:
        text = " ".join(str(item) for item in _exception_chain(exc)).casefold()
        budget_markers = (
            "budget",
            "max steps",
            "remaining_steps",
            "token_usage_unavailable",
        )
        if isinstance(exc, ModelRequestRejected) or any(
            request.status == "rejected" for request in model.requests[-1:]
        ):
            if any(marker in text for marker in budget_markers):
                return ContinueTermination.BUDGET_EXHAUSTION
            return ContinueTermination.UNVERIFIABLE
        category = classify_model_exception(exc)
        if category is not ModelErrorCategory.UNKNOWN:
            return ContinueTermination.MODEL_SERVICE_FAILURE
        if isinstance(exc, AgentGenerationError) and any(
            request.status == "failed" for request in model.requests[-1:]
        ):
            return ContinueTermination.MODEL_SERVICE_FAILURE
        if isinstance(exc, CheckpointResumeError):
            return ContinueTermination.UNVERIFIABLE
        return ContinueTermination.AGENT_ERROR

    def run_checkpoint(
        self,
        *,
        audit: CheckpointAuditRecord,
        worker: WorkerConfig,
        branch_attempt_index: int,
    ) -> dict[str, Any]:
        if branch_attempt_index < 1:
            raise ValueError("branch_attempt_index must be positive")
        task = self.tasks[audit.task_id]
        branch_id = f"continue_{audit.checkpoint_id}"
        branch_attempt_id = f"{branch_id}_attempt_{branch_attempt_index:03d}"
        branch_root = (
            self.run_directory
            / "branches"
            / audit.checkpoint_id
            / branch_attempt_id
        )
        branch_root.mkdir(parents=True, exist_ok=False)
        checkpoint_path = Path(audit.checkpoint_path)
        checkpoint_before = checkpoint_inventory(checkpoint_path)
        verify_environment = TaskEnvironment(branch_root / "verify", task)
        verified = self.manager.verify_v2(
            checkpoint_id=audit.checkpoint_id,
            environment=verify_environment,
        )
        self._validate_identity(verified, worker)
        environment = TaskEnvironment(branch_root / "workspace", task)
        restored = self.manager.restore_v2(
            checkpoint_id=audit.checkpoint_id,
            environment=environment,
        )
        restored_environment_state_hash = environment.state_hash()
        source_signatures = _prohibited_source_signatures(
            restored.runtime_state, replay_policy=self.replay_policy
        )
        raw_tool_writer = JsonlTraceWriter(branch_root / "trajectory.jsonl")
        monitor = self._monitor(
            task=task,
            restored=restored,
            worker=worker,
            audit=audit,
            branch_id=branch_id,
            branch_attempt_id=branch_attempt_id,
            branch_writer=raw_tool_writer,
        )
        fault_injector = (
            self.fault_injector_factory(audit, checkpoint_path)
            if self.fault_injector_factory is not None
            else None
        )
        fault_document = _read_json(
            checkpoint_path / "runtime/fault_adapter_state.json"
        )
        if fault_document.get("status") == "stateful":
            if fault_injector is None:
                raise ContinueGateError(
                    "Checkpoint contains fault adapter state but no restore factory"
                )
            state = fault_document.get("state")
            if not isinstance(state, Mapping):
                raise ContinueGateError("Checkpoint fault adapter state is malformed")
            fault_injector.restore_checkpoint_state(state)
        elif fault_injector is not None:
            raise ContinueGateError(
                "Fault restore factory was supplied for a Checkpoint without adapter state"
            )
        tools = create_local_domain_tools(
            environment=environment,
            monitor=monitor,
            checkpoint_id=audit.checkpoint_id,
            prohibited_call_signatures=source_signatures,
            fault_injector=fault_injector,
        )
        adapter_document = _read_json(
            checkpoint_path / "runtime/tool_adapter_state.json"
        )
        restore_local_tool_adapter_state(tools, adapter_document["state"]["tools"])
        agent, model, underlying = self.agent_factory(
            self.settings,
            worker,
            tools,
            restored.runtime_state,
            max_steps=int(restored.manifest.agent_context["max_steps"]),
            max_model_calls=int(
                restored.manifest.runtime_state["budget_state"]["max_model_calls"]
            ),
            agent_name=f"b3_{worker.worker_id.replace('-', '_')}",
        )
        budget = restored.manifest.runtime_state["budget_state"]
        model.restore_model_calls_used(int(budget["model_calls_used"]))
        agent.restore_snapshot(restored.agent_state_snapshot)
        if self.replay_policy == READONLY_REFRESH_POLICY:
            agent.protect_restored_tool_call_ids()
        model.expect(
            expected_request_from_checkpoint(
                manifest=restored.manifest,
                snapshot=restored.agent_state_snapshot,
                runtime_state=restored.runtime_state,
            )
        )
        history_before = len(restored.runtime_state.tool_history)
        events: list[dict[str, Any]] = []
        candidate_result: Any = None
        termination = ContinueTermination.AGENT_ERROR
        replay_detected = False
        stream = None
        started = self.clock()
        try:
            stream = agent.resume(stream=True)
            for event in stream:
                event_id = f"agent_event_{uuid.uuid4().hex}"
                if isinstance(event, (ActionStep, PlanningStep)):
                    payload = serialize_memory_step(event)
                elif isinstance(event, FinalAnswerStep):
                    payload = {"type": "final_answer", "output": _json_safe(event.output)}
                else:
                    payload = {"type": type(event).__name__, "value": _json_safe(event)}
                event_record = {
                    "agent_visible_event_id": event_id,
                    "run_id": self.run_id,
                    "checkpoint_id": audit.checkpoint_id,
                    "branch_id": branch_id,
                    "branch_attempt_id": branch_attempt_id,
                    "event": payload,
                    **B3_RESEARCH_FLAGS,
                }
                events.append(event_record)
                self.visible_writer.append(event_record)
                if isinstance(event, ActionStep) and event.error is not None:
                    if "checkpoint_resume_replay_detected" in str(event.error):
                        replay_detected = True
                        termination = ContinueTermination.AGENT_ERROR
                        break
                if isinstance(event, FinalAnswerStep):
                    candidate_result = event.output
                    final_actions = [
                        step
                        for step in agent.memory.steps
                        if isinstance(step, ActionStep) and step.is_final_answer
                    ]
                    termination = (
                        ContinueTermination.FINAL_ANSWER
                        if final_actions
                        else ContinueTermination.BUDGET_EXHAUSTION
                    )
                if self.clock() - started >= float(budget["max_latency_seconds"]):
                    termination = ContinueTermination.BUDGET_EXHAUSTION
                    break
        except KeyboardInterrupt:
            termination = ContinueTermination.MANUAL_ABORT
        except Exception as exc:
            termination = self._termination_for_exception(exc, model)
            events.append(
                {
                    "agent_visible_event_id": f"agent_event_{uuid.uuid4().hex}",
                    "run_id": self.run_id,
                    "checkpoint_id": audit.checkpoint_id,
                    "branch_id": branch_id,
                    "branch_attempt_id": branch_attempt_id,
                    "event": {
                        "type": "execution_exception",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                    **B3_RESEARCH_FLAGS,
                }
            )
            self.visible_writer.append(events[-1])
        finally:
            if stream is not None:
                stream.close()
            client = getattr(underlying, "client", None)
            if client is not None and hasattr(client, "close"):
                client.close()

        wall_seconds = self.clock() - started
        checkpoint_after = checkpoint_inventory(checkpoint_path)
        checkpoint_immutable = checkpoint_after == checkpoint_before
        if not checkpoint_immutable:
            termination = ContinueTermination.UNVERIFIABLE
        new_history = restored.runtime_state.tool_history[history_before:]
        tool_error_observed = any(entry.error_type is not None for entry in new_history)
        model_event_ids: list[str] = []
        for request in model.requests:
            model_event_id = f"model_request_{uuid.uuid4().hex}"
            model_event_ids.append(model_event_id)
            record = {
                "model_request_id": model_event_id,
                "run_id": self.run_id,
                "checkpoint_id": audit.checkpoint_id,
                "branch_id": branch_id,
                "branch_attempt_id": branch_attempt_id,
                "worker_id": worker.worker_id,
                "endpoint": worker.base_url,
                "model_id": self.settings.model_id,
                "model_version": self.settings.model_version,
                "input_hash": request.input_hash,
                "status": request.status,
                "difference": request.difference,
                "prompt_tokens": request.prompt_tokens,
                "completion_tokens": request.completion_tokens,
                "usage_status": (
                    "observed"
                    if request.prompt_tokens is not None
                    and request.completion_tokens is not None
                    else "unavailable"
                ),
                "latency_seconds": request.latency_seconds,
                "first_request_contract_verified": request.first_request_contract_verified,
                **B3_RESEARCH_FLAGS,
            }
            self.model_writer.append(record)
            JsonlTraceWriter(branch_root / "model_events.jsonl").append(record)
        pending_calls: list[Any] = []
        captured_step = int(restored.manifest.agent_context["step_number"])
        for step in agent.memory.steps:
            if not isinstance(step, ActionStep) or step.step_number <= captured_step:
                continue
            pending_calls.extend(
                call for call in (step.tool_calls or []) if call.name != "final_answer"
            )
        tool_event_ids: list[str] = []
        matched_call_indexes: set[int] = set()
        for entry in new_history:
            tool_event_id = f"tool_event_{uuid.uuid4().hex}"
            tool_event_ids.append(tool_event_id)
            entry_signature = canonical_tool_call_signature(
                entry.tool_name, entry.tool_arguments
            )
            tool_call_id = None
            for call_index, call in enumerate(pending_calls):
                if call_index in matched_call_indexes:
                    continue
                if canonical_tool_call_signature(call.name, call.arguments) == entry_signature:
                    matched_call_indexes.add(call_index)
                    tool_call_id = call.id
                    break
            record = {
                "tool_event_id": tool_event_id,
                "tool_call_id": tool_call_id,
                "run_id": self.run_id,
                "checkpoint_id": audit.checkpoint_id,
                "branch_id": branch_id,
                "branch_attempt_id": branch_attempt_id,
                **entry.to_dict(),
                "side_effect_status": (
                    "error_observed" if entry.error_type is not None else "completed"
                ),
                **B3_RESEARCH_FLAGS,
            }
            self.tool_writer.append(record)
            JsonlTraceWriter(branch_root / "tool_events.jsonl").append(record)

        final_agent_path = branch_root / "final_agent_state.json"
        final_environment_path = branch_root / "final_environment_state.json"
        final_environment_state_hash: str | None = None
        try:
            final_snapshot = AgentStateAdapter.capture(agent)
            final_environment_state = environment.state_manifest()
            final_environment_state_hash = environment.state_hash()
            final_agent_document: Mapping[str, Any] = final_snapshot.to_dict()
            final_environment_document: Mapping[str, Any] = final_environment_state
        except Exception as exc:
            termination = ContinueTermination.ENVIRONMENT_FAILURE
            final_agent_document = {
                "status": "unavailable",
                "error": f"{type(exc).__name__}: {exc}",
            }
            final_environment_document = {
                "status": "unavailable",
                "error": f"{type(exc).__name__}: {exc}",
            }
        final_agent_path.write_text(
            json.dumps(final_agent_document, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        final_environment_path.write_text(
            json.dumps(
                final_environment_document, ensure_ascii=False, indent=2, sort_keys=True
            )
            + "\n",
            encoding="utf-8",
        )
        first_request = model.requests[0] if model.requests else None
        first_input_exact = bool(
            first_request is not None
            and first_request.first_request_contract_verified
            and first_request.input_hash == restored.manifest.hashes["next_model_input_hash"]
        )
        hashes_match = {
            "agent_state_hash": restored.restored_agent_state_hash
            == restored.manifest.hashes["agent_state_hash"],
            "next_model_input_hash": restored.restored_next_model_input_hash
            == restored.manifest.hashes["next_model_input_hash"],
            "environment_state_hash": restored.restored_environment_hash
            == restored.manifest.hashes["environment_state_hash"],
            "runtime_state_hash": restored.restored_runtime_hash
            == restored.manifest.hashes["runtime_state_hash"],
            "identity_config_hash": canonical_hash(
                _read_json(checkpoint_path / "identity/identity_config.json")
            )
            == restored.manifest.hashes["identity_config_hash"],
            "combined_state_hash": restored.restored_combined_state_hash
            == restored.manifest.hashes["combined_state_hash"],
        }
        lineage = ContinueLineage(
            checkpoint_id=audit.checkpoint_id,
            source_task_run_id=audit.task_run_id,
            branch_id=branch_id,
            branch_attempt_id=branch_attempt_id,
            restored_state_hash=restored.restored_combined_state_hash,
            first_model_input_hash=first_request.input_hash if first_request else None,
            model_event_ids=tuple(model_event_ids),
            tool_event_ids=tuple(tool_event_ids),
            final_agent_state_ref=final_agent_path.relative_to(self.run_directory).as_posix(),
            final_environment_state_ref=final_environment_path.relative_to(
                self.run_directory
            ).as_posix(),
        )
        outcome: ContinueOutcome = self.outcome_evaluator.evaluate(
            task=task,
            environment=environment,
            candidate_result=candidate_result,
            termination=termination,
            tool_error_observed=tool_error_observed,
            lineage=lineage,
        )
        outcome_record = {
            **outcome.to_dict(),
            "run_id": self.run_id,
            "task_id": task.task_id,
            "template_id": task.template_id,
            "instance_id": task.instance_id,
            "split": task.split.value if task.split is not None else None,
            "worker_id": worker.worker_id,
            "model_id": self.settings.model_id,
            "model_version": self.settings.model_version,
            "prompt_version": self.settings.prompt_version,
            "tool_version": TOOL_VERSION,
            "protocol_id": self.protocol_id,
            "replay_policy": self.replay_policy,
            "checkpoint_schema": restored.manifest.schema_version,
            "verifier_code_sha256": sha256_file(
                "src/risk_agent/benchmark/verifier.py"
            ),
        }
        self.outcome_writer.append(outcome_record)
        (branch_root / "outcome.json").write_text(
            json.dumps(outcome_record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if outcome.verifier_outcome_id is not None:
            verifier_record = {
                "verifier_outcome_id": outcome.verifier_outcome_id,
                "continue_outcome_id": outcome.continue_outcome_id,
                "run_id": self.run_id,
                "checkpoint_id": audit.checkpoint_id,
                "branch_attempt_id": branch_attempt_id,
                "passed": outcome.verifier_passed,
                "checks": outcome.verifier_checks,
                "error": outcome.verifier_error,
                **B3_RESEARCH_FLAGS,
            }
            self.verifier_writer.append(verifier_record)
            (branch_root / "verifier.json").write_text(
                json.dumps(verifier_record, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
        prompt_tokens_before = budget["prompt_tokens_used"]
        completion_tokens_before = budget["completion_tokens_used"]
        tokens_before = (
            int(prompt_tokens_before) + int(completion_tokens_before)
            if prompt_tokens_before is not None and completion_tokens_before is not None
            else None
        )
        budget_record = {
            "run_id": self.run_id,
            "checkpoint_id": audit.checkpoint_id,
            "branch_attempt_id": branch_attempt_id,
            "model_calls_before": int(budget["model_calls_used"]),
            "model_calls_after": model.model_calls_used,
            "tool_calls_before": int(budget["tool_calls_used"]),
            "tool_calls_after": restored.runtime_state.budget.tool_calls_used,
            "tokens_before": tokens_before,
            "tokens_after": restored.runtime_state.budget.tokens_used,
            "model_requests_added": len(model.requests),
            "tool_calls_added": len(new_history),
            "wall_latency_seconds": wall_seconds,
            "remaining_budget": {
                "model_calls": int(budget["max_model_calls"]) - model.model_calls_used,
                "tool_calls": int(budget["max_tool_calls"])
                - restored.runtime_state.budget.tool_calls_used,
                "tokens": int(budget["max_tokens"])
                - restored.runtime_state.budget.tokens_used,
                "latency_seconds": float(budget["max_latency_seconds"])
                - restored.runtime_state.budget.latency_seconds_used,
            },
            **B3_RESEARCH_FLAGS,
        }
        self.budget_writer.append(budget_record)
        tool_lineage_complete = len(matched_call_indexes) == len(new_history)
        complete_chain = bool(
            outcome.y_continue is not None
            and outcome.verifier_outcome_id is not None
            and first_input_exact
            and all(hashes_match.values())
            and not replay_detected
            and checkpoint_immutable
            and tool_lineage_complete
        )
        return {
            "run_id": self.run_id,
            "source_task_run_id": audit.task_run_id,
            "replay_policy": self.replay_policy,
            "checkpoint_id": audit.checkpoint_id,
            "task_id": task.task_id,
            "branch_id": branch_id,
            "branch_attempt_id": branch_attempt_id,
            "worker_id": worker.worker_id,
            "restore_hashes": hashes_match,
            "first_input_exact": first_input_exact,
            "model_requests_added": len(model.requests),
            "tool_calls_added": len(new_history),
            "tool_lineage_complete": tool_lineage_complete,
            "tool_replay_detected": replay_detected,
            "checkpoint_immutable": checkpoint_immutable,
            "termination": termination.value,
            "failure_category": outcome.failure_category,
            "verifier_passed": outcome.verifier_passed,
            "y_continue": outcome.y_continue,
            "continue_outcome_id": outcome.continue_outcome_id,
            "verifier_outcome_id": outcome.verifier_outcome_id,
            "wall_latency_seconds": wall_seconds,
            "safe_reattempt_allowed": (
                termination is ContinueTermination.MODEL_SERVICE_FAILURE
                and len(new_history) == 0
                and final_environment_state_hash == restored_environment_state_hash
            ),
            "complete_chain": complete_chain,
            **B3_RESEARCH_FLAGS,
        }


def _exception_chain(exc: BaseException) -> tuple[BaseException, ...]:
    result: list[BaseException] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        result.append(current)
        current = current.__cause__ or current.__context__
    return tuple(result)


__all__ = [
    "B3_RESEARCH_FLAGS",
    "CheckpointAuditRecord",
    "CheckpointRunAudit",
    "ContinueGateError",
    "ContinueGateRunner",
    "EXPERIMENT_ID",
    "audit_checkpoint_run",
]
