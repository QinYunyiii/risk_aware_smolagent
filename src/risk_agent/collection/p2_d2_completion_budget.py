"""Strict identities for the versioned P2-D2 1024-token T1 diagnostic."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from risk_agent.agent.worker_router import RemoteModelSettings


EXPERIMENT_ID_1024 = "p2_d2_t1_completion_1024_diagnostic"
EXPERIMENT_CONFIG_VERSION_1024 = "p2-d2-completion-budget-1024-v1"
GENERATION_CONFIG_VERSION_1024 = "qwen3-8b-completion-1024-v1"
DIAGNOSTIC_MODE_1024 = "single-t1-budget-1024-diagnostic"
T1_TASK_ID = "p2_data_region_units_sum_001"
BASELINE_EXPERIMENT_ID = "p2_d2_real_qwen_gate"
BASELINE_COMPLETION_TOKENS = 256
DIAGNOSTIC_COMPLETION_TOKENS = 1024

EXPECTED_LIMITS_1024: dict[str, int] = {
    "max_agent_steps": 10,
    "max_model_calls": 10,
    "max_tool_calls": 12,
    "max_total_tokens": 50_000,
    "max_completion_tokens": DIAGNOSTIC_COMPLETION_TOKENS,
    "max_context_tokens": 8_192,
    "request_timeout_seconds": 120,
    "task_wall_clock_timeout_seconds": 900,
}

EXPECTED_RESEARCH_FLAGS: dict[str, Any] = {
    "formal_experiment": False,
    "execution_mode": "model_backed_smoke",
    "controller_type": "qwen3_model",
    "research_dataset_eligible": False,
    "y_continue": None,
    "y_retry": None,
    "y_replan": None,
}


class CompletionBudgetConfigurationError(ValueError):
    """The requested configuration is not a registered completion-budget profile."""


@dataclass(frozen=True, slots=True)
class CompletionBudgetProfile:
    protocol_id: str
    experiment_id: str
    experiment_config_version: str
    generation_config_version: str
    catalog_path: str
    canonical_catalog_hash: str
    fixed_task_id: str
    limits: Mapping[str, int]
    research_flags: Mapping[str, Any]
    baseline_gate_config_sha256: str
    baseline_remote_model_sha256: str


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_yaml(path: str | Path) -> dict[str, Any]:
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CompletionBudgetConfigurationError(
            f"Completion-budget configuration must be an object: {path}"
        )
    return value


def load_completion_budget_profile(
    path: str | Path,
    *,
    baseline_gate_config: str | Path = "configs/p2_d2_real_qwen_gate.yaml",
    baseline_remote_config: str | Path = "configs/remote_model.yaml",
) -> CompletionBudgetProfile:
    """Load only the registered 1024-token profile and bind it to the 256 baseline."""

    value = _read_yaml(path)
    checks = {
        "protocol_id": value.get("protocol_id") == "risk-continue-v2.0.0",
        "experiment_id": value.get("experiment_id") == EXPERIMENT_ID_1024,
        "experiment_config_version": value.get("experiment_config_version")
        == EXPERIMENT_CONFIG_VERSION_1024,
        "generation_config_version": value.get("generation_config_version")
        == GENERATION_CONFIG_VERSION_1024,
        "baseline_experiment_id": value.get("baseline_experiment_id")
        == BASELINE_EXPERIMENT_ID,
        "baseline_gate_hash": value.get("baseline_gate_config_sha256")
        == sha256_file(baseline_gate_config),
        "baseline_remote_hash": value.get("baseline_remote_model_sha256")
        == sha256_file(baseline_remote_config),
        "catalog_path": value.get("catalog_path") == "configs/task_templates_v2.yaml",
        "catalog_hash": value.get("canonical_catalog_hash")
        == "c37f9bb3dee6c8493e8a174aa977595769caf6b7cff8e1601bb60ccdc5b9d64c",
        "fixed_t1": value.get("fixed_task_ids") == [T1_TASK_ID],
        "faults_disabled": value.get("fault_injection_enabled") is False,
        "changed_factor": value.get("changed_factor")
        == {
            "name": "max_completion_tokens",
            "baseline": BASELINE_COMPLETION_TOKENS,
            "diagnostic": DIAGNOSTIC_COMPLETION_TOKENS,
        },
        "limits": value.get("limits") == EXPECTED_LIMITS_1024,
        "research_flags": value.get("research_flags") == EXPECTED_RESEARCH_FLAGS,
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        raise CompletionBudgetConfigurationError(
            f"Unregistered P2-D2 1024-token profile fields: {failed}"
        )
    return CompletionBudgetProfile(
        protocol_id=str(value["protocol_id"]),
        experiment_id=str(value["experiment_id"]),
        experiment_config_version=str(value["experiment_config_version"]),
        generation_config_version=str(value["generation_config_version"]),
        catalog_path=str(value["catalog_path"]),
        canonical_catalog_hash=str(value["canonical_catalog_hash"]),
        fixed_task_id=str(value["fixed_task_ids"][0]),
        limits=dict(value["limits"]),
        research_flags=dict(value["research_flags"]),
        baseline_gate_config_sha256=str(value["baseline_gate_config_sha256"]),
        baseline_remote_model_sha256=str(value["baseline_remote_model_sha256"]),
    )


def validate_completion_budget_settings(
    settings: RemoteModelSettings,
    *,
    candidate_config: str | Path,
    baseline_config: str | Path = "configs/remote_model.yaml",
) -> None:
    """Prove the registered model config differs only by version metadata and cap."""

    candidate = _read_yaml(candidate_config)
    baseline = _read_yaml(baseline_config)
    version = candidate.pop("generation_config_version", None)
    inference = candidate.get("inference")
    if not isinstance(inference, dict):
        raise CompletionBudgetConfigurationError("Candidate inference config is missing")
    if version != GENERATION_CONFIG_VERSION_1024:
        raise CompletionBudgetConfigurationError(
            "Candidate generation_config_version is not registered"
        )
    if inference.get("max_tokens") != DIAGNOSTIC_COMPLETION_TOKENS:
        raise CompletionBudgetConfigurationError(
            "Registered 1024 profile requires max_tokens=1024"
        )
    baseline_inference = baseline.get("inference")
    if not isinstance(baseline_inference, dict) or baseline_inference.get("max_tokens") != (
        BASELINE_COMPLETION_TOKENS
    ):
        raise CompletionBudgetConfigurationError(
            "Frozen baseline no longer has max_tokens=256"
        )
    candidate["inference"] = dict(inference)
    candidate["inference"]["max_tokens"] = BASELINE_COMPLETION_TOKENS
    if candidate != baseline:
        raise CompletionBudgetConfigurationError(
            "1024 model config changes fields other than version and max_tokens"
        )
    if settings.inference != {
        "seed": 20261118,
        "temperature": 0.0,
        "max_tokens": DIAGNOSTIC_COMPLETION_TOKENS,
        "chat_template_kwargs": {"enable_thinking": False},
    }:
        raise CompletionBudgetConfigurationError(
            "Loaded model settings do not match the registered 1024 profile"
        )


__all__ = [
    "BASELINE_COMPLETION_TOKENS",
    "CompletionBudgetConfigurationError",
    "CompletionBudgetProfile",
    "DIAGNOSTIC_COMPLETION_TOKENS",
    "DIAGNOSTIC_MODE_1024",
    "EXPERIMENT_CONFIG_VERSION_1024",
    "EXPERIMENT_ID_1024",
    "GENERATION_CONFIG_VERSION_1024",
    "T1_TASK_ID",
    "load_completion_budget_profile",
    "sha256_file",
    "validate_completion_budget_settings",
]
