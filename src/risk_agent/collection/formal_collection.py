"""Train-only formal producer: first decision, frozen features, isolated CONTINUE."""
from __future__ import annotations

import fcntl
import hashlib
import json
import subprocess
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from risk_agent.agent.agent_factory import create_checkpointable_benchmark_agent
from risk_agent.agent.agent_state_adapter import canonical_hash
from risk_agent.agent.worker_router import RemoteModelSettings
from risk_agent.checkpoint import CheckpointManager
from risk_agent.monitoring import JsonlTraceWriter
from risk_agent.monitoring.provider_response_audit import sanitize_audit_value
from .continue_acceptance import read_jsonl
from .continue_acceptance_v2 import engineering_passed
from .formal_audit import ContextWriter, FormalProviderAudit
from .formal_contract import MANIFEST, PLAN, RecordLog, load_formal_contract, read_records, write_once_json as write_json
from .formal_continue import FormalContinueRunner
from .formal_faults import FormalFaultConfig, FormalFaultInjector
from .formal_features import extract_decision_features
from .formal_source import FormalSource, NoFormalCheckpoint
from .output_contract import LosslessAgentFactory
from .day5_gate import checkpoint_inventory, sha256_file


class FormalCollector(FormalSource):
    def __init__(self, *, root: Path, run_directory: Path, run_id: str,
                 offline_fixture=False, authorize_formal_train=False, resume=False,
                 agent_factory=create_checkpointable_benchmark_agent, clock=time.monotonic):
        self.root = root.resolve()
        self.profile, self.plan, self.contract = load_formal_contract(self.root)
        if not offline_fixture and (not authorize_formal_train or agent_factory is not create_checkpointable_benchmark_agent):
            raise ValueError("Real formal Train requires authorization and the production factory")
        self.run_id, self.directory = run_id, run_directory.resolve()
        from risk_agent.benchmark.schema import validate_identifier
        validate_identifier(run_id, "run_id")
        if resume:
            previous = json.loads((self.directory / "run_manifest.json").read_text())
            if (previous["run_id"] != run_id or previous["formal_manifest_sha256"] != sha256_file(self.root / MANIFEST)
                or previous["offline_fixture"] != offline_fixture):
                raise ValueError("Resume cannot change run identity, mode or frozen code")
        else:
            self.directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        self._lock = (self.directory / ".collector.lock").open("a+")
        fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.offline_fixture = offline_fixture
        self.flags = dict(formal_experiment=not offline_fixture,
            execution_mode="model_backed_smoke" if offline_fixture else "model_backed_formal",
            controller_type="qwen3_model", research_dataset_eligible=False, offline_fixture=offline_fixture)
        self.experiment_id = self.plan["formal_experiment_id"]
        self.clock, self.base_factory = clock, LosslessAgentFactory(agent_factory)
        self.models = {}
        self.settings = RemoteModelSettings.from_yaml(self.root / self.profile.config["dependencies"]["remote_model_config"])
        self.tasks = {t.task_id:t for t in self.profile.tasks}
        self.cases = {c["task_id"]:c for c in self.plan["tasks"]}
        self.workers = {w.worker_id:w for w in self.settings.workers}
        self.audit = FormalProviderAudit(self.directory / "audit", flags=self.flags,
            experiment_id=self.experiment_id, tasks=self.tasks)
        self.manager = CheckpointManager(self.directory / "source/checkpoints")
        self.journal = RecordLog(self.directory / "journal.jsonl", run_id=run_id, experiment_id=self.experiment_id)
        self.records = {name:RecordLog(self.directory / "records" / (name + ".jsonl"),
            run_id=run_id, experiment_id=self.experiment_id) for name in ("tasks", "checkpoints", "continues", "risk_candidates")}
        if not resume:
            write_json(self.directory / "run_manifest.json", {"run_id":run_id,
                "formal_manifest_sha256":sha256_file(self.root / MANIFEST),
                "parent_manifest_sha256":self.profile.manifest_sha256,
                "plan_sha256":sha256_file(self.root / PLAN), "protocol_id":self.profile.config["protocol_id"],
                "git_commit":subprocess.check_output(["git","rev-parse","HEAD"],cwd=self.root,text=True).strip(),
                "git_worktree_dirty":bool(subprocess.check_output(["git","status","--short"],cwd=self.root,text=True).strip()),
                "authorized_split":"train", "primary_target":96, "repeat_target":19,
                "inference":self.settings.inference, **self.flags})
            for name in {MANIFEST, *self.contract["files"]}:
                target = self.directory / "frozen_inputs" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as f:f.write((self.root / name).read_bytes())

    def close(self):
        if not self._lock.closed:
            fcntl.flock(self._lock, fcntl.LOCK_UN); self._lock.close()

    def fault_injector(self, task_id: str, phase: str):
        context = {"experiment_id":self.experiment_id,"run_id":self.run_id,
            "task_id":task_id,"split":self.tasks[task_id].split.value,**self.flags}
        writer = ContextWriter(JsonlTraceWriter(self.directory / "internal" / task_id / (phase + ".jsonl")), context)
        return FormalFaultInjector(FormalFaultConfig.from_case(self.cases[task_id]), trace_writer=writer)

    def _factory(self, task, phase, started, clients):
        base=super()._factory(task,phase,started,clients)
        def create(*args,**kwargs):
            agent,model,underlying=base(*args,**kwargs)
            self.models[(task.task_id,phase)]=model
            return agent,model,underlying
        return create

    def _valid_provider_audit(self, task_id):
        audit=self._audit_complete(task_id)
        expected=sum(m.executed_calls for (tid,phase),m in self.models.items() if tid==task_id)
        return bool(audit["complete"] and audit["provider_events"]==expected)

    def _packet(self, record, runtime, task_root):
        snapshot = json.loads((Path(record.checkpoint_path) / "agent/agent_state.json").read_text())["payload"]
        packet = extract_decision_features(agent_payload=snapshot, budget=runtime.budget.to_dict(),
            source_model_records=json.loads((task_root / "source_model_requests.json").read_text()),
            source_tool_records=read_jsonl(task_root / "source_tools.jsonl"))
        packet.update(checkpoint_id=record.checkpoint_id, task_id=record.task_id, split="train", **self.flags)
        write_json(task_root / "decision_features.json", packet)
        self.journal.append({"record_type":"features_frozen", "task_id":record.task_id,"split":"train",
            "checkpoint_id":record.checkpoint_id,"features_sha256":packet["features_sha256"],
            "provenance_sha256":canonical_hash(packet["provenance"]), **self.flags})
        return packet

    def _branch(self, task, record, worker, clients, started, attempt):
        phase = "continue" if attempt == 1 else "repeat"
        runner = FormalContinueRunner(settings=self.settings, tasks=self.profile.tasks,
            run_directory=self.directory / "continue", checkpoint_run=self.directory / "source",
            run_id=self.run_id, experiment_id=self.experiment_id, research_flags=self.flags,
            audit_valid=lambda:self._valid_provider_audit(task.task_id),
            replay_policy=self.profile.config["continue"]["replay_policy"], protocol_id=self.profile.config["protocol_id"],
            agent_factory=self._factory(task, phase, started, clients), clock=self.clock,
            fault_injector_factory=lambda audit,path:self.fault_injector(task.task_id, "branch_"+str(attempt)))
        return runner.run_checkpoint(audit=record, worker=worker, branch_attempt_index=attempt)

    def run_task(self, task_id: str) -> dict[str, Any]:
        load_formal_contract(self.root)
        task, case = self.tasks[task_id], self.cases[task_id]
        if task.split.value != "train": raise ValueError("Only Train is authorized; Validation/Test remain sealed")
        if any(r.get("task_id") == task_id and r["record_type"] == "task_claimed" for r in read_records(self.journal.path)):
            raise ValueError("Task already claimed; no automatic replay")
        self.journal.append({"record_type":"task_claimed","task_id":task_id,"split":"train",**self.flags})
        worker = self.workers[case["worker_id"]]
        task_root = self.directory / "tasks" / task_id; task_root.mkdir(parents=True, exist_ok=False)
        started = self.clock(); clients=[]; record=None; packet=None; source_runtime=None; repeated=None
        result = {"task_id":task_id,"template_id":task.template_id,"split":"train","worker_id":worker.worker_id,
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
            "task_run_id":f"{self.run_id}_{task.task_id}_source","split":"train",
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

    def _complete_journal(self, task_id, task_root, record):
        # Per-task immutable archives avoid hashing global JSONL files that will append.
        providers=[r for r in read_jsonl(self.audit.response_path) if r["task_id"]==task_id]
        ids={r["audit_request_id"] for r in providers}
        parses=[r for r in read_jsonl(self.audit.parsing_path) if r["audit_request_id"] in ids]
        write_json(task_root/"provider_archive.json",providers);write_json(task_root/"parsing_archive.json",parses)
        roots=[task_root,self.directory/"source/environments"/task_id,self.directory/"internal"/task_id]
        if record:roots +=[Path(record.checkpoint_path),self.directory/"continue/branches"/record.checkpoint_id]
        inventory={str(p.relative_to(self.directory)):sha256_file(p) for base in roots for p in base.rglob("*") if p.is_file()}
        self.journal.append({"record_type":"task_completed","task_id":task_id,"split":"train",
            "artifacts":inventory,**self.flags})

    def run_train(self, *, task_limit=None):
        plans=[c for c in self.plan["tasks"] if c["split"]=="train"]
        if task_limit is not None:
            if not self.offline_fixture:raise ValueError("Live Train cohort cannot be replaced or shortened")
            plans=plans[:task_limit]
        journal=read_records(self.journal.path)
        claimed={r["task_id"] for r in journal if r["record_type"]=="task_claimed"}
        completed={r["task_id"]:r for r in journal if r["record_type"]=="task_completed"}
        if claimed-set(completed):raise RuntimeError("Interrupted task requires review; automatic replay refused")
        for record in completed.values():
            for name,digest in record["artifacts"].items():
                path=(self.directory/name).resolve()
                if not path.is_relative_to(self.directory) or sha256_file(path)!=digest:raise ValueError("Completed task artifact drift")
        stopped=False
        for case in plans:
            if case["task_id"] in completed:
                existing=json.loads((self.directory/"tasks"/case["task_id"]/"task_result.json").read_text())
                if existing["batch_must_stop"]:raise RuntimeError("Previous infrastructure/safety stop requires review")
                continue
            result=self.run_task(case["task_id"])
            if result["batch_must_stop"]:stopped=True;break
        return self.summary(stopped)

    def summary(self, stopped):
        rows=read_records(self.records["risk_candidates"].path)
        labels=[r["label"] for r in rows]
        completed=read_records(self.journal.path)
        count=sum(r["record_type"]=="task_completed" for r in completed)
        full=count==96 and not stopped
        outcomes=read_records(self.records["continues"].path)
        repeats=[r for r in outcomes if r["branch_kind"]=="repeat"]
        observed={}; f6_states=set(); valid_tasks={r["task_id"] for r in rows}
        for case in self.plan["tasks"]:
            if case["split"]!="train" or case["task_id"] not in valid_tasks:continue
            events=[]
            for phase in ("source","branch_1"):
                events+=read_jsonl(self.directory/"internal"/case["task_id"]/(phase+".jsonl"))
            matching=[e for e in events if e.get("triggered")]
            f6_states.update(e["commit_status"] for e in matching if e["fault_type"]=="F6_PARTIAL_FAILURE")
            if matching or case["fault_type"]=="F0_NO_FAULT" and events:
                observed.setdefault(case["domain"],set()).add(case["fault_type"])
        common={"F0_NO_FAULT","F1_TIMEOUT","F2_API_503","F3_INVALID_ARGUMENT","F5_REPEATED_ACTION"}
        coverage=all(common <= observed.get(domain,set()) for domain in {self.tasks[c["task_id"]].domain.value
            for c in self.plan["tasks"] if c["split"]=="train"})
        coverage=coverage and {"F4_STALE_OBSERVATION","F6_PARTIAL_FAILURE"} <= observed.get("api_workflow",set())
        coverage=coverage and {"not_committed","committed","unknown"} <= f6_states
        quality=full and len(rows)>=72 and 0 in labels and 1 in labels and coverage
        summary={"run_id":self.run_id,"split":"train","planned_primary":96,"attempted":count,
            "valid_primary":len(rows),"successes":labels.count(1),"valid_failures":labels.count(0),
            "scheduled_repeat_targets":19,"complete":full,"quality_passed":quality,
            "repeat_branches_observed":len(repeats),
            "repeat_branches_valid":sum(r["complete_chain"] for r in repeats),
            "observed_fault_types_by_domain":{k:sorted(v) for k,v in observed.items()},
            "fault_coverage_passed":coverage,
            "observed_f6_commit_states":sorted(f6_states),
            "batch_stopped":stopped,"formal_dataset_ready":False,
            "gate":"TRAIN_COLLECTION_COMPLETE" if full else "TRAIN_COLLECTION_BLOCKED" if stopped else "TRAIN_COLLECTION_IN_PROGRESS",
            **self.flags}
        index=len(list(self.directory.glob("collection_summary_*.json")))+1
        if quality and not self.offline_fixture:
            # New qualification record referencing already-formal raw candidates;
            # immutable rows (and all Smoke data) are never re-labelled in place.
            write_json(self.directory/f"train_release_{index:04d}.json",{
                "record_type":"dataset_eligibility_release","split":"train","run_id":self.run_id,
                "formal_experiment":True,"research_dataset_eligible":True,
                "formal_manifest_sha256":sha256_file(self.root/MANIFEST),
                "risk_candidate_record_hashes":[r["record_sha256"] for r in rows],
                "primary_rows":len(rows),"repeat_rows_are_not_independent_examples":True})
        write_json(self.directory/f"collection_summary_{index:04d}.json",summary)
        return summary
