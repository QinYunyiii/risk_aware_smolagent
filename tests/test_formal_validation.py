from __future__ import annotations

import ast,json,inspect,textwrap
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from risk_agent.collection.validation_collection import ValidationCollector
from risk_agent.collection.validation_contract import load_validation_contract
from risk_agent.collection.validation_continue import ValidationContinueRunner
from risk_agent.collection.validation_lifecycle import ValidationLifecycle
from risk_agent.collection.formal_collection import FormalCollector
from risk_agent.collection.formal_continue import FormalContinueRunner
from risk_agent.collection.formal_source import FormalSource
from risk_agent.collection.formal_fixture import FormalOfflineFactory
from risk_agent.collection.formal_contract import read_records
from risk_agent.collection.continue_gate import ContinueGateError

ROOT=Path(__file__).resolve().parents[1]
pytestmark=pytest.mark.timeout(180)


@pytest.fixture(scope="module")
def collected(tmp_path_factory):
    profile,plan,_,config=load_validation_contract(ROOT)
    factory=FormalOfflineFactory(profile.tasks)
    directory=tmp_path_factory.mktemp("validation_contract")/"run"
    collector=ValidationCollector(root=ROOT,run_directory=directory,run_id="validation_contract_fixture",
        offline_fixture=True,agent_factory=factory)
    try:summary=collector.run_validation()
    finally:collector.close()
    return directory,summary,factory,collector


def test_full_frozen_validation_schedule_and_records_are_separate(collected):
    directory,summary,factory,collector=collected
    assert summary["complete"]and summary["quality_passed"]and summary["attempted"]==32
    assert summary["valid_primary"]==32 and summary["repeat_branches_valid"]==7
    assert summary["observed_f6_commit_states"]==["committed","unknown"]
    assert summary["split"]=="validation"and not summary["formal_experiment"]and not summary["allowed_for_training"]
    assert {r["task_id"]for r in factory.requests}==set(collector.validation_config["task_ids"])
    assert not(directory/"validation_eligibility_release.json").exists()
    manifest=json.loads((directory/"run_manifest.json").read_text())
    assert manifest["authorized_split"]=="validation"and manifest["primary_target"]==32 and manifest["repeat_target"]==7
    for name in("tasks","checkpoints","continues","risk_candidates"):
        rows=read_records(directory/"records"/(name+".jsonl"))
        assert rows and all(r["split"]=="validation"and r["offline_fixture"]and not r["formal_experiment"]for r in rows)
    for path in directory.glob("tasks/*/source_tools.jsonl"):
        assert all(json.loads(s)["split"]=="validation"for s in path.read_text().splitlines())
    providers=[json.loads(s)for s in(directory/"audit/provider_response_audit.jsonl").read_text().splitlines()]
    assert providers and all(r["split"]=="validation"and r["offline_fixture"]for r in providers)
    for path in directory.glob("continue/branches/*/*/outcome.json"):
        outcome=json.loads(path.read_text());assert outcome["split"]=="validation"and not outcome["formal_experiment"]


def test_capture_restore_features_budget_and_repeat_lineage(collected):
    directory,_,_,collector=collected
    for path in directory.glob("tasks/*/task_result.json"):
        result=json.loads(path.read_text());component=result["component"]
        assert result["capture_step"]==1 and result["source_unchanged"]and result["resources_released"]
        assert component["first_input_exact"]and len(component["restore_hashes"])==6 and all(component["restore_hashes"].values())
        assert component["checkpoint_immutable"]and not component["tool_replay_detected"]and component["complete_chain"]
        assert result["provider_audit"]["decision_order_valid"]
        packet=json.loads((path.parent/"decision_features.json").read_text())
        assert packet["split"]=="validation"and len(packet["values"])==18
        providers=json.loads((path.parent/"provider_archive.json").read_text())
        assert collector.order.verify(result["task_id"],providers)
        assert sum(r["phase"]in("source","continue")for r in providers)<=10
        if'repeat'in result:
            assert collector.cases[result["task_id"]]["scheduled_repeat"]
            assert result["repeat"]["checkpoint_id"]==component["checkpoint_id"]and result["repeat"]["first_input_exact"]
            assert all(result["repeat"]["restore_hashes"].values())
    with pytest.raises(ValueError,match="already claimed"):collector.run_validation()


def test_other_splits_and_real_fixture_backend_refused_before_creation(tmp_path):
    profile,_,_,_=load_validation_contract(ROOT);factory=FormalOfflineFactory(profile.tasks)
    for args in({},{"authorize_validation":True}):
        with pytest.raises(ValueError,match="authorization"):
            ValidationCollector(root=ROOT,run_directory=tmp_path/"bad",run_id="bad_fixture",agent_factory=factory,**args)
    assert not(tmp_path/"bad").exists()
    with pytest.raises(ValueError,match="fixture backend"):
        ValidationCollector(root=ROOT,run_directory=tmp_path/"unsafe",run_id="unsafe_fixture",offline_fixture=True)
    collector=ValidationCollector(root=ROOT,run_directory=tmp_path/"run",run_id="split_fixture",
        offline_fixture=True,agent_factory=factory)
    try:
        for split in("train","test"):
            task=next(t for t in profile.tasks if t.split.value==split)
            with pytest.raises(ValueError,match="Only Validation"):collector.run_task(task.task_id)
        with pytest.raises(ValueError,match="cannot execute Train"):collector.run_train()
        assert not factory.requests and not collector.journal.path.exists()
    finally:collector.close()


