"""Normalized, read-only Recovery-Bench artifact records."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class NormalizedToolCall:
    step_id: str
    timestamp: str | None
    model_name: str | None
    tool_call_id: str
    function_name: str
    argument_keys: tuple[str, ...]
    arguments_sha256: str
    observation_count: int
    observation_sha256: tuple[str, ...]
    result_association: str
    step_prompt_tokens: int
    step_completion_tokens: int
    step_cost_usd: float
    arguments: dict[str, Any] | None = None
    observation_contents: tuple[str, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NormalizedObservation:
    step_id: str
    timestamp: str | None
    source_call_id: str | None
    association: str
    content_sha256: str
    content: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RecoveryBenchTrial:
    schema_version: int
    source_run: str
    source_trial_directory: str
    result_sha256: str
    config_sha256: str
    trajectory_sha256: str
    trial_id: str
    task_name: str
    trial_name: str
    source: str
    task_checksum: str
    task_git_url: str | None
    task_git_commit_id: str | None
    task_path: str | None
    atif_schema_version: str
    agent_name: str
    agent_version: str
    model_name: str
    environment_type: str
    started_at: str
    finished_at: str
    duration_seconds: float
    status: str
    success: bool
    verifier_reward: float | None
    exception_type: str | None
    input_tokens: int
    cache_tokens: int
    output_tokens: int
    cost_usd: float
    step_count: int
    tool_call_count: int
    observation_result_count: int
    unmatched_observation_result_count: int
    config_matches_result: bool
    verifier_reward_file_matches: bool | None
    checkpoint_restore_supported: bool
    branch_utility_eligible: bool
    branch_ineligibility_reason: str
    tool_calls: tuple[NormalizedToolCall, ...]
    observations: tuple[NormalizedObservation, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
