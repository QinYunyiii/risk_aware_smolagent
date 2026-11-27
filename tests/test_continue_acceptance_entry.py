from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx2
import pytest

from risk_agent.collection.continue_acceptance import (
    ENTRY_MANIFEST_PATH, ContinueAcceptanceRunner, load_entry,
)
from risk_agent.collection.continue_acceptance_fixture import OfflineAcceptanceFactory
from risk_agent.collection.protocol_freeze import CURRENT_MANIFEST_PATH, load_frozen_collection

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts/run_frozen_continue_acceptance.py"


def make_runner(tmp_path: Path, transform=None, clock=None):
    profile = load_frozen_collection(ROOT)
    factory = OfflineAcceptanceFactory(profile.tasks, transform=transform)
    runner = ContinueAcceptanceRunner(
        root=ROOT, run_directory=tmp_path / "run", run_id="entry_fixture_001",
        offline_fixture=True, agent_factory=factory, **({"clock": clock} if clock else {}))
    return runner, factory


def test_fixed_five_full_streams_exact_restore_budgets_and_audit(tmp_path: Path) -> None:
    runner, factory = make_runner(tmp_path)
    summary = runner.run_fixed_gate()
    assert summary["passed"] and summary["gate"] == "CONTINUE_ENTRY_OFFLINE_PASS"
    assert summary["task_attempts"] == 5 and summary["real_model_calls"] == 0
    assert [r["task_id"] for r in summary["tasks"]] == runner.config["task_ids"]
    for task in summary["tasks"]:
        assert task["capture_step"] == task["source_model_calls"] == 1
        assert task["source_unchanged"] and task["resources_released"]
        assert task["accepted_y_continue"] == 1
        assert task["component"]["termination"] == "final_answer"
        assert all(task["component"]["restore_hashes"].values())
        assert task["component"]["first_input_exact"] and task["provider_audit"]["complete"]
        assert runner.run_id in task["component"]["source_task_run_id"]
        assert task["provider_audit"]["source_events"] == 1
        assert task["provider_audit"]["continue_events"] == task["component"]["model_requests_added"]
        assert not task["research_dataset_eligible"]
    assert len(factory.requests) == sum(t["provider_audit"]["provider_events"] for t in summary["tasks"])
    assert all(row["body"]["max_tokens"] == 1024 for row in factory.requests)
    budgets = [json.loads(line) for line in (runner.directory / "continue/budget_audit.jsonl").read_text().splitlines()]
    for budget in budgets:
        assert budget["model_calls_before"] == budget["tool_calls_before"] == 1
        assert budget["tokens_before"] == 50
        assert budget["tokens_after"] == budget["model_calls_after"] * 50
    manifest = json.loads((runner.directory / "run_manifest.json").read_text())
    assert manifest["freeze_manifest_sha256"] == load_frozen_collection(ROOT).manifest_sha256
    assert hashlib.sha256((runner.directory / "entry_manifest_snapshot.json").read_bytes()).hexdigest() == manifest["entry_manifest_sha256"]


def test_skips_ineligible_parsing_step_and_captures_first_eligible_observation(tmp_path: Path) -> None:
    seen = set()
    def malformed_once(task, runtime, response):
        if task.task_id not in seen:
            seen.add(task.task_id)
            response["choices"][0]["message"] = {"role": "assistant", "content": "fixture parsing error"}
        return response
    runner, _ = make_runner(tmp_path, malformed_once)
    result = runner.run_task(runner.config["task_ids"][0], "worker-0")
    assert result["passed"] and result["capture_step"] == result["source_model_calls"] == 2
    assert result["provider_audit"]["complete"]
    decisions = [json.loads(l) for l in (runner.directory / "tasks" / result["task_id"] / "capture_decisions.jsonl").read_text().splitlines()]
    assert [d["accepted"] for d in decisions] == [False, True]


