from __future__ import annotations

import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from risk_agent.agent.agent_state_adapter import canonical_hash
from risk_agent.collection.causal_collection import CausalFormalCollector
from risk_agent.collection.continue_acceptance_v2 import load_entry
from risk_agent.collection.decision_order import DecisionOrderLedger
from risk_agent.collection.formal_contract import read_records,write_once_json
from risk_agent.collection.formal_fixture import FormalOfflineFactory
from risk_agent.collection.train_continuation import TrainContinuationCollector,audit_train_union
from risk_agent.monitoring import AuditPersistenceError
from test_budget_boundary_audit import loop_to_budget,stopped_parent

ROOT=Path(__file__).resolve().parents[1]
TASK="p2_api_process_invalid_001"


def ledger(tmp_path,clock=None):
    packet={"task_id":"task","checkpoint_id":"cp","values":{"step_index":1},
        "features_sha256":canonical_hash({"step_index":1})}
    write_once_json(tmp_path/"features.json",packet)
    kw={"clock_ns":clock}if clock else{}
    return DecisionOrderLedger(tmp_path,run_id="fixture",experiment_id="fixture",**kw)


def context(phase="continue"):
    return SimpleNamespace(run_id="fixture",task_id="task",phase=phase,worker_id="worker-0")


def test_request_requires_durable_features_and_exact_hash(tmp_path):
    early=ledger(tmp_path/"early")
    with pytest.raises(AuditPersistenceError,match="cutoff"):early.request_started(context(),"too-early")
    with pytest.raises(AuditPersistenceError,match="latched"):early.request_started(context(),"no-retry")
    order=ledger(tmp_path/"proper")
    order.request_started(context("source"),"source")
    cutoff=order.freeze(task_id="task",checkpoint_id="cp",features_path=tmp_path/"proper/features.json")
    order.request_started(context(),"valid")
    rows=read_records(order.log.path)
    assert [r["event_seq"]for r in rows]==[1,2,3]
    assert rows[-1]["cutoff_record_sha256"]==cutoff["record_sha256"]
    assert order.verify("task",[{"audit_request_id":"source","phase":"source","worker_id":"worker-0"},
                                {"audit_request_id":"valid","phase":"continue","worker_id":"worker-0"}])
    (tmp_path/"proper/features.json").write_text((tmp_path/"proper/features.json").read_text()+" ")
    with pytest.raises(AuditPersistenceError,match="changed"):order.request_started(context(),"tampered")


@pytest.mark.parametrize("fault",["source_after_cutoff","duplicate_id"])
def test_invalid_boundary_identity_is_rejected(tmp_path,fault):
    order=ledger(tmp_path)
    order.freeze(task_id="task",checkpoint_id="cp",features_path=tmp_path/"features.json")
    order.request_started(context(),"valid")
    with pytest.raises(AuditPersistenceError,match="Source|Duplicate"):
        order.request_started(context("source")if fault=="source_after_cutoff"else context(),"valid"if fault=="duplicate_id"else"late")


def test_new_process_epoch_cannot_reuse_previous_cutoff(tmp_path):
    first=ledger(tmp_path)
    first.freeze(task_id="task",checkpoint_id="cp",features_path=tmp_path/"features.json")
    next_process=DecisionOrderLedger(tmp_path,run_id="fixture",experiment_id="fixture")
    with pytest.raises(AuditPersistenceError,match="current-process"):
        next_process.request_started(context(),"cross-process")


def test_monotonic_regression_is_not_accepted(tmp_path):
    ticks=iter([100,99]);order=ledger(tmp_path,lambda:next(ticks))
    order.freeze(task_id="task",checkpoint_id="cp",features_path=tmp_path/"features.json")
    with pytest.raises(AuditPersistenceError,match="regressed"):order.request_started(context(),"regressed")
    assert len(read_records(order.log.path))==1


def test_returned_response_does_not_fill_an_unwritten_boundary(tmp_path):
    order=ledger(tmp_path)
    order.freeze(task_id="task",checkpoint_id="cp",features_path=tmp_path/"features.json")
    assert not order.verify("task",[{"audit_request_id":"unobserved","phase":"continue","worker_id":"worker-0"}])


