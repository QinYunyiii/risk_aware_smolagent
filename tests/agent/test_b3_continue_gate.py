from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx2
import pytest

from risk_agent.agent import agent_factory
from risk_agent.agent.worker_router import RemoteModelSettings, WorkerConfig
from risk_agent.benchmark import TaskEnvironment
from risk_agent.benchmark.schema import DataSplit, Domain, TaskSpec, ToolStep
from risk_agent.collection.benchmark_tools import (
    CompletedToolReplayError,
    canonical_tool_call_signature,
    create_local_domain_tools,
)
from risk_agent.collection.continue_gate import (
    CheckpointAuditRecord,
    ContinueGateRunner,
    _prohibited_source_signatures,
)
from risk_agent.collection.day5_gate import (
    Day5GateRunner,
    checkpoint_inventory,
    create_unique_run_directory,
)
from risk_agent.monitoring import (
    BudgetState,
    ExecutionMonitor,
    JsonlTraceWriter,
    RuntimeState,
    ToolHistoryEntry,
)


def _settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[RemoteModelSettings, WorkerConfig]:
    ca = tmp_path / "unused-ca.pem"
    ca.write_text("patched transport", encoding="utf-8")
    monkeypatch.setenv("B3_TEST_API_KEY", "fixture-key")
    monkeypatch.setenv("B3_TEST_CA", str(ca))
    worker = WorkerConfig("worker-test-0", "https://worker.test/v1")
    return (
        RemoteModelSettings(
            model_id="Qwen/Qwen3-8B",
            model_version="fixture-qwen-snapshot",
            prompt_version="fixture-prompt-v1",
            api_key_env="B3_TEST_API_KEY",
            ca_bundle_env="B3_TEST_CA",
            request_timeout_seconds=30.0,
            connect_timeout_seconds=5.0,
            router_strategy="round_robin",
            inference={
                "seed": 20261118,
                "temperature": 0.0,
                "max_tokens": 256,
                "chat_template_kwargs": {"enable_thinking": False},
            },
            workers=(worker,),
        ),
        worker,
    )


def _task() -> TaskSpec:
    return TaskSpec(
        task_id="b3_continue_task",
        template_id="b3_continue_template",
        instance_id="b3_continue_001",
        domain=Domain.CALCULATION_LOGIC,
        seed=20261123,
        prompt="Use the calculator for 1 + 1, then return only 2.",
        files=(),
        inventory=(),
        orders=(),
        reference_steps=(ToolStep("calculator", {"expression": "1 + 1"}),),
        expected={"result": "2"},
        dataset_role="day7_catalog",
        split=DataSplit.TRAIN,
    )


def _completion_tool(call_id: str, expression: str, prompt_tokens: int) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{call_id}",
        "object": "chat.completion",
        "created": 1_800_000_000,
        "model": "Qwen/Qwen3-8B",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": "calculator",
                                "arguments": json.dumps({"expression": expression}),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": 4,
            "total_tokens": prompt_tokens + 4,
        },
    }


def _completion_final(call_id: str, answer: str, prompt_tokens: int) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{call_id}",
        "object": "chat.completion",
        "created": 1_800_000_001,
        "model": "Qwen/Qwen3-8B",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": "final_answer",
                                "arguments": json.dumps({"answer": answer}),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": 3,
            "total_tokens": prompt_tokens + 3,
        },
    }


def test_b3_runner_consumes_multiround_resume_then_verifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, worker = _settings(tmp_path, monkeypatch)
    responses = [
        _completion_tool("call-source-001", "1 + 1", 12),
        _completion_final("call-b2-controlled-002", "2", 18),
        _completion_tool("call-b3-new-002", "2 + 2", 18),
        _completion_final("call-b3-final-003", "2", 24),
    ]
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if not responses:
            raise AssertionError("Generator close or replay caused an extra request")
        return httpx2.Response(200, json=responses.pop(0))

    monkeypatch.setattr(
        agent_factory,
        "_http_client",
        lambda *args, **kwargs: httpx2.Client(
            transport=httpx2.MockTransport(handler), trust_env=False
        ),
    )
    checkpoint_run = create_unique_run_directory(tmp_path / "runs", "b2_fixture_run")
    day5 = Day5GateRunner(
        settings=settings,
        run_directory=checkpoint_run,
        run_id="b2_fixture_run",
        catalog_hash="a" * 64,
        split_hash="b" * 64,
        verifier_content_hash=__import__("hashlib").sha256(
            Path("src/risk_agent/benchmark/verifier.py").read_bytes()
        ).hexdigest(),
    )
    source = day5.run_task(task=_task(), worker=worker, attempt_index=1)
    checkpoint_path = Path(source["checkpoint_path"])
    audit = CheckpointAuditRecord(
        checkpoint_id=source["checkpoint_id"],
        checkpoint_path=str(checkpoint_path),
        task_id=source["task_id"],
        task_run_id=source["task_run_id"],
        worker_id=source["worker_id"],
        schema_version="2.0.0",
        manifest_sha256="c" * 64,
        artifact_count=1,
        inventory_file_count=len(checkpoint_inventory(checkpoint_path)),
        step_number=1,
        next_step_number=2,
        model_calls_used=1,
        tool_calls_used=1,
        hashes=source["checkpoint_hashes"],
        all_hashes_verified=True,
        checkpoint_inventory=checkpoint_inventory(checkpoint_path),
    )
    b3_run = create_unique_run_directory(tmp_path / "b3", "b3_fixture_run")
    runner = ContinueGateRunner(
        settings=settings,
        run_directory=b3_run,
        checkpoint_run=checkpoint_run,
        run_id="b3_fixture_run",
        tasks=(_task(),),
    )

    result = runner.run_checkpoint(
        audit=audit,
        worker=worker,
        branch_attempt_index=1,
    )

    assert result["complete_chain"] is True
    assert result["first_input_exact"] is True
    assert result["model_requests_added"] == 2
    assert result["tool_calls_added"] == 1
    assert result["tool_replay_detected"] is False
    assert result["checkpoint_immutable"] is True
    assert result["verifier_passed"] is True
    assert result["y_continue"] == 1
    assert len(requests) == 4
    assert not responses
    model_events = [
        json.loads(line)
        for line in (b3_run / "model_events.jsonl").read_text().splitlines()
    ]
    assert model_events[0]["first_request_contract_verified"] is True
    assert model_events[1]["input_hash"] != model_events[0]["input_hash"]
    assert all(row["usage_status"] == "observed" for row in model_events)
    tool_events = [
        json.loads(line)
        for line in (b3_run / "tool_events.jsonl").read_text().splitlines()
    ]
    assert [row["tool_call_id"] for row in tool_events] == ["call-b3-new-002"]
    assert checkpoint_inventory(checkpoint_path) == audit.checkpoint_inventory


