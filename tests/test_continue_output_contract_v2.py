from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

import httpx2
import pytest
import yaml

from risk_agent.benchmark.runner import ReferenceTaskRunner
from risk_agent.collection.continue_acceptance import load_entry as load_legacy_entry
from risk_agent.collection.continue_acceptance_fixture import OfflineAcceptanceFactory
from risk_agent.collection.continue_acceptance_v2 import (
    ContinueAcceptanceRunner, OutputContractContinueGateRunner, engineering_passed, load_entry,
)
from risk_agent.collection.continue_outcome import ContinueTermination
from risk_agent.collection.output_contract import OutputContractError, encode_observation
from risk_agent.collection.protocol_freeze_v2_2 import MANIFEST_PATH, append_output_contract

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts/run_frozen_continue_acceptance_v2.py"
PROFILE, CONFIG = load_entry(ROOT)


def make_runner(tmp_path, transform=None):
    factory = OfflineAcceptanceFactory(PROFILE.tasks, transform=transform)
    return ContinueAcceptanceRunner(root=ROOT, run_directory=tmp_path / "run",
        run_id="output_contract_fixture", offline_fixture=True, agent_factory=factory), factory


def observed_final(task, runtime, response):
    """Final answer comes from the observed raw tool result, never expected."""
    call = response["choices"][0]["message"]["tool_calls"][0]["function"]
    if call["name"] == "final_answer":
        call["arguments"] = json.dumps({"answer": runtime.tool_history[-1].tool_result})
    return response


def last_observation(request):
    content = request["body"]["messages"][-1]["content"]
    text = content if isinstance(content, str) else "".join(c["text"] for c in content)
    assert text.startswith("Observation:\n")
    return text.removeprefix("Observation:\n")


@pytest.mark.parametrize("task", PROFILE.tasks, ids=lambda t: t.task_id)
def test_all_tasks_keep_original_answers_splits_and_native_outputs(tmp_path, task):
    parent, _ = load_legacy_entry(ROOT)
    old = next(t for t in parent.tasks if t.task_id == task.task_id)
    before, after = asdict(old), asdict(task)
    before.pop("prompt"); after.pop("prompt")
    assert before == after
    contract = (ROOT / "configs/continue_output_contract_v1.txt").read_text()
    assert task.prompt == append_output_contract(old.prompt, contract)
    result = ReferenceTaskRunner(tmp_path).run(task)
    assert result.passed and result.reset_verified
    decoded = json.loads(encode_observation(result.final_result))
    assert type(decoded) is type(result.final_result) and decoded == result.final_result


def test_only_approved_profile_and_remote_fields_change():
    old, _ = load_legacy_entry(ROOT)
    new = dict(PROFILE.config)
    assert new["limits"] == old.config["limits"]
    assert new["capture"] == old.config["capture"]
    assert new["continue"] == old.config["continue"]
    assert new["labels"] == old.config["labels"]
    assert new["release_requirements"]["local_integrity_check"] == (
        "scripts/run_frozen_continue_acceptance_v2.py --mode local-preflight")
    assert PROFILE.audit_summary()["split_manifest_sha256"] == old.audit_summary()["split_manifest_sha256"]
    a = yaml.safe_load((ROOT / old.config["dependencies"]["remote_model_config"]).read_text())
    b = yaml.safe_load((ROOT / new["dependencies"]["remote_model_config"]).read_text())
    assert a.pop("prompt_version") != b.pop("prompt_version")
    assert a == b


