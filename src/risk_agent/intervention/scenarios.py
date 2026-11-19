"""Prepare leakage-aware Day 9 checkpoint states from eligible Day 8 tasks."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from risk_agent.benchmark import TaskEnvironment
from risk_agent.benchmark.schema import TaskSpec, ToolStep
from risk_agent.checkpoint import CheckpointManager
from risk_agent.faults import describe_operation
from risk_agent.faults.schema import CommitStatus, FaultType
from risk_agent.monitoring import BudgetState, RuntimeState, ToolHistoryEntry

from .schema import InterventionScenario, PreparedCheckpoint


def eligible_day8_records(path: str | Path) -> tuple[dict[str, Any], ...]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    records = document.get("records")
    if not isinstance(records, list):
        raise ValueError("Day 8 logical results must contain records")
    allowed_statuses = {"success", "task_failure", "infra_failure"}
    unknown_statuses = {
        str(record.get("task_status"))
        for record in records
        if record.get("task_status") not in allowed_statuses
    }
    if unknown_statuses:
        raise ValueError(f"Unknown Day 8 task statuses: {sorted(unknown_statuses)}")
    eligible = tuple(
        dict(record)
        for record in records
        if record.get("task_status") in {"success", "task_failure"}
    )
    if not eligible:
        raise ValueError("No non-infrastructure Day 8 records are eligible")
    if len({record["task_id"] for record in eligible}) != len(eligible):
        raise ValueError("Eligible Day 8 task IDs must be unique")
    return eligible


def _wrong_arguments(step: ToolStep) -> dict[str, Any]:
    arguments = dict(step.arguments)
    tool = step.tool
    if tool in {"read_csv", "filter_data", "aggregate_data"}:
        arguments["path"] = "missing/day9_input.csv"
    elif tool in {"list_files", "search_files"}:
        arguments["directory"] = "missing/day9_directory"
    elif tool == "read_file":
        arguments["path"] = "missing/day9_file.txt"
    elif tool == "query_inventory":
        arguments["sku"] = "missing-day9-sku"
    elif tool == "get_order":
        arguments["order_id"] = "missing-day9-order"
    elif tool == "update_order":
        arguments["expected_status"] = "cancelled"
    elif tool == "calculator":
        arguments["expression"] = "1 / 0"
    elif tool == "unit_converter":
        arguments["from_unit"] = "unsupported"
    elif tool == "validate_result":
        arguments["tolerance"] = "-1"
    else:
        raise ValueError(f"No Day 9 invalid-argument adapter for {tool}")
    return arguments


def _failure(
    *, task: TaskSpec, scenario_id: str, fault_type: FaultType, commit_status: CommitStatus
) -> dict[str, object]:
    return {
        "event_id": f"event_{scenario_id}",
        "fault_id": scenario_id,
        "fault_type": fault_type.value,
        "commit_status": commit_status.value,
        "response_received": fault_type not in {FaultType.F2_API_503, FaultType.F6_PARTIAL_FAILURE},
        "phase": "checkpointed_failure",
        "call_index": 1,
        "error_type": f"{fault_type.value}_checkpoint",
        "arguments_hash": hashlib.sha256(task.task_id.encode("utf-8")).hexdigest(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def build_intervention_scenario(task: TaskSpec, position: int) -> InterventionScenario:
    reference = task.reference_steps[-1]
    operation = describe_operation(reference.tool, dict(reference.arguments))
    if reference.tool == "update_order":
        scenario_id = "partial_failure_not_committed"
        failure = _failure(
            task=task,
            scenario_id=scenario_id,
            fault_type=FaultType.F6_PARTIAL_FAILURE,
            commit_status=CommitStatus.NOT_COMMITTED,
        )
        observation = ToolStep("get_order", {"order_id": reference.arguments["order_id"]})
        return InterventionScenario(
            scenario_id=scenario_id,
            fault_type=FaultType.F6_PARTIAL_FAILURE,
            pending_step=reference,
            pending_read_only=False,
            last_failure=failure,
            replan_steps=(observation, reference),
        )

    variant = position % 4
    if variant == 0:
        return InterventionScenario(
            scenario_id="no_fault_pending",
            fault_type=None,
            pending_step=reference,
            pending_read_only=operation.read_only,
            last_failure=None,
            replan_steps=(),
        )
    if variant == 1:
        scenario_id = "transient_service_unavailable"
        return InterventionScenario(
            scenario_id=scenario_id,
            fault_type=FaultType.F2_API_503,
            pending_step=reference,
            pending_read_only=operation.read_only,
            last_failure=_failure(
                task=task,
                scenario_id=scenario_id,
                fault_type=FaultType.F2_API_503,
                commit_status=CommitStatus.NOT_STARTED,
            ),
            replan_steps=(),
        )
    fault_type = FaultType.F3_INVALID_ARGUMENT if variant == 2 else FaultType.F4_STALE_OBSERVATION
    scenario_id = "invalid_argument" if variant == 2 else "stale_observation"
    return InterventionScenario(
        scenario_id=scenario_id,
        fault_type=fault_type,
        pending_step=ToolStep(reference.tool, _wrong_arguments(reference)),
        pending_read_only=operation.read_only,
        last_failure=_failure(
            task=task,
            scenario_id=scenario_id,
            fault_type=fault_type,
            commit_status=CommitStatus.NOT_STARTED if variant == 2 else CommitStatus.NOT_APPLICABLE,
        ),
        replan_steps=tuple(task.reference_steps),
    )


def _decision_tokens(record: Mapping[str, Any]) -> int:
    steps = record.get("agent_steps", [])
    if not isinstance(steps, list):
        return 0
    for step in steps:
        if not isinstance(step, dict) or not step.get("tool_calls"):
            continue
        usage = step.get("token_usage")
        if isinstance(usage, dict):
            return int(usage.get("total_tokens", 0))
    return 0


def prepare_intervention_checkpoints(
    *,
    tasks_by_id: Mapping[str, TaskSpec],
    source_records: tuple[dict[str, Any], ...],
    checkpoint_manager: CheckpointManager,
    source_root: str | Path,
) -> tuple[PreparedCheckpoint, ...]:
    prepared: list[PreparedCheckpoint] = []
    root = Path(source_root)
    for position, record in enumerate(source_records, start=1):
        task_id = str(record["task_id"])
        task = tasks_by_id[task_id]
        if task.split is None or str(record.get("split")) != task.split.value:
            raise ValueError(f"Split mismatch for {task_id}")
        scenario = build_intervention_scenario(task, position)
        checkpoint_id = f"day9_cp_{position:03d}"
        environment = TaskEnvironment(root / checkpoint_id, task)
        environment.reset()
        failure = dict(scenario.last_failure) if scenario.last_failure is not None else None
        history: list[ToolHistoryEntry] = []
        if failure is not None:
            history.append(
                ToolHistoryEntry(
                    step_id="checkpoint_failure_001",
                    tool_name=scenario.pending_step.tool,
                    tool_arguments=dict(scenario.pending_step.arguments),
                    tool_result=None,
                    error_type=str(failure["error_type"]),
                    timestamp=str(failure["timestamp"]),
                    token_usage={"total_tokens": _decision_tokens(record)},
                    latency_seconds=0.0,
                    worker_id=str(record["worker_id"]),
                    environment_id=f"{checkpoint_id}_source",
                    checkpoint_id=checkpoint_id,
                )
            )
        tool_state: dict[str, Any] = {
            "scenario_id": scenario.scenario_id,
            "source_day8_task_run_id": record["task_run_id"],
            "pending_operation": {
                "tool_name": scenario.pending_step.tool,
                "arguments": dict(scenario.pending_step.arguments),
                "read_only": scenario.pending_read_only,
            },
            "replan_steps": [
                {"tool_name": step.tool, "arguments": dict(step.arguments)}
                for step in scenario.replan_steps
            ],
        }
        if failure is not None:
            tool_state["last_failure"] = failure
        decision_tokens = _decision_tokens(record)
        runtime = RuntimeState(
            seed=task.seed,
            agent_memory=[
                {"role": "system", "content": "Day 9 deterministic intervention checkpoint."},
                {"role": "user", "content": task.prompt},
                {
                    "role": "controller",
                    "scenario_id": scenario.scenario_id,
                    "pending_tool": scenario.pending_step.tool,
                },
            ],
            tool_history=history,
            tool_state=tool_state,
            budget=BudgetState(
                max_tokens=20_000,
                max_tool_calls=20,
                max_latency_seconds=600.0,
                tokens_used=decision_tokens,
                tool_calls_used=1 if failure is not None else 0,
                latency_seconds_used=0.0,
            ),
        )
        snapshot = checkpoint_manager.snapshot(
            checkpoint_id=checkpoint_id,
            environment=environment,
            runtime_state=runtime,
        )
        prepared.append(
            PreparedCheckpoint(
                checkpoint_id=checkpoint_id,
                task_id=task_id,
                source_task_run_id=str(record["task_run_id"]),
                source_day8_run_id=str(record["run_id"]),
                split=task.split.value,
                scenario=scenario,
                state_hash=snapshot.manifest.state_hash,
            )
        )
    return tuple(prepared)
