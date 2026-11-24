from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx2
import pytest
import yaml

from risk_agent.agent import agent_factory
from risk_agent.agent.worker_router import RemoteModelSettings
from risk_agent.benchmark import TaskEnvironment, generate_task_instances, load_task_templates
from risk_agent.benchmark.step_references import resolve_step_value
from risk_agent.checkpoint.v2_manager import CHECKPOINT_SCHEMA_VERSION
from risk_agent.collection import create_local_domain_tools
from risk_agent.collection.day5_gate import create_unique_run_directory
from risk_agent.collection.p2_d2_completion_budget import (
    CompletionBudgetConfigurationError,
    FULL_GATE_EXPERIMENT_CONFIG_VERSION_1024,
    FULL_GATE_EXPERIMENT_ID_1024,
    FULL_GATE_MODE_1024,
    FULL_GATE_WORKER_SCHEDULE,
    GENERATION_CONFIG_VERSION_1024,
    load_completion_budget_profile,
    load_full_gate_1024_profile,
    validate_completion_budget_settings,
)
from risk_agent.collection.p2_d2_real_qwen_gate import (
    FIXED_TASK_IDS,
    P2D2RealQwenGateRunner,
    RESEARCH_FLAGS,
    select_p2_d2_tasks,
)
from risk_agent.monitoring import (
    AuditPersistenceError,
    BudgetState,
    ExecutionMonitor,
    JsonlTraceWriter,
    RuntimeState,
)
from scripts.run_p2_d2_real_qwen_gate import _load_gate_config


ROOT = Path(__file__).resolve().parents[1]
BASELINE_GATE_SHA256 = "56f1428791a76c8a5e8eed982251cc5d030472e55ebcbd8628d4a111ad5d89e5"
BASELINE_REMOTE_SHA256 = "1a347dcf96848d7ea4ea390987d0ebe4526b06f6143d70c5f379903feccc7436"
VERIFIED_T1_SHA256 = "c3ff6fca4d00402567de8f6f183a9ae0333399ab66af1d50fc0a50a69bde93b3"
VERIFIED_REMOTE_SHA256 = "4731cb56c128e3c150712745e25b9881fd8c19d64a37fd87a97f48ae3a5a57a5"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tasks():
    return generate_task_instances(
        load_task_templates(ROOT / "configs" / "task_templates_v2.yaml"), 2
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
    completion_tokens: int = 12,
) -> dict[str, Any]:
    prompt_tokens = 100
    return {
        "id": f"chatcmpl-{call_id}",
        "object": "chat.completion",
        "created": 1_800_000_200,
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


def _truncated_completion() -> dict[str, Any]:
    return {
        "id": "chatcmpl-truncated-1024",
        "object": "chat.completion",
        "created": 1_800_000_201,
        "model": "Qwen/Qwen3-8B",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": '<tool_call>{"name":"filter_records","arguments":',
                    "tool_calls": [],
                },
                "finish_reason": "length",
            }
        ],
        "usage": {
            "prompt_tokens": 500,
            "completion_tokens": 1024,
            "total_tokens": 1524,
        },
    }


def _reference_calls(tmp_path: Path) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    for task in select_p2_d2_tasks(_tasks()):
        environment = TaskEnvironment(tmp_path / "reference" / task.task_id, task)
        environment.reset()
        runtime = RuntimeState(
            seed=task.seed,
            budget=BudgetState(50_000, 12, 900.0),
        )
        monitor = ExecutionMonitor(
            writer=JsonlTraceWriter(tmp_path / "reference.jsonl"),
            runtime_state=runtime,
            experiment_id="synthetic_reference",
            run_id="synthetic_reference_run",
            task_id=task.task_id,
            template_id=task.template_id,
            instance_id=task.instance_id,
            model_id="Qwen/Qwen3-8B",
            model_version="synthetic",
            prompt_version="day2-v1",
            tool_version="p2-d2-local-tools-v1",
            worker_id="worker-local",
            environment_id=f"reference:{task.task_id}",
            additional_context={"formal_experiment": False},
        )
        tools = {
            tool.name: tool
            for tool in create_local_domain_tools(
                environment=environment,
                monitor=monitor,
            )
        }
        context: dict[str, Any] = {}
        for index, step in enumerate(task.reference_steps, 1):
            arguments = resolve_step_value(step.arguments, context)
            calls.append((step.tool, arguments))
            if index < 3:
                context[f"step_{index}"] = tools[step.tool].forward(**arguments)
    return calls