def test_lifecycle_is_exact_pinned_algorithm_with_scope_substitution():
    assert ValidationCollector._capture is FormalSource._capture
    assert ValidationContinueRunner.run_checkpoint is FormalContinueRunner.run_checkpoint
    for name in("_packet","_branch","run_task","_persist_records"):
        old=textwrap.dedent(inspect.getsource(getattr(FormalCollector,name))).replace('"train"','"validation"').replace(
            'Only Train is authorized; Validation/Test remain sealed','Only Validation is authorized; Train/Test remain sealed').replace(
            'load_formal_contract(self.root)','load_validation_contract(self.root)').replace('FormalContinueRunner(','ValidationContinueRunner(')
        if name=="_packet":old=old.replace('    return packet\n','    self.order.freeze(task_id=record.task_id,checkpoint_id=record.checkpoint_id,features_path=task_root / "decision_features.json")\n    return packet\n')
        new=textwrap.dedent(inspect.getsource(getattr(ValidationLifecycle,name)))
        assert ast.dump(ast.parse(old))==ast.dump(ast.parse(new)),name


@pytest.mark.parametrize("field,value",[("split","train"),("split","test"),("run_id","another_run"),
                                      ("execution_mode","model_backed_formal"),("experiment_id","other_experiment")])
def test_restore_rejects_foreign_identity_without_provider(collected,tmp_path,field,value):
    directory,_,factory,collector=collected
    cp=next((directory/"source/checkpoints").iterdir());manifest=json.loads((cp/"manifest.json").read_text())
    identity=deepcopy(manifest["identity"]);identity[field]=value
    verified=SimpleNamespace(manifest=SimpleNamespace(identity=identity),checkpoint_path=cp)
    runner=ValidationContinueRunner(settings=collector.settings,tasks=collector.profile.tasks,
        run_directory=tmp_path/"runner",checkpoint_run=directory/"source",run_id=collector.run_id,
        experiment_id=collector.experiment_id,research_flags=collector.flags,audit_valid=lambda:True,
        replay_policy=collector.profile.config["continue"]["replay_policy"],protocol_id=collector.profile.config["protocol_id"])
    before=len(factory.requests)
    with pytest.raises(ContinueGateError):runner._validate_identity(verified,collector.workers[manifest["identity"]["worker_id"]])
    assert len(factory.requests)==before


@pytest.mark.parametrize("phase",["source","continue"])
def test_missing_usage_stays_null_and_stops(tmp_path,phase):
    profile,_,_,cfg=load_validation_contract(ROOT)
    def transform(task,runtime,response):
        if(phase=="source")== (not runtime.tool_history):response.pop("usage")
        return response
    factory=FormalOfflineFactory(profile.tasks,transform)
    collector=ValidationCollector(root=ROOT,run_directory=tmp_path/"run",run_id="usage_fixture",
        offline_fixture=True,agent_factory=factory)
    try:result=collector.run_task(cfg["task_ids"][0])
    finally:collector.close()
    assert result["accepted_y_continue"]is None and result["batch_must_stop"]and result["resources_released"]
    assert not read_records(tmp_path/"run/records/risk_candidates.jsonl")
    assert all(r["y_continue"]is None for r in read_records(tmp_path/"run/records/continues.jsonl"))


def test_budget_boundary_with_rollback_keeps_valid_failure(tmp_path):
    profile,plan,_,_=load_validation_contract(ROOT)
    target=next(c for c in plan["tasks"]if c["split"]=="validation"and c["fault_type"]=="F3_INVALID_ARGUMENT"and not c["scheduled_repeat"])
    first=None
    def transform(task,runtime,response):
        nonlocal first
        call=response["choices"][0]["message"]["tool_calls"][0]
        if first is None:first=deepcopy(call["function"])
        else:call["function"]=deepcopy(first)
        return response
    factory=FormalOfflineFactory(profile.tasks,transform)
    collector=ValidationCollector(root=ROOT,run_directory=tmp_path/"run",run_id="budget_fixture",
        offline_fixture=True,agent_factory=factory)
    collector.audit._wall_clock=lambda:"2000-01-01T00:00:00+00:00"
    try:result=collector.run_task(target["task_id"])
    finally:collector.close()
    assert result["accepted_y_continue"]==0 and result["component"]["termination"]=="budget_exhaustion"
    assert result["provider_audit"]["complete"]and result["provider_audit"]["decision_order_valid"]
    assert len(factory.requests)==10 and not result["batch_must_stop"]
    rows=[json.loads(s)for s in(tmp_path/"run/audit/parsing_outcome_audit.jsonl").read_text().splitlines()]
    assert len(rows)==10 and rows[-1]["parsing_status"]=="not_attempted"


