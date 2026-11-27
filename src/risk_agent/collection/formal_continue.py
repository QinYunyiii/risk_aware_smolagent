"""Formal-context adaptation of the pinned CONTINUE restore/stream/verifier method.

The method body is copied from the pinned v2.2 dependency with only research_flags
and the public fault-tool factory substituted. Hash/restore/budget/replay/label
operations are unchanged; legacy files and their manifests are untouched.
"""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping
import json
import uuid
from risk_agent.monitoring import ExecutionMonitor
from .continue_acceptance_v2 import OutputContractContinueGateRunner
from .continue_outcome import ContinueOutcomeEvaluator
from .continue_gate import (ActionStep, AgentStateAdapter, CheckpointAuditRecord,
 ContinueGateError, ContinueLineage, ContinueTermination, FinalAnswerStep,
 JsonlTraceWriter, PlanningStep, READONLY_REFRESH_POLICY, TOOL_VERSION, TaskEnvironment,
 _json_safe, _prohibited_source_signatures, _read_json, canonical_hash,
 canonical_tool_call_signature, checkpoint_inventory, expected_request_from_checkpoint,
 restore_local_tool_adapter_state, serialize_memory_step, sha256_file, _TeeWriter)
from .formal_faults import create_formal_tools

class FormalOutcomeEvaluator(ContinueOutcomeEvaluator):
    def __init__(self, flags, audit_valid):
        super().__init__(); self.flags = flags; self.audit_valid = audit_valid
    def evaluate(self, **kwargs):
        if not self.audit_valid():
            kwargs['termination'] = ContinueTermination.UNVERIFIABLE
        return replace(super().evaluate(**kwargs), **{k:v for k,v in self.flags.items()
            if k in ('formal_experiment','execution_mode','research_dataset_eligible')})

class FormalContinueRunner(OutputContractContinueGateRunner):
    def __init__(self, *, research_flags, audit_valid, **kwargs):
        self.research_flags = research_flags
        super().__init__(outcome_evaluator=FormalOutcomeEvaluator(research_flags, audit_valid), **kwargs)
    def _validate_identity(self, verified, worker):
        super()._validate_identity(verified, worker)
        identity = verified.manifest.identity
        if (identity['execution_mode'] != self.research_flags['execution_mode']
            or identity['experiment_id'] != self.experiment_id or identity['split'] != 'train'):
            raise ContinueGateError('Formal Train cannot import Smoke or other-split Checkpoints')
    def _monitor(self, **kwargs):
        monitor = super()._monitor(**kwargs)
        monitor.context.update(self.research_flags)
        return monitor

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
        tools = create_formal_tools(
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
                    **self.research_flags,
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
                    **self.research_flags,
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
                **self.research_flags,
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
                **self.research_flags,
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
                **self.research_flags,
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
            **self.research_flags,
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
            **self.research_flags,
        }