def test_visible_tool_error_is_eligible_and_resume_can_recover(tmp_path: Path) -> None:
    seen = set()
    def error_once(task, runtime, response):
        if task.task_id not in seen:
            seen.add(task.task_id)
            response["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = json.dumps({"order_id": "missing-fixture"})
        return response
    runner, _ = make_runner(tmp_path, error_once)
    result = runner.run_task("p2_api_process_control_001", "worker-0")
    assert result["passed"] and result["capture_step"] == 1
    assert result["source_model_calls"] == 1
    tools = [json.loads(l) for l in (runner.directory / "tasks/p2_api_process_control_001/source_tools.jsonl").read_text().splitlines()]
    assert tools[0]["error_type"] is not None


@pytest.mark.parametrize("failure", ["source_terminal", "infra", "wrong_final", "context"])
def test_failure_stops_gate_and_preserves_tri_state_outcomes(tmp_path: Path, failure: str) -> None:
    def transform(task, runtime, response):
        if failure == "infra" and runtime.tool_history:
            raise httpx2.ReadTimeout("fixture provider interruption")
        if failure == "source_terminal" and not runtime.tool_history:
            call = response["choices"][0]["message"]["tool_calls"][0]["function"]
            call.update(name="final_answer", arguments=json.dumps({"answer": "fixture terminal"}))
        if failure == "wrong_final":
            call = response["choices"][0]["message"]["tool_calls"][0]["function"]
            if call["name"] == "final_answer":
                call["arguments"] = json.dumps({"answer": "deliberately wrong fixture"})
        if failure == "context":
            response["usage"] = {"prompt_tokens": 8190, "completion_tokens": 10, "total_tokens": 8200}
        return response
    runner, factory = make_runner(tmp_path, transform)
    summary = runner.run_fixed_gate()
    assert not summary["passed"] and summary["task_attempts"] == 1
    result = summary["tasks"][0]
    assert result["resources_released"]
    assert result["accepted_y_continue"] == (0 if failure == "wrong_final" else None)
    assert {r["task_id"] for r in factory.requests} == {runner.config["task_ids"][0]}
    if failure == "infra":
        assert len(factory.requests) == 2  # zero provider retries
    if failure == "context":
        assert len(factory.requests) == 1
        assert not (runner.directory / "tasks" / result["task_id"] / "source_tools.jsonl").exists()


def test_shared_wall_deadline_blocks_tools_and_closes_client(tmp_path: Path) -> None:
    now = [0.0]
    def advance(task, runtime, response):
        now[0] = 901.0
        return response
    runner, factory = make_runner(tmp_path, advance, clock=lambda: now[0])
    result = runner.run_fixed_gate()["tasks"][0]
    assert result["accepted_y_continue"] is None and result["resources_released"]
    assert "task_wall_clock_budget" in result["exception_chain"]
    assert len(factory.requests) == 1


def test_audit_failure_cannot_be_accepted_as_agent_failure(tmp_path: Path) -> None:
    runner, _ = make_runner(tmp_path)
    writer = runner.audit._response_writer
    class FailAfterSource:
        count = 0
        def append(self, record):
            self.count += 1
            if self.count > 1:
                raise OSError("fixture disk failure")
            writer.append(record)
    runner.audit._response_writer = FailAfterSource()
    result = runner.run_fixed_gate()["tasks"][0]
    assert not result["passed"] and result["accepted_y_continue"] is None
    assert result["resources_released"]


def test_wrong_worker_and_repeat_attempt_do_not_reach_provider(tmp_path: Path) -> None:
    runner, factory = make_runner(tmp_path)
    task = runner.config["task_ids"][0]
    with pytest.raises(ValueError, match="failover"):
        runner.run_task(task, "worker-1")
    assert not factory.requests
    assert runner.run_task(task, "worker-0")["passed"]
    count = len(factory.requests)
    with pytest.raises(ValueError, match="already attempted"):
        runner.run_task(task, "worker-0")
    assert len(factory.requests) == count


@pytest.mark.parametrize("phase", ["source", "continue"])
def test_manual_abort_closes_owned_client_before_releasing_request_marker(tmp_path: Path, phase: str) -> None:
    def interrupt(task, runtime, response):
        if (phase == "source") == (not runtime.tool_history):
            raise KeyboardInterrupt()
        return response
    runner, _ = make_runner(tmp_path, interrupt)
    class Observer:
        def __init__(self):
            self.active = {}
        def request_started(self, worker, request_id):
            self.active[request_id] = worker
        def request_finished(self, worker, request_id, exception):
            self.active.pop(request_id)
    observer = Observer()
    runner.audit.connection_observer = observer
    result = runner.run_fixed_gate()["tasks"][0]
    assert result["accepted_y_continue"] is None and result["resources_released"]
    assert not observer.active and not runner.audit.pending
    release = json.loads((runner.directory / "tasks" / result["task_id"] / "resource_release.json").read_text())
    assert release["aborted_requests_released_after_client_close"] == 1


def test_entry_drift_rejected_without_changing_parent_freeze(tmp_path: Path) -> None:
    parent = json.loads((ROOT / CURRENT_MANIFEST_PATH).read_text())
    entry = json.loads((ROOT / ENTRY_MANIFEST_PATH).read_text())
    for name in {*parent["files"], *entry["files"], CURRENT_MANIFEST_PATH, ENTRY_MANIFEST_PATH}:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    target = tmp_path / "src/risk_agent/collection/continue_acceptance.py"
    target.write_text(target.read_text() + "\n# drift\n")
    assert load_frozen_collection(tmp_path).manifest_sha256
    with pytest.raises(ValueError, match="Acceptance entry input changed"):
        load_entry(tmp_path)


@pytest.mark.parametrize("flags", [[], ["--authorize-remote"]])
def test_real_cli_requires_authorization_and_proof_before_output(tmp_path: Path, flags: list[str]) -> None:
    result = subprocess.run([sys.executable, str(CLI), "--mode", "real-five-task-gate",
        "--output-root", str(tmp_path / "output"), *flags], capture_output=True, text=True)
    assert result.returncode != 0
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("mode", ["local-preflight", "offline-smoke"])
def test_offline_cli_needs_no_real_credentials_and_publishes_exact_manifest(tmp_path: Path, mode: str) -> None:
    env = {k: v for k, v in os.environ.items() if k not in {"RISK_AGENT_API_KEY", "RISK_AGENT_CA_BUNDLE"}}
    result = subprocess.run([sys.executable, str(CLI), "--mode", mode,
        "--output-root", str(tmp_path), "--run-id", "cli_offline_fixture"],
        capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr + result.stdout
    root = tmp_path / "cli_offline_fixture"
    final = json.loads((root / "gate_summary_final_audit.json").read_text())
    assert final["gate"] == ("CONTINUE_ENTRY_OFFLINE_PASS" if mode == "offline-smoke" else "CONTINUE_ENTRY_PREFLIGHT_PASS")
    assert final["real_model_calls"] == 0
    manifest = json.loads((root / "artifact_manifest.json").read_text())
    actual = {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and p.name != "artifact_manifest.json"}
    assert actual == {r["path"] for r in manifest["files"]}
    assert all(hashlib.sha256((root / r["path"]).read_bytes()).hexdigest() == r["sha256"] for r in manifest["files"])
    before = (root / "artifact_manifest.json").read_bytes()
    repeat = subprocess.run([sys.executable, str(CLI), "--mode", mode,
        "--output-root", str(tmp_path), "--run-id", "cli_offline_fixture"], capture_output=True, text=True, env=env)
    assert repeat.returncode != 0 and (root / "artifact_manifest.json").read_bytes() == before


@pytest.mark.parametrize("defect", [None, "stale_gpu", "stale_ownership", "other_run", "container", "user", "listener"])
def test_fresh_remote_receipt_identity_and_run_binding_without_network(defect: str | None) -> None:
    from scripts.run_frozen_continue_acceptance import validate_fresh_remote_evidence
    from scripts.run_p2_d2_real_qwen_gate import EXPECTED_WEIGHTS
    _, config = load_entry(ROOT)
    now = datetime.now(timezone.utc)
    value = {
        **{key: True for key in ("remote_operations_authorized", "https_service_authorized",
            "service_ownership_verified", "gpu_mapping_verified", "model_version_verified", "weight_identity_verified")},
        "model_weight_identity": EXPECTED_WEIGHTS,
        "versions": {"vllm": "0.34.0", "torch": "2.14.0+cu132", "transformers": "5.22.2"},
        "checked_at": now.isoformat(), "acceptance_run_id": "fixture_receipt_001",
        "gpu_samples_utc": [(now - timedelta(seconds=90)).isoformat(), (now - timedelta(seconds=20)).isoformat()],
        "worker_gpu_mapping": {"worker-0": "fixture-gpu-0", "worker-1": "fixture-gpu-1"},
        "worker_ownership": {"observed_at": now.isoformat(), "passed": True, "workers": [
            {"worker_id": wid, "remote_container_id": config["preflight"]["expected_remote_container"],
             "remote_user": "root", "alive": True, "ownership_verified": True, "listener_ownership_verified": True}
            for wid in ("worker-0", "worker-1")]},
    }
    if defect == "stale_gpu":
        value["gpu_samples_utc"] = [(now - timedelta(seconds=900)).isoformat(), (now - timedelta(seconds=800)).isoformat()]
    if defect == "stale_ownership":
        value["worker_ownership"]["observed_at"] = (now - timedelta(seconds=900)).isoformat()
    if defect == "other_run":
        value["acceptance_run_id"] = "unrelated_run"
    worker = value["worker_ownership"]["workers"][0]
    if defect == "container":
        worker["remote_container_id"] = "unrelated-container"
    if defect == "user":
        worker["remote_user"] = "unrelated-user"
    if defect == "listener":
        worker["listener_ownership_verified"] = False
    if defect:
        with pytest.raises(RuntimeError):
            validate_fresh_remote_evidence(value, "fixture_receipt_001", config)
    else:
        validate_fresh_remote_evidence(value, "fixture_receipt_001", config)
