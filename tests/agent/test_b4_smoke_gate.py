from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx2
import pytest

from risk_agent.agent import agent_factory
from risk_agent.agent.worker_router import RemoteModelSettings, WorkerConfig
from risk_agent.benchmark import generate_task_instances, load_task_templates
from risk_agent.benchmark.schema import DataSplit, Domain, TaskSpec, ToolStep
from risk_agent.collection.b4_smoke_gate import (
    B4_RESEARCH_FLAGS,
    _audit_from_source,
    load_b4_smoke_plan,
    validate_b4_plan,
)
from risk_agent.collection.continue_gate import ContinueGateRunner
from risk_agent.collection.day5_gate import Day5GateRunner, create_unique_run_directory
from risk_agent.faults import (
    FaultConfig,
    FaultInjector,
    FaultType,
    InjectedFaultError,
    describe_operation,
)


ROOT = Path(__file__).parents[2]


def _settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[RemoteModelSettings, WorkerConfig]:
    ca = tmp_path / "unused-ca.pem"
    ca.write_text("mock transport", encoding="utf-8")
    monkeypatch.setenv("B4_TEST_API_KEY", "fixture-key")
    monkeypatch.setenv("B4_TEST_CA", str(ca))
    worker = WorkerConfig("worker-test-0", "https://worker.test/v1")
    return (
        RemoteModelSettings(
            model_id="Qwen/Qwen3-8B",
            model_version="fixture-qwen-snapshot",
            prompt_version="fixture-prompt-v1",
            api_key_env="B4_TEST_API_KEY",
            ca_bundle_env="B4_TEST_CA",
            request_timeout_seconds=30.0,
            connect_timeout_seconds=5.0,
            router_strategy="round_robin",
            inference={
                "seed": 20261118,
                "temperature": 0.0,
                "max_tokens": 256,
                "chat_template_kwargs": {"enable_thinking": False},
            },
            workers=(worker, WorkerConfig("worker-test-1", "https://worker-1.test/v1")),
        ),
        worker,
    )


def _task() -> TaskSpec:
    return TaskSpec(
        task_id="b4_fault_continue_task",
        template_id="b4_fault_continue_template",
        instance_id="b4_fault_continue_001",
        domain=Domain.CALCULATION_LOGIC,
        seed=20261123,
        prompt="Use the calculator for 1 + 1, recover if needed, then return only 2.",
        files=(),
        inventory=(),
        orders=(),
        reference_steps=(ToolStep("calculator", {"expression": "1 + 1"}),),
        expected={"result": "2"},
        dataset_role="day7_catalog",
        split=DataSplit.TRAIN,
    )


def _tool_response(call_id: str) -> dict[str, Any]:
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
                                "arguments": json.dumps({"expression": "1 + 1"}),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
    }


def _final_response(call_id: str) -> dict[str, Any]:
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
                                "arguments": json.dumps({"answer": "2"}),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 18, "completion_tokens": 3, "total_tokens": 21},
    }


def test_b4_plan_covers_four_domains_and_six_fault_types() -> None:
    plan = load_b4_smoke_plan(ROOT / "configs/b4_smoke.yaml")
    tasks = generate_task_instances(load_task_templates(ROOT / "configs/task_templates.yaml"))
    audit = validate_b4_plan(plan, tasks)

    assert audit["passed"] is True
    assert audit["task_count"] == 10
    assert len(audit["domains"]) == 4
    assert len(audit["fault_types"]) == 6
    assert all(row["split"] == "train" for row in audit["tasks"])
    assert all(row["formal_dataset_excluded"] for row in audit["tasks"])
    assert B4_RESEARCH_FLAGS["formal_experiment"] is False
    assert B4_RESEARCH_FLAGS["research_dataset_eligible"] is False


def test_fault_injector_checkpoint_state_round_trip_is_independent() -> None:
    config = FaultConfig(
        fault_id="b4_round_trip",
        fault_type=FaultType.F3_INVALID_ARGUMENT,
        seed=44001,
        target_tool="calculator",
        call_indices=(1,),
        probability=0.0,
        parameters={},
    )
    source = FaultInjector(config)
    operation = describe_operation("calculator", {"expression": "1 + 1"})
    with pytest.raises(InjectedFaultError):
        source.execute(operation, {"expression": "1 + 1"}, lambda: "2")
    state = source.checkpoint_state()
    branch_a = FaultInjector(config)
    branch_b = FaultInjector(config)
    branch_a.restore_checkpoint_state(state)
    branch_b.restore_checkpoint_state(state)

    assert branch_a.checkpoint_state() == branch_b.checkpoint_state() == state
    assert branch_a.execute(operation, {"expression": "1 + 1"}, lambda: "2") == "2"
    assert branch_a.checkpoint_state() != branch_b.checkpoint_state()


def test_faulted_checkpoint_to_full_continue_uses_exact_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, worker = _settings(tmp_path, monkeypatch)
    responses = [
        _tool_response("call-source-fault"),
        _final_response("call-b2-controlled"),
        _final_response("call-b4-continue"),
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
    config = FaultConfig(
        fault_id="b4_invalid_argument",
        fault_type=FaultType.F3_INVALID_ARGUMENT,
        seed=44002,
        target_tool="calculator",
        call_indices=(1,),
        probability=0.0,
        parameters={},
    )
    run = create_unique_run_directory(tmp_path / "runs", "b4_fault_run")
    source_runner = Day5GateRunner(
        settings=settings,
        run_directory=run,
        run_id="b4_fault_run",
        catalog_hash="a" * 64,
        split_hash="b" * 64,
        verifier_content_hash=hashlib.sha256(
            Path("src/risk_agent/benchmark/verifier.py").read_bytes()
        ).hexdigest(),
        experiment_id="b4_model_backed_smoke_gate",
        research_flags=B4_RESEARCH_FLAGS,
    )
    source = source_runner.run_task(
        task=_task(), worker=worker, attempt_index=1, fault_config=config
    )
    audit = _audit_from_source(source)
    continue_runner = ContinueGateRunner(
        settings=settings,
        run_directory=run,
        checkpoint_run=run,
        run_id="b4_fault_run",
        tasks=(_task(),),
        fault_injector_factory=lambda record, path: FaultInjector(config),
        experiment_id="b4_model_backed_smoke_gate",
    )
    result = continue_runner.run_checkpoint(
        audit=audit,
        worker=worker,
        branch_attempt_index=1,
    )

    assert source["fault_triggered"] is True
    assert source["fault_events"][-1]["error_type"] == "InjectedInvalidArgumentError"
    assert result["complete_chain"] is True
    assert result["first_input_exact"] is True
    assert result["verifier_passed"] is True
    assert result["y_continue"] == 1
    assert result["tool_replay_detected"] is False
    assert result["checkpoint_immutable"] is True
    assert len(requests) == 3
    assert not responses
    fault_state = json.loads(
        (Path(source["checkpoint_path"]) / "runtime/fault_adapter_state.json").read_text()
    )
    assert fault_state["status"] == "stateful"
    assert fault_state["state"]["call_counts"]["calculator"] == 1


def test_b4_remote_mode_refuses_output_without_stage_b_authorization(tmp_path: Path) -> None:
    output = tmp_path / "must_not_exist"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_b4_smoke_gate.py",
            "--mode",
            "smoke-gate",
            "--single-chain",
            "--run-id",
            "b4_remote_denied",
            "--output-root",
            str(output),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "explicit Stage B approval" in result.stderr
    assert not output.exists()
