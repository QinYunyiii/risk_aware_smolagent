from __future__ import annotations
import ast
import inspect
import json
import shutil
import textwrap
from copy import deepcopy
from pathlib import Path

import pytest
from risk_agent.agent.agent_state_adapter import canonical_hash
from risk_agent.collection.formal_collection import FormalCollector
from risk_agent.collection.formal_contract import read_records
from risk_agent.collection.formal_fixture import FormalOfflineFactory
from risk_agent.collection.formal_faults import FormalFaultConfig, FormalFaultInjector, NoProgressFailure
from risk_agent.collection.formal_features import NAMES, extract_decision_features, visible_error
from risk_agent.collection.continue_acceptance_v2 import load_entry
from risk_agent.faults import ToolOperation

ROOT=Path(__file__).resolve().parents[1]
pytestmark=pytest.mark.timeout(180)


@pytest.fixture(scope="module")
def collected(tmp_path_factory):
    profile,_=load_entry(ROOT)
    factory=FormalOfflineFactory(profile.tasks)
    root=tmp_path_factory.mktemp("formal_contract")/"run"
    collector=FormalCollector(root=ROOT,run_directory=root,run_id="formal_contract_fixture",
        offline_fixture=True,agent_factory=factory)
    try:summary=collector.run_train()
    finally:collector.close()
    return root,summary,factory


def test_full_train_and_repeats_are_offline_valid_and_not_published(collected):
    root,summary,factory=collected
    assert summary["complete"] and summary["attempted"]==96 and summary["valid_primary"]==96
    assert summary["quality_passed"] and summary["fault_coverage_passed"]
    assert summary["repeat_branches_observed"]==summary["repeat_branches_valid"]==19
    assert summary["successes"]>0 and summary["valid_failures"]>0
    assert not summary["formal_experiment"] and not summary["research_dataset_eligible"]
    assert set(summary["observed_f6_commit_states"])=={"committed","not_committed","unknown"}
    assert not list(root.glob("train_release_*.json"))
    rows=read_records(root/"records/risk_candidates.jsonl")
    assert len(rows)==96 and all(r["split"]=="train" and r["future_data_excluded"] for r in rows)
    assert all(not r["formal_experiment"] and not r["research_dataset_eligible"] for r in rows)
    assert len({r["checkpoint_id"] for r in rows})==96
    assert {r["task_id"] for r in rows}=={r["task_id"] for r in factory.requests}
    providers=[json.loads(x) for x in (root/"audit/provider_response_audit.jsonl").read_text().splitlines()]
    assert all(r["offline_fixture"] and not r["formal_experiment"] and r["split"]=="train" for r in providers)


def test_features_are_frozen_before_any_continue_or_repeat_and_bind_hashes(collected):
    root,_,_=collected
    providers=[json.loads(x) for x in (root/"audit/provider_response_audit.jsonl").read_text().splitlines()]
    for path in root.glob("tasks/*/decision_features.json"):
        packet=json.loads(path.read_text())
        assert set(packet["values"])==set(NAMES) and len(NAMES)==18
        assert packet["features_sha256"]==canonical_hash(packet["values"])
        times=[p["request_started_at"] for p in providers if p["task_id"]==packet["task_id"] and p["phase"]!="source"]
        assert times and packet["provenance"]["created_at"]<=min(times)
    for path in root.glob("tasks/*/task_result.json"):
        row=json.loads(path.read_text());c=row["component"]
        assert c["first_input_exact"] and len(c["restore_hashes"])==6 and all(c["restore_hashes"].values())
        assert row["source_unchanged"] and row["resources_released"] and not c["tool_replay_detected"]
        assert c["model_requests_added"]+row["source_model_calls"]<=10
        if "repeat" in row:
            assert row["repeat"]["checkpoint_id"]==c["checkpoint_id"]
            assert row["repeat"]["branch_attempt_id"]!=c["branch_attempt_id"]
            assert row["repeat"]["first_input_exact"]


def test_feature_values_ignore_hidden_and_future_fields(collected):
    root,_,_=collected
    taskroot=next((root/"tasks").iterdir());source=json.loads((taskroot/"source_result.json").read_text())
    payload=json.loads((Path(source["checkpoint"]["checkpoint_path"])/"agent/agent_state.json").read_text())["payload"]
    tools=[json.loads(x) for x in (taskroot/"source_tools.jsonl").read_text().splitlines()]
    models=json.loads((taskroot/"source_model_requests.json").read_text())
    kwargs=dict(agent_payload=payload,budget=source["source_budget"],source_model_records=models,source_tool_records=tools)
    expected=extract_decision_features(**kwargs)
    poisoned=deepcopy(kwargs)
    for tool in poisoned["source_tool_records"]:
        tool.update(fault_type="F6_PARTIAL_FAILURE",last_failure={"commit_status":"unknown"},expected="secret",label=0,future_outcome=1)
    poisoned["budget"].update(future_label=0,last_failure={"type":"fault"})
    poisoned["agent_payload"]["future_outcome"]=0
    assert extract_decision_features(**poisoned)["values"]==expected["values"]
    missing=deepcopy(kwargs);missing["source_model_records"][0]["prompt_tokens"]=None
    with pytest.raises(ValueError,match="usage provenance"):
        extract_decision_features(**missing)


