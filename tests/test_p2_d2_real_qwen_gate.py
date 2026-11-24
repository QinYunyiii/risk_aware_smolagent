from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest

from risk_agent.agent import agent_factory
from risk_agent.agent.worker_router import RemoteModelSettings, WorkerConfig
from risk_agent.benchmark import generate_task_instances, load_task_templates
from risk_agent.collection.day5_gate import create_unique_run_directory
from risk_agent.collection.p2_d2_real_qwen_gate import (
    FIXED_TASK_IDS,
    P2D2GateError,
    P2D2RealQwenGateRunner,
    REQUIRED_AUDIT_FILES,
    classify_action_error,
    select_p2_d2_tasks,
    validate_first_dependency,
)


ROOT = Path(__file__).resolve().parents[1]


def _tasks():
    return generate_task_instances(
        load_task_templates(ROOT / "configs" / "task_templates_v2.yaml"),
        2,
    )


def _settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[RemoteModelSettings, WorkerConfig]:
    ca = tmp_path / "unused-ca.crt"
    ca.write_text("mock transport replaces TLS", encoding="utf-8")
    monkeypatch.setenv("P2_D2_TEST_API_KEY", "fixture-key")
    monkeypatch.setenv("P2_D2_TEST_CA", str(ca))
    worker = WorkerConfig("worker-0", "https://worker.test/v1")
    settings = RemoteModelSettings(
        model_id="Qwen/Qwen3-8B",
        model_version="Qwen3-8B-modelscope-snapshot",
        prompt_version="day2-v1",
        api_key_env="P2_D2_TEST_API_KEY",
        ca_bundle_env="P2_D2_TEST_CA",
        request_timeout_seconds=120.0,
        connect_timeout_seconds=30.0,
        router_strategy="round_robin",
        inference={
            "seed": 20261118,
            "temperature": 0.0,
            "max_tokens": 256,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        workers=(
            worker,
            WorkerConfig("worker-1", "https://worker-1.test/v1"),
        ),
    )
    return settings, worker


def _completion(
    call_id: str, tool_name: str, arguments: dict[str, Any], prompt_tokens: int
) -> dict[str, Any]:
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
                                "name": tool_name,
                                "arguments": json.dumps(arguments),
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


def test_fixed_selection_is_new_train_multistep_and_ordered() -> None:
    selected = select_p2_d2_tasks(_tasks())

    assert tuple(task.task_id for task in selected) == FIXED_TASK_IDS
    assert all(task.split.value == "train" for task in selected)
    assert all(task.template_id.startswith("p2_") for task in selected)
    assert {task.domain.value for task in selected} == {
        "data_analysis",
        "file_retrieval",
        "api_workflow",
        "calculation_logic",
    }


def test_dependency_validation_uses_actual_first_observation() -> None:
    task = next(task for task in _tasks() if task.task_id == FIXED_TASK_IDS[3])
    valid = [
        SimpleNamespace(
            tool_name="calculator",
            tool_arguments={"expression": "7 + 3"},
            tool_result="10",
            error_type=None,
        ),
        SimpleNamespace(
            tool_name="calculator",
            tool_arguments={"expression": "10 * 2"},
            tool_result="20",
            error_type=None,
        ),
    ]

    assert validate_first_dependency(task, valid)["dependency_verified"] is True
    invalid = list(valid)
    invalid[1] = SimpleNamespace(
        tool_name="calculator",
        tool_arguments={"expression": "11 * 2"},
        tool_result="22",
        error_type=None,
    )
    with pytest.raises(P2D2GateError, match="not derived"):
        validate_first_dependency(task, invalid)


def test_agent_parsing_error_is_model_behavior_not_task_execution() -> None:
    AgentParsingError = type("AgentParsingError", (RuntimeError,), {})

    assert classify_action_error(AgentParsingError("malformed tool JSON")) == (
        "model_behavior_failure"
    )
    assert classify_action_error(ValueError("local tool rejected arguments")) == (
        "task_execution_failure"
    )


def test_real_provider_wiring_captures_second_step_and_executes_restore_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, worker = _settings(tmp_path, monkeypatch)
    responses = [
        _completion("call-source-001", "calculator", {"expression": "7 + 3"}, 20),
        _completion("call-source-002", "calculator", {"expression": "10 * 2"}, 30),
        _completion(
            "call-restore-003",
            "validate_result",
            {"actual": "20", "expected": "20", "tolerance": "0"},
            40,
        ),
    ]
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if not responses:
            raise AssertionError("Unexpected provider request")
        return httpx2.Response(200, json=responses.pop(0))

    monkeypatch.setattr(
        agent_factory,
        "_http_client",
        lambda *args, **kwargs: httpx2.Client(
            transport=httpx2.MockTransport(handler), trust_env=False
        ),
    )
    run_directory = create_unique_run_directory(tmp_path / "runs", "p2_d2_test_run")
    runner = P2D2RealQwenGateRunner(
        settings=settings,
        run_directory=run_directory,
        run_id="p2_d2_test_run",
        catalog_hash="a" * 64,
        split_hash="b" * 64,
        verifier_content_hash="c" * 64,
    )
    task = next(task for task in _tasks() if task.task_id == FIXED_TASK_IDS[3])

    result = runner.run_task(task=task, worker=worker, attempt_index=4)

    assert result["passed"] is True
    assert result["capture_step"] == 2
    assert result["dependency"]["dependency_verified"] is True
    assert result["source_model_calls"] == 2
    assert result["source_tool_calls"] == 2
    assert result["restore_model_calls"] == 3
    assert result["restore_tool_calls_added"] == 0
    assert result["exact_input_match"] is True
    assert all(result["six_hash_matches"].values())
    assert result["replay_guard_result"] == "blocked_before_execution"
    assert result["restored_response_type"] == "tool_call"
    assert result["prompt_tokens"] == {"source": 50, "restore": 40}
    assert len(requests) == 3
    assert not responses
    for name in REQUIRED_AUDIT_FILES:
        if name != "task_attempts.jsonl":
            assert (run_directory / name).is_file(), name


def test_remote_cli_requires_authorization_before_creating_run(tmp_path: Path) -> None:
    output_root = tmp_path / "must_not_exist"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_p2_d2_real_qwen_gate.py",
            "--mode",
            "gate",
            "--run-id",
            "p2_d2_denied",
            "--output-root",
            str(output_root),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "--authorize-remote" in result.stderr
    assert not output_root.exists()
