"""Reference execution used to prove each Day 3 task is runnable and verifiable."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from risk_agent.tools.task_tool_registry import TaskToolRegistry

from .environment import TaskEnvironment
from .schema import TaskSpec
from .verifier import DeterministicVerifier


@dataclass(frozen=True, slots=True)
class TaskRunResult:
    task_id: str
    template_id: str
    instance_id: str
    domain: str
    dataset_role: str
    split: str | None
    seed: int
    passed: bool
    reset_verified: bool
    initial_state_hash: str
    restored_state_hash: str
    final_result: Any
    checks: dict[str, bool]
    tool_calls: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ReferenceTaskRunner:
    def __init__(self, base_directory: str | Path) -> None:
        self.base_directory = Path(base_directory)
        self.verifier = DeterministicVerifier()

    def run(self, task: TaskSpec) -> TaskRunResult:
        environment = TaskEnvironment(self.base_directory, task)
        initial_hash = environment.reset()
        registry = TaskToolRegistry(environment)
        result: Any = None
        for step in task.reference_steps:
            result = registry.execute(task.domain, step.tool, dict(step.arguments))
        validation = self.verifier.verify(task, environment, result)
        restored_hash = environment.reset()
        return TaskRunResult(
            task_id=task.task_id,
            template_id=task.template_id,
            instance_id=task.instance_id,
            domain=task.domain.value,
            dataset_role=task.dataset_role,
            split=task.split.value if task.split is not None else None,
            seed=task.seed,
            passed=validation.passed,
            reset_verified=initial_hash == restored_hash,
            initial_state_hash=initial_hash,
            restored_state_hash=restored_hash,
            final_result=result,
            checks=dict(validation.checks),
            tool_calls=len(task.reference_steps),
        )