def test_resume_skips_all_completed_tasks_and_rejects_artifact_drift(tmp_path,collected):
    root,_,_=collected;copy=tmp_path/"copy";shutil.copytree(root,copy)
    profile,_=load_entry(ROOT);factory=FormalOfflineFactory(profile.tasks)
    collector=FormalCollector(root=ROOT,run_directory=copy,run_id="formal_contract_fixture",
        offline_fixture=True,agent_factory=factory,resume=True)
    try:assert collector.run_train()["complete"]
    finally:collector.close()
    assert not factory.requests
    path=next(copy.glob("tasks/*/task_result.json"));path.write_text(path.read_text()+" ")
    collector=FormalCollector(root=ROOT,run_directory=copy,run_id="formal_contract_fixture",
        offline_fixture=True,agent_factory=factory,resume=True)
    try:
        with pytest.raises(ValueError,match="artifact drift"):collector.run_train()
    finally:collector.close()
    assert not factory.requests


def test_incomplete_claim_is_not_automatically_replayed(tmp_path):
    profile,_=load_entry(ROOT);factory=FormalOfflineFactory(profile.tasks)
    args=dict(root=ROOT,run_directory=tmp_path/"run",run_id="interrupted_formal_fixture",
        offline_fixture=True,agent_factory=factory)
    collector=FormalCollector(**args)
    collector.journal.append({"record_type":"task_claimed","task_id":next(t.task_id for t in profile.tasks if t.split.value=="train")})
    collector.close();collector=FormalCollector(**args,resume=True)
    try:
        with pytest.raises(RuntimeError,match="Interrupted task"):collector.run_train()
    finally:collector.close()
    assert not factory.requests


def test_other_splits_and_live_injected_backend_refused_before_requests(tmp_path):
    profile,_=load_entry(ROOT);factory=FormalOfflineFactory(profile.tasks)
    with pytest.raises(ValueError,match="authorization"):
        FormalCollector(root=ROOT,run_directory=tmp_path/"bad",run_id="bad_formal_fixture",agent_factory=factory)
    assert not (tmp_path/"bad").exists()
    collector=FormalCollector(root=ROOT,run_directory=tmp_path/"run",run_id="split_formal_fixture",
        offline_fixture=True,agent_factory=factory)
    try:
        for split in ("validation","test"):
            task=next(t for t in profile.tasks if t.split.value==split)
            with pytest.raises(ValueError,match="Only Train"):collector.run_task(task.task_id)
        assert not factory.requests and not collector.journal.path.exists()
    finally:collector.close()


def test_no_progress_has_real_no_execution_and_restored_cursor():
    case=dict(fault_id="f5_formal_test",fault_type="F5_REPEATED_ACTION",fault_seed=1,target_tool="calculator",
        call_indices=[1],parameters={"variant":"visible_no_progress_without_execution"})
    injector=FormalFaultInjector(FormalFaultConfig.from_case(case));calls=[]
    operation=ToolOperation("calculator",True)
    with pytest.raises(NoProgressFailure):injector.execute(operation,{"expression":"1+1"},lambda:calls.append(1))
    assert calls==[] and injector.events[-1].triggered
    restored=FormalFaultInjector(FormalFaultConfig.from_case(case))
    restored.restore_checkpoint_state(json.loads(json.dumps(injector.checkpoint_state())))
    assert restored.execute(operation,{"expression":"1+1"},lambda:2)==2
    assert sum(e.triggered for e in restored.events)==1


def test_real_visible_fault_responses_do_not_expose_internal_truth(collected):
    root,_,_=collected
    seen=set()
    for cp in (root/"source/checkpoints").iterdir():
        state=json.loads((cp/"agent/agent_state.json").read_text())["payload"]
        for step in state["memory"]["steps"]:
            if step.get("error"):
                message=step["error"]["message"]
                assert not any(x in message for x in ("Injected", "PartialFailureError", "formal_0", "not_committed", "after_commit"))
                seen.add("error")
    assert seen
    assert visible_error('Observation:\n"Error: normal file text"')=="none"
    assert visible_error('Call id: x\nError:\nVisibleToolError: No progress: operation was not performed.')=="no_progress"


