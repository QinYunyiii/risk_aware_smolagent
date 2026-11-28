"""Frozen formal methods specialized only for Validation identity and records.

Generated from FormalCollector's methods with explicit split/runner substitutions.
Source capture, full CONTINUE, budgets and Verifier remain inherited unchanged.
"""
from __future__ import annotations
import json,traceback
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
from risk_agent.agent.agent_state_adapter import canonical_hash
from risk_agent.monitoring.provider_response_audit import sanitize_audit_value
from .continue_acceptance import read_jsonl
from .continue_acceptance_v2 import engineering_passed
from .formal_source import NoFormalCheckpoint
from .formal_features import extract_decision_features
from .formal_contract import read_records,write_once_json as write_json
from .day5_gate import sha256_file
from .validation_contract import load_validation_contract
from .validation_continue import ValidationContinueRunner


class ValidationLifecycle:
    def _packet(self, record, runtime, task_root):
        snapshot = json.loads((Path(record.checkpoint_path) / "agent/agent_state.json").read_text())["payload"]
        packet = extract_decision_features(agent_payload=snapshot, budget=runtime.budget.to_dict(),
            source_model_records=json.loads((task_root / "source_model_requests.json").read_text()),
            source_tool_records=read_jsonl(task_root / "source_tools.jsonl"))
        packet.update(checkpoint_id=record.checkpoint_id, task_id=record.task_id, split="validation", **self.flags)
        write_json(task_root / "decision_features.json", packet)
        self.journal.append({"record_type":"features_frozen", "task_id":record.task_id,"split":"validation",
            "checkpoint_id":record.checkpoint_id,"features_sha256":packet["features_sha256"],
            "provenance_sha256":canonical_hash(packet["provenance"]), **self.flags})
        self.order.freeze(task_id=record.task_id,checkpoint_id=record.checkpoint_id,features_path=task_root / "decision_features.json")
        return packet

    def _branch(self, task, record, worker, clients, started, attempt):
        phase = "continue" if attempt == 1 else "repeat"
        runner = ValidationContinueRunner(settings=self.settings, tasks=self.profile.tasks,
            run_directory=self.directory / "continue", checkpoint_run=self.directory / "source",
            run_id=self.run_id, experiment_id=self.experiment_id, research_flags=self.flags,
            audit_valid=lambda:self._valid_provider_audit(task.task_id),
            replay_policy=self.profile.config["continue"]["replay_policy"], protocol_id=self.profile.config["protocol_id"],
            agent_factory=self._factory(task, phase, started, clients), clock=self.clock,
            fault_injector_factory=lambda audit,path:self.fault_injector(task.task_id, "branch_"+str(attempt)))
        return runner.run_checkpoint(audit=record, worker=worker, branch_attempt_index=attempt)

    def run_task(self, task_id: str) -> dict[str, Any]:
        load_validation_contract(self.root)
        task, case = self.tasks[task_id], self.cases[task_id]
        if task.split.value != "validation": raise ValueError("Only Validation is authorized; Train/Test remain sealed")
        if any(r.get("task_id") == task_id and r["record_type"] == "task_claimed" for r in read_records(self.journal.path)):
            raise ValueError("Task already claimed; no automatic replay")
        self.journal.append({"record_type":"task_claimed","task_id":task_id,"split":"validation",**self.flags})
        worker = self.workers[case["worker_id"]]
        task_root = self.directory / "tasks" / task_id; task_root.mkdir(parents=True, exist_ok=False)
        started = self.clock(); clients=[]; record=None; packet=None; source_runtime=None; repeated=None
        result = {"task_id":task_id,"template_id":task.template_id,"split":"validation","worker_id":worker.worker_id,
            "started_at":datetime.now(timezone.utc).isoformat(),
            "accepted_y_continue":None,"engineering_passed":False,"batch_must_stop":False,**self.flags}
        try:
            record, environment, source_runtime, model = self._capture(task,worker,task_root,started,clients)
            packet = self._packet(record,source_runtime,task_root)
            source_hash, runtime_hash = environment.state_hash(), canonical_hash(source_runtime.to_dict())
            source_calls = model.executed_calls
            source_wall = self.clock()-started
            component = self._branch(task,record,worker,clients,started,1)
            result["primary_wall_seconds"] = self.clock()-started
            audit = self._audit_complete(task_id)
            audit["complete"] = bool(audit["complete"] and audit["source_events"] == source_calls
                and audit["continue_events"] == self.models[(task_id,"continue")].executed_calls)
            result.update(checkpoint_id=record.checkpoint_id,capture_step=record.step_number,
                component=component,provider_audit=audit,source_model_calls=source_calls,
                source_unchanged=environment.state_hash()==source_hash and canonical_hash(source_runtime.to_dict())==runtime_hash and model.executed_calls==source_calls)
            result["accepted_y_continue"] = component["y_continue"] if component["complete_chain"] and audit["complete"] and result["source_unchanged"] else None
            if result["accepted_y_continue"] is not None and case["scheduled_repeat"]:
                # Independent branch at the SAME checkpoint with Source budget,
                # not the primary branch's already consumed budget or late queue time.
                repeat_started = self.clock()-source_wall
                repeated = self._branch(task,record,worker,clients,repeat_started,2)
                result["repeat"] = repeated
                result["full_provider_audit_passed"] = self._valid_provider_audit(task_id)
                result["source_unchanged"] = bool(result["source_unchanged"] and environment.state_hash()==source_hash and canonical_hash(source_runtime.to_dict())==runtime_hash)
                if not repeated["complete_chain"] or not result["full_provider_audit_passed"]:result["batch_must_stop"] = True
        except NoFormalCheckpoint as exc:
            result.update(capture_status="no_eligible_checkpoint",reason=str(exc))
            events=[r for r in read_jsonl(self.audit.response_path) if r["task_id"]==task_id]
            if not events or any(r.get("request_status")!="response_received" or r.get("usage_completeness")!="complete" for r in events):
                result.update(batch_must_stop=True,infrastructure_failure=True)
        except (Exception, KeyboardInterrupt) as exc:
            result.update(batch_must_stop=True,error_type=type(exc).__name__,
                exception_chain=sanitize_audit_value("".join(traceback.format_exception(exc))))
        finally:
            errors=[]
            for phase,client in clients:
                try:
                    client.close()
                    closed=client.is_closed() if callable(client.is_closed) else client.is_closed
                    if not closed:raise RuntimeError("Owned client still open")
                except Exception as exc:errors.append({"phase":phase,"error_type":type(exc).__name__})
            if not errors:
                try:self.audit.release_aborted(task_id)
                except Exception as exc:errors.append({"phase":"audit_release","error_type":type(exc).__name__})
            result["resources_released"] = not errors
            write_json(task_root / "resource_release.json",{"owned_clients":len(clients),"all_closed":not errors,"close_errors":errors,**self.flags})
            if errors:result["batch_must_stop"]=True
        if record:
            result["engineering_passed"] = engineering_passed(result)
            if not result["engineering_passed"]:result.update(accepted_y_continue=None,batch_must_stop=True)
        result["wall_seconds"] = self.clock()-started
        result["ended_at"] = datetime.now(timezone.utc).isoformat()
        write_json(task_root / "task_result.json",result)
        self._persist_records(task,record,packet,result,repeated,task_root)
        self._complete_journal(task_id,task_root,record)
        return result

    def _persist_records(self, task, record, packet, result, repeated, task_root):
        from .day5_gate import TOOL_VERSION
        provider=[r for r in read_jsonl(self.audit.response_path) if r["task_id"]==task.task_id]
        def measure(rows,key):
            if not rows:return {"value":None,"status":"not_occurred"}
            if any(r.get(key) is None for r in rows):return {"value":None,"status":"not_recorded"}
            return {"value":sum(r[key] for r in rows),"status":"observed"}
        def usage(phase):
            selected=[r for r in provider if r["phase"]==phase]
            return {name:measure(selected,key) for name,key in (("prompt_tokens","prompt_tokens"),
                ("completion_tokens","completion_tokens"),("total_tokens","total_tokens"),
                ("model_latency_seconds","latency_seconds"))}
        common={"task_id":task.task_id,"template_id":task.template_id,"instance_id":task.instance_id,
            "task_run_id":f"{self.run_id}_{task.task_id}_source","split":"validation",
            "protocol_id":self.profile.config["protocol_id"],"model_id":self.settings.model_id,
            "model_version":self.settings.model_version,"prompt_version":self.settings.prompt_version,
            "tool_version":TOOL_VERSION,"verifier_version":"deterministic-verifier-v2.1",
            "verifier_content_hash":sha256_file(self.root/"src/risk_agent/benchmark/verifier.py"),
            "catalog_hash":self.profile.audit_summary()["catalog_sha256"],
            "split_manifest_hash":self.profile.audit_summary()["split_manifest_sha256"],
            "checkpoint_schema_version":"2.0.0","worker_id":self.cases[task.task_id]["worker_id"],
            "generation_config_hash":provider[0]["generation_config_hash"] if provider else None,**self.flags}
        self.records["tasks"].append({"record_type":"task_attempt",**common,
            "task_run_id":f"{self.run_id}_{task.task_id}_source",
            "task_status":"checkpoint_captured" if record else result.get("capture_status","environment_infra_failure"),
            "source_attempt_index":1,"seed":task.seed,"usage":usage("source"),
            "started_at":result["started_at"],"result_ref":str((task_root/"task_result.json").relative_to(self.directory))})
        if record:
            checkpoint_record=self.records["checkpoints"].append({"record_type":"decision_checkpoint",**common,
                "checkpoint_id":record.checkpoint_id,"step_index":record.step_number,
                "hashes":dict(record.hashes),"checkpoint_ref":str(Path(record.checkpoint_path).relative_to(self.directory)),
                "feature_cutoff_event_id":f"{self.run_id}:{task.task_id}:source:step_{record.step_number}",
                "features_ref":str((task_root/"decision_features.json").relative_to(self.directory)) if packet else None})
            for kind,component in (("primary",result.get("component")),("repeat",repeated)):
                if not component:continue
                outcome=self.records["continues"].append({"record_type":"continue_outcome",**common,
                    "branch_kind":kind, "usage":usage("continue" if kind=="primary" else "repeat"),
                    **component, **self.flags})
                if kind=="primary" and result["engineering_passed"] and packet:
                    self.records["risk_candidates"].append({"record_type":"risk_example",**common,
                        "risk_example_id":"risk_"+record.checkpoint_id,"checkpoint_id":record.checkpoint_id,
                        "continue_outcome_id":component["continue_outcome_id"],"label":result["accepted_y_continue"],
                        "label_semantics":"1=continue_success;0=continue_failure",
                        "features":packet["values"],"feature_names":packet["feature_names"],
                        "feature_schema_version":packet["feature_schema_version"],
                        "feature_provenance_ref":str((task_root/"decision_features.json").relative_to(self.directory)),
                        "features_sha256":packet["features_sha256"],"provenance_sha256":canonical_hash(packet["provenance"]),
                        "future_data_excluded":True,"checkpoint_record_sha256":checkpoint_record["record_sha256"],
                        "outcome_record_sha256":outcome["record_sha256"],"cluster_id":task.template_id})
