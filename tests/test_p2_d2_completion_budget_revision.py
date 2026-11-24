from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx2
import pytest
import yaml
from smolagents import Model
from smolagents.models import ChatMessage, MessageRole
from smolagents.monitoring import TokenUsage

from risk_agent.agent import ExpectedModelRequest, ModelRequestRejected, RecordingModel
from risk_agent.agent import agent_factory
from risk_agent.agent.agent_state_adapter import (
    canonical_hash,
    serialize_chat_message,
)
from risk_agent.agent.worker_router import RemoteModelSettings
from risk_agent.benchmark import generate_task_instances, load_task_templates
from risk_agent.checkpoint.v2_manager import CHECKPOINT_SCHEMA_VERSION
from risk_agent.collection.p2_d2_completion_budget import (
    CompletionBudgetConfigurationError,
    DIAGNOSTIC_MODE_1024,
    EXPERIMENT_CONFIG_VERSION_1024,
    EXPERIMENT_ID_1024,
    GENERATION_CONFIG_VERSION_1024,
    T1_TASK_ID,
    load_completion_budget_profile,
    validate_completion_budget_settings,
)
from risk_agent.collection.p2_d2_real_qwen_gate import (
    FIXED_TASK_IDS,
    P2D2RealQwenGateRunner,
)
from risk_agent.monitoring import BudgetState, RuntimeState
from scripts.run_p2_d2_real_qwen_gate import _load_gate_config


ROOT = Path(__file__).resolve().parents[1]
BASELINE_REMOTE_SHA256 = "1a347dcf96848d7ea4ea390987d0ebe4526b06f6143d70c5f379903feccc7436"
BASELINE_GATE_SHA256 = "56f1428791a76c8a5e8eed982251cc5d030472e55ebcbd8628d4a111ad5d89e5"
CATALOG_FILE_SHA256 = "3cae7519dffd97cd105f1bfca87183f34d15bc7e613b611a44a810d4dfff1d4f"
T1_PROMPT_HASH = "9c86b154c684d22a976ca1a4bff45b8c158c36a48f9d632c4b23bedb7aa509ec"
T1_REFERENCE_STEPS_HASH = "7d7c404dca887610c92a8d0fcd29d3367a23e3ce958ed1517953c20631c64334"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tasks():
    return generate_task_instances(
        load_task_templates(ROOT / "configs" / "task_templates_v2.yaml"),
        2,
    )