def test_core_continue_algorithm_is_unchanged():
    from risk_agent.collection.continue_gate import ContinueGateRunner
    from risk_agent.collection.formal_continue import FormalContinueRunner
    old=textwrap.dedent(inspect.getsource(ContinueGateRunner.run_checkpoint)).replace(
        "B3_RESEARCH_FLAGS","self.research_flags").replace("create_local_domain_tools(","create_formal_tools(")
    new=textwrap.dedent(inspect.getsource(FormalContinueRunner.run_checkpoint))
    assert ast.dump(ast.parse(old))==ast.dump(ast.parse(new))


@pytest.mark.parametrize("phase", ["source", "continue"])
def test_missing_provider_usage_stops_without_false_failure_label(tmp_path, phase):
    profile,_=load_entry(ROOT)
    def transform(task,runtime,response):
        if (phase=="source") == (not runtime.tool_history):
            response.pop("usage")
        return response
    factory=FormalOfflineFactory(profile.tasks,transform)
    collector=FormalCollector(root=ROOT,run_directory=tmp_path/"run",run_id="usage_failure_fixture",
        offline_fixture=True,agent_factory=factory)
    try:
        result=collector.run_task(next(c["task_id"] for c in collector.plan["tasks"] if c["split"]=="train"))
    finally:collector.close()
    assert result["batch_must_stop"] and result["accepted_y_continue"] is None
    assert result["resources_released"]
    assert not read_records(tmp_path/"run/records/risk_candidates.jsonl")
    outcomes=read_records(tmp_path/"run/records/continues.jsonl")
    assert all(o["y_continue"] is None for o in outcomes)


def test_no_checkpoint_is_retained_without_replacement(tmp_path):
    profile,_=load_entry(ROOT)
    def transform(task,runtime,response):
        response["choices"][0]["message"]["tool_calls"][0]["function"].update(
            name="final_answer",arguments=json.dumps({"answer":"No action"}))
        return response
    factory=FormalOfflineFactory(profile.tasks,transform)
    collector=FormalCollector(root=ROOT,run_directory=tmp_path/"run",run_id="no_checkpoint_fixture",
        offline_fixture=True,agent_factory=factory)
    try:
        result=collector.run_task(next(c["task_id"] for c in collector.plan["tasks"] if c["split"]=="train"))
    finally:collector.close()
    assert result["capture_status"]=="no_eligible_checkpoint" and not result["batch_must_stop"]
    assert len(factory.requests)==1 and result["accepted_y_continue"] is None
    assert (tmp_path/"run/tasks"/result["task_id"]/"source_agent_state.json").exists()
    assert not read_records(tmp_path/"run/records/risk_candidates.jsonl")


def test_record_chain_and_exclusive_evidence_file(tmp_path):
    from risk_agent.collection.formal_contract import RecordLog,write_once_json
    log=RecordLog(tmp_path/"records.jsonl",run_id="fixture",experiment_id="fixture")
    log.append({"record_type":"one"});log.append({"record_type":"two"})
    assert len(read_records(log.path))==2
    log.path.write_text(log.path.read_text().replace('"one"','"changed"'))
    with pytest.raises(ValueError,match="corrupted"):read_records(log.path)
    path=tmp_path/"evidence.json";write_once_json(path,{"value":1})
    with pytest.raises(FileExistsError):write_once_json(path,{"value":2})
    assert json.loads(path.read_text())=={"value":1}


def test_f3_unchanged_arguments_remain_invalid_after_restore():
    from risk_agent.faults.injector import InjectedInvalidArgumentError
    case=dict(fault_id="f3_fixture",fault_type="F3_INVALID_ARGUMENT",fault_seed=1,
        target_tool="calculator",call_indices=[1],parameters={})
    original=FormalFaultInjector(FormalFaultConfig.from_case(case))
    operation=ToolOperation("calculator",True);calls=[]
    with pytest.raises(InjectedInvalidArgumentError):original.execute(operation,{"expression":"1+1"},lambda:calls.append(1))
    restored=FormalFaultInjector(FormalFaultConfig.from_case(case))
    restored.restore_checkpoint_state(json.loads(json.dumps(original.checkpoint_state())))
    with pytest.raises(InjectedInvalidArgumentError):restored.execute(operation,{"expression":"1+1"},lambda:calls.append(1))
    assert not calls
    assert restored.execute(operation,{"expression":"2+1"},lambda:3)==3


def test_live_cli_rejects_missing_qualification_before_creating_output(tmp_path):
    from scripts.run_formal_train_collection import main
    with pytest.raises(ValueError,match="requires authorization"):
        main(["--mode","train","--run-id","unqualified_fixture","--output-root",str(tmp_path/"output")])
    assert not (tmp_path/"output").exists()
