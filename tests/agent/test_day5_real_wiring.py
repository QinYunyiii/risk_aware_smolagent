from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx2
import openai
import pytest

from risk_agent.agent import (
    AgentStateAdapter,
    CheckpointResumeError,
    CheckpointableToolCallingAgent,
    RecordingModel,
)
from risk_agent.agent import agent_factory
from risk_agent.agent.agent_factory import create_checkpointable_benchmark_agent
from risk_agent.agent.worker_router import RemoteModelSettings, WorkerConfig
from risk_agent.benchmark import TaskEnvironment
from risk_agent.benchmark.schema import DataSplit, Domain, TaskSpec, ToolStep
from risk_agent.checkpoint import CheckpointManager
from risk_agent.collection.day5_gate import (
    Day5GateRunner,
    RUN_RESEARCH_FLAGS,
    create_unique_run_directory,
)
from risk_agent.monitoring import BudgetState, RuntimeState
from risk_agent.tools.calculation_tools import CalculatorTool
from tests.agent.test_checkpointable_agent import (
    _agent,
    _capture,
    _expected,
    _final_answer,
)


def _settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[RemoteModelSettings, WorkerConfig]:
    ca = tmp_path / "unused-ca.pem"
    ca.write_text("patched HTTP transport", encoding="utf-8")
    monkeypatch.setenv("DAY5_TEST_API_KEY", "fixture-key")
    monkeypatch.setenv("DAY5_TEST_CA", str(ca))
    worker = WorkerConfig("worker-test-0", "https://worker.test/v1")
    settings = RemoteModelSettings(
        model_id="Qwen/Qwen3-8B",
        model_version="fixture-qwen-snapshot",
        prompt_version="fixture-prompt-v1",
        api_key_env="DAY5_TEST_API_KEY",
        ca_bundle_env="DAY5_TEST_CA",
        request_timeout_seconds=30.0,
        connect_timeout_seconds=5.0,
        router_strategy="round_robin",
        inference={
            "seed": 20261118,
            "temperature": 0.0,
            "max_tokens": 256,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        workers=(
            worker,
            WorkerConfig("worker-test-1", "https://worker-1.test/v1"),
        ),
    )
    return settings, worker


def _task() -> TaskSpec:
    return TaskSpec(
        task_id="day5_calc_task",
        template_id="day5_calc_template",
        instance_id="day5_calc_001",
        domain=Domain.CALCULATION_LOGIC,
        seed=20261118,
        prompt="Use the calculator to evaluate 1 + 1, then return the result.",
        files=(),
        inventory=(),
        orders=(),
        reference_steps=(ToolStep("calculator", {"expression": "1 + 1"}),),
        expected={"value": "2"},
        dataset_role="day7_catalog",
        split=DataSplit.TRAIN,
    )


def _completion(call_id: str, expression: str, prompt_tokens: int) -> dict[str, Any]:
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


def test_production_day5_wiring_capture_restore_and_controlled_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, worker = _settings(tmp_path, monkeypatch)
    responses = [
        _completion("call-source-001", "1 + 1", 12),
        _completion("call-restored-002", "99 + 1", 20),
    ]
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if not responses:
            raise AssertionError("Unexpected provider request")
        return httpx2.Response(200, json=responses.pop(0))

    def mock_http_client(*args: Any, **kwargs: Any) -> httpx2.Client:
        return httpx2.Client(transport=httpx2.MockTransport(handler), trust_env=False)

    monkeypatch.setattr(agent_factory, "_http_client", mock_http_client)
    run_directory = create_unique_run_directory(tmp_path / "runs", "day5_test_run")
    runner = Day5GateRunner(
        settings=settings,
        run_directory=run_directory,
        run_id="day5_test_run",
        catalog_hash="a" * 64,
        split_hash="b" * 64,
        verifier_content_hash="c" * 64,
    )

    result = runner.run_task(task=_task(), worker=worker, attempt_index=1)

    assert result["passed"] is True
    assert result["source_next_input_status"] == "intercepted"
    assert result["source_next_input_hash"] == result["checkpoint_next_input_hash"]
    assert result["restored_actual_input_hash"] == result["checkpoint_next_input_hash"]
    assert (
        result["source_next_input_canonical"]
        == result["checkpoint_next_input_canonical"]
        == result["restored_actual_input_canonical"]
    )
    assert result["source_model_calls"] == 1
    assert result["restored_model_calls"] == 2
    assert result["source_tool_calls"] == 1
    assert result["restore_tool_calls_added"] == 0
    assert result["controlled_response"]["tool_calls"][0]["id"] == "call-restored-002"
    assert result["controlled_request_usage"]["prompt_tokens"] == 20
    assert {key: result[key] for key in RUN_RESEARCH_FLAGS} == RUN_RESEARCH_FLAGS
    assert len(requests) == 2
    assert all(
        request.url == httpx2.URL("https://worker.test/v1/chat/completions")
        for request in requests
    )
    tool_records = (run_directory / "tool_trajectories.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(tool_records) == 1


def test_checkpointable_factory_freezes_worker_and_single_attempt_429(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, worker = _settings(tmp_path, monkeypatch)
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            429,
            headers={"x-request-id": "day5-429"},
            json={"error": {"message": "day5 rate limit", "type": "rate_limit_error"}},
        )

    monkeypatch.setattr(
        agent_factory,
        "_http_client",
        lambda *args, **kwargs: httpx2.Client(
            transport=httpx2.MockTransport(handler), trust_env=False
        ),
    )
    runtime = RuntimeState(
        seed=1,
        budget=BudgetState(50_000, 12, 900.0),
    )
    agent, recording, underlying = create_checkpointable_benchmark_agent(
        settings,
        worker,
        [CalculatorTool()],
        runtime,
        max_steps=10,
        max_model_calls=10,
        agent_name="day5_factory_test",
    )
    try:
        assert isinstance(agent, CheckpointableToolCallingAgent)
        assert isinstance(recording, RecordingModel)
        optional = recording.model_fingerprint["optional"]
        assert optional["endpoint"] == worker.base_url
        assert optional["model_version"] == settings.model_version
        assert optional["worker_id"] == worker.worker_id
        assert recording.model_fingerprint["client"]["base_url"].rstrip("/") == worker.base_url
        agent.step_number = 1
        with pytest.raises(openai.RateLimitError, match="day5 rate limit"):
            recording.generate(
                agent.write_memory_to_messages(),
                tools_to_call_from=agent.tools_and_managed_agents,
            )
        assert len(requests) == 1
        assert recording.executed_calls == 1
        assert recording.model_calls_used == 1
        assert underlying.client.max_retries == 0
        assert underlying.retryer.max_attempts == 1
    finally:
        underlying.client.close()


def test_unique_run_directory_refuses_overwrite(tmp_path: Path) -> None:
    first = create_unique_run_directory(tmp_path, "day5_unique_run")
    marker = first / "keep.txt"
    marker.write_text("preserve", encoding="utf-8")

    with pytest.raises(FileExistsError):
        create_unique_run_directory(tmp_path, "day5_unique_run")

    assert marker.read_text(encoding="utf-8") == "preserve"


def test_scripted_controlled_request_is_one_shot_and_never_processes_tools(
    tmp_path: Path,
) -> None:
    task, _, source_runtime, source_agent, source_model, source_stream, _ = _capture(
        tmp_path
    )
    expected = _expected(
        AgentStateAdapter.capture(source_agent),
        source_model.requests[0],
        source_runtime,
    )
    source_stream.close()
    restored_environment = TaskEnvironment(tmp_path / "controlled_restore", task)
    restored_environment.reset()
    restored = CheckpointManager(tmp_path / "checkpoints").restore_v2(
        checkpoint_id="day3_checkpoint",
        environment=restored_environment,
    )
    agent, model = _agent(restored_environment, restored.runtime_state, [_final_answer()])
    model.restore_model_calls_used(1)
    agent.restore_snapshot(restored.agent_state_snapshot)
    model.expect(expected)
    memory_count = len(agent.memory.steps)
    history_count = len(restored.runtime_state.tool_history)

    response = agent.request_next_model_response()

    assert response.step_number == 2
    assert response.message.tool_calls[0].function.name == "final_answer"
    assert len(agent.memory.steps) == memory_count
    assert len(restored.runtime_state.tool_history) == history_count
    assert model.underlying.calls == 1
    with pytest.raises(CheckpointResumeError, match="unconsumed first-request contract"):
        agent.request_next_model_response()


def test_cli_local_preflight_is_offline_and_preserves_nonformal_flags(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "day5_runs"
    command = [
        sys.executable,
        "scripts/run_b2_day5_real_qwen_gate.py",
        "--mode",
        "local-preflight",
        "--run-id",
        "day5_local_preflight",
        "--output-root",
        str(output_root),
    ]

    result = subprocess.run(command, check=False, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    summary = json.loads(
        (output_root / "day5_local_preflight/local_preflight.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["network_requests_made"] == 0
    assert summary["research_flags"] == RUN_RESEARCH_FLAGS
    manifest = json.loads(
        (output_root / "day5_local_preflight/run_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert {key: manifest[key] for key in RUN_RESEARCH_FLAGS} == RUN_RESEARCH_FLAGS

    repeated = subprocess.run(command, check=False, capture_output=True, text=True)
    assert repeated.returncode != 0
    assert summary == json.loads(
        (output_root / "day5_local_preflight/local_preflight.json").read_text(
            encoding="utf-8"
        )
    )


def test_cli_remote_mode_requires_explicit_authorization_before_any_output(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "must_not_exist"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_b2_day5_real_qwen_gate.py",
            "--mode",
            "remote-preflight",
            "--run-id",
            "day5_remote_denied",
            "--output-root",
            str(output_root),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "--authorize-remote" in result.stderr
    assert not output_root.exists()