def _responses_for_all_tasks(tmp_path: Path) -> list[dict[str, Any]]:
    return [
        _completion(f"call-{index:03d}", tool_name, arguments)
        for index, (tool_name, arguments) in enumerate(
            _reference_calls(tmp_path), 1
        )
    ]


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[dict[str, Any] | tuple[int, dict[str, Any]]],
    requests: list[httpx2.Request],
) -> None:
    queue = list(responses)

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if not queue:
            raise AssertionError("Unexpected provider request")
        response = queue.pop(0)
        status, document = response if isinstance(response, tuple) else (200, response)
        return httpx2.Response(
            status,
            json=document,
            headers={"x-request-id": f"synthetic-{len(requests):03d}"},
        )

    monkeypatch.setattr(
        agent_factory,
        "_http_client",
        lambda *args, **kwargs: httpx2.Client(
            transport=httpx2.MockTransport(handler), trust_env=False
        ),
    )


def _runner(settings: RemoteModelSettings, run_directory: Path) -> P2D2RealQwenGateRunner:
    return P2D2RealQwenGateRunner(
        settings=settings,
        run_directory=run_directory,
        run_id=run_directory.name,
        catalog_hash="a" * 64,
        split_hash="b" * 64,
        verifier_content_hash="c" * 64,
        experiment_id=FULL_GATE_EXPERIMENT_ID_1024,
        experiment_config_version=FULL_GATE_EXPERIMENT_CONFIG_VERSION_1024,
        gate_mode=FULL_GATE_MODE_1024,
    )


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_full_gate_profile_is_hash_bound_and_isolated_from_historical_configs() -> None:
    profile = load_full_gate_1024_profile(
        ROOT / "configs" / "p2_d2_full_gate_1024.yaml",
        baseline_gate_config=ROOT / "configs" / "p2_d2_real_qwen_gate.yaml",
        baseline_remote_config=ROOT / "configs" / "remote_model.yaml",
        verified_t1_config=ROOT / "configs" / "p2_d2_t1_budget_1024.yaml",
        verified_remote_config=ROOT / "configs" / "remote_model_p2_d2_1024.yaml",
    )

    assert profile.experiment_id == FULL_GATE_EXPERIMENT_ID_1024
    assert profile.experiment_config_version == FULL_GATE_EXPERIMENT_CONFIG_VERSION_1024
    assert profile.generation_config_version == GENERATION_CONFIG_VERSION_1024
    assert profile.checkpoint_schema_version == CHECKPOINT_SCHEMA_VERSION
    assert profile.fixed_task_ids == FIXED_TASK_IDS
    assert profile.worker_schedule == FULL_GATE_WORKER_SCHEDULE
    assert profile.limits["max_completion_tokens"] == 1024
    assert profile.limits["http_automatic_retries"] == 0
    assert profile.research_flags == RESEARCH_FLAGS
    assert _sha256(ROOT / "configs" / "p2_d2_real_qwen_gate.yaml") == BASELINE_GATE_SHA256
    assert _sha256(ROOT / "configs" / "remote_model.yaml") == BASELINE_REMOTE_SHA256
    assert _sha256(ROOT / "configs" / "p2_d2_t1_budget_1024.yaml") == VERIFIED_T1_SHA256
    assert _sha256(ROOT / "configs" / "remote_model_p2_d2_1024.yaml") == VERIFIED_REMOTE_SHA256

    with pytest.raises(CompletionBudgetConfigurationError):
        load_full_gate_1024_profile(ROOT / "configs" / "p2_d2_t1_budget_1024.yaml")
    with pytest.raises(CompletionBudgetConfigurationError):
        load_completion_budget_profile(ROOT / "configs" / "p2_d2_full_gate_1024.yaml")
    with pytest.raises(RuntimeError, match="Frozen P2-D2 configuration mismatch"):
        _load_gate_config(ROOT / "configs" / "p2_d2_full_gate_1024.yaml")