@pytest.mark.parametrize("mode", ["positive", "first_negative", "all_negative"])
def test_five_full_streams_preserve_observed_types_and_allow_valid_zero(tmp_path, mode):
    def transform(task, runtime, response):
        response = observed_final(task, runtime, response)
        call = response["choices"][0]["message"]["tool_calls"][0]["function"]
        if call["name"] == "final_answer" and (mode == "all_negative" or
            mode == "first_negative" and task.task_id == CONFIG["task_ids"][0]):
            call["arguments"] = json.dumps({"answer": "deliberately wrong offline fixture"})
        return response
    runner, factory = make_runner(tmp_path, transform)
    summary = runner.run_fixed_gate()
    assert summary["passed"] and summary["engineering_passed"] and summary["task_attempts"] == 5
    expected_zeros = {"positive": 0, "first_negative": 1, "all_negative": 5}[mode]
    assert summary["successes"] == 5 - expected_zeros and summary["valid_failures"] == expected_zeros
    assert summary["task_success_rate_among_valid"] == (5 - expected_zeros) / 5
    assert summary["censored_or_invalid"] == summary["not_attempted"] == 0
    assert not summary["formal_collection_ready"] and not summary["research_dataset_eligible"]
    for row in summary["tasks"]:
        assert row["engineering_passed"] and row["source_unchanged"] and row["resources_released"]
        assert row["task_success"] is (row["accepted_y_continue"] == 1)
        assert row["capture_step"] == row["source_model_calls"] == 1
        c = row["component"]
        assert all(c["restore_hashes"].values()) and c["first_input_exact"]
        assert c["checkpoint_immutable"] and not c["tool_replay_detected"]
        assert row["provider_audit"]["complete"] and c["termination"] == "final_answer"
        request = [r for r in factory.requests if r["task_id"] == row["task_id"]][-1]
        decoded = json.loads(last_observation(request))
        task = runner.tasks[row["task_id"]]
        assert decoded == task.expected["result"]
        assert type(decoded) is type(task.expected["result"])
        if task.domain.value == "file_retrieval":
            assert decoded.endswith("\n")
        assert request["body"]["max_tokens"] == 1024 and request["body"]["seed"] == 20261118
    # T3 genuinely performed update -> new-ID reread in the restored branch.
    api = [r for r in summary["tasks"] if r["task_id"] == "p2_api_process_control_001"][0]
    assert api["component"]["tool_calls_added"] == 2
    budget = [json.loads(l) for l in (runner.directory / "continue/budget_audit.jsonl").read_text().splitlines()]
    assert all(b["model_calls_before"] == b["tool_calls_before"] == 1 for b in budget)
    assert all(b["tokens_after"] == b["model_calls_after"] * 50 for b in budget)


@pytest.mark.parametrize("value", ["  exact\n文本\x00\r\n", "20", True, False, None,
    {"é": [" leading\n", 2, False]}, [1, "2", {"x": 3.5}]])
def test_actual_tool_to_snapshot_and_restored_model_input_is_lossless(tmp_path, monkeypatch, value):
    from risk_agent.tools.task_tool_registry import TaskToolRegistry
    monkeypatch.setattr(TaskToolRegistry, "execute", lambda *a, **k: value)
    def stop_after_observation(task, runtime, response):
        if runtime.tool_history:
            response["choices"][0]["message"]["tool_calls"][0]["function"].update(
                name="final_answer", arguments=json.dumps({"answer": value}))
        return response
    runner, factory = make_runner(tmp_path, stop_after_observation)
    row = runner.run_task(CONFIG["task_ids"][0], "worker-0")
    assert row["engineering_passed"] and row["component"]["first_input_exact"]
    observed = json.loads(last_observation(factory.requests[-1]))
    assert type(observed) is type(value) and observed == value
    source = json.loads((runner.directory / "tasks" / row["task_id"] / "source_result.json").read_text())
    cp = Path(source["checkpoint"]["checkpoint_path"])
    manifest = json.loads((cp / "manifest.json").read_text())
    assert manifest["schema_version"] == "2.0.0"
    assert manifest["identity"]["prompt_version"] == runner.settings.prompt_version
    assert manifest["identity"]["catalog_hash"] == PROFILE.audit_summary()["catalog_sha256"]
    state = json.loads((cp / "agent/agent_state.json").read_text())
    assert state["payload"]["agent"]["class"] == (
        "risk_agent.collection.output_contract.CheckpointableToolCallingAgent")


@pytest.mark.parametrize("value", [(1, 2), {1: "coerced key"}, float("nan"), float("inf"), object(), "\ud800"])
def test_unrepresentable_results_have_no_lossy_fallback(value):
    with pytest.raises(OutputContractError):
        encode_observation(value)


def test_cycles_fail_closed():
    cycle = []; cycle.append(cycle)
    with pytest.raises(OutputContractError):
        encode_observation(cycle)


