from __future__ import annotations

import json
from pathlib import Path

import pytest

from risk_agent.collection.continue_acceptance_v2 import load_entry
from risk_agent.collection.day5_gate import sha256_file
from risk_agent.collection.formal_budget_revision import (
    BudgetAuditFormalCollector, REVISION, load_revision, prepare_continuation_plan)
from risk_agent.collection.formal_collection import FormalCollector
from risk_agent.collection.formal_contract import read_records, write_once_json
from risk_agent.collection.formal_fixture import FormalOfflineFactory

ROOT = Path(__file__).resolve().parents[1]
TASK = "p2_api_process_invalid_001"


def loop_to_budget(task, runtime, response):
    call = response["choices"][0]["message"]["tool_calls"][0]["function"]
    if runtime.tool_history and runtime.tool_history[-1].tool_name == "get_order":
        call.update(name="update_order", arguments=json.dumps({"order_id": task.orders[0]["order_id"],
            "expected_status": "pending", "new_status": "processing", "idempotency_key": "budget-boundary-fixture"}))
    else:
        call.update(name="get_order", arguments=json.dumps({"order_id": task.orders[0]["order_id"]}))
    return response


def collect(directory, *, cls=BudgetAuditFormalCollector, transform=None, audit_failure=False):
    profile, _ = load_entry(ROOT)
    factory = FormalOfflineFactory(profile.tasks, transform)
    collector = cls(root=ROOT, run_directory=directory, run_id="budget_fixture",
        offline_fixture=True, agent_factory=factory)
    if audit_failure:
        original = collector.audit._parsing_writer
        class Broken:
            def append(self, record):
                if record["parsing_status"] == "not_attempted":
                    raise OSError("simulated audit disk failure")
                original.append(record)
        collector.audit._parsing_writer = Broken()
    try:
        result = collector.run_task(TASK)
    finally:
        collector.close()
    return result, factory


def read_audit(directory, name):
    p = directory / "audit" / (name + ".jsonl")
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def test_exact_budget_boundary_fixed_without_provider_or_tool_behavior_change(tmp_path):
    old, old_factory = collect(tmp_path/"old", cls=FormalCollector, transform=loop_to_budget)
    new, new_factory = collect(tmp_path/"new", transform=loop_to_budget)
    assert old["accepted_y_continue"] is None and old["batch_must_stop"]
    assert new["accepted_y_continue"] == 0 and not new["batch_must_stop"]
    assert new["component"]["failure_category"] == "budget_exhaustion"
    assert new["component"]["termination"] == "budget_exhaustion" and new["component"]["complete_chain"]
    assert new["component"]["verifier_passed"] is False
    assert old_factory.requests == new_factory.requests and len(new_factory.requests) == 10
    assert new["component"]["tool_calls_added"] == old["component"]["tool_calls_added"] == 8
    assert new["component"]["model_requests_added"] == 10  # 9 SDK + one locally rejected fallback
    assert new["source_unchanged"] and new["resources_released"]
    assert new["component"]["first_input_exact"] and all(new["component"]["restore_hashes"].values())
    assert not new["formal_experiment"] and not new["research_dataset_eligible"]
    providers = read_audit(tmp_path/"new", "provider_response_audit")
    parses = read_audit(tmp_path/"new", "parsing_outcome_audit")
    assert len(providers) == len(parses) == 10
    assert {r["audit_request_id"] for r in providers} == {r["audit_request_id"] for r in parses}
    skipped = [r for r in parses if r["parsing_status"] == "not_attempted"]
    assert len(skipped) == 1 and skipped[0]["reason"].endswith("terminal_model_call_budget")
    assert skipped[0]["error_category"] is None and skipped[0]["parsing_error"] is None
    assert len(read_audit(tmp_path/"old", "parsing_outcome_audit")) == 9
    assert new["implementation_revision"] == REVISION
    binding = json.loads((tmp_path/"new/implementation_binding.json").read_text())
    assert binding["agent_class"].endswith("budget_boundary_audit.CheckpointableToolCallingAgent")


