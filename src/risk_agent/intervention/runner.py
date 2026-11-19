"""Execute applicable Day 9 intervention branches from independent restores."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from risk_agent.benchmark import DeterministicVerifier, TaskEnvironment
from risk_agent.benchmark.schema import TaskSpec
from risk_agent.branching import BranchApplicabilityEvaluator, DAY6_ACTIONS
from risk_agent.checkpoint import CheckpointManager, normalized_state_hash
from risk_agent.faults.schema import Action
from risk_agent.monitoring import ExecutionMonitor, JsonlTraceWriter
from risk_agent.tools.task_tool_registry import TaskToolRegistry

from .schema import PreparedCheckpoint


class InterventionBranchRunner:
    def __init__(
        self,
        *,
        checkpoint_manager: CheckpointManager,
        branch_root: str | Path,
        outcome_writer: JsonlTraceWriter,
        experiment_id: str,
        run_id: str,
        controller_type: str = "deterministic_local_reference",
        model_id: str = "local-reference-controller",
        model_version: str = "day9-v1",
        prompt_version: str = "day9-v1",
        tool_version: str = "day9-v1",
        branch_id_prefix: str | None = None,
    ) -> None:
        self.checkpoint_manager = checkpoint_manager
        self.branch_root = Path(branch_root).resolve()
        self.branch_root.mkdir(parents=True, exist_ok=True)
        self.outcome_writer = outcome_writer
        self.experiment_id = experiment_id
        self.run_id = run_id
        self.controller_type = controller_type
        self.model_id = model_id
        self.model_version = model_version
        self.prompt_version = prompt_version
        self.tool_version = tool_version
        self.branch_id_prefix = branch_id_prefix
        self.applicability = BranchApplicabilityEvaluator()
        self.verifier = DeterministicVerifier()

    def run_checkpoint(
        self, *, prepared: PreparedCheckpoint, task: TaskSpec
    ) -> tuple[dict[str, Any], ...]:
        records = tuple(
            self._run_action(prepared=prepared, task=task, action=action)
            for action in DAY6_ACTIONS
        )
        for record in records:
            self.outcome_writer.append(record)
        return records

    def run_action(
        self, *, prepared: PreparedCheckpoint, task: TaskSpec, action: Action
    ) -> dict[str, Any]:
        """Execute one policy-selected action and append its outcome."""

        record = self._run_action(prepared=prepared, task=task, action=action)
        self.outcome_writer.append(record)
        return record

    def _run_action(
        self, *, prepared: PreparedCheckpoint, task: TaskSpec, action: Action
    ) -> dict[str, Any]:
        base_branch_id = f"{prepared.checkpoint_id}_{action.value.lower()}"
        branch_id = (
            f"{self.branch_id_prefix}_{base_branch_id}"
            if self.branch_id_prefix
            else base_branch_id
        )
        action_root = self.branch_root / prepared.checkpoint_id / action.value.lower()
        environment = TaskEnvironment(action_root / "environment", task)
        restored = self.checkpoint_manager.restore(
            checkpoint_id=prepared.checkpoint_id, environment=environment
        )
        runtime = restored.runtime_state
        decision = self.applicability.evaluate(action, runtime)
        initial_environment_hash = environment.state_hash()
        if not decision.applicable:
            return {
                "record_type": "intervention_branch",
                "experiment_id": self.experiment_id,
                "run_id": self.run_id,
                "source_day8_run_id": prepared.source_day8_run_id,
                "source_task_run_id": prepared.source_task_run_id,
                "task_id": task.task_id,
                "template_id": task.template_id,
                "instance_id": task.instance_id,
                "split": prepared.split,
                "checkpoint_id": prepared.checkpoint_id,
                "branch_id": branch_id,
                "scenario_id": prepared.scenario.scenario_id,
                "fault_type": prepared.scenario.fault_type.value if prepared.scenario.fault_type else None,
                "action": action.value,
                "action_applicable": False,
                "applicability_reason": decision.reason,
                "branch_created": False,
                "success": False,
                "validation_passed": False,
                "validation_checks": {},
                "extra_tokens": 0,
                "extra_tool_calls": 0,
                "extra_latency_seconds": 0.0,
                "termination_reason": "action_not_applicable",
                "initial_state_hash": prepared.state_hash,
                "restored_state_hash": restored.restored_state_hash,
                "initial_environment_hash": initial_environment_hash,
                "final_state_hash": None,
                "final_environment_hash": None,
                "controller_type": self.controller_type,
                "result": None,
                "error_type": None,
            }

        initial_budget = runtime.budget.to_dict()
        trajectory = action_root / "trajectory.jsonl"
        monitor = ExecutionMonitor(
            writer=JsonlTraceWriter(trajectory),
            runtime_state=runtime,
            experiment_id=self.experiment_id,
            run_id=self.run_id,
            task_id=task.task_id,
            template_id=task.template_id,
            instance_id=task.instance_id,
            model_id=self.model_id,
            model_version=self.model_version,
            prompt_version=self.prompt_version,
            tool_version=self.tool_version,
            worker_id="local-wsl",
            environment_id=branch_id,
            additional_context={
                "checkpoint_id": prepared.checkpoint_id,
                "branch_id": branch_id,
                "action": action.value,
                "action_applicable": True,
                "split": prepared.split,
            },
        )
        registry = TaskToolRegistry(environment)
        result: Any = None
        error: Exception | None = None
        try:
            result = self._execute(
                action=action,
                task=task,
                runtime_state=runtime,
                registry=registry,
                monitor=monitor,
                checkpoint_id=prepared.checkpoint_id,
            )
        except Exception as exc:
            error = exc
        validation = self.verifier.verify(task, environment, result)
        runtime.agent_memory.append(
            {
                "role": "branch_controller",
                "action": action.value,
                "validation_passed": validation.passed,
            }
        )
        runtime.tool_state["last_action"] = action.value
        runtime.tool_state["last_observation"] = result
        current_budget = runtime.budget.to_dict()
        final_state_hash = normalized_state_hash(environment, runtime, task)
        record = {
            "record_type": "intervention_branch",
            "experiment_id": self.experiment_id,
            "run_id": self.run_id,
            "source_day8_run_id": prepared.source_day8_run_id,
            "source_task_run_id": prepared.source_task_run_id,
            "task_id": task.task_id,
            "template_id": task.template_id,
            "instance_id": task.instance_id,
            "split": prepared.split,
            "checkpoint_id": prepared.checkpoint_id,
            "branch_id": branch_id,
            "scenario_id": prepared.scenario.scenario_id,
            "fault_type": prepared.scenario.fault_type.value if prepared.scenario.fault_type else None,
            "action": action.value,
            "action_applicable": True,
            "applicability_reason": decision.reason,
            "branch_created": True,
            "success": error is None and validation.passed,
            "validation_passed": validation.passed,
            "validation_checks": dict(validation.checks),
            "extra_tokens": int(current_budget["tokens_used"]) - int(initial_budget["tokens_used"]),
            "extra_tool_calls": int(current_budget["tool_calls_used"]) - int(initial_budget["tool_calls_used"]),
            "extra_latency_seconds": float(current_budget["latency_seconds_used"]) - float(initial_budget["latency_seconds_used"]),
            "termination_reason": (
                "deterministic_verifier_passed"
                if error is None and validation.passed
                else f"branch_error:{type(error).__name__}" if error is not None
                else "deterministic_verifier_failed"
            ),
            "initial_state_hash": prepared.state_hash,
            "restored_state_hash": restored.restored_state_hash,
            "initial_environment_hash": initial_environment_hash,
            "final_state_hash": final_state_hash,
            "final_environment_hash": environment.state_hash(),
            "controller_type": self.controller_type,
            "result": result,
            "error_type": type(error).__name__ if error is not None else None,
            "workspace_path": str(environment.workspace.resolve()),
            "trajectory_path": str(trajectory.resolve()),
        }
        return record

    @staticmethod
    def _execute(
        *,
        action: Action,
        task: TaskSpec,
        runtime_state,
        registry: TaskToolRegistry,
        monitor: ExecutionMonitor,
        checkpoint_id: str,
    ) -> Any:
        if action in {Action.CONTINUE, Action.RETRY}:
            pending = dict(runtime_state.tool_state["pending_operation"])
            steps = [(str(pending["tool_name"]), dict(pending["arguments"]))]
        elif action == Action.REPLAN:
            steps = [
                (str(item["tool_name"]), dict(item["arguments"]))
                for item in runtime_state.tool_state["replan_steps"]
            ]
        else:
            raise ValueError(f"Unsupported action: {action.value}")
        result: Any = None
        for index, (tool_name, arguments) in enumerate(steps, start=1):
            result = monitor.execute_tool(
                step_id=f"{action.value.lower()}_{index:03d}",
                tool_name=tool_name,
                arguments=arguments,
                token_usage={"total_tokens": 0},
                checkpoint_id=checkpoint_id,
                call=lambda tool_name=tool_name, arguments=arguments: registry.execute(
                    task.domain, tool_name, arguments
                ),
            )
        return result
