"""Explicit parent/child execution of only the unused approved Train schedule."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from risk_agent.agent.agent_state_adapter import canonical_hash
from .causal_collection import CausalFormalCollector
from .formal_budget_revision import prepare_continuation_plan
from .formal_contract import read_records,write_once_json
from .continue_acceptance import read_jsonl
from .day5_gate import sha256_file


class TrainContinuationCollector(CausalFormalCollector):
    def __init__(self,*,parent_run:Path,parent_manifest_sha256:str,parent_fixture:bool=False,**kwargs:Any):
        self.parent_run=parent_run.resolve()
        directory=Path(kwargs["run_directory"]).resolve()
        if directory.is_relative_to(self.parent_run):raise ValueError("Child cannot write inside historical parent")
        self.continuation=prepare_continuation_plan(root=kwargs["root"],source_run=self.parent_run,
            expected_manifest_sha256=parent_manifest_sha256,offline_fixture=parent_fixture)
        if kwargs.get("resume"):raise ValueError("Interrupted child requires separate review; automatic resume refused")
        self.allowed={c["task_id"]for c in self.continuation["remaining_tasks"]}
        self.scope_sha=canonical_hash(self.continuation)
        super().__init__(**kwargs)
        self.flags.update(parent_run_id=self.continuation["parent_run_id"],continuation_scope_sha256=self.scope_sha)
        write_once_json(self.directory/"continuation_binding.json",{
            "run_id":self.run_id,"parent_run_id":self.continuation["parent_run_id"],
            "parent_artifact_manifest_sha256":parent_manifest_sha256,
            "continuation_scope_sha256":self.scope_sha,"remaining_primary":len(self.allowed),
            "remaining_repeats":len(self.continuation["remaining_repeat_task_ids"]),
            "inherited_run_manifest_targets_describe_original_cohort":True,
            "allowed_tasks":self.continuation["remaining_tasks"],
            "excluded_attempted_task_ids":self.continuation["excluded_attempted_task_ids"],
            "censored_task_ids_preserved":self.continuation["censored_task_ids_preserved"]})

    def run_task(self,task_id):
        if task_id not in self.allowed:raise ValueError("Task is outside the explicit unused Train schedule")
        return super().run_task(task_id)

    def run_train(self):
        if read_records(self.journal.path):raise ValueError("Child invocation already claimed work; no automatic replay")
        stopped=False
        for case in self.continuation["remaining_tasks"]:
            result=self.run_task(case["task_id"])
            if result["batch_must_stop"]:stopped=True;break
        return self.summary(stopped)

    def summary(self,stopped):
        tasks=read_records(self.records["tasks"].path);risks=read_records(self.records["risk_candidates"].path)
        outcomes=read_records(self.records["continues"].path)
        repeats=[r for r in outcomes if r["branch_kind"]=="repeat"]
        complete=len(tasks)==len(self.allowed)and not stopped
        result={"run_id":self.run_id,"parent_run_id":self.continuation["parent_run_id"],
            "planned_primary":len(self.allowed),"attempted":len(tasks),"valid_primary":len(risks),
            "successes":sum(r["label"]==1 for r in risks),"valid_failures":sum(r["label"]==0 for r in risks),
            "scheduled_repeat_targets":len(self.continuation["remaining_repeat_task_ids"]),
            "repeat_branches_observed":len(repeats),"repeat_branches_valid":sum(r["complete_chain"]for r in repeats),
            "complete":complete,"batch_stopped":stopped,"formal_dataset_ready":False,
            "gate":"TRAIN_CONTINUATION_COMPLETE"if complete else"TRAIN_CONTINUATION_BLOCKED",**self.flags}
        write_once_json(self.directory/"child_collection_summary.json",result)
        return result


def audit_train_union(collector:TrainContinuationCollector,child_summary:dict[str,Any],*,release:bool=False):
    parent=collector.parent_run;child=collector.directory
    # Revalidate the trusted parent rather than accepting a stale in-memory list.
    fresh=prepare_continuation_plan(root=collector.root,source_run=parent,
        expected_manifest_sha256=collector.continuation["parent_artifact_manifest_sha256"],
        offline_fixture=collector.continuation["qualification_only"])
    if canonical_hash(fresh)!=collector.scope_sha:raise ValueError("Parent scope changed during child execution")
    all_tasks=[];all_risks=[];all_repeats=[];observed={};states=set();references=[];nulls=[]
    for directory in(parent,child):
        tasks=read_records(directory/"records/tasks.jsonl");risks=read_records(directory/"records/risk_candidates.jsonl")
        outcomes=read_records(directory/"records/continues.jsonl")
        all_tasks.extend(tasks);all_risks.extend(risks);all_repeats.extend(r for r in outcomes if r["branch_kind"]=="repeat")
        for record in read_records(directory/"journal.jsonl"):
            if record["record_type"]=="task_completed":
                for name,digest in record["artifacts"].items():
                    path=(directory/name).resolve()
                    if not path.is_relative_to(directory)or sha256_file(path)!=digest:raise ValueError("Task evidence drift")
        for task in tasks:
            result=json.loads((directory/"tasks"/task["task_id"]/"task_result.json").read_text())
            if result["accepted_y_continue"]is None:nulls.append(task["task_id"])
        for risk in risks:
            case=collector.cases[risk["task_id"]]
            result=json.loads((directory/"tasks"/risk["task_id"]/"task_result.json").read_text())
            if risk["label"]not in(0,1)or not result["engineering_passed"]:raise ValueError("Invalid risk example")
            component=result["component"]
            if not all(component["restore_hashes"].values())or not component["first_input_exact"]:
                raise ValueError("Restore/input qualification missing")
            if directory==child:
                providers=json.loads((directory/"tasks"/risk["task_id"]/"provider_archive.json").read_text())
                if not collector.order.verify(risk["task_id"],providers):raise ValueError("Child causal order failed")
            events=[]
            for phase in("source","branch_1"):
                events+=read_jsonl(directory/"internal"/risk["task_id"]/(phase+".jsonl"))
            triggered=[e for e in events if e.get("triggered")]
            if triggered or case["fault_type"]=="F0_NO_FAULT"and events:
                observed.setdefault(case["domain"],set()).add(case["fault_type"])
            states.update(e["commit_status"]for e in triggered if e["fault_type"]=="F6_PARTIAL_FAILURE")
            references.append({"run_id":risk["run_id"],"task_id":risk["task_id"],"record_sha256":risk["record_sha256"]})
    ids=[r["task_id"]for r in all_tasks]
    if len(set(ids))!=len(ids):raise ValueError("Duplicate Source attempts across parent/child")
    planned=[c["task_id"]for c in collector.plan["tasks"]if c["split"]=="train"]
    full=bool(child_summary["complete"]and ids==planned)
    common={"F0_NO_FAULT","F1_TIMEOUT","F2_API_503","F3_INVALID_ARGUMENT","F5_REPEATED_ACTION"}
    coverage=all(common<=observed.get(d,set())for d in("data_analysis","file_retrieval","api_workflow","calculation_logic"))
    coverage=coverage and {"F4_STALE_OBSERVATION","F6_PARTIAL_FAILURE"}<=observed.get("api_workflow",set())
    coverage=coverage and {"committed","not_committed","unknown"}<=states
    labels=[r["label"]for r in all_risks]
    quality=full and len(labels)>=72 and 0 in labels and 1 in labels and coverage
    repeats_valid=sum(r["complete_chain"]for r in all_repeats)
    valid_ids={r["task_id"]for r in all_risks}
    targets={c["task_id"]for c in collector.plan["tasks"]if c["split"]=="train"and c["scheduled_repeat"]}
    eligible=targets&valid_ids
    repeat_ids=[r["task_id"]for r in all_repeats]
    quality=quality and repeats_valid==len(all_repeats)and len(set(repeat_ids))==len(repeat_ids)and set(repeat_ids)==eligible
    result={"gate":"TRAIN_COHORT_COMPLETE_WITH_CENSORING"if full and nulls else"TRAIN_COHORT_COMPLETE"if full else"TRAIN_COHORT_BLOCKED",
        "parent_run_id":collector.continuation["parent_run_id"],"child_run_id":collector.run_id,
        "unique_source_attempts":len(ids),"valid_primary":len(labels),"successes":labels.count(1),"valid_failures":labels.count(0),
        "censored_task_ids":nulls,"repeat_branches_observed":len(all_repeats),"repeat_branches_valid":repeats_valid,
        "repeat_targets":len(targets),"repeat_targets_not_occurred":sorted(targets-set(repeat_ids)),
        "complete":full,"quality_passed":quality,"fault_coverage_passed":coverage,
        "observed_fault_types_by_domain":{k:sorted(v)for k,v in observed.items()},
        "observed_f6_commit_states":sorted(states),"parent_status_preserved":"TRAIN_COLLECTION_BLOCKED",
        "formal_dataset_ready":False,"formal_experiment":not collector.offline_fixture}
    write_once_json(child/"train_union_audit.json",result)
    if release and quality and not collector.offline_fixture:
        write_once_json(child/"train_eligibility_release.json",{"split":"train","research_dataset_eligible":True,
            "formal_experiment":True,"scope":"train_only_with_explicit_censoring",
            "parent_manifest_sha256":collector.continuation["parent_artifact_manifest_sha256"],
            "child_causal_manifest_sha256":collector.flags["causal_manifest_sha256"],
            "censored_task_ids":nulls,"risk_record_references":references,
            "repeats_are_not_additional_independent_examples":True})
    return result