@pytest.mark.parametrize("failure", ["infra", "source_terminal", "context", "encoding", "audit", "cleanup"])
def test_incomplete_chain_stops_and_never_becomes_zero(tmp_path, monkeypatch, failure):
    def transform(task, runtime, response):
        if failure == "infra" and runtime.tool_history:
            raise httpx2.ReadTimeout("offline provider interruption")
        if failure == "source_terminal" and not runtime.tool_history:
            response["choices"][0]["message"]["tool_calls"][0]["function"].update(
                name="final_answer", arguments=json.dumps({"answer": "terminal"}))
        if failure == "context":
            response["usage"] = {"prompt_tokens": 8190, "completion_tokens": 10, "total_tokens": 8200}
        return observed_final(task, runtime, response) if runtime.tool_history else response
    runner, factory = make_runner(tmp_path, transform)
    if failure == "encoding":
        from risk_agent.collection import output_contract
        original = output_contract.encode_observation
        calls = []
        def fail_in_branch(value):
            calls.append(value)
            if len(calls) > 1:
                raise OutputContractError("offline unsupported observation")
            return original(value)
        monkeypatch.setattr(output_contract, "encode_observation", fail_in_branch)
    if failure == "audit":
        writer = runner.audit._response_writer
        class BrokenWriter:
            calls = 0
            def append(self, value):
                self.calls += 1
                if self.calls > 1:
                    raise OSError("offline audit failure")
                writer.append(value)
        runner.audit._response_writer = BrokenWriter()
    if failure == "cleanup":
        original = runner._factory
        def factory_with_failed_close(task, phase, started, clients):
            base = original(task, phase, started, clients)
            def create(*args, **kwargs):
                agent, model, underlying = base(*args, **kwargs)
                if phase == "continue":
                    def broken_close():
                        raise RuntimeError("offline close failure")
                    monkeypatch.setattr(underlying.client, "close", broken_close)
                return agent, model, underlying
            return create
        monkeypatch.setattr(runner, "_factory", factory_with_failed_close)
    summary = runner.run_fixed_gate()
    assert not summary["passed"] and summary["task_attempts"] == 1
    row = summary["tasks"][0]
    assert row["accepted_y_continue"] is None and row["task_success"] is None
    assert summary["valid_failures"] == summary["successes"] == 0
    assert {r["task_id"] for r in factory.requests} == {CONFIG["task_ids"][0]}
    if failure == "encoding":
        assert row["component"]["termination"] == "environment_failure"
        assert row["component"]["y_continue"] is None


def test_wrong_worker_and_repeat_attempt_do_not_reach_provider(tmp_path):
    runner, factory = make_runner(tmp_path, observed_final)
    with pytest.raises(ValueError, match="failover"):
        runner.run_task(CONFIG["task_ids"][0], "worker-1")
    assert not factory.requests
    runner.run_task(CONFIG["task_ids"][0], "worker-0")
    count = len(factory.requests)
    with pytest.raises(ValueError, match="already attempted"):
        runner.run_task(CONFIG["task_ids"][0], "worker-0")
    assert len(factory.requests) == count