def test_b3_remote_mode_requires_stage_b_authorization_before_output(tmp_path: Path) -> None:
    output_root = tmp_path / "must_not_exist"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_b3_continue_gate.py",
            "--mode",
            "five-continue-gate",
            "--run-id",
            "b3_remote_denied",
            "--output-root",
            str(output_root),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "explicit Stage B user approval" in result.stderr
    assert not output_root.exists()


def test_completed_source_tool_replay_is_blocked_before_execution(tmp_path: Path) -> None:
    task = _task()
    environment = TaskEnvironment(tmp_path / "workspace", task)
    environment.reset()
    runtime = RuntimeState(
        seed=task.seed,
        budget=BudgetState(50_000, 12, 900.0),
    )
    monitor = ExecutionMonitor(
        writer=JsonlTraceWriter(tmp_path / "tool_events.jsonl"),
        runtime_state=runtime,
        experiment_id="b3_replay_test",
        run_id="b3_replay_run",
        task_id=task.task_id,
        template_id=task.template_id,
        instance_id=task.instance_id,
        model_id="Qwen/Qwen3-8B",
        model_version="fixture",
        prompt_version="fixture",
        tool_version="fixture",
        worker_id="worker-test-0",
        environment_id="replay-test",
    )
    signature = canonical_tool_call_signature("calculator", {"expression": "1 + 1"})
    tools = create_local_domain_tools(
        environment=environment,
        monitor=monitor,
        prohibited_call_signatures=frozenset({signature}),
    )
    calculator = next(tool for tool in tools if tool.name == "calculator")
    state_before = environment.state_hash()

    with pytest.raises(CompletedToolReplayError, match="checkpoint_resume_replay_detected"):
        calculator.forward(expression="1 + 1")

    assert environment.state_hash() == state_before
    assert runtime.tool_history == []
    assert runtime.budget.tool_calls_used == 0
    assert not (tmp_path / "tool_events.jsonl").exists()


def test_replay_guard_distinguishes_completed_and_safely_failed_calls() -> None:
    def entry(tool_name: str, arguments: dict[str, Any], error: str | None) -> ToolHistoryEntry:
        return ToolHistoryEntry(
            step_id="source-001",
            tool_name=tool_name,
            tool_arguments=arguments,
            tool_result=None if error else {"ok": True},
            error_type=error,
            timestamp="2026-11-23T00:00:00+00:00",
            token_usage={"total_tokens": 0},
            latency_seconds=0.1,
            worker_id="worker-test-0",
            environment_id="source",
            checkpoint_id=None,
        )

    completed = entry("calculator", {"expression": "1 + 1"}, None)
    read_failure = entry(
        "search_files",
        {"directory": "workspace", "query": "needle"},
        "InjectedTimeoutError",
    )
    completed_signature = canonical_tool_call_signature(
        completed.tool_name, completed.tool_arguments
    )
    read_failure_signature = canonical_tool_call_signature(
        read_failure.tool_name, read_failure.tool_arguments
    )
    read_runtime = RuntimeState(
        seed=1,
        tool_history=[completed, read_failure],
        budget=BudgetState(50_000, 12, 900.0),
    )
    assert _prohibited_source_signatures(read_runtime) == frozenset(
        {completed_signature}
    )
    assert read_failure_signature not in _prohibited_source_signatures(read_runtime)

    write_arguments = {"order_id": "ord-1", "new_status": "processing"}
    write_failure = entry("update_order", write_arguments, "PartialFailureError")
    write_signature = canonical_tool_call_signature("update_order", write_arguments)
    for status, prohibited in (("committed", True), ("unknown", True), ("not_committed", False)):
        runtime = RuntimeState(
            seed=1,
            tool_history=[write_failure],
            tool_state={
                "last_failure": {
                    "tool_name": "update_order",
                    "commit_status": status,
                }
            },
            budget=BudgetState(50_000, 12, 900.0),
        )
        assert (write_signature in _prohibited_source_signatures(runtime)) is prohibited