@pytest.mark.parametrize("failure", ["usage", "provider", "audit"])
def test_boundary_fix_does_not_convert_unobservable_outcomes_to_zero(tmp_path, failure):
    def transform(task, runtime, response):
        response = loop_to_budget(task, runtime, response)
        if failure == "usage" and len(runtime.tool_history) >= 9:
            response.pop("usage")
        if failure == "provider" and runtime.tool_history:
            raise OSError("simulated provider failure")
        return response
    result, _ = collect(tmp_path/"run", transform=transform, audit_failure=failure=="audit")
    assert result["accepted_y_continue"] is None and result["batch_must_stop"]
    assert result["resources_released"]
    assert not read_records(tmp_path/"run/records/risk_candidates.jsonl")
    assert all(r["y_continue"] is None for r in read_records(tmp_path/"run/records/continues.jsonl"))


def test_oversized_tool_batch_is_skipped_atomically_then_can_terminate(tmp_path):
    count = 0
    def transform(task, runtime, response):
        nonlocal count
        count += 1
        if count == 2:
            response["choices"][0]["message"]["tool_calls"] = [{"id":f"batch-{i}","type":"function",
                "function":{"name":"get_order","arguments":json.dumps({"order_id":task.orders[0]["order_id"]})}}
                for i in range(12)]
        elif count >= 3:
            response["choices"][0]["message"]["tool_calls"][0]["function"].update(
                name="final_answer", arguments=json.dumps({"answer":"No update completed"}))
        return response
    result, factory = collect(tmp_path/"run", transform=transform)
    assert result["accepted_y_continue"] == 0 and not result["batch_must_stop"]
    assert result["component"]["tool_calls_added"] == 0 and len(factory.requests) == 3
    rows = read_audit(tmp_path/"run", "parsing_outcome_audit")
    skipped = [r for r in rows if r["parsing_status"] == "not_attempted"]
    assert len(skipped) == 1 and "tool_call_budget: batch requires 12" in skipped[0]["reason"]
    assert len({r["audit_request_id"] for r in rows}) == len(rows) == 3


def test_real_parsing_failure_and_success_have_no_extra_receipts(tmp_path):
    count = 0
    def transform(task, runtime, response):
        nonlocal count
        count += 1
        if count == 2:
            response["choices"][0]["message"]["tool_calls"] = []
        elif count >= 3:
            response["choices"][0]["message"]["tool_calls"][0]["function"].update(
                name="final_answer", arguments=json.dumps({"answer":"No answer"}))
        return response
    result, factory = collect(tmp_path/"run", transform=transform)
    assert result["accepted_y_continue"] == 0 and result["engineering_passed"]
    rows = read_audit(tmp_path/"run", "parsing_outcome_audit")
    assert len(rows) == len(factory.requests) == 3
    assert [r["parsing_status"] for r in rows] == ["success","failed","success"]


def test_replayed_id_keeps_original_receipt_and_safety_stop(tmp_path):
    def transform(task,runtime,response):
        if runtime.tool_history:
            response["choices"][0]["message"]["tool_calls"][0]["id"]=f"{task.task_id}-call-1"
        return response
    result,factory=collect(tmp_path/"run",transform=transform)
    assert result["accepted_y_continue"] is None and result["batch_must_stop"]
    assert result["component"]["tool_replay_detected"]
    parses=read_audit(tmp_path/"run","parsing_outcome_audit")
    assert len(parses)==len(factory.requests)==2
    assert all(r["parsing_status"]=="success"for r in parses)


def test_valid_final_answer_on_last_model_call_remains_allowed(tmp_path):
    count=0
    def transform(task,runtime,response):
        nonlocal count
        count+=1
        response=loop_to_budget(task,runtime,response)
        if count==10:
            response["choices"][0]["message"]["tool_calls"][0]["function"].update(
                name="final_answer",arguments=json.dumps({"answer":"No update completed"}))
        return response
    result,factory=collect(tmp_path/"run",transform=transform)
    assert result["accepted_y_continue"]==0 and not result["batch_must_stop"]
    assert result["component"]["termination"]=="final_answer" and len(factory.requests)==10
    parses=read_audit(tmp_path/"run","parsing_outcome_audit")
    assert len(parses)==10 and all(r["parsing_status"]=="success"for r in parses)


