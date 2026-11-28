"""Opt-in Validation revision over the unchanged frozen lifecycle."""
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


from risk_agent.agent.agent_state_adapter import canonical_hash
from .validation_collection import ValidationCollector
from .aggregate_target_contract import load_aggregate_contract,TARGET_CONFIG,TARGET_MANIFEST
from .aggregate_target_policy import AggregateFaultInjector,POLICY
from .formal_faults import FormalFaultConfig, FormalFaultInjector


class AggregateValidationCollector(ValidationCollector):
    def __init__(self,*,root:Path,run_directory:Path,run_id:str,offline_fixture:bool=False,
                 authorize_aggregate_validation:bool=False,agent_factory=create_checkpointable_benchmark_agent,
                 clock=time.monotonic):
        self.root=root.resolve()
        self.profile,self.plan,self.validation_contract,self.validation_config,self.target_contract,self.target_config=load_aggregate_contract(self.root)
        if not offline_fixture and(not authorize_aggregate_validation or agent_factory is not create_checkpointable_benchmark_agent):
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
                "implementation_revision":"formal-validation-aggregate-family-v1",
                "aggregate_manifest_sha256":sha256_file(self.root/TARGET_MANIFEST),
                "target_match_policy":POLICY,"policy_sha256":sha256_file(self.root/TARGET_CONFIG),
                "resolved_plan_sha256":canonical_hash(self.plan),"validation_manifest_sha256":sha256_file(self.root/VALIDATION_MANIFEST),
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
            write_once_json(self.directory/"resolved_plan.json",self.plan)
            snapshots={VALIDATION_MANIFEST,*self.validation_contract["files"],TARGET_MANIFEST,*self.target_contract["files"]}
            for parent in(MANIFEST,REVISION_MANIFEST,CAUSAL_MANIFEST):
                snapshots.add(parent);snapshots.update(json.loads((self.root/parent).read_text())["files"])
            for name in snapshots:
                target=self.directory/"frozen_inputs"/name;target.parent.mkdir(parents=True,exist_ok=True)
                with target.open("xb")as handle:handle.write((self.root/name).read_bytes())
        except BaseException:
            self.close();raise

    def _check_policy_binding(self) -> None:
        _,plan,_,_,_,_=load_aggregate_contract(self.root)
        if (canonical_hash(plan)!=self.flags['resolved_plan_sha256']
                or canonical_hash(self.plan)!=canonical_hash(plan)
                or self.cases!={c['task_id']:c for c in plan['tasks']}
                or self.flags['aggregate_manifest_sha256']!=sha256_file(self.root/TARGET_MANIFEST)
                or self.flags['policy_sha256']!=sha256_file(self.root/TARGET_CONFIG)):
            raise ValueError('Aggregate policy binding changed')

    def run_task(self,task_id: str) -> dict[str, Any]:
        self._check_policy_binding()
        return super().run_task(task_id)

    def fault_injector(self,task_id: str,phase: str) -> FormalFaultInjector:
        if task_id not in self.allowed:raise ValueError('Only Validation is authorized')
        base=super().fault_injector(task_id,phase)
        if 'target_match_policy'not in base.config.parameters:return base
        return AggregateFaultInjector(base.config,domain=self.tasks[task_id].domain.value,trace_writer=base.trace_writer)

    def _factory(self,task,phase,started,clients):
        base=super()._factory(task,phase,started,clients)
        def create(settings,worker,tools,runtime,**kwargs):
            expected=FormalFaultConfig.from_case(self.cases[task.task_id])
            active=expected.parameters.get('target_match_policy')==POLICY
            for tool in tools:
                injector=tool.fault_injector
                if (injector is None or injector.config!=expected
                        or active and type(injector)is not AggregateFaultInjector):
                    raise ValueError('Aggregate fault adapter identity/type mismatch before SDK')
            return base(settings,worker,tools,runtime,**kwargs)
        return create

    def publish_validation_eligibility(self,summary:dict[str,Any],*,resources_released:bool)->None:
        self._check_policy_binding()
        if not resources_released or not summary.get("quality_passed")or self.offline_fixture:
            raise ValueError("Validation eligibility requires qualified real evidence and released resources")
        rows=read_records(self.records["risk_candidates"].path)
        if any(r.get("aggregate_manifest_sha256")!=self.flags["aggregate_manifest_sha256"]
               or r.get("policy_sha256")!=self.flags["policy_sha256"] for r in rows):
            raise ValueError("Aggregate eligibility policy provenance mismatch")
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
            "aggregate_manifest_sha256":self.flags["aggregate_manifest_sha256"],
            "target_match_policy":POLICY,"policy_sha256":self.flags["policy_sha256"],
            "resolved_plan_sha256":self.flags["resolved_plan_sha256"],"experiment_id":self.experiment_id,
            "risk_candidate_record_hashes":[r["record_sha256"]for r in rows]})
