"""Day 8 real-model task execution with local tools and failure provenance."""

from __future__ import annotations

import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from risk_agent.agent.agent_factory import (
    ModelErrorCategory,
    classify_model_exception,
    create_benchmark_agent,
)
from risk_agent.agent.worker_router import RemoteModelSettings, WorkerConfig
from risk_agent.benchmark import DeterministicVerifier, TaskEnvironment
from risk_agent.benchmark.schema import DataSplit, TaskSpec
from risk_agent.monitoring import BudgetState, ExecutionMonitor, JsonlTraceWriter, RuntimeState

from .benchmark_tools import create_local_domain_tools


TASK_REQUIRED_FIELDS = (
    "experiment_id",
    "run_id",
    "task_run_id",
    "task_id",
    "template_id",
    "instance_id",
    "split",
    "seed",
    "catalog_sha256",
    "worker_id",
    "endpoint",
    "model_id",
    "model_version",
    "prompt_version",
    "tool_version",
    "started_at",
    "latency_seconds",
    "model_request_made",
    "task_status",
    "failure_origin",
    "token_usage",
    "tool_call_count",
    "validation_passed",
)


def select_initial_tasks(tasks: Iterable[TaskSpec]) -> tuple[TaskSpec, ...]:
    """Select instance 001 from every Train template: 24 tasks, 6 per domain."""
    selected = tuple(
        task
        for task in tasks
        if task.split == DataSplit.TRAIN and task.instance_id.endswith("_001")
    )
    if len(selected) != 24:
        raise ValueError(f"Expected 24 initial Train tasks, got {len(selected)}")
    counts = Counter(task.domain.value for task in selected)
    if set(counts.values()) != {6} or len(counts) != 4:
        raise ValueError(f"Expected six initial tasks per domain, got {dict(counts)}")
    return selected


def log_completeness(
    records: Iterable[Mapping[str, Any]], required_fields: tuple[str, ...] = TASK_REQUIRED_FIELDS
) -> float:
    rows = list(records)
    if not rows:
        return 0.0
    populated = sum(
        field in record and record[field] is not None
        for record in rows
        for field in required_fields
    )
    return populated / (len(rows) * len(required_fields))


def classify_task_status(
    *, validation_passed: bool, error_category: ModelErrorCategory | None, tool_call_count: int
) -> tuple[str, str]:
    if error_category is not None and error_category != ModelErrorCategory.UNKNOWN:
        return "infra_failure", "model_service"
    if error_category == ModelErrorCategory.UNKNOWN and tool_call_count == 0:
        return "infra_failure", "model_service_unknown"
    if validation_passed:
        return "success", "none"
    return "task_failure", "agent_or_tool"


def is_retryable_pretool_infra(record: Mapping[str, Any]) -> bool:
    """Only infrastructure failures before any tool call are safe to resume."""
    return record.get("task_status") == "infra_failure" and int(
        record.get("tool_call_count", 0)
    ) == 0