def _settings_1024(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> RemoteModelSettings:
    ca = tmp_path / "mock-ca.crt"
    ca.write_text("MockTransport bypasses TLS", encoding="utf-8")
    monkeypatch.setenv("RISK_AGENT_API_KEY", "fixture-key")
    monkeypatch.setenv("RISK_AGENT_CA_BUNDLE", str(ca))
    settings = RemoteModelSettings.from_yaml(
        ROOT / "configs" / "remote_model_p2_d2_1024.yaml"
    )
    validate_completion_budget_settings(
        settings,
        candidate_config=ROOT / "configs" / "remote_model_p2_d2_1024.yaml",
        baseline_config=ROOT / "configs" / "remote_model.yaml",
    )
    return settings


def _completion(
    call_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    prompt_tokens: int,
    completion_tokens: int,
) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{call_id}",
        "object": "chat.completion",
        "created": 1_800_000_100,
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
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _text_completion(
    content: str,
    *,
    finish_reason: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> dict[str, Any]:
    return {
        "id": "chatcmpl-t1-1024-truncated",
        "object": "chat.completion",
        "created": 1_800_000_101,
        "model": "Qwen/Qwen3-8B",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [],
                },
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _t1_rows(task: Any) -> list[dict[str, str]]:
    fixture = next(item for item in task.files if item.path.endswith(".csv"))
    return list(csv.DictReader(io.StringIO(fixture.content)))


def _runner(
    *, settings: RemoteModelSettings, run_directory: Path, run_id: str
) -> P2D2RealQwenGateRunner:
    return P2D2RealQwenGateRunner(
        settings=settings,
        run_directory=run_directory,
        run_id=run_id,
        catalog_hash="a" * 64,
        split_hash="b" * 64,
        verifier_content_hash="c" * 64,
        experiment_id=EXPERIMENT_ID_1024,
        experiment_config_version=EXPERIMENT_CONFIG_VERSION_1024,
        diagnostic_mode=DIAGNOSTIC_MODE_1024,
    )


def test_frozen_256_configs_and_historical_gate_validation_are_unchanged() -> None:
    remote = yaml.safe_load((ROOT / "configs/remote_model.yaml").read_text())
    gate = yaml.safe_load((ROOT / "configs/p2_d2_real_qwen_gate.yaml").read_text())

    assert remote["inference"]["max_tokens"] == 256
    assert gate["limits"]["max_completion_tokens"] == 256
    assert _sha256(ROOT / "configs/remote_model.yaml") == BASELINE_REMOTE_SHA256
    assert _sha256(ROOT / "configs/p2_d2_real_qwen_gate.yaml") == BASELINE_GATE_SHA256
    assert _load_gate_config(ROOT / "configs/p2_d2_real_qwen_gate.yaml")
    with pytest.raises(RuntimeError, match="Frozen P2-D2 configuration mismatch"):
        _load_gate_config(ROOT / "configs/p2_d2_t1_budget_1024.yaml")


def test_registered_1024_profile_and_model_config_are_strictly_isolated() -> None:
    profile = load_completion_budget_profile(
        ROOT / "configs/p2_d2_t1_budget_1024.yaml",
        baseline_gate_config=ROOT / "configs/p2_d2_real_qwen_gate.yaml",
        baseline_remote_config=ROOT / "configs/remote_model.yaml",
    )
    settings = RemoteModelSettings.from_yaml(
        ROOT / "configs/remote_model_p2_d2_1024.yaml"
    )
    validate_completion_budget_settings(
        settings,
        candidate_config=ROOT / "configs/remote_model_p2_d2_1024.yaml",
        baseline_config=ROOT / "configs/remote_model.yaml",
    )

    assert profile.experiment_id == EXPERIMENT_ID_1024
    assert profile.experiment_config_version == EXPERIMENT_CONFIG_VERSION_1024
    assert profile.generation_config_version == GENERATION_CONFIG_VERSION_1024
    assert profile.fixed_task_id == T1_TASK_ID
    assert profile.limits["max_completion_tokens"] == 1024
    assert settings.inference["max_tokens"] == 1024


@pytest.mark.parametrize("token_limit", [256, 512, 2048])
def test_unregistered_1024_experiment_token_values_are_rejected(
    tmp_path: Path, token_limit: int
) -> None:
    value = yaml.safe_load(
        (ROOT / "configs/p2_d2_t1_budget_1024.yaml").read_text(encoding="utf-8")
    )
    value["limits"]["max_completion_tokens"] = token_limit
    path = tmp_path / "invalid-experiment.yaml"
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")

    with pytest.raises(CompletionBudgetConfigurationError, match="profile fields"):
        load_completion_budget_profile(
            path,
            baseline_gate_config=ROOT / "configs/p2_d2_real_qwen_gate.yaml",
            baseline_remote_config=ROOT / "configs/remote_model.yaml",
        )


@pytest.mark.parametrize("token_limit", [256, 512, 2048])
def test_unregistered_model_token_values_are_rejected(
    tmp_path: Path, token_limit: int
) -> None:
    value = yaml.safe_load(
        (ROOT / "configs/remote_model_p2_d2_1024.yaml").read_text(encoding="utf-8")
    )
    value["inference"]["max_tokens"] = token_limit
    path = tmp_path / "invalid-model.yaml"
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    settings = RemoteModelSettings.from_yaml(path)

    with pytest.raises(CompletionBudgetConfigurationError, match="max_tokens=1024"):
        validate_completion_budget_settings(
            settings,
            candidate_config=path,
            baseline_config=ROOT / "configs/remote_model.yaml",
        )


def test_1024_cli_is_separate_fixed_t1_and_remote_fails_closed_without_authorization(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "must-not-exist"
    command = [
        sys.executable,
        "scripts/run_p2_d2_t1_budget_diagnostic.py",
        "--mode",
        DIAGNOSTIC_MODE_1024,
        "--run-id",
        "p2_d2_t1_1024_denied",
        "--output-root",
        str(output_root),
    ]
    denied = subprocess.run(
        command, cwd=ROOT, check=False, capture_output=True, text=True
    )
    arbitrary = subprocess.run(
        [*command, "--task-id", FIXED_TASK_IDS[1]],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert denied.returncode != 0
    assert "--authorize-remote" in denied.stderr
    assert arbitrary.returncode != 0
    assert "unrecognized arguments: --task-id" in arbitrary.stderr
    assert not output_root.exists()


def test_old_gate_rejects_1024_model_config_before_creating_run(tmp_path: Path) -> None:
    output_root = tmp_path / "old-gate-must-not-exist"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_p2_d2_real_qwen_gate.py",
            "--mode",
            "local-preflight",
            "--run-id",
            "old_gate_with_1024",
            "--output-root",
            str(output_root),
            "--remote-config",
            "configs/remote_model_p2_d2_1024.yaml",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Remote inference configuration differs" in result.stderr
    assert not output_root.exists()


def test_actual_provider_boundary_receives_1024_and_remaining_total_budget_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings_1024(tmp_path, monkeypatch)
    worker = settings.workers[0]
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": f"chatcmpl-budget-{len(bodies)}",
                "object": "chat.completion",
                "created": 1_800_000_102,
                "model": "Qwen/Qwen3-8B",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "done"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
        )

    monkeypatch.setattr(
        agent_factory,
        "_http_client",
        lambda *args, **kwargs: httpx2.Client(
            transport=httpx2.MockTransport(handler), trust_env=False
        ),
    )
    runtime_full = RuntimeState(seed=1, budget=BudgetState(50_000, 12, 900.0))
    full_agent, full, full_underlying = agent_factory.create_checkpointable_benchmark_agent(
        settings,
        worker,
        [agent_factory.CalculatorTool()],
        runtime_full,
        max_steps=10,
        max_model_calls=10,
        agent_name="full_1024_boundary",
    )
    runtime_limited = RuntimeState(seed=1, budget=BudgetState(50_000, 12, 900.0))
    runtime_limited.budget.tokens_used = 49_400
    limited_agent, limited, limited_underlying = (
        agent_factory.create_checkpointable_benchmark_agent(
        settings,
        worker,
        [agent_factory.CalculatorTool()],
        runtime_limited,
        max_steps=10,
        max_model_calls=10,
        agent_name="remaining_budget_boundary",
        )
    )
    full_agent.step_number = 1
    limited_agent.step_number = 1
    message = [ChatMessage(role=MessageRole.USER, content="Return a final answer.")]
    try:
        full.generate(message)
        limited.generate(message)
    finally:
        full_underlying.client.close()
        limited_underlying.client.close()

    assert [body["max_tokens"] for body in bodies] == [1024, 600]
    assert runtime_full.budget.tokens_used == 15
    assert runtime_limited.budget.tokens_used == 49_415
    assert full.model_fingerprint["generation_config"]["max_tokens"] == 1024
    assert limited.requests[-1].status == "succeeded"


def test_valid_over_256_tool_call_completes_1024_source_restore_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings_1024(tmp_path, monkeypatch)
    worker = settings.workers[0]
    task = next(task for task in _tasks() if task.task_id == T1_TASK_ID)
    rows = _t1_rows(task)
    filtered = [row for row in rows if row["category"] == "segment_1"]
    second_arguments = {
        "records": rows,
        "column": "category",
        "operator": "eq",
        "value": "segment_1",
    }
    responses = [
        _completion(
            "call-1024-read",
            "read_csv",
            {"path": "data/p2_data_region_units_sum_001.csv"},
            prompt_tokens=20,
            completion_tokens=31,
        ),
        _completion(
            "call-1024-filter",
            "filter_records",
            second_arguments,
            prompt_tokens=30,
            completion_tokens=400,
        ),
        _completion(
            "call-1024-restore",
            "aggregate_records",
            {
                "records": filtered,
                "group_by": "region",
                "value_column": "units",
                "operation": "sum",
            },
            prompt_tokens=40,
            completion_tokens=35,
        ),
    ]
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        if not responses:
            raise AssertionError("1024 diagnostic made an extra Provider request")
        return httpx2.Response(200, json=responses.pop(0))

    monkeypatch.setattr(
        agent_factory,
        "_http_client",
        lambda *args, **kwargs: httpx2.Client(
            transport=httpx2.MockTransport(handler), trust_env=False
        ),
    )
    run_directory = tmp_path / "successful-1024"
    summary = _runner(
        settings=settings, run_directory=run_directory, run_id="successful_1024"
    ).run_single_t1(tasks=_tasks(), worker=worker)

    assert summary["status"] == "PASS"
    assert summary["mode"] == DIAGNOSTIC_MODE_1024
    assert summary["experiment_id"] == EXPERIMENT_ID_1024
    assert summary["attempted_task_ids"] == [T1_TASK_ID]
    assert summary["attempt_count"] == 1
    assert summary["automatic_model_retries"] == 0
    assert summary["replacement_tasks"] == 0
    assert summary["t2_t5_started"] is False
    result = summary["result"]
    assert result["source_model_calls"] == 2
    assert result["source_tool_calls"] == 2
    assert result["restore_model_calls"] == 3
    assert result["restore_tool_calls_added"] == 0
    assert result["exact_input_match"] is True
    assert all(result["six_hash_matches"].values())
    assert result["replay_guard_result"] == "blocked_before_execution"
    assert [body["max_tokens"] for body in bodies] == [1024, 1024, 1024]
    assert len(bodies) == 3
    assert not responses
    assert len(json.dumps(second_arguments)) > 256
    checkpoint = run_directory / "checkpoints/checkpoint_p2_data_region_units_sum_001_001"
    snapshot = json.loads((checkpoint / "agent/agent_state.json").read_text())
    manifest = json.loads((checkpoint / "manifest.json").read_text())
    assert snapshot["payload"]["model"]["generation"]["max_tokens"] == 1024
    assert manifest["identity"]["experiment_id"] == EXPERIMENT_ID_1024
    assert len(manifest["hashes"]) == 6
    budget = manifest["runtime_state"]["budget_state"]
    assert budget["model_calls_used"] == 2
    assert budget["tool_calls_used"] == 2
    assert budget["prompt_tokens_used"] + budget["completion_tokens_used"] == 481
    assert budget["latency_seconds_used"] >= 0


def test_1024_length_response_is_audited_and_stops_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings_1024(tmp_path, monkeypatch)
    worker = settings.workers[0]
    malformed = (
        '<tool_call>\n{"name":"filter_records","arguments":{"records":'
        '[{"record_id":"r1","category":"segment_2"}'
    )
    responses = [
        _completion(
            "call-1024-read-failure",
            "read_csv",
            {"path": "data/p2_data_region_units_sum_001.csv"},
            prompt_tokens=20,
            completion_tokens=31,
        ),
        _text_completion(
            malformed,
            finish_reason="length",
            prompt_tokens=300,
            completion_tokens=1024,
        ),
    ]
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if not responses:
            raise AssertionError("Malformed 1024 response was retried")
        return httpx2.Response(200, json=responses.pop(0))

    monkeypatch.setattr(
        agent_factory,
        "_http_client",
        lambda *args, **kwargs: httpx2.Client(
            transport=httpx2.MockTransport(handler), trust_env=False
        ),
    )
    run_directory = tmp_path / "truncated-1024"
    summary = _runner(
        settings=settings, run_directory=run_directory, run_id="truncated_1024"
    ).run_single_t1(tasks=_tasks(), worker=worker)

    assert summary["status"] == "BLOCKED"
    assert summary["attempt_count"] == 1
    assert summary["automatic_model_retries"] == 0
    assert summary["replacement_tasks"] == 0
    assert summary["t2_t5_started"] is False
    assert summary["checkpoint_created"] is False
    assert len(requests) == 2
    provider = [
        json.loads(line)
        for line in (run_directory / "audit/provider_response_audit.jsonl")
        .read_text()
        .splitlines()
    ]
    parsing = [
        json.loads(line)
        for line in (run_directory / "audit/parsing_outcome_audit.jsonl")
        .read_text()
        .splitlines()
    ]
    assert provider[1]["finish_reason"] == "length"
    assert provider[1]["completion_tokens"] == 1024
    assert provider[1]["delegated_completion_token_limit"] == 1024
    assert provider[1]["raw_response_snapshot"]["choices"][0]["message"]["content"] == (
        malformed
    )
    assert len(provider[1]["raw_response_sha256"]) == 64
    assert parsing[1]["audit_request_id"] == provider[1]["audit_request_id"]
    assert parsing[1]["parsing_status"] == "failed"
    assert parsing[1]["exception_type"] == "AgentParsingError"
    assert len((run_directory / "tool_trajectories.jsonl").read_text().splitlines()) == 1


class _VersionedLocalModel(Model):
    def __init__(self, max_tokens: int) -> None:
        super().__init__(
            model_id="Qwen/Qwen3-8B",
            seed=20261118,
            temperature=0.0,
            max_tokens=max_tokens,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        self.calls = 0

    def generate(self, *args: Any, **kwargs: Any) -> ChatMessage:
        self.calls += 1
        return ChatMessage(
            role=MessageRole.ASSISTANT,
            content="done",
            token_usage=TokenUsage(input_tokens=1, output_tokens=1),
        )


@pytest.mark.parametrize("source_limit,restore_limit", [(256, 1024), (1024, 256)])
def test_cross_version_resume_contract_is_rejected_before_provider(
    source_limit: int, restore_limit: int
) -> None:
    source = RecordingModel(_VersionedLocalModel(source_limit), max_model_calls=10)
    restore_underlying = _VersionedLocalModel(restore_limit)
    restore = RecordingModel(restore_underlying, max_model_calls=10)
    messages = [ChatMessage(role=MessageRole.USER, content="Continue after checkpoint.")]
    serialized = [serialize_chat_message(item) for item in messages]
    expected = ExpectedModelRequest(
        messages=serialized,
        next_model_input_hash=canonical_hash(serialized),
        public_tool_schemas=[],
        generation_config=dict(source.kwargs),
        model_id=source.model_id,
        step_number=2,
        remaining_steps=9,
        model_calls_used=0,
        max_model_calls=10,
        tool_calls_used=0,
        max_tool_calls=12,
        tokens_used=0,
        max_tokens=50_000,
        latency_seconds_used=0.0,
        max_latency_seconds=900.0,
    )
    restore.expect(expected)

    with pytest.raises(ModelRequestRejected, match="generation_config"):
        restore.generate(messages)

    assert restore_underlying.calls == 0
    assert restore.requests[-1].status == "rejected"
    assert restore.requests[-1].difference == "generation_config"
    assert source.model_fingerprint != restore.model_fingerprint


def test_catalog_prompt_reference_checkpoint_and_replay_implementations_are_frozen() -> None:
    task = next(task for task in _tasks() if task.task_id == T1_TASK_ID)
    reference_steps = [
        {"tool": step.tool, "arguments": dict(step.arguments)}
        for step in task.reference_steps
    ]

    assert _sha256(ROOT / "configs/task_templates_v2.yaml") == CATALOG_FILE_SHA256
    assert canonical_hash(task.prompt) == T1_PROMPT_HASH
    assert canonical_hash(reference_steps) == T1_REFERENCE_STEPS_HASH
    assert task.split.value == "train"
    assert task.seed == 81001
    assert CHECKPOINT_SCHEMA_VERSION == "2.0.0"
    assert _sha256(ROOT / "src/risk_agent/checkpoint/v2_manager.py") == (
        "3cd25d70df2295d05d767eacb33c13f7ac21235a4b5b0747c2c0ff0a1e1810ff"
    )
    assert _sha256(ROOT / "src/risk_agent/collection/benchmark_tools.py") == (
        "c59a5e6fd71cdf1bad8849c8ac8513173688ff4d18af015334c753780d949d35"
    )
    assert _sha256(ROOT / "src/risk_agent/agent/agent_state_adapter.py") == (
        "502a265255d426cbd7714230bb9c5ed640f6e57db3790e0d57944cbfbda15d11"
    )
