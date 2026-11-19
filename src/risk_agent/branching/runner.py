"""Restore isolated copies and execute Day 6 candidate action branches."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from risk_agent.benchmark import DeterministicVerifier, TaskEnvironment
from risk_agent.benchmark.schema import TaskSpec
from risk_agent.checkpoint import CheckpointManager, normalized_state_hash
from risk_agent.faults.schema import Action
from risk_agent.monitoring import ExecutionMonitor, JsonlTraceWriter, RuntimeState
from risk_agent.tools.task_tool_registry import TaskToolRegistry

from .applicability import BranchApplicabilityEvaluator
from .schema import BranchResult, BranchSetResult


DAY6_ACTIONS = (Action.CONTINUE, Action.RETRY, Action.REPLAN)


class BranchExecutionError(RuntimeError):
    pass


class BranchRunner:
    def __init__(
        self,
        *,
        checkpoint_manager: CheckpointManager,
        branch_root: str | Path,
        experiment_id: str,
        run_id: str,
    ) -> None:
        self.checkpoint_manager = checkpoint_manager
        self.branch_root = Path(branch_root).expanduser().resolve()
        self.branch_root.mkdir(parents=True, exist_ok=True)
        self.experiment_id = experiment_id
        self.run_id = run_id
        self.applicability = BranchApplicabilityEvaluator()
        self.verifier = DeterministicVerifier()

    def run_checkpoint(self, *, checkpoint_id: str, task: TaskSpec) -> BranchSetResult:
        branch_results: list[BranchResult] = []
        environments: list[TaskEnvironment] = []
        runtimes: list[RuntimeState] = []
        for action in DAY6_ACTIONS:
            result, environment, runtime = self._run_branch(
                checkpoint_id=checkpoint_id, task=task, action=action
            )
            branch_results.append(result)
            environments.append(environment)
            runtimes.append(runtime)

        checkpoint_hash = branch_results[0].initial_state_hash
        workspaces = [str(environment.workspace.resolve()) for environment in environments]
        database_files = [
            (environment.database_path.stat().st_dev, environment.database_path.stat().st_ino)
            for environment in environments
        ]
        initial_hashes = {branch.initial_state_hash for branch in branch_results}
        restored_hashes = {branch.restored_state_hash for branch in branch_results}
        initial_environment_hashes = {
            branch.initial_environment_hash for branch in branch_results
        }
        checks = {
            "three_actions_executed": tuple(branch.action for branch in branch_results)
            == DAY6_ACTIONS,
            "all_actions_applicable": all(
                branch.action_applicable for branch in branch_results
            ),
            "initial_state_hashes_identical": len(initial_hashes) == 1,
            "restored_hashes_match_checkpoint": restored_hashes == {checkpoint_hash},
            "initial_environment_hashes_identical": len(initial_environment_hashes) == 1,
            "workspace_paths_unique": len(set(workspaces)) == len(DAY6_ACTIONS),
            "database_files_unique": len(set(database_files)) == len(DAY6_ACTIONS),
            "runtime_objects_unique": len({id(runtime) for runtime in runtimes})
            == len(DAY6_ACTIONS),
            "cross_talk_probe_passed": self._cross_talk_probe(
                checkpoint_id=checkpoint_id, task=task
            ),
            "all_deterministic_validations_passed": all(
                branch.validation_passed for branch in branch_results
            ),
        }
        return BranchSetResult(
            checkpoint_id=checkpoint_id,
            checkpoint_state_hash=checkpoint_hash,
            branches=tuple(branch_results),
            checks=checks,
        )

    def _run_branch(
        self, *, checkpoint_id: str, task: TaskSpec, action: Action
    ) -> tuple[BranchResult, TaskEnvironment, RuntimeState]:
        branch_id = f"{checkpoint_id}_{action.value.lower()}"
        action_root = self.branch_root / checkpoint_id / action.value.lower()
        environment = TaskEnvironment(action_root / "environment", task)
        restored = self.checkpoint_manager.restore(
            checkpoint_id=checkpoint_id, environment=environment
        )
        runtime = restored.runtime_state
        decision = self.applicability.evaluate(action, runtime)
        initial_environment_hash = environment.state_hash()
        initial_budget = runtime.budget.to_dict()
        trajectory_path = action_root / "trajectory.jsonl"
        monitor = ExecutionMonitor(
            writer=JsonlTraceWriter(trajectory_path),
            runtime_state=runtime,
            experiment_id=self.experiment_id,
            run_id=self.run_id,
            task_id=task.task_id,
            template_id=task.template_id,
            instance_id=task.instance_id,
            model_id="local-reference-runner",
            model_version="day6",
            prompt_version="day6-v1",
            tool_version="day6-v1",
            worker_id="local-wsl",
            environment_id=branch_id,
            additional_context={
                "branch_id": branch_id,
                "action": action.value,
                "action_applicable": decision.applicable,
            },
        )
        result: Any = None
        error: Exception | None = None
        validation_checks: dict[str, bool] = {}
        validation_passed = False
        if decision.applicable:
            try:
                result = self._execute_action(
                    action=action,
                    checkpoint_id=checkpoint_id,
                    task=task,
                    environment=environment,
                    runtime=runtime,
                    monitor=monitor,
                )
                validation = self.verifier.verify(task, environment, result)
                validation_checks = dict(validation.checks)
                validation_passed = validation.passed
            except Exception as exc:  # retained in structured branch outcome
                error = exc
        runtime.agent_memory.append(
            {
                "role": "branch_controller",
                "action": action.value,
                "validation_passed": validation_passed,
            }
        )
        runtime.tool_state["last_action"] = action.value
        runtime.tool_state["last_observation"] = result
        final_state_hash = normalized_state_hash(environment, runtime, task)
        current_budget = runtime.budget.to_dict()
        extra_tokens = int(current_budget["tokens_used"]) - int(initial_budget["tokens_used"])
        extra_tool_calls = int(current_budget["tool_calls_used"]) - int(
            initial_budget["tool_calls_used"]
        )
        extra_latency = float(current_budget["latency_seconds_used"]) - float(
            initial_budget["latency_seconds_used"]
        )
        if not decision.applicable:
            termination_reason = "action_not_applicable"
        elif error is not None:
            termination_reason = f"branch_error:{type(error).__name__}"
        elif validation_passed:
            termination_reason = "deterministic_verifier_passed"
        else:
            termination_reason = "deterministic_verifier_failed"
        branch_result = BranchResult(
            checkpoint_id=checkpoint_id,
            branch_id=branch_id,
            action=action,
            action_applicable=decision.applicable,
            applicability_reason=decision.reason,
            initial_state_hash=restored.manifest.state_hash,
            restored_state_hash=restored.restored_state_hash,
            initial_environment_hash=initial_environment_hash,
            final_state_hash=final_state_hash,
            final_environment_hash=environment.state_hash(),
            success=error is None and validation_passed,
            validation_passed=validation_passed,
            validation_checks=validation_checks,
            result=result,
            extra_tokens=extra_tokens,
            extra_tool_calls=extra_tool_calls,
            extra_latency_seconds=extra_latency,
            termination_reason=termination_reason,
            workspace_path=environment.workspace.resolve(),
            database_path=environment.database_path.resolve(),
            trajectory_path=trajectory_path.resolve(),
        )
        JsonlTraceWriter(action_root / "branch_result.jsonl").append(
            {"record_type": "branch_result", **branch_result.to_dict()}
        )
        return branch_result, environment, runtime

    def _execute_action(
        self,
        *,
        action: Action,
        checkpoint_id: str,
        task: TaskSpec,
        environment: TaskEnvironment,
        runtime: RuntimeState,
        monitor: ExecutionMonitor,
    ) -> Any:
        registry = TaskToolRegistry(environment)
        pending = dict(runtime.tool_state["pending_operation"])
        tool_name = str(pending["tool_name"])
        arguments = dict(pending["arguments"])
        if action in {Action.CONTINUE, Action.RETRY}:
            return monitor.execute_tool(
                step_id=f"{action.value.lower()}_001",
                tool_name=tool_name,
                arguments=arguments,
                token_usage={"total_tokens": 0},
                checkpoint_id=checkpoint_id,
                call=lambda: registry.execute(task.domain, tool_name, arguments),
            )
        if action != Action.REPLAN:
            raise BranchExecutionError(f"Unsupported branch action: {action.value}")
        if tool_name != "update_order":
            raise BranchExecutionError("Day 6 replan adapter currently expects update_order")
        observation_spec = dict(runtime.tool_state["replan_observation"])
        observation_tool = str(observation_spec["tool_name"])
        observation_arguments = dict(observation_spec["arguments"])
        observation = monitor.execute_tool(
            step_id="replan_001_observe",
            tool_name=observation_tool,
            arguments=observation_arguments,
            token_usage={"total_tokens": 0},
            checkpoint_id=checkpoint_id,
            call=lambda: registry.execute(
                task.domain, observation_tool, observation_arguments
            ),
        )
        replanned_arguments = dict(arguments)
        replanned_arguments["expected_status"] = str(observation["status"])
        replanned_arguments["idempotency_key"] = f"{arguments['idempotency_key']}-replan"
        return monitor.execute_tool(
            step_id="replan_002_execute",
            tool_name=tool_name,
            arguments=replanned_arguments,
            token_usage={"total_tokens": 0},
            checkpoint_id=checkpoint_id,
            call=lambda: registry.execute(task.domain, tool_name, replanned_arguments),
        )

    def _cross_talk_probe(self, *, checkpoint_id: str, task: TaskSpec) -> bool:
        probe_root = self.branch_root / checkpoint_id / "isolation_probe"
        environment_a = TaskEnvironment(probe_root / "a", task)
        environment_b = TaskEnvironment(probe_root / "b", task)
        restored_a = self.checkpoint_manager.restore(
            checkpoint_id=checkpoint_id, environment=environment_a
        )
        restored_b = self.checkpoint_manager.restore(
            checkpoint_id=checkpoint_id, environment=environment_b
        )
        b_hash_before = normalized_state_hash(environment_b, restored_b.runtime_state, task)
        sentinel = environment_a.resolve_path("probe/sentinel.txt", must_exist=False)
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("branch-a-only\n", encoding="utf-8")
        with environment_a.database() as connection:
            connection.execute(
                "UPDATE orders SET version = version + 100 WHERE order_id = 'ord-100'"
            )
            connection.commit()
        restored_a.runtime_state.agent_memory.append({"probe": "a-only"})
        b_hash_after = normalized_state_hash(environment_b, restored_b.runtime_state, task)
        with sqlite3.connect(environment_b.database_path) as connection:
            b_order = connection.execute(
                "SELECT status, version FROM orders WHERE order_id = 'ord-100'"
            ).fetchone()
        return (
            restored_a.restored_state_hash == restored_b.restored_state_hash
            and b_hash_before == b_hash_after
            and not environment_b.resolve_path(
                "probe/sentinel.txt", must_exist=False
            ).exists()
            and b_order == ("processing", 1)
            and environment_a.database_path.stat().st_ino
            != environment_b.database_path.stat().st_ino
        )