@pytest.mark.parametrize("mode", ["local-preflight", "offline-smoke"])
def test_cli_manifest_no_credentials_no_overwrite(tmp_path, mode):
    env = {k: v for k, v in os.environ.items() if k not in {"RISK_AGENT_API_KEY", "RISK_AGENT_CA_BUNDLE"}}
    args = [sys.executable, str(CLI), "--mode", mode, "--output-root", str(tmp_path),
            "--run-id", "v22_cli_fixture"]
    result = subprocess.run(args, capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr + result.stdout
    output = tmp_path / "v22_cli_fixture"
    final = json.loads((output / "gate_summary_final_audit.json").read_text())
    assert final["passed"] and final["engineering_passed"] and final["connection_resources_released"]
    assert final["real_model_calls"] == 0 and not final["research_dataset_eligible"]
    manifest = json.loads((output / "artifact_manifest.json").read_text())
    assert {r["path"] for r in manifest["files"]} == {
        str(p.relative_to(output)) for p in output.rglob("*") if p.is_file() and p.name != "artifact_manifest.json"}
    for row in manifest["files"]:
        data = (output / row["path"]).read_bytes()
        assert len(data) == row["size_bytes"] and hashlib.sha256(data).hexdigest() == row["sha256"]
    before = (output / "artifact_manifest.json").read_bytes()
    assert subprocess.run(args, capture_output=True, env=env).returncode != 0
    assert (output / "artifact_manifest.json").read_bytes() == before


@pytest.mark.parametrize("flags", [[], ["--authorize-remote"]])
def test_real_mode_fails_before_output_without_authorization_or_proof(tmp_path, flags):
    result = subprocess.run([sys.executable, str(CLI), "--mode", "real-five-task-gate",
        "--output-root", str(tmp_path / "output"), *flags], capture_output=True)
    assert result.returncode != 0 and not (tmp_path / "output").exists()


def test_v22_drift_rejected_with_legacy_freeze_unchanged(tmp_path):
    manifests = [json.loads((ROOT / p).read_text()) for p in (
        "docs/phase2/protocol_freeze_manifest_v2_1_readonly_refresh.json",
        "docs/phase2/continue_acceptance_entry_manifest_v1.json", MANIFEST_PATH)]
    names = {MANIFEST_PATH, *(name for m in manifests for name in m["files"])}
    for name in names:
        target = tmp_path / name; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    contract = tmp_path / "configs/continue_output_contract_v1.txt"
    contract.write_text(contract.read_text() + "drift")
    load_legacy_entry(tmp_path)
    with pytest.raises(ValueError, match="v2.2 frozen input changed"):
        load_entry(tmp_path)


def test_encoding_failure_is_infrastructure_not_agent_failure():
    assert OutputContractContinueGateRunner._termination_for_exception(
        OutputContractError("local encoding error"), None) is ContinueTermination.ENVIRONMENT_FAILURE


@pytest.fixture(scope="module")
def verified_row(tmp_path_factory):
    runner, _ = make_runner(tmp_path_factory.mktemp("verified_contract_row"), observed_final)
    return runner.run_task(CONFIG["task_ids"][0], "worker-0")


@pytest.mark.parametrize("bad", ["null", "bool_label", "label_mismatch", "verifier_mismatch",
    "missing_verifier", "incomplete_chain", "missing_hash", "false_hash", "nonbool_hash",
    "input_mismatch", "checkpoint_mutated", "lineage_incomplete", "replay", "source_mutated",
    "audit_incomplete", "release_failed", "local_exception", "malformed_component", "malformed_hash"])
def test_gate_rejects_missing_or_inconsistent_evidence(verified_row, bad):
    row = deepcopy(verified_row); c = row["component"]
    assert engineering_passed(row)
    if bad == "null": row["accepted_y_continue"] = c["y_continue"] = None; c["verifier_passed"] = None
    if bad == "bool_label": row["accepted_y_continue"] = True
    if bad == "label_mismatch": c["y_continue"] = 0
    if bad == "verifier_mismatch": c["verifier_passed"] = False
    if bad == "missing_verifier": c["verifier_outcome_id"] = None
    if bad == "incomplete_chain": c["complete_chain"] = False
    if bad == "missing_hash": c["restore_hashes"].pop("agent_state_hash")
    if bad == "false_hash": c["restore_hashes"]["agent_state_hash"] = False
    if bad == "nonbool_hash": c["restore_hashes"]["agent_state_hash"] = 1
    if bad == "input_mismatch": c["first_input_exact"] = False
    if bad == "checkpoint_mutated": c["checkpoint_immutable"] = False
    if bad == "lineage_incomplete": c["tool_lineage_complete"] = False
    if bad == "replay": c["tool_replay_detected"] = True
    if bad == "source_mutated": row["source_unchanged"] = False
    if bad == "audit_incomplete": row["provider_audit"]["complete"] = False
    if bad == "release_failed": row["resources_released"] = False
    if bad == "local_exception": row["error_type"] = "OSError"
    if bad == "malformed_component": row["component"] = None
    if bad == "malformed_hash": c["restore_hashes"] = None
    assert not engineering_passed(row)


@pytest.mark.parametrize("defect", ["hash", "input", "replay", "checkpoint"])
def test_full_runner_does_not_accept_corrupted_component_even_when_complete_chain_true(tmp_path, monkeypatch, defect):
    original = OutputContractContinueGateRunner.run_checkpoint
    def corrupt(self, **kwargs):
        result = original(self, **kwargs)
        assert result["complete_chain"] and result["y_continue"] == 1
        if defect == "hash": result["restore_hashes"] = {}
        if defect == "input": result["first_input_exact"] = False
        if defect == "replay": result["tool_replay_detected"] = True
        if defect == "checkpoint": result["checkpoint_immutable"] = False
        return result
    monkeypatch.setattr(OutputContractContinueGateRunner, "run_checkpoint", corrupt)
    runner, _ = make_runner(tmp_path, observed_final)
    summary = runner.run_fixed_gate()
    assert not summary["passed"] and summary["task_attempts"] == 1
    assert summary["tasks"][0]["accepted_y_continue"] is None


def test_legacy_checkpoint_rejected_before_any_new_provider_creation(tmp_path):
    from risk_agent.collection.continue_acceptance import ContinueAcceptanceRunner as LegacyRunner
    from risk_agent.collection.continue_gate import CheckpointAuditRecord, ContinueGateError
    from risk_agent.checkpoint.manager import CheckpointIntegrityError
    parent, _ = load_legacy_entry(ROOT)
    factory = OfflineAcceptanceFactory(parent.tasks)
    legacy = LegacyRunner(root=ROOT, run_directory=tmp_path / "legacy", run_id="legacy_output_fixture",
                          offline_fixture=True, agent_factory=factory)
    row = legacy.run_task(CONFIG["task_ids"][0], "worker-0")
    record = json.loads((legacy.directory / "tasks" / row["task_id"] / "source_result.json").read_text())["checkpoint"]
    new, _ = make_runner(tmp_path / "new")
    def forbidden(*args, **kwargs):
        pytest.fail("No provider may be constructed for a legacy Checkpoint")
    continuation = OutputContractContinueGateRunner(settings=new.settings, tasks=PROFILE.tasks,
        run_directory=tmp_path / "probe", checkpoint_run=legacy.directory / "source",
        run_id=new.run_id, agent_factory=forbidden, replay_policy=PROFILE.config["continue"]["replay_policy"],
        protocol_id=PROFILE.config["protocol_id"])
    with pytest.raises((ContinueGateError, CheckpointIntegrityError), match="Prompt Version|Task prompt"):
        continuation.run_checkpoint(audit=CheckpointAuditRecord(**record),
                                  worker=new.settings.workers[0], branch_attempt_index=1)


def test_final_connection_cleanup_failure_blocks_cli_without_real_transport(tmp_path, monkeypatch, capsys):
    from scripts import run_frozen_continue_acceptance_v2 as cli
    from scripts import run_p2_d2_real_qwen_gate as legacy_cli
    from risk_agent.agent.worker_router import RemoteModelSettings
    from risk_agent.monitoring import connection_lifecycle
    evidence = tmp_path / "receipt.json"; evidence.write_text("{}")
    specs = tmp_path / "spec.json"; specs.write_text("[]")
    monkeypatch.setattr(cli, "validate_fresh_remote_evidence", lambda *a: None)
    monkeypatch.setattr(legacy_cli, "_api_preflight", lambda *a: {"offline_fixture": True})
    monkeypatch.setattr(RemoteModelSettings, "resolve_api_key", lambda *a: "offline-fixture-value")
    monkeypatch.setattr(RemoteModelSettings, "resolve_ca_bundle", lambda *a: tmp_path)
    class FakeConnections:
        def __init__(self, *a): self.adapters = []
        def launch(self, specs): assert specs == []
        def close(self): raise RuntimeError("offline owned-resource release failure")
    monkeypatch.setattr(connection_lifecycle, "ConnectionLifecycle", FakeConnections)
    monkeypatch.setattr(cli.ContinueAcceptanceRunner, "run_fixed_gate", lambda *a: {
        "passed": True, "engineering_passed": True, "gate": "CONTINUE_ENGINEERING_GATE_PASS",
        "formal_collection_ready": False, "research_dataset_eligible": False})
    code = cli.main(["--mode", "real-five-task-gate", "--authorize-remote", "--run-id", "cleanup_fixture",
        "--output-root", str(tmp_path / "out"), "--remote-evidence", str(evidence), "--connection-spec", str(specs)])
    assert code == 1
    final = json.loads((tmp_path / "out/cleanup_fixture/gate_summary_final_audit.json").read_text())
    assert not final["passed"] and not final["engineering_passed"]
    assert not final["connection_resources_released"]
    assert final["gate"] == "CONTINUE_ENGINEERING_GATE_BLOCKED"
    capsys.readouterr()


def test_multiple_tool_observations_keep_sorted_boundaries_across_restore(tmp_path, monkeypatch):
    from risk_agent.tools.task_tool_registry import TaskToolRegistry
    values = {"first.csv": "  first\n", "second.csv": {"第二": [False, "2"]}}
    monkeypatch.setattr(TaskToolRegistry, "execute", lambda self, domain, tool, args: values[args["path"]])
    def batch_then_final(task, runtime, response):
        calls = response["choices"][0]["message"]["tool_calls"]
        if not runtime.tool_history:
            calls[0]["function"]["arguments"] = json.dumps({"path": "first.csv"})
            calls.append({"id": "zz-second-call", "type": "function", "function": {
                "name": "read_csv", "arguments": json.dumps({"path": "second.csv"})}})
        else:
            calls[0]["function"].update(name="final_answer", arguments=json.dumps({"answer": "fixture"}))
        return response
    runner, factory = make_runner(tmp_path, batch_then_final)
    row = runner.run_task(CONFIG["task_ids"][0], "worker-0")
    assert row["engineering_passed"] and row["accepted_y_continue"] == 0
    assert row["component"]["first_input_exact"]
    observations = last_observation(factory.requests[-1]).splitlines()
    assert [json.loads(s) for s in observations] == [values["first.csv"], values["second.csv"]]
    assert len(observations) == 2


def test_actual_production_factory_uses_json_observations_with_mock_http_only(tmp_path, monkeypatch):
    from risk_agent.agent import agent_factory
    requests, clients = [], []
    task = next(t for t in PROFILE.tasks if t.task_id == CONFIG["task_ids"][0])
    def handler(request):
        body = json.loads(request.content); requests.append(body)
        index = len(requests)
        if index == 1:
            name, arguments = "read_csv", {"path": task.files[0].path}
        else:
            visible = last_observation({"body": body})
            observed = json.loads(visible)
            if index == 2:
                name, arguments = "filter_records", {"records": observed, "column": "category",
                    "operator": "eq", "value": "segment_1"}
            elif index == 3:
                name, arguments = "aggregate_records", {"records": observed, "group_by": "region",
                    "value_column": "units", "operation": "sum"}
            else:
                assert index == 4
                name, arguments = "final_answer", {"answer": observed}
        return httpx2.Response(200, json={"id": f"mock-real-factory-{index}",
            "object": "chat.completion", "created": 1800000000, "model": "Qwen/Qwen3-8B",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "",
                "tool_calls": [{"id": f"mock-real-call-{index}", "type": "function", "function": {
                    "name": name, "arguments": json.dumps(arguments)}}]}, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 10, "total_tokens": 50}})
    def http_client(settings, ca):
        client = httpx2.Client(transport=httpx2.MockTransport(handler), trust_env=False,
            timeout=httpx2.Timeout(settings.request_timeout_seconds, connect=settings.connect_timeout_seconds))
        clients.append(client)
        return client
    monkeypatch.setattr(agent_factory, "_http_client", http_client)
    ca = tmp_path / "mock-ca-marker"; ca.write_text("MockTransport marker; not a certificate")
    monkeypatch.setenv("RISK_AGENT_API_KEY", "offline-fixture-value")
    monkeypatch.setenv("RISK_AGENT_CA_BUNDLE", str(ca))
    runner = ContinueAcceptanceRunner(root=ROOT, run_directory=tmp_path / "entry",
        run_id="mock_production_factory", authorize_remote=True)
    row = runner.run_task(task.task_id, "worker-0")
    assert row["engineering_passed"] and row["task_success"] and row["accepted_y_continue"] == 1
    assert row["source_model_calls"] == 1 and row["component"]["model_requests_added"] == 3
    assert row["provider_audit"]["complete"] and len(requests) == 4
    assert len(clients) == 2 and all(c.is_closed for c in clients)