def test_predelegation_rejection_never_creates_a_provider_receipt(tmp_path):
    profile,_ = load_entry(ROOT)
    factory = FormalOfflineFactory(profile.tasks)
    collector = BudgetAuditFormalCollector(root=ROOT,run_directory=tmp_path/"run",run_id="before_sdk_fixture",
        offline_fixture=True,agent_factory=factory)
    original = collector._factory
    def pre_exhausted(*args, **kwargs):
        base = original(*args, **kwargs)
        def create(*a, **kw):
            agent, model, underlying = base(*a, **kw)
            model.restore_model_calls_used(10)
            return agent, model, underlying
        return create
    collector._factory = pre_exhausted
    try:
        result = collector.run_task(TASK)
    finally:
        collector.close()
    assert result["accepted_y_continue"] is None and result["batch_must_stop"]
    assert not factory.requests and not read_audit(tmp_path/"run", "provider_response_audit")
    assert not read_audit(tmp_path/"run", "parsing_outcome_audit")


@pytest.fixture
def stopped_parent(tmp_path):
    profile,_=load_entry(ROOT)
    count=0
    def transform(task,runtime,response):
        nonlocal count
        count+=1
        if count==3:response.pop("usage")
        return response
    factory=FormalOfflineFactory(profile.tasks,transform)
    directory=tmp_path/"parent"
    collector=FormalCollector(root=ROOT,run_directory=directory,run_id="parent_fixture",offline_fixture=True,agent_factory=factory)
    try:summary=collector.run_train(task_limit=2)
    finally:collector.close()
    assert summary["batch_stopped"] and summary["attempted"]==2
    write_once_json(directory/"final_summary_fixture.json",summary)
    manifest=directory/"artifact_manifest_fixture.json"
    write_once_json(manifest,{"files":[{"path":str(p.relative_to(directory)),"size_bytes":p.stat().st_size,"sha256":sha256_file(p)}
        for p in sorted(directory.rglob("*"))if p.is_file()]})
    return directory,sha256_file(manifest)


def test_continuation_plan_excludes_all_attempts_including_null(stopped_parent):
    directory,digest=stopped_parent
    plan=prepare_continuation_plan(root=ROOT,source_run=directory,expected_manifest_sha256=digest,offline_fixture=True)
    assert plan["remaining_primary"]==94 and not plan["execution_authorized"]
    assert plan["qualification_only"] and len(plan["excluded_attempted_task_ids"])==2
    assert len(plan["censored_task_ids_preserved"])==1
    assert not set(plan["excluded_attempted_task_ids"]) & {c["task_id"]for c in plan["remaining_tasks"]}
    assert all(c["split"]=="train"for c in plan["remaining_tasks"])
    with pytest.raises(ValueError,match="stopped Train"):
        prepare_continuation_plan(root=ROOT,source_run=directory,expected_manifest_sha256=digest)


@pytest.mark.parametrize("mutation",["digest","file","identity"])
def test_parent_drift_is_refused(stopped_parent,mutation):
    directory,digest=stopped_parent
    if mutation=="digest":digest="0"*64
    else:
        path=directory/('run_manifest.json'if mutation=="identity"else'journal.jsonl')
        path.write_text(path.read_text()+" ")
    with pytest.raises(ValueError,match="Manifest|artifact drift"):
        prepare_continuation_plan(root=ROOT,source_run=directory,expected_manifest_sha256=digest,offline_fixture=True)


def test_old_parent_cannot_be_resumed_with_new_implementation(stopped_parent):
    directory,_=stopped_parent
    before={p:sha256_file(p)for p in directory.rglob('*')if p.is_file()}
    with pytest.raises(ValueError,match="Cannot resume"):
        BudgetAuditFormalCollector(root=ROOT,run_directory=directory,run_id="parent_fixture",resume=True,offline_fixture=True)
    assert all(sha256_file(p)==h for p,h in before.items())
    assert load_revision(ROOT)["research_protocol_unchanged"]