def test_full_gate_rejects_unregistered_experiment_identity(tmp_path: Path) -> None:
    value = yaml.safe_load(
        (ROOT / "configs" / "p2_d2_full_gate_1024.yaml").read_text(encoding="utf-8")
    )
    value["experiment_id"] = "unregistered_full_gate"
    path = tmp_path / "invalid-identity.yaml"
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")

    with pytest.raises(CompletionBudgetConfigurationError, match="full Gate"):
        load_full_gate_1024_profile(path)


@pytest.mark.parametrize("token_limit", [256, 512, 2048])
def test_full_gate_rejects_unregistered_completion_caps(
    tmp_path: Path, token_limit: int
) -> None:
    value = yaml.safe_load(
        (ROOT / "configs" / "p2_d2_full_gate_1024.yaml").read_text(encoding="utf-8")
    )
    value["limits"]["max_completion_tokens"] = token_limit
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")

    with pytest.raises(CompletionBudgetConfigurationError, match="full Gate"):
        load_full_gate_1024_profile(path)


def test_cli_is_fixed_local_only_by_default_and_artifact_manifest_is_verifiable(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "runs"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_p2_d2_full_gate_1024.py",
            "--mode",
            "local-preflight",
            "--run-id",
            "full_gate_1024_local_preflight",
            "--output-root",
            str(output_root),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    denied_root = tmp_path / "denied"
    denied = subprocess.run(
        [
            sys.executable,
            "scripts/run_p2_d2_full_gate_1024.py",
            "--mode",
            FULL_GATE_MODE_1024,
            "--output-root",
            str(denied_root),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    arbitrary = subprocess.run(
        [
            sys.executable,
            "scripts/run_p2_d2_full_gate_1024.py",
            "--task-id",
            FIXED_TASK_IDS[1],
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    run_directory = output_root / "full_gate_1024_local_preflight"
    summary = json.loads((run_directory / "gate_summary.json").read_text())
    manifest = json.loads((run_directory / "artifact_manifest.json").read_text())
    assert summary["network_requests_made"] == 0
    assert summary["fixed_task_ids"] == list(FIXED_TASK_IDS)
    assert summary["worker_schedule"] == list(FULL_GATE_WORKER_SCHEDULE)
    for item in manifest["artifacts"]:
        assert _sha256(run_directory / item["path"]) == item["sha256"]
    manifest_hash = _sha256(run_directory / "artifact_manifest.json")
    duplicate = subprocess.run(
        [
            sys.executable,
            "scripts/run_p2_d2_full_gate_1024.py",
            "--mode",
            "local-preflight",
            "--run-id",
            "full_gate_1024_local_preflight",
            "--output-root",
            str(output_root),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert duplicate.returncode != 0
    assert _sha256(run_directory / "artifact_manifest.json") == manifest_hash
    assert denied.returncode != 0
    assert "--authorize-remote" in denied.stderr
    assert not denied_root.exists()
    assert arbitrary.returncode != 0
    assert "unrecognized arguments: --task-id" in arbitrary.stderr


def test_synthetic_five_task_gate_passes_with_fixed_workers_and_t3_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings_1024(tmp_path, monkeypatch)
    requests: list[httpx2.Request] = []
    _install_transport(monkeypatch, _responses_for_all_tasks(tmp_path), requests)
    run_directory = create_unique_run_directory(tmp_path / "runs", "synthetic_all_five")

    summary = _runner(settings, run_directory).run_fixed_gate(
        tasks=_tasks(), workers=settings.workers
    )

    assert summary["passed"] is True
    assert summary["task_ids"] == list(FIXED_TASK_IDS)
    assert summary["attempted_task_ids"] == list(FIXED_TASK_IDS)
    assert summary["worker_schedule"] == list(FULL_GATE_WORKER_SCHEDULE)
    assert summary["attempt_count"] == 5
    assert summary["completed_task_count"] == 5
    assert summary["checkpoints"] == 5
    assert summary["six_hash_matches"] == 5
    assert summary["exact_input_matches"] == 5
    assert summary["real_restore_requests"] == 5
    assert summary["automatic_model_retries"] == 0
    assert summary["replacement_tasks"] == 0
    assert summary["worker_failovers"] == 0
    assert len(summary["task_costs"]) == 5
    assert summary["gate_cost_totals"]["prompt_tokens"] == sum(
        item["prompt_tokens"] for item in summary["task_costs"]
    )
    assert summary["gate_cost_totals"]["completion_tokens"] == sum(
        item["completion_tokens"] for item in summary["task_costs"]
    )
    assert len(requests) == 15
    connection_events = _jsonl(run_directory / "connection_audit.jsonl")
    assert sum(r["event"] == "provider_request_started" for r in connection_events) == 15
    assert sum(r["event"] == "provider_request_finished" for r in connection_events) == 15
    assert all(r["process_state"] == "UNKNOWN" for r in connection_events)
    from scripts.run_p2_d2_real_qwen_gate import _artifact_manifest

    artifacts = _artifact_manifest(run_directory)["artifacts"]
    assert "connection_audit.jsonl" in {item["path"] for item in artifacts}
    assert all(_sha256(run_directory / item["path"]) == item["sha256"] for item in artifacts)
    for checkpoint in (run_directory / "checkpoints").rglob("*.json"):
        assert "connection_audit" not in checkpoint.read_text()
    assert [request.url.host for request in requests] == [
        host
        for host in (
            "localhost",
            "localhost",
            "localhost",
            "localhost",
            "localhost",
        )
        for _ in range(3)
    ]
    assert [request.url.port for request in requests] == [
        port
        for port in (8000, 8001, 8000, 8001, 8000)
        for _ in range(3)
    ]
    attempts = _jsonl(run_directory / "task_attempts.jsonl")
    assert all(item["source_model_calls"] == 2 for item in attempts)
    assert all(item["source_tool_calls"] == 2 for item in attempts)
    assert all(item["restore_model_calls"] == 3 for item in attempts)
    assert {item["tokens_before_restore_request"] for item in attempts} == {224}
    assert {item["tokens_after_restore_request"] for item in attempts} == {336}
    provider_records = _jsonl(
        run_directory / "audit" / "provider_response_audit.jsonl"
    )
    parsing_records = _jsonl(
        run_directory / "audit" / "parsing_outcome_audit.jsonl"
    )
    assert len(provider_records) == 15
    assert len(parsing_records) == 15
    assert all(item["delegated_completion_token_limit"] == 1024 for item in provider_records)
    assert all(item["usage_completeness"] == "complete" for item in provider_records)
    assert all(item["raw_response_sha256"] for item in provider_records)
    assert all(item["input_hash"] for item in provider_records)
    assert all(item["latency_seconds"] >= 0 for item in provider_records)
    assert all(item["parsing_status"] in {"success", "not_attempted"} for item in parsing_records)
    t3 = attempts[2]
    assert t3["worker_id"] == "worker-0"
    assert t3["api_write_audit"]["idempotency_key_count"] == 1
    assert t3["api_write_audit"]["update_order_history_count"] == 1
    order = next(
        item
        for item in t3["api_write_audit"]["orders"]
        if item["order_id"] == "order-83001"
    )
    assert order["status"] == "processing"
    assert order["version"] == 2
    assert t3["replay_guard_result"] == "blocked_before_execution"
    assert all(t3[key] == value for key, value in RESEARCH_FLAGS.items())


def test_t1_truncation_stops_gate_without_retry_or_t2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings_1024(tmp_path, monkeypatch)
    first_call = _reference_calls(tmp_path)[0]
    requests: list[httpx2.Request] = []
    _install_transport(
        monkeypatch,
        [_completion("t1-first", *first_call), _truncated_completion()],
        requests,
    )
    run_directory = create_unique_run_directory(tmp_path / "runs", "t1_truncated")

    summary = _runner(settings, run_directory).run_fixed_gate(
        tasks=_tasks(), workers=settings.workers
    )

    assert summary["passed"] is False
    assert summary["attempted_task_ids"] == [FIXED_TASK_IDS[0]]
    assert summary["completed_task_count"] == 0
    assert summary["attempt_count"] == 1
    assert summary["failure"]["failure_category"] == "model_behavior_failure"
    assert summary["automatic_model_retries"] == 0
    assert len(requests) == 2
    provider_records = _jsonl(run_directory / "audit" / "provider_response_audit.jsonl")
    assert provider_records[-1]["finish_reason"] == "length"
    assert provider_records[-1]["completion_tokens"] == 1024
    assert not (run_directory / "source_environments" / f"{run_directory.name}_{FIXED_TASK_IDS[1]}_attempt_002").exists()


def test_t3_failure_preserves_t1_t2_and_never_starts_t4_t5(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings_1024(tmp_path, monkeypatch)
    calls = _reference_calls(tmp_path)
    responses = [
        _completion(f"before-t3-{index:03d}", tool, arguments)
        for index, (tool, arguments) in enumerate(calls[:7], 1)
    ]
    responses.append(_truncated_completion())
    requests: list[httpx2.Request] = []
    _install_transport(monkeypatch, responses, requests)
    run_directory = create_unique_run_directory(tmp_path / "runs", "t3_failure")

    summary = _runner(settings, run_directory).run_fixed_gate(
        tasks=_tasks(), workers=settings.workers
    )

    assert summary["passed"] is False
    assert summary["completed_task_count"] == 2
    assert summary["attempted_task_ids"] == list(FIXED_TASK_IDS[:3])
    assert summary["failure"]["task_id"] == FIXED_TASK_IDS[2]
    assert summary["failure"]["worker_id"] == "worker-0"
    assert summary["replacement_tasks"] == 0
    assert len(requests) == 8
    checkpoint_ids = {path.name for path in (run_directory / "checkpoints").iterdir()}
    assert checkpoint_ids == {
        f"checkpoint_{FIXED_TASK_IDS[0]}_001",
        f"checkpoint_{FIXED_TASK_IDS[1]}_002",
    }
    for task_id in FIXED_TASK_IDS[3:]:
        assert task_id not in "\n".join(
            path.as_posix() for path in run_directory.rglob("*")
        )


def test_worker_failure_has_no_failover_and_audit_persistence_failure_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings_1024(tmp_path, monkeypatch)
    calls = _reference_calls(tmp_path)
    requests: list[httpx2.Request] = []
    responses: list[dict[str, Any] | tuple[int, dict[str, Any]]] = [
        _completion(f"t1-{index}", tool, arguments)
        for index, (tool, arguments) in enumerate(calls[:3], 1)
    ]
    responses.append((503, {"error": {"message": "worker-1 unavailable"}}))
    _install_transport(monkeypatch, responses, requests)
    run_directory = create_unique_run_directory(tmp_path / "runs", "worker_failure")

    summary = _runner(settings, run_directory).run_fixed_gate(
        tasks=_tasks(), workers=settings.workers
    )

    assert summary["completed_task_count"] == 1
    assert summary["attempted_task_ids"] == list(FIXED_TASK_IDS[:2])
    assert summary["failure"]["worker_id"] == "worker-1"
    assert summary["worker_failovers"] == 0
    assert len(requests) == 4
    assert summary["failure"]["failure_category"] == "infrastructure_failure"
    assert requests[-1].url.port == 8001

    audit_requests: list[httpx2.Request] = []
    _install_transport(
        monkeypatch,
        [_completion("audit-failure", *calls[0])],
        audit_requests,
    )
    audit_directory = create_unique_run_directory(tmp_path / "runs", "audit_failure")
    runner = _runner(settings, audit_directory)

    def fail_audit(*args: Any, **kwargs: Any) -> None:
        raise AuditPersistenceError("synthetic durable write failure")

    monkeypatch.setattr(runner.provider_audit, "record_response", fail_audit)
    audit_summary = runner.run_fixed_gate(tasks=_tasks(), workers=settings.workers)
    assert audit_summary["completed_task_count"] == 0
    assert audit_summary["failure"]["failure_category"] == "audit_persistence_failure"
    assert audit_summary["attempted_task_ids"] == [FIXED_TASK_IDS[0]]
    assert len(audit_requests) == 1
    assert not (audit_directory / "tool_trajectories.jsonl").exists()


@pytest.mark.parametrize("failure_kind", ["read_timeout", "tls", "connection", "http"])
def test_t2_restore_transport_failure_preserves_checkpoint_and_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_kind: str
) -> None:
    import ssl

    settings = _settings_1024(tmp_path, monkeypatch)
    responses = _responses_for_all_tasks(tmp_path)[:5]
    requests = []

    def handler(request):
        requests.append(request)
        if responses:
            return httpx2.Response(200, json=responses.pop(0))
        if failure_kind == "http":
            return httpx2.Response(503, json={"error": {"message": "synthetic unavailable"}})
        if failure_kind == "read_timeout":
            raise httpx2.ReadTimeout("synthetic read timeout", request=request)
        if failure_kind == "tls":
            raise httpx2.ConnectError("certificate verify failed", request=request) from ssl.SSLCertVerificationError("synthetic certificate")
        raise httpx2.ConnectError("synthetic connection closed", request=request)

    monkeypatch.setattr(agent_factory, "_http_client", lambda *a, **k: httpx2.Client(
        transport=httpx2.MockTransport(handler), trust_env=False))
    run_directory = create_unique_run_directory(tmp_path / "runs", f"restore_{failure_kind}")
    summary = _runner(settings, run_directory).run_fixed_gate(tasks=_tasks(), workers=settings.workers)
    assert summary['status'] == 'BLOCKED'
    assert summary['completed_task_count'] == 1
    assert summary['attempted_task_ids'] == list(FIXED_TASK_IDS[:2])
    assert summary['failure']['failure_category'] == 'infrastructure_failure'
    assert len(requests) == 6
    assert requests[-1].url.port == 8001
    assert len(list((run_directory / 'checkpoints').glob('*/manifest.json'))) == 2
    provider = _jsonl(run_directory / 'audit/provider_response_audit.jsonl')[-1]
    assert provider['prompt_tokens'] is None
    assert provider['completion_tokens'] is None
    events = _jsonl(run_directory / 'connection_audit.jsonl')
    diagnosis = events[-1]['diagnosis']
    assert diagnosis['connection_diagnostic'] == {
        'read_timeout': 'provider_read_timeout', 'tls': 'tls_connection_failure',
        'connection': 'provider_connection_failure', 'http': 'provider_http_error',
    }[failure_kind]
    assert diagnosis['root_cause_confidence'] == 'UNDETERMINED'
    assert summary['automatic_model_retries'] == summary['worker_failovers'] == 0
