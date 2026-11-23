"""Offline-first B4 smoke planning and model-backed orchestration.

The local preflight in this module never creates a remote client.  The execution
runner composes the accepted B2 capture and B3 CONTINUE implementations so B4 does
not introduce a second Checkpoint or resume protocol.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from risk_agent.agent.worker_router import RemoteModelSettings, WorkerRouter
from risk_agent.agent.agent_factory import ModelErrorCategory, classify_model_exception
from risk_agent.benchmark import (
    DeterministicVerifier,
    ReferenceTaskRunner,
    TaskEnvironment,
)
from risk_agent.benchmark.schema import DataSplit, Domain, TaskSpec
from risk_agent.collection.benchmark_tools import DOMAIN_TOOL_NAMES
from risk_agent.faults import (
    CommitStatus,
    FaultConfig,
    FaultEvent,
    FaultInjector,
    FaultType,
    RetrySafetyEvaluator,
    ToolOperation,
)
from risk_agent.monitoring import JsonlTraceWriter
from risk_agent.tools.task_tool_registry import TaskToolRegistry

from .continue_gate import CheckpointAuditRecord, ContinueGateRunner
from .continue_outcome import (
    ContinueLineage,
    ContinueOutcomeEvaluator,
    ContinueTermination,
)
from .day5_gate import (
    PROTOCOL_ID,
    TOOL_VERSION,
    Day5GateRunner,
    NoEligibleCheckpointError,
    canonical_task_catalog_hash,
    checkpoint_inventory,
    sha256_file,
    split_manifest_hash,
)


EXPERIMENT_ID = "b4_model_backed_smoke_gate"
B4_RESEARCH_FLAGS = {
    "formal_experiment": False,
    "execution_mode": "model_backed_smoke",
    "controller_type": "qwen3_model",
    "research_dataset_eligible": False,
    "exclude_from_formal_datasets": True,
}


class B4ConfigurationError(ValueError):
    """A B4 plan violates the frozen smoke protocol."""


@dataclass(frozen=True, slots=True)
class B4TaskCase:
    task_id: str
    fault: FaultConfig
    complete_continue: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "fault": {
                "fault_id": self.fault.fault_id,
                "fault_type": self.fault.fault_type.value,
                "seed": self.fault.seed,
                "target_tool": self.fault.target_tool,
                "call_indices": list(self.fault.call_indices),
                "probability": self.fault.probability,
                "parameters": dict(self.fault.parameters),
            },
            "complete_continue": self.complete_continue,
        }


@dataclass(frozen=True, slots=True)
class B4SmokePlan:
    protocol_id: str
    experiment_id: str
    catalog_source: str
    catalog_sha256: str
    checkpoint_trigger: str
    minimum_valid_primary_checkpoints: int
    minimum_complete_continue_chains: int
    cases: tuple[B4TaskCase, ...]
    formal_experiment: bool
    execution_mode: str
    controller_type: str
    research_dataset_eligible: bool
    exclude_from_formal_datasets: bool

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["tasks"] = [case.to_dict() for case in self.cases]
        value.pop("cases")
        return value


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise B4ConfigurationError(f"{field} must be a mapping")
    return value


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise B4ConfigurationError(f"{field} must be a positive integer")
    return value


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise B4ConfigurationError(f"{field} must be boolean")
    return value


def load_b4_smoke_plan(path: str | Path) -> B4SmokePlan:
    """Load a strict, non-formal B4 task and fault schedule."""

    root = _mapping(yaml.safe_load(Path(path).read_text(encoding="utf-8")), "B4 plan")
    allowed_root = {
        "version",
        "protocol_id",
        "experiment_id",
        "catalog_source",
        "catalog_sha256",
        "checkpoint_trigger",
        "minimum_valid_primary_checkpoints",
        "minimum_complete_continue_chains",
        "formal_experiment",
        "execution_mode",
        "controller_type",
        "research_dataset_eligible",
        "exclude_from_formal_datasets",
        "tasks",
    }
    extra = set(root) - allowed_root
    if extra:
        raise B4ConfigurationError(f"Unsupported B4 plan fields: {sorted(extra)}")
    if root.get("version") != 1:
        raise B4ConfigurationError("Only B4 plan schema version 1 is supported")
    raw_cases = root.get("tasks")
    if not isinstance(raw_cases, list) or not 8 <= len(raw_cases) <= 12:
        raise B4ConfigurationError("B4 requires 8-12 task cases")
    cases: list[B4TaskCase] = []
    allowed_case = {
        "task_id",
        "fault_id",
        "fault_type",
        "target_tool",
        "call_indices",
        "parameters",
        "complete_continue",
    }
    for index, raw_value in enumerate(raw_cases):
        raw = _mapping(raw_value, f"tasks[{index}]")
        case_extra = set(raw) - allowed_case
        if case_extra:
            raise B4ConfigurationError(
                f"Unsupported tasks[{index}] fields: {sorted(case_extra)}"
            )
        indices = raw.get("call_indices", [])
        if not isinstance(indices, list):
            raise B4ConfigurationError(f"tasks[{index}].call_indices must be a list")
        complete_continue = raw.get("complete_continue")
        if not isinstance(complete_continue, bool):
            raise B4ConfigurationError(
                f"tasks[{index}].complete_continue must be boolean"
            )
        fault_type = FaultType(str(raw["fault_type"]))
        cases.append(
            B4TaskCase(
                task_id=str(raw["task_id"]),
                fault=FaultConfig(
                    fault_id=str(raw["fault_id"]),
                    fault_type=fault_type,
                    seed=44_000 + index,
                    target_tool=str(raw["target_tool"]),
                    call_indices=tuple(int(item) for item in indices),
                    probability=0.0,
                    parameters=dict(_mapping(raw.get("parameters", {}), "parameters")),
                ),
                complete_continue=complete_continue,
            )
        )
    plan = B4SmokePlan(
        protocol_id=str(root["protocol_id"]),
        experiment_id=str(root["experiment_id"]),
        catalog_source=str(root["catalog_source"]),
        catalog_sha256=str(root["catalog_sha256"]),
        checkpoint_trigger=str(root["checkpoint_trigger"]),
        minimum_valid_primary_checkpoints=_positive_int(
            root["minimum_valid_primary_checkpoints"],
            "minimum_valid_primary_checkpoints",
        ),
        minimum_complete_continue_chains=_positive_int(
            root["minimum_complete_continue_chains"],
            "minimum_complete_continue_chains",
        ),
        cases=tuple(cases),
        formal_experiment=_boolean(root["formal_experiment"], "formal_experiment"),
        execution_mode=str(root["execution_mode"]),
        controller_type=str(root["controller_type"]),
        research_dataset_eligible=_boolean(
            root["research_dataset_eligible"], "research_dataset_eligible"
        ),
        exclude_from_formal_datasets=_boolean(
            root["exclude_from_formal_datasets"], "exclude_from_formal_datasets"
        ),
    )
    task_ids = [case.task_id for case in plan.cases]
    fault_ids = [case.fault.fault_id for case in plan.cases]
    if len(task_ids) != len(set(task_ids)) or len(fault_ids) != len(set(fault_ids)):
        raise B4ConfigurationError("B4 task_id and fault_id values must be unique")
    if plan.protocol_id != PROTOCOL_ID:
        raise B4ConfigurationError("B4 protocol_id differs from the frozen protocol")
    if plan.experiment_id != EXPERIMENT_ID:
        raise B4ConfigurationError("B4 experiment_id is not the frozen smoke ID")
    if plan.checkpoint_trigger != "first_eligible_nonterminal_tool_observation":
        raise B4ConfigurationError("B4 checkpoint trigger differs from Protocol v2")
    if not re.fullmatch(r"[0-9a-f]{64}", plan.catalog_sha256):
        raise B4ConfigurationError("B4 catalog_sha256 must be a lowercase SHA-256")
    if plan.minimum_valid_primary_checkpoints > len(plan.cases):
        raise B4ConfigurationError("B4 primary Checkpoint minimum exceeds task count")
    if (
        plan.formal_experiment
        or plan.execution_mode != "model_backed_smoke"
        or plan.controller_type != "qwen3_model"
        or plan.research_dataset_eligible
        or not plan.exclude_from_formal_datasets
    ):
        raise B4ConfigurationError("B4 smoke exclusion flags are invalid")
    if sum(case.complete_continue for case in plan.cases) < plan.minimum_complete_continue_chains:
        raise B4ConfigurationError("B4 plan has too few CONTINUE-eligible cases")
    return plan


def validate_b4_plan(
    plan: B4SmokePlan, tasks: Iterable[TaskSpec]
) -> dict[str, Any]:
    """Validate domain/fault/split/tool coverage against the actual catalog."""

    task_tuple = tuple(tasks)
    task_map = {task.task_id: task for task in task_tuple}
    selected: list[TaskSpec] = []
    for case in plan.cases:
        task = task_map.get(case.task_id)
        if task is None:
            raise B4ConfigurationError(f"B4 task is absent from catalog: {case.task_id}")
        if task.split is not DataSplit.TRAIN:
            raise B4ConfigurationError(f"B4 task is not Train: {case.task_id}")
        if case.fault.target_tool not in DOMAIN_TOOL_NAMES[task.domain]:
            raise B4ConfigurationError(
                f"Fault target {case.fault.target_tool} is outside {task.domain.value}"
            )
        if case.fault.target_tool not in {step.tool for step in task.reference_steps}:
            raise B4ConfigurationError(
                f"Fault target is absent from reference steps: {case.task_id}"
            )
        selected.append(task)
    domains = {task.domain.value for task in selected}
    fault_types = {case.fault.fault_type.value for case in plan.cases}
    visible_errors = {
        FaultType.F1_TIMEOUT.value,
        FaultType.F2_API_503.value,
        FaultType.F3_INVALID_ARGUMENT.value,
        FaultType.F6_PARTIAL_FAILURE.value,
    }
    checks = {
        "task_count_8_to_12": 8 <= len(selected) <= 12,
        "four_domains": domains == {domain.value for domain in Domain},
        "at_least_four_fault_types": len(fault_types) >= 4,
        "includes_f0": FaultType.F0_NO_FAULT.value in fault_types,
        "includes_agent_visible_error": bool(fault_types & visible_errors),
        "includes_partial_failure": FaultType.F6_PARTIAL_FAILURE.value in fault_types,
        "at_least_five_continue": sum(case.complete_continue for case in plan.cases)
        >= plan.minimum_complete_continue_chains,
        "all_train": all(task.split is DataSplit.TRAIN for task in selected),
        "smoke_excluded": (
            not plan.formal_experiment
            and not plan.research_dataset_eligible
            and plan.exclude_from_formal_datasets
        ),
        "catalog_hash": canonical_task_catalog_hash(task_tuple) == plan.catalog_sha256,
    }
    return {
        "checks": checks,
        "passed": all(checks.values()),
        "task_count": len(selected),
        "domains": sorted(domains),
        "fault_types": sorted(fault_types),
        "tasks": [
            {
                "task_id": task.task_id,
                "template_id": task.template_id,
                "domain": task.domain.value,
                "split": task.split.value if task.split else None,
                "fault_id": case.fault.fault_id,
                "fault_type": case.fault.fault_type.value,
                "target_tool": case.fault.target_tool,
                "checkpoint_trigger": plan.checkpoint_trigger,
                "complete_continue": case.complete_continue,
                "verifier": "DeterministicVerifier",
                "formal_dataset_excluded": True,
            }
            for case, task in zip(plan.cases, selected, strict=True)
        ],
    }


def _read_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected JSON object: {path}")
    return value


def _artifact_manifest(root: Path) -> dict[str, Any]:
    rows = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "artifact_manifest.json":
            continue
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return {"artifact_count": len(rows), "artifacts": rows}


def _f6_safety_probe() -> dict[str, Any]:
    evaluator = RetrySafetyEvaluator()
    operation = ToolOperation(
        tool_name="update_order",
        read_only=False,
        idempotency_key_present=True,
    )
    rows = []
    expected = {
        CommitStatus.NOT_COMMITTED: (True, False),
        CommitStatus.COMMITTED: (False, True),
        CommitStatus.UNKNOWN: (False, True),
    }
    for status, (retry_allowed, state_check) in expected.items():
        event = FaultEvent(
            event_id=f"b4_f6_{status.value}",
            fault_id="b4_f6_probe",
            fault_type=FaultType.F6_PARTIAL_FAILURE,
            seed=44_999,
            tool_name="update_order",
            call_index=1,
            triggered=True,
            phase="after_commit" if status is CommitStatus.COMMITTED else "uncertain",
            commit_status=status,
            response_received=False,
            error_type="PartialFailureError",
            arguments_hash="0" * 64,
            timestamp="2026-11-23T00:00:00+00:00",
        )
        decision = evaluator.evaluate(event, operation)
        rows.append(
            {
                "commit_status": status.value,
                "retry_allowed": decision.retry_allowed,
                "requires_state_check": decision.requires_state_check,
                "passed": decision.retry_allowed is retry_allowed
                and decision.requires_state_check is state_check,
            }
        )
    return {"records": rows, "passed": all(row["passed"] for row in rows)}


def _verifier_probes(
    *, selected: Iterable[TaskSpec], output_root: Path
) -> dict[str, Any]:
    verifier = DeterministicVerifier()
    reference = ReferenceTaskRunner(output_root / "reference")
    records = []
    for task in selected:
        positive = reference.run(task)
        environment = TaskEnvironment(output_root / "negative", task)
        environment.reset()
        registry = TaskToolRegistry(environment)
        result: Any = None
        for step in task.reference_steps:
            result = registry.execute(task.domain, step.tool, dict(step.arguments))
        wrong = verifier.verify(task, environment, {"b4_deliberately_wrong": True})
        missing = verifier.verify(task, environment, None)
        mismatched = json.dumps(result, ensure_ascii=False, sort_keys=True)
        type_mismatch = verifier.verify(task, environment, mismatched)
        records.append(
            {
                "task_id": task.task_id,
                "positive_passed": positive.passed,
                "negative_rejected": not wrong.passed,
                "missing_rejected": not missing.passed,
                "type_mismatch_rejected": not type_mismatch.passed,
                "checks": positive.checks,
            }
        )
    return {
        "records": records,
        "passed": all(
            row["positive_passed"]
            and row["negative_rejected"]
            and row["missing_rejected"]
            and row["type_mismatch_rejected"]
            for row in records
        ),
    }


def _infrastructure_null_probe(task: TaskSpec, root: Path) -> dict[str, Any]:
    environment = TaskEnvironment(root, task)
    environment.reset()
    lineage = ContinueLineage(
        checkpoint_id="b4_probe_checkpoint",
        source_task_run_id="b4_probe_source",
        branch_id="b4_probe_branch",
        branch_attempt_id="b4_probe_attempt",
        restored_state_hash="1" * 64,
        first_model_input_hash=None,
        model_event_ids=(),
        tool_event_ids=(),
        final_agent_state_ref="unavailable",
        final_environment_state_ref="unavailable",
    )
    outcome = ContinueOutcomeEvaluator().evaluate(
        task=task,
        environment=environment,
        candidate_result=None,
        termination=ContinueTermination.MODEL_SERVICE_FAILURE,
        tool_error_observed=False,
        lineage=lineage,
    )
    return {
        "termination": outcome.termination,
        "failure_category": outcome.failure_category,
        "y_continue": outcome.y_continue,
        "verifier_outcome_id": outcome.verifier_outcome_id,
        "passed": outcome.y_continue is None and outcome.verifier_outcome_id is None,
    }


def run_b4_local_preflight(
    *,
    plan: B4SmokePlan,
    tasks: Iterable[TaskSpec],
    run_directory: str | Path,
    b2_gate_run: str | Path,
    b3_gate_run: str | Path,
) -> dict[str, Any]:
    """Run all network-free B4 Stage A contract probes and persist evidence."""

    run_root = Path(run_directory).resolve()
    task_tuple = tuple(tasks)
    task_map = {task.task_id: task for task in task_tuple}
    selected = tuple(task_map[case.task_id] for case in plan.cases)
    plan_audit = validate_b4_plan(plan, task_tuple)
    actual_catalog_hash = canonical_task_catalog_hash(task_tuple)
    b2_summary = _read_json(Path(b2_gate_run) / "gate_summary.json")
    b3_summary = _read_json(Path(b3_gate_run) / "gate_summary.json")
    prior_gate_audit = {
        "b2_status": b2_summary.get("status"),
        "b2_passed_count": b2_summary.get("passed_count"),
        "b3_status": b3_summary.get("status"),
        "b3_complete_chains": b3_summary.get("complete_chains"),
        "b3_label_counts": b3_summary.get("label_counts"),
        "b3_history_preserved": b3_summary.get("label_counts")
        == {"0": 4, "1": 1, "null": 0},
    }
    prior_gate_audit["passed"] = (
        prior_gate_audit["b2_status"] == "PASS"
        and prior_gate_audit["b2_passed_count"] == 5
        and prior_gate_audit["b3_status"] == "PASS"
        and prior_gate_audit["b3_complete_chains"] == 5
        and prior_gate_audit["b3_history_preserved"]
    )
    verifier = _verifier_probes(
        selected=selected,
        output_root=run_root / "verifier_probe_workspaces",
    )
    f6 = _f6_safety_probe()
    infra = _infrastructure_null_probe(selected[0], run_root / "infra_probe_workspace")
    catalog_audit = {
        "expected_catalog_sha256": plan.catalog_sha256,
        "actual_catalog_sha256": actual_catalog_hash,
        "match": actual_catalog_hash == plan.catalog_sha256,
        "split_manifest_hash": split_manifest_hash(task_tuple),
    }
    gate_checks = {
        "plan_coverage": plan_audit["passed"],
        "catalog_hash": catalog_audit["match"],
        "prior_b2_b3_gates": prior_gate_audit["passed"],
        "verifier_probes": verifier["passed"],
        "f6_safety": f6["passed"],
        "infrastructure_is_null": infra["passed"],
        "smoke_data_excluded": (
            not plan.formal_experiment
            and not plan.research_dataset_eligible
            and plan.exclude_from_formal_datasets
        ),
        "network_requests_made_zero": True,
    }
    summary = {
        "protocol_id": PROTOCOL_ID,
        "experiment_id": EXPERIMENT_ID,
        "mode": "local-preflight",
        "task_count": len(plan.cases),
        "domain_count": len(plan_audit["domains"]),
        "fault_type_count": len(plan_audit["fault_types"]),
        "planned_primary_checkpoints": len(plan.cases),
        "planned_complete_continue_chains": sum(
            case.complete_continue for case in plan.cases
        ),
        "network_requests_made": 0,
        "checks": gate_checks,
        "passed": all(gate_checks.values()),
        "status": "PASS" if all(gate_checks.values()) else "BLOCKED",
        **B4_RESEARCH_FLAGS,
    }
    documents = {
        "task_plan.json": plan_audit,
        "catalog_audit.json": catalog_audit,
        "prior_gate_audit.json": prior_gate_audit,
        "verifier_probe_audit.json": verifier,
        "f6_safety_audit.json": f6,
        "infrastructure_null_audit.json": infra,
        "gate_summary.json": summary,
    }
    for name, document in documents.items():
        (run_root / name).write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    (run_root / "artifact_manifest.json").write_text(
        json.dumps(_artifact_manifest(run_root), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return summary


def _audit_from_source(record: Mapping[str, Any]) -> CheckpointAuditRecord:
    checkpoint_path = Path(str(record["checkpoint_path"]))
    manifest = _read_json(checkpoint_path / "manifest.json")
    inventory = checkpoint_inventory(checkpoint_path)
    return CheckpointAuditRecord(
        checkpoint_id=str(record["checkpoint_id"]),
        checkpoint_path=str(checkpoint_path),
        task_id=str(record["task_id"]),
        task_run_id=str(record["task_run_id"]),
        worker_id=str(record["worker_id"]),
        schema_version=str(manifest["schema_version"]),
        manifest_sha256=sha256_file(checkpoint_path / "manifest.json"),
        artifact_count=len(manifest["artifacts"]),
        inventory_file_count=len(inventory),
        step_number=int(manifest["agent_context"]["step_number"]),
        next_step_number=int(manifest["agent_context"]["next_step_number"]),
        model_calls_used=int(manifest["runtime_state"]["budget_state"]["model_calls_used"]),
        tool_calls_used=int(manifest["runtime_state"]["budget_state"]["tool_calls_used"]),
        hashes=dict(record["checkpoint_hashes"]),
        all_hashes_verified=all(record["hashes_match"].values()),
        checkpoint_inventory=inventory,
    )


class B4SmokeGateRunner:
    """Run the authorized real-model B4 source and CONTINUE chains sequentially."""

    def __init__(
        self,
        *,
        settings: RemoteModelSettings,
        run_directory: str | Path,
        run_id: str,
        tasks: Iterable[TaskSpec],
        plan: B4SmokePlan,
    ) -> None:
        self.settings = settings
        self.run_directory = Path(run_directory).resolve()
        self.run_id = run_id
        self.tasks = tuple(tasks)
        self.task_map = {task.task_id: task for task in self.tasks}
        self.plan = plan
        validate_b4_plan(plan, self.tasks)
        self.router = WorkerRouter(settings.workers)
        self.case_by_task = {case.task_id: case for case in plan.cases}
        self.source_runner = Day5GateRunner(
            settings=settings,
            run_directory=self.run_directory,
            run_id=run_id,
            catalog_hash=canonical_task_catalog_hash(self.tasks),
            split_hash=split_manifest_hash(self.tasks),
            verifier_content_hash=sha256_file("src/risk_agent/benchmark/verifier.py"),
            experiment_id=EXPERIMENT_ID,
            research_flags=B4_RESEARCH_FLAGS,
        )
        self.continue_runner = ContinueGateRunner(
            settings=settings,
            run_directory=self.run_directory,
            checkpoint_run=self.run_directory,
            run_id=run_id,
            tasks=self.tasks,
            fault_injector_factory=self._fault_injector,
            experiment_id=EXPERIMENT_ID,
        )

    def _fault_injector(
        self, audit: CheckpointAuditRecord, checkpoint_path: Path
    ) -> FaultInjector:
        del checkpoint_path
        return FaultInjector(self.case_by_task[audit.task_id].fault)

    def run(
        self,
        *,
        task_limit: int | None = None,
        evaluate_final_gate: bool = True,
    ) -> dict[str, Any]:
        if task_limit is not None and not 1 <= task_limit <= len(self.plan.cases):
            raise ValueError("task_limit must select at least one planned B4 case")
        selected_cases = (
            self.plan.cases if task_limit is None else self.plan.cases[:task_limit]
        )
        if not evaluate_final_gate and len(selected_cases) != 1:
            raise ValueError("Non-final B4 execution is restricted to one canary chain")
        attempts = JsonlTraceWriter(self.run_directory / "task_attempts.jsonl")
        checkpoints = JsonlTraceWriter(self.run_directory / "checkpoint_index.jsonl")
        continues = JsonlTraceWriter(self.run_directory / "continue_audit.jsonl")
        source_models = JsonlTraceWriter(
            self.run_directory / "model_request_audit.jsonl"
        )
        source_budgets = JsonlTraceWriter(self.run_directory / "budget_audit.jsonl")
        failures = JsonlTraceWriter(self.run_directory / "failures.jsonl")
        valid: list[dict[str, Any]] = []
        complete: list[dict[str, Any]] = []
        harness_errors = 0
        for index, case in enumerate(selected_cases, 1):
            task = self.task_map[case.task_id]
            worker = self.router.select()
            try:
                source = self.source_runner.run_task(
                    task=task,
                    worker=worker,
                    attempt_index=1,
                    fault_config=case.fault,
                )
                expected_trigger = case.fault.fault_type is not FaultType.F0_NO_FAULT
                target_observed = any(
                    event["tool_name"] == case.fault.target_tool
                    for event in source["fault_events"]
                )
                source["fault_schedule_satisfied"] = (
                    target_observed and source["fault_triggered"] is expected_trigger
                )
                attempts.append(source)
                for phase, usage in (
                    ("source", source["source_request_usage"]),
                    ("controlled_restore", source["controlled_request_usage"]),
                ):
                    source_models.append(
                        {
                            "run_id": self.run_id,
                            "task_run_id": source["task_run_id"],
                            "task_id": source["task_id"],
                            "worker_id": source["worker_id"],
                            "phase": phase,
                            "prompt_tokens": usage["prompt_tokens"],
                            "completion_tokens": usage["completion_tokens"],
                            "latency_seconds": usage["latency_seconds"],
                            "usage_status": (
                                "observed"
                                if usage["prompt_tokens"] is not None
                                and usage["completion_tokens"] is not None
                                and usage["latency_seconds"] is not None
                                else "unavailable"
                            ),
                            **B4_RESEARCH_FLAGS,
                        }
                    )
                source_budgets.append(
                    {
                        "run_id": self.run_id,
                        "task_run_id": source["task_run_id"],
                        "task_id": source["task_id"],
                        "phase": "primary_checkpoint",
                        "model_calls_used": source["source_model_calls"],
                        "tool_calls_used": source["source_tool_calls"],
                        "remaining_budget": source["remaining_budget_at_checkpoint"],
                        **B4_RESEARCH_FLAGS,
                    }
                )
                if not source["passed"] or not source["fault_schedule_satisfied"]:
                    failures.append(source)
                    continue
                valid.append(source)
                audit = _audit_from_source(source)
                checkpoints.append(audit.to_dict())
                if not case.complete_continue:
                    continue
                result = self.continue_runner.run_checkpoint(
                    audit=audit,
                    worker=worker,
                    branch_attempt_index=1,
                )
                continues.append(result)
                if not result["complete_chain"] and result["safe_reattempt_allowed"]:
                    failures.append(result)
                    result = self.continue_runner.run_checkpoint(
                        audit=audit,
                        worker=worker,
                        branch_attempt_index=2,
                    )
                    continues.append(result)
                if result["complete_chain"]:
                    complete.append(result)
                else:
                    failures.append(result)
            except Exception as exc:
                model_category = classify_model_exception(exc)
                if isinstance(exc, NoEligibleCheckpointError) or str(exc) == (
                    "Source Agent ended without an ActionStep"
                ):
                    outcome_status = "checkpoint_not_captured"
                elif model_category is not ModelErrorCategory.UNKNOWN:
                    outcome_status = "model_service_failure"
                else:
                    outcome_status = "harness_error"
                    harness_errors += 1
                failure = {
                    "run_id": self.run_id,
                    "task_id": task.task_id,
                    "fault_id": case.fault.fault_id,
                    "error_type": type(exc).__name__,
                    "failure_reason": str(exc),
                    "outcome_status": outcome_status,
                    "attempt_index": index,
                    **B4_RESEARCH_FLAGS,
                }
                failures.append(failure)
                attempts.append({**failure, "passed": False})
        observed_fault_types = {
            row["fault_type"]
            for row in valid
            if row["fault_type"] == FaultType.F0_NO_FAULT.value
            or row["fault_triggered"]
        }
        agent_visible_error_count = sum(
            any(event.get("error_type") is not None for event in row["fault_events"])
            for row in valid
        )
        valid_domains = {
            self.task_map[row["task_id"]].domain.value for row in valid
        }
        model_rows = []
        for name in ("model_request_audit.jsonl", "model_events.jsonl"):
            path = self.run_directory / name
            if path.exists():
                model_rows.extend(
                    json.loads(line)
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line
                )
        tool_rows = []
        for name in ("tool_trajectories.jsonl", "tool_events.jsonl"):
            path = self.run_directory / name
            if path.exists():
                tool_rows.extend(
                    json.loads(line)
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line
                )
        def missing_rate(values: list[Any]) -> float:
            return sum(value is None for value in values) / len(values) if values else 0.0

        missing_rates = {
            "prompt_tokens": missing_rate([row.get("prompt_tokens") for row in model_rows]),
            "completion_tokens": missing_rate(
                [row.get("completion_tokens") for row in model_rows]
            ),
            "model_latency": missing_rate(
                [row.get("latency_seconds") for row in model_rows]
            ),
            "tool_latency": missing_rate(
                [row.get("latency_seconds") for row in tool_rows]
            ),
            "wall_latency": missing_rate(
                [row.get("task_wall_seconds") for row in valid]
                + [row.get("wall_latency_seconds") for row in complete]
            ),
        }
        checkpoint_ids = [row["checkpoint_id"] for row in complete]
        branch_ids = [row["branch_id"] for row in complete]
        cross_talk_count = int(
            len(checkpoint_ids) != len(set(checkpoint_ids))
            or len(branch_ids) != len(set(branch_ids))
        )
        summary = {
            "protocol_id": PROTOCOL_ID,
            "run_id": self.run_id,
            "task_attempts": len(selected_cases),
            "valid_primary_checkpoints": len(valid),
            "complete_continue_chains": len(complete),
            "domain_count": len(valid_domains),
            "observed_fault_types": sorted(observed_fault_types),
            "agent_visible_error_count": agent_visible_error_count,
            "tool_replay_count": sum(row["tool_replay_detected"] for row in complete),
            "checkpoint_mutation_count": sum(not row["checkpoint_immutable"] for row in complete),
            "branch_cross_talk_count": cross_talk_count,
            "harness_error_count": harness_errors,
            "missing_rates": missing_rates,
            "label_counts": {
                "1": sum(row["y_continue"] == 1 for row in complete),
                "0": sum(row["y_continue"] == 0 for row in complete),
                "null": sum(row["y_continue"] is None for row in complete),
            },
            "gate_scope": "final" if evaluate_final_gate else "single_chain_canary",
            **B4_RESEARCH_FLAGS,
        }
        common_checks = bool(
            summary["harness_error_count"] == 0
            and summary["tool_replay_count"] == 0
            and summary["checkpoint_mutation_count"] == 0
            and summary["branch_cross_talk_count"] == 0
            and all(rate <= 0.05 for rate in missing_rates.values())
        )
        if evaluate_final_gate:
            summary["passed"] = bool(
                common_checks
                and summary["task_attempts"] >= 8
                and summary["domain_count"] == 4
                and len(observed_fault_types) >= 4
                and FaultType.F0_NO_FAULT.value in observed_fault_types
                and summary["agent_visible_error_count"] >= 1
                and len(valid) >= self.plan.minimum_valid_primary_checkpoints
                and len(complete) >= self.plan.minimum_complete_continue_chains
            )
        else:
            summary["passed"] = bool(
                common_checks
                and summary["task_attempts"] == 1
                and len(valid) == 1
                and len(complete) == 1
                and FaultType.F0_NO_FAULT.value in observed_fault_types
            )
        summary["status"] = "PASS" if summary["passed"] else "BLOCKED"
        for name in (
            "task_attempts.jsonl",
            "checkpoint_index.jsonl",
            "continue_audit.jsonl",
            "model_request_audit.jsonl",
            "model_events.jsonl",
            "tool_events.jsonl",
            "agent_visible_events.jsonl",
            "continue_outcomes.jsonl",
            "verifier_outcomes.jsonl",
            "budget_audit.jsonl",
            "failures.jsonl",
        ):
            (self.run_directory / name).touch(exist_ok=True)
        (self.run_directory / "gate_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (self.run_directory / "artifact_manifest.json").write_text(
            json.dumps(
                _artifact_manifest(self.run_directory),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return summary


def b4_plan_sha256(plan: B4SmokePlan) -> str:
    payload = json.dumps(
        plan.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def b4_run_manifest(
    *, run_id: str, mode: str, plan: B4SmokePlan, git_commit: str
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "protocol_id": PROTOCOL_ID,
        "experiment_id": EXPERIMENT_ID,
        "run_id": run_id,
        "mode": mode,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit,
        "checkpoint_schema": "2.0.0",
        "tool_version": TOOL_VERSION,
        "plan_sha256": b4_plan_sha256(plan),
        **B4_RESEARCH_FLAGS,
    }


__all__ = [
    "B4ConfigurationError",
    "B4SmokeGateRunner",
    "B4SmokePlan",
    "B4TaskCase",
    "B4_RESEARCH_FLAGS",
    "EXPERIMENT_ID",
    "b4_plan_sha256",
    "b4_run_manifest",
    "load_b4_smoke_plan",
    "run_b4_local_preflight",
    "validate_b4_plan",
]