def test_no_checkpoint_retained_and_not_promoted(tmp_path):
    profile,_,_,cfg=load_validation_contract(ROOT)
    def transform(task,runtime,response):
        response["choices"][0]["message"]["tool_calls"][0]["function"].update(name="final_answer",arguments='{"answer":null}')
        return response
    factory=FormalOfflineFactory(profile.tasks,transform)
    collector=ValidationCollector(root=ROOT,run_directory=tmp_path/"run",run_id="no_cp_fixture",
        offline_fixture=True,agent_factory=factory)
    try:result=collector.run_task(cfg["repeat_task_ids"][0])
    finally:collector.close()
    assert result["capture_status"]=="no_eligible_checkpoint"and not result["batch_must_stop"]
    assert len(factory.requests)==1 and 'repeat'not in result
    assert not read_records(tmp_path/"run/records/risk_candidates.jsonl")


def test_validation_fixture_cannot_publish_training_or_formal_eligibility(collected):
    directory,summary,_,collector=collected
    with pytest.raises(ValueError,match="qualified real"):
        collector.publish_validation_eligibility(summary,resources_released=True)
    collector.offline_fixture=False
    try:
        with pytest.raises(ValueError,match="cannot promote fixtures"):
            collector.publish_validation_eligibility(summary,resources_released=True)
    finally:collector.offline_fixture=True
    assert not(directory/"validation_eligibility_release.json").exists()


def test_cli_requires_explicit_authorization_before_output(tmp_path):
    from scripts.run_formal_validation import main
    with pytest.raises(ValueError,match="separate authorization"):
        main(["--mode","validation","--run-id","unauthorized_fixture","--output-root",str(tmp_path/"output")])
    assert not(tmp_path/"output").exists()


def test_factory_cannot_failover_before_client_creation(tmp_path):
    profile,_,_,cfg=load_validation_contract(ROOT);factory=FormalOfflineFactory(profile.tasks)
    collector=ValidationCollector(root=ROOT,run_directory=tmp_path/"run",run_id="worker_fixture",
        offline_fixture=True,agent_factory=factory)
    try:
        task=collector.tasks[cfg["task_ids"][0]]
        worker=next(w for w in collector.workers.values()if w.worker_id!=collector.cases[task.task_id]["worker_id"])
        create=collector._factory(task,"source",collector.clock(),[])
        with pytest.raises(ValueError,match="failover forbidden"):create(collector.settings,worker,[],None)
        assert not factory.requests
    finally:collector.close()


@pytest.mark.parametrize("split,formal,offline",[("test",True,False),("validation",True,False),("train",False,True)])
def test_train_prerequisite_rejects_wrong_split_or_fixture_before_outcomes(tmp_path,monkeypatch,split,formal,offline):
    import risk_agent.collection.validation_prerequisite as prerequisite
    profile=load_validation_contract(ROOT)[0]
    monkeypatch.setattr(prerequisite,"load_formal_contract",lambda root:(profile,{},{}))
    def no_labels(path):raise AssertionError("Other-split or fixture outcome access")
    monkeypatch.setattr(prerequisite,"read_records",no_labels)
    source=tmp_path/"runs/input_fixture";source.mkdir(parents=True)
    (source/"run_manifest.json").write_text(json.dumps({"run_id":"input_fixture","authorized_split":split,
        "formal_experiment":formal,"offline_fixture":offline,"test_fixture":True}))
    proof=tmp_path/"proof.json";proof.write_text(json.dumps({"runs":[{"run_id":"input_fixture","directory":str(source)}]}))
    with pytest.raises(ValueError,match="actual formal Train"):
        prerequisite.verify_train_prerequisite(tmp_path,proof)


def test_missing_causal_proof_is_not_an_agent_failure(tmp_path):
    profile,_,_,cfg=load_validation_contract(ROOT);factory=FormalOfflineFactory(profile.tasks)
    collector=ValidationCollector(root=ROOT,run_directory=tmp_path/"run",run_id="order_failure_fixture",
        offline_fixture=True,agent_factory=factory)
    original=collector._packet
    def packet(*args):
        result=original(*args);collector.order.cutoffs.clear();return result
    collector._packet=packet
    try:result=collector.run_task(cfg["task_ids"][0])
    finally:collector.close()
    assert len(factory.requests)==1 and result["accepted_y_continue"]is None and result["batch_must_stop"]
    outcomes=read_records(tmp_path/"run/records/continues.jsonl")
    assert outcomes and all(r["y_continue"]is None and r["failure_category"]=="environment_failure"for r in outcomes)
