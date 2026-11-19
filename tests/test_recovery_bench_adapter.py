from __future__ import annotations

import json
from pathlib import Path

import pytest

from risk_agent.recovery_bench import RecoveryBenchAdapter, RecoveryBenchArtifactError


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _trial(
    root: Path,
    *,
    name: str = "trial-1",
    schema: str = "ATIF-v1.6",
    reward: float = 1.0,
    exception_type: str | None = None,
    calls: list[dict] | None = None,
    results: list[dict] | None = None,
) -> Path:
    directory = root / name
    config = {
        "environment": {"type": "docker"},
        "agent": {"name": "fixture"},
    }
    exception = (
        {"exception_type": exception_type, "exception_message": "fixture"}
        if exception_type
        else None
    )
    result = {
        "id": "result-id",
        "task_name": "fixture-task",
        "trial_name": name,
        "source": "fixture",
        "task_checksum": "a" * 64,
        "task_id": {
            "git_url": "https://example.invalid/tasks.git",
            "git_commit_id": "abc123",
            "path": "fixture-task",
        },
        "config": config,
        "agent_info": {
            "name": "fixture-agent",
            "version": "1.0",
            "model_info": {"name": "fixture-model"},
        },
        "agent_result": {
            "n_input_tokens": 10,
            "n_cache_tokens": 3,
            "n_output_tokens": 4,
            "cost_usd": 0.01,
        },
        "verifier_result": {"rewards": {"reward": reward}},
        "exception_info": exception,
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:00:02Z",
    }
    _write_json(directory / "config.json", config)
    _write_json(directory / "result.json", result)
    _write_json(
        directory / "agent" / "trajectory.json",
        {
            "schema_version": schema,
            "session_id": "session",
            "agent": {},
            "steps": [
                {
                    "step_id": "step-1",
                    "timestamp": "2026-01-01T00:00:01Z",
                    "model_name": "fixture-model",
                    "tool_calls": calls
                    if calls is not None
                    else [
                        {
                            "tool_call_id": "call-1",
                            "function_name": "shell",
                            "arguments": {"command": "true"},
                        }
                    ],
                    "observation": {
                        "results": results
                        if results is not None
                        else [{"content": "ok"}]
                    },
                    "metrics": {
                        "prompt_tokens": 10,
                        "completion_tokens": 4,
                        "cost_usd": 0.01,
                    },
                }
            ],
            "final_metrics": {},
        },
    )
    reward_path = directory / "verifier" / "reward.txt"
    reward_path.parent.mkdir(parents=True, exist_ok=True)
    reward_path.write_text(str(reward), encoding="utf-8")
    return directory


def test_adapter_loads_redacted_read_only_trial(tmp_path: Path) -> None:
    _trial(tmp_path)
    adapter = RecoveryBenchAdapter(tmp_path)
    trial = adapter.load_trial("trial-1", expected_task_checksum="a" * 64)
    assert adapter.list_trial_names() == ("trial-1",)
    assert trial.status == "success"
    assert trial.success
    assert trial.duration_seconds == 2.0
    assert trial.tool_call_count == 1
    assert trial.observation_result_count == 1
    assert trial.unmatched_observation_result_count == 0
    assert trial.tool_calls[0].result_association == "positional"
    assert trial.tool_calls[0].arguments is None
    assert trial.observations[0].content is None
    assert trial.config_matches_result
    assert trial.verifier_reward_file_matches
    assert not trial.checkpoint_restore_supported
    assert not trial.branch_utility_eligible


def test_adapter_preserves_ambiguous_observation_without_false_link(tmp_path: Path) -> None:
    calls = [
        {"tool_call_id": "a", "function_name": "shell", "arguments": {}},
        {"tool_call_id": "b", "function_name": "shell", "arguments": {}},
    ]
    _trial(tmp_path, calls=calls, results=[{"content": "combined"}])
    trial = RecoveryBenchAdapter(tmp_path).load_trial("trial-1", include_content=True)
    assert trial.unmatched_observation_result_count == 1
    assert {call.result_association for call in trial.tool_calls} == {
        "step_level_unlinked"
    }
    assert trial.observations[0].association == "step_level_unlinked"
    assert trial.observations[0].content == "combined"
    assert trial.tool_calls[0].arguments == {}


def test_adapter_classifies_agent_error_before_reward(tmp_path: Path) -> None:
    _trial(tmp_path, reward=0.0, exception_type="AgentTimeoutError")
    trial = RecoveryBenchAdapter(tmp_path).load_trial("trial-1")
    assert trial.status == "agent_error"
    assert trial.exception_type == "AgentTimeoutError"
    assert not trial.success


def test_adapter_rejects_unsupported_schema_checksum_and_path_escape(
    tmp_path: Path,
) -> None:
    _trial(tmp_path, schema="ATIF-v9")
    adapter = RecoveryBenchAdapter(tmp_path)
    with pytest.raises(RecoveryBenchArtifactError, match="Unsupported ATIF"):
        adapter.load_trial("trial-1")
    with pytest.raises(RecoveryBenchArtifactError, match="one path component"):
        adapter.load_trial("../trial-1")

    compatible = tmp_path / "compatible"
    compatible.mkdir()
    _trial(compatible)
    with pytest.raises(RecoveryBenchArtifactError, match="checksum"):
        RecoveryBenchAdapter(compatible).load_trial(
            "trial-1", expected_task_checksum="b" * 64
        )


def test_adapter_rejects_config_result_divergence(tmp_path: Path) -> None:
    directory = _trial(tmp_path)
    _write_json(directory / "config.json", {"environment": {"type": "docker"}})
    with pytest.raises(RecoveryBenchArtifactError, match="differs"):
        RecoveryBenchAdapter(tmp_path).load_trial("trial-1")
