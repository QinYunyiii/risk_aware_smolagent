"""Validation-only producer over frozen Source, CONTINUE and causal audit code."""
from __future__ import annotations

import fcntl,json,subprocess,time
from pathlib import Path
from typing import Any

from risk_agent.agent.agent_factory import create_checkpointable_benchmark_agent
from risk_agent.agent.worker_router import RemoteModelSettings
from risk_agent.checkpoint import CheckpointManager
from .budget_boundary_audit import BudgetBoundaryAgentFactory
from .causal_collection import CausalFormalCollector,CausalProviderAudit,CAUSAL_MANIFEST
from .decision_order import DecisionOrderLedger
from .formal_budget_revision import REVISION_MANIFEST
from .formal_contract import MANIFEST,PLAN,RecordLog,read_records,write_once_json
from .continue_acceptance import read_jsonl
from .day5_gate import sha256_file
from .validation_contract import VALIDATION_MANIFEST,load_validation_contract
from .validation_lifecycle import ValidationLifecycle


class ValidationCollector(ValidationLifecycle,CausalFormalCollector):
    def __init__(self,*,root:Path,run_directory:Path,run_id:str,offline_fixture:bool=False,
                 authorize_validation:bool=False,agent_factory=create_checkpointable_benchmark_agent,
                 clock=time.monotonic):
        self.root=root.resolve()
        self.profile,self.plan,self.validation_contract,self.validation_config=load_validation_contract(self.root)
        if not offline_fixture and(not authorize_validation or agent_factory is not create_checkpointable_benchmark_agent):
            raise ValueError("Real Validation requires explicit authorization and the production factory")
        if offline_fixture and agent_factory is create_checkpointable_benchmark_agent:
            raise ValueError("Offline Validation requires an explicit fixture backend")
        from risk_agent.benchmark.schema import validate_identifier
        validate_identifier(run_id,"run_id")
        self.run_id,self.directory=run_id,run_directory.resolve()
        self.directory.mkdir(parents=True,exist_ok=False,mode=0o700)
        self._lock=(self.directory/".collector.lock").open("a+")
        fcntl.flock(self._lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            self.offline_fixture=offline_fixture
            self.flags={"formal_experiment":not offline_fixture,"execution_mode":"model_backed_smoke"if offline_fixture else"model_backed_formal",
                "controller_type":"qwen3_model","research_dataset_eligible":False,"offline_fixture":offline_fixture,
                "implementation_revision":"formal-validation-v1","validation_manifest_sha256":sha256_file(self.root/VALIDATION_MANIFEST),
                "budget_audit_manifest_sha256":sha256_file(self.root/REVISION_MANIFEST),
                "causal_manifest_sha256":sha256_file(self.root/CAUSAL_MANIFEST)}
            self.experiment_id=self.plan["formal_experiment_id"]
            self.clock,self.base_factory=clock,BudgetBoundaryAgentFactory(agent_factory)
            self.models={}
            self.settings=RemoteModelSettings.from_yaml(self.root/self.profile.config["dependencies"]["remote_model_config"])
            self.tasks={t.task_id:t for t in self.profile.tasks}
            self.cases={c["task_id"]:c for c in self.plan["tasks"]}
            self.allowed=set(self.validation_config["task_ids"])
            self.workers={w.worker_id:w for w in self.settings.workers}
            self.order=DecisionOrderLedger(self.directory,run_id=run_id,experiment_id=self.experiment_id)
            self.audit=CausalProviderAudit(self.directory/"audit",ledger=self.order,flags=self.flags,
                experiment_id=self.experiment_id,tasks=self.tasks)
            self.manager=CheckpointManager(self.directory/"source/checkpoints")
            self.journal=RecordLog(self.directory/"journal.jsonl",run_id=run_id,experiment_id=self.experiment_id)
            self.records={n:RecordLog(self.directory/"records"/(n+".jsonl"),run_id=run_id,experiment_id=self.experiment_id)
                          for n in("tasks","checkpoints","continues","risk_candidates")}
            write_once_json(self.directory/"run_manifest.json",{"run_id":run_id,"authorized_split":"validation",
                "primary_target":32,"repeat_target":7,"minimum_valid_primary":24,
                "formal_manifest_sha256":sha256_file(self.root/MANIFEST),"parent_manifest_sha256":self.profile.manifest_sha256,
                "plan_sha256":sha256_file(self.root/PLAN),"protocol_id":self.profile.config["protocol_id"],
                "inference":self.settings.inference,"git_commit":subprocess.check_output(["git","rev-parse","HEAD"],cwd=self.root,text=True).strip(),
                "git_worktree_dirty":bool(subprocess.check_output(["git","status","--short"],cwd=self.root,text=True).strip()),**self.flags})
            snapshots={VALIDATION_MANIFEST,*self.validation_contract["files"]}
            for parent in(MANIFEST,REVISION_MANIFEST,CAUSAL_MANIFEST):
                snapshots.add(parent);snapshots.update(json.loads((self.root/parent).read_text())["files"])
            for name in snapshots:
                target=self.directory/"frozen_inputs"/name;target.parent.mkdir(parents=True,exist_ok=True)
                with target.open("xb")as handle:handle.write((self.root/name).read_bytes())
        except BaseException:
            self.close();raise

    def run_task(self,task_id:str)->dict[str,Any]:
        if task_id not in self.allowed:
            raise ValueError("Only Validation is authorized; Train/Test remain sealed")
        return ValidationLifecycle.run_task(self,task_id)

    def _factory(self,task,phase,started,clients):
        base=super()._factory(task,phase,started,clients)
        def create(settings,worker,tools,runtime,**kwargs):
            if task.task_id not in self.allowed or task.split.value!="validation":
                raise ValueError("Validation factory cannot execute another split")
            if worker.worker_id!=self.cases[task.task_id]["worker_id"]:
                raise ValueError("Validation worker assignment differs; failover forbidden")
            for tool in tools:tool.monitor.context["split"]="validation"
            return base(settings,worker,tools,runtime,**kwargs)
        return create

    def run_validation(self)->dict[str,Any]:
        if read_records(self.journal.path):
            raise ValueError("Validation work already claimed; automatic replay/resume refused")
        stopped=False
        for task_id in self.validation_config["task_ids"]:
            result=self.run_task(task_id)
            if result["batch_must_stop"]:stopped=True;break
        return self.summary(stopped)

    def run_train(self,*args,**kwargs):
        raise ValueError("Validation entry cannot execute Train")

    def summary(self,stopped:bool)->dict[str,Any]:
        rows=read_records(self.records["risk_candidates"].path)
        tasks=read_records(self.records["tasks"].path)
        repeats=[r for r in read_records(self.records["continues"].path)if r["branch_kind"]=="repeat"]
        valid_ids={r["task_id"]for r in rows};observed={};states=set()
        for task_id in self.validation_config["task_ids"]:
            if task_id not in valid_ids:continue
            case=self.cases[task_id];events=[]
            for phase in("source","branch_1"):events+=read_jsonl(self.directory/"internal"/task_id/(phase+".jsonl"))
            triggered=[e for e in events if e.get("triggered")]
            if triggered or case["fault_type"]=="F0_NO_FAULT"and events:
                observed.setdefault(case["domain"],set()).add(case["fault_type"])
            states.update(e["commit_status"]for e in triggered if e["fault_type"]=="F6_PARTIAL_FAILURE")
        common={"F0_NO_FAULT","F1_TIMEOUT","F2_API_503","F3_INVALID_ARGUMENT","F5_REPEATED_ACTION"}
        coverage=all(common<=observed.get(d,set())for d in("data_analysis","file_retrieval","api_workflow","calculation_logic"))
        coverage=coverage and {"F4_STALE_OBSERVATION","F6_PARTIAL_FAILURE"}<=observed.get("api_workflow",set())
        coverage=coverage and {"committed","unknown"}<=states
        targets=set(self.validation_config["repeat_task_ids"]);expected=targets&valid_ids;actual=[r["task_id"]for r in repeats]
        repeat_valid=all(r["complete_chain"]for r in repeats)and len(set(actual))==len(actual)and set(actual)==expected
        complete=len(tasks)==32 and not stopped
        labels=[r["label"]for r in rows]
        quality=complete and len(rows)>=24 and coverage and repeat_valid
        result={"run_id":self.run_id,"split":"validation","gate":"VALIDATION_COLLECTION_COMPLETE"if complete else"VALIDATION_COLLECTION_BLOCKED",
            "complete":complete,"batch_stopped":stopped,"quality_passed":quality,"attempted":len(tasks),
            "planned_primary":32,"valid_primary":len(rows),"minimum_valid_primary":24,
            "successes":labels.count(1),"valid_failures":labels.count(0),"single_class_labels":len(set(labels))<2,
            "auc_policy":"undefined_if_single_class","scheduled_repeat_targets":7,
            "repeat_branches_observed":len(repeats),"repeat_branches_valid":sum(r["complete_chain"]for r in repeats),
            "repeat_targets_not_occurred":sorted(targets-set(actual)),"fault_coverage_passed":coverage,
            "observed_fault_types_by_domain":{k:sorted(v)for k,v in observed.items()},"observed_f6_commit_states":sorted(states),
            "formal_dataset_ready":False,"allowed_for_training":False,**self.flags}
        write_once_json(self.directory/"validation_collection_summary.json",result)
        return result

    def publish_validation_eligibility(self,summary:dict[str,Any],*,resources_released:bool)->None:
        if not resources_released or not summary.get("quality_passed")or self.offline_fixture:
            raise ValueError("Validation eligibility requires qualified real evidence and released resources")
        rows=read_records(self.records["risk_candidates"].path)
        stored=json.loads((self.directory/"validation_collection_summary.json").read_text())
        if (not stored["quality_passed"]or not stored["formal_experiment"]or stored["offline_fixture"]
                or any(summary.get(k)!=stored[k]for k in("run_id","split","attempted","valid_primary","quality_passed"))
                or len(rows)!=stored["valid_primary"]or len(rows)<24
                or any(r["split"]!="validation"or not r["formal_experiment"]or r["offline_fixture"]for r in rows)):
            raise ValueError("Validation qualification cannot promote fixtures or mismatched evidence")
        completed=[r for r in read_records(self.journal.path)if r["record_type"]=="task_completed"]
        if [r["task_id"]for r in completed]!=self.validation_config["task_ids"]:
            raise ValueError("Validation completion evidence is incomplete")
        for record in completed:
            for name,digest in record["artifacts"].items():
                path=(self.directory/name).resolve()
                if not path.is_relative_to(self.directory)or sha256_file(path)!=digest:
                    raise ValueError("Validation immutable evidence changed before eligibility release")
        write_once_json(self.directory/"validation_eligibility_release.json",{"split":"validation","run_id":self.run_id,
            "formal_experiment":True,"research_dataset_eligible":True,"allowed_for_training":False,
            "allowed_usage":["model_selection","threshold_selection","calibration_evaluation"],
            "validation_manifest_sha256":self.flags["validation_manifest_sha256"],
            "risk_candidate_record_hashes":[r["record_sha256"]for r in rows]})