def test_utc_rollback_budget_path_has_pre_request_causal_proof(tmp_path):
    profile,_=load_entry(ROOT);factory=FormalOfflineFactory(profile.tasks,loop_to_budget)
    collector=CausalFormalCollector(root=ROOT,run_directory=tmp_path/"run",run_id="causal_fixture",
        offline_fixture=True,agent_factory=factory)
    anchor=datetime.now(timezone.utc);counter=0
    def backwards_utc():
        nonlocal counter
        counter+=1
        return(anchor-timedelta(seconds=counter)).isoformat()
    collector.audit._wall_clock=backwards_utc
    try:result=collector.run_task(TASK)
    finally:collector.close()
    assert result["accepted_y_continue"]==0 and not result["batch_must_stop"]
    assert result["provider_audit"]["decision_order_valid"]
    assert len(factory.requests)==10 and result["component"]["tool_calls_added"]==8
    feature=json.loads((tmp_path/"run/tasks"/TASK/"decision_features.json").read_text())
    providers=json.loads((tmp_path/"run/tasks"/TASK/"provider_archive.json").read_text())
    assert all(r["request_started_at"]<feature["provenance"]["created_at"]for r in providers)
    assert collector.order.verify(TASK,providers)
    archive=json.loads((tmp_path/"run/tasks"/TASK/"decision_order_archive.json").read_text())
    cutoff=next(r for r in archive if r["record_type"]=="feature_cutoff")
    assert all(r["event_seq"]>cutoff["event_seq"]and r["monotonic_ns"]>=cutoff["monotonic_ns"]
        for r in archive if r.get("phase")in("continue","repeat"))


@pytest.mark.parametrize("fault",["feature_changed","ledger_unwritable","missing_cutoff"])
def test_causal_gate_stops_before_first_continue_sdk(tmp_path,fault):
    profile,_=load_entry(ROOT);factory=FormalOfflineFactory(profile.tasks)
    collector=CausalFormalCollector(root=ROOT,run_directory=tmp_path/"run",run_id="causal_failure_fixture",
        offline_fixture=True,agent_factory=factory)
    original=collector._packet
    def packet(record,runtime,path):
        result=original(record,runtime,path)
        if fault=="feature_changed":(path/"decision_features.json").write_text("{}")
        elif fault=="missing_cutoff":collector.order.cutoffs.clear()
        else:
            class Broken:
                def append(self,row):raise OSError("simulated journal failure")
            collector.order.log.writer=Broken()
        return result
    collector._packet=packet
    try:result=collector.run_task(TASK)
    finally:collector.close()
    assert result["accepted_y_continue"]is None and result["batch_must_stop"]and result["resources_released"]
    assert result["component"]["failure_category"]=="environment_failure"
    assert len(factory.requests)==1
    assert not read_records(tmp_path/"run/records/risk_candidates.jsonl")
    assert all(r["y_continue"]is None for r in read_records(tmp_path/"run/records/continues.jsonl"))


@pytest.mark.timeout(180)
def test_child_executes_only_unused_tasks_and_union_preserves_null(stopped_parent,tmp_path):
    parent,digest=stopped_parent
    profile,_=load_entry(ROOT);factory=FormalOfflineFactory(profile.tasks)
    child=TrainContinuationCollector(root=ROOT,parent_run=parent,parent_manifest_sha256=digest,parent_fixture=True,
        run_directory=tmp_path/"child",run_id="child_fixture",offline_fixture=True,agent_factory=factory)
    try:
        old=child.continuation["excluded_attempted_task_ids"]
        with pytest.raises(ValueError,match="unused"):child.run_task(old[0])
        assert not factory.requests
        summary=child.run_train()
        union=audit_train_union(child,summary,release=True)
        with pytest.raises(ValueError,match="no automatic replay"):child.run_train()
    finally:child.close()
    assert summary["complete"]and summary["planned_primary"]==94 and summary["valid_primary"]==94
    assert not set(old)&{r["task_id"]for r in factory.requests}
    assert union["complete"]and union["quality_passed"]and union["unique_source_attempts"]==96
    assert union["valid_primary"]==95 and len(union["censored_task_ids"])==1
    assert union["repeat_branches_valid"]==19
    assert not(tmp_path/"child/train_eligibility_release.json").exists()


def test_live_entry_needs_qualification_before_creating_output(tmp_path):
    from scripts.run_formal_train_continuation import main
    with pytest.raises(ValueError,match="prior authorization"):
        main(["--mode","train","--parent-run",str(tmp_path/"parent"),"--parent-manifest-sha256","0"*64,
              "--run-id","unqualified_fixture","--output-root",str(tmp_path/"output")])
    assert not(tmp_path/"output").exists()