class InitialTrajectoryRunner:
    def __init__(
        self,
        *,
        settings: RemoteModelSettings,
        run_directory: str | Path,
        experiment_id: str,
        run_id: str,
        catalog_sha256: str,
        max_steps: int = 5,
    ) -> None:
        self.settings = settings
        self.run_directory = Path(run_directory).resolve()
        self.run_directory.mkdir(parents=True, exist_ok=True)
        self.experiment_id = experiment_id
        self.run_id = run_id
        self.catalog_sha256 = catalog_sha256
        self.max_steps = max_steps
        self.verifier = DeterministicVerifier()
        self.tool_writer = JsonlTraceWriter(self.run_directory / "tool_trajectories.jsonl")

    def run_task(
        self, *, task: TaskSpec, worker: WorkerConfig, attempt_index: int = 1
    ) -> dict[str, Any]:
        if attempt_index < 1:
            raise ValueError("attempt_index must be positive")
        task_run_id = f"{self.run_id}_{task.task_id}_attempt_{attempt_index:03d}"
        environment = TaskEnvironment(self.run_directory / "environments", task)
        initial_environment_hash = environment.reset()
        runtime = RuntimeState(
            seed=task.seed,
            agent_memory=[{"role": "user", "content": task.prompt}],
            tool_state={"catalog_sha256": self.catalog_sha256},
            budget=BudgetState(
                max_tokens=20_000,
                max_tool_calls=20,
                max_latency_seconds=600.0,
            ),
        )
        monitor = ExecutionMonitor(
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
            tool_version="day8-v1",
            worker_id=worker.worker_id,
            environment_id=task_run_id,
            additional_context={
                "task_run_id": task_run_id,
                "split": task.split.value if task.split is not None else None,
                "catalog_sha256": self.catalog_sha256,
                "execution_location": "local_wsl",
            },
        )
        tools = create_local_domain_tools(environment=environment, monitor=monitor)
        agent, model = create_benchmark_agent(
            self.settings,
            worker,
            tools,
            max_steps=self.max_steps,
            agent_name=f"day8_{worker.worker_id.replace('-', '_')}",
        )
        started_at = datetime.now(timezone.utc).isoformat()
        started = time.perf_counter()
        agent_result: Any = None
        agent_output: str | None = None
        agent_state = "error"
        error_category: ModelErrorCategory | None = None
        error_type: str | None = None
        token_usage: dict[str, int] | None = None
        try:
            instruction = (
                "Complete this MiniToolBench-R task by calling the available local tools. "
                "Do not invent tool results. For a write, perform it at most once and use a unique "
                f"idempotency key containing {task.instance_id}. Return the exact requested result.\n\n"
                f"Task: {task.prompt}"
            )
            agent_result = agent.run(instruction, return_full_result=True)
            agent_output = str(agent_result.output)
            agent_state = str(agent_result.state)
            if agent_result.token_usage is not None:
                token_usage = {
                    key: int(value)
                    for key, value in agent_result.token_usage.dict().items()
                    if value is not None
                }
        except Exception as exc:
            error_category = classify_model_exception(exc)
            error_type = type(exc).__name__
        finally:
            agent_steps = agent.memory.get_succinct_steps()
            model.client.close()

        expected_tool = task.reference_steps[-1].tool
        candidates = [
            entry.tool_result
            for entry in runtime.tool_history
            if entry.tool_name == expected_tool and entry.error_type is None
        ]
        candidate_result = candidates[-1] if candidates else None
        validation = self.verifier.verify(task, environment, candidate_result)
        task_status, failure_origin = classify_task_status(
            validation_passed=validation.passed,
            error_category=error_category,
            tool_call_count=len(runtime.tool_history),
        )
        latency = time.perf_counter() - started
        return {
            "record_type": "task_run",
            "experiment_id": self.experiment_id,
            "run_id": self.run_id,
            "task_run_id": task_run_id,
            "attempt_index": attempt_index,
            "task_id": task.task_id,
            "template_id": task.template_id,
            "instance_id": task.instance_id,
            "domain": task.domain.value,
            "split": task.split.value if task.split is not None else None,
            "seed": task.seed,
            "catalog_sha256": self.catalog_sha256,
            "worker_id": worker.worker_id,
            "endpoint": worker.base_url,
            "model_id": self.settings.model_id,
            "model_version": self.settings.model_version,
            "prompt_version": self.settings.prompt_version,
            "tool_version": "day8-v1",
            "inference": dict(self.settings.inference),
            "started_at": started_at,
            "latency_seconds": latency,
            "model_request_made": True,
            "task_status": task_status,
            "failure_origin": failure_origin,
            "agent_state": agent_state,
            "agent_output": agent_output,
            "agent_steps": agent_steps,
            "token_usage": token_usage,
            "token_usage_complete": token_usage is not None,
            "tool_call_count": len(runtime.tool_history),
            "tool_error_count": sum(
                entry.error_type is not None for entry in runtime.tool_history
            ),
            "validation_passed": validation.passed,
            "validation_checks": dict(validation.checks),
            "candidate_result": candidate_result,
            "initial_environment_hash": initial_environment_hash,
            "final_environment_hash": environment.state_hash(),
            "error_category": error_category.value if error_category is not None else None,
            "error_type": error_type,
        }
