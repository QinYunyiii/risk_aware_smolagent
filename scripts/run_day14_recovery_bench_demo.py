#!/usr/bin/env python3
"""Validate a small Recovery-Bench sample through the read-only adapter."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from risk_agent.benchmark.schema import validate_identifier
from risk_agent.recovery_bench import RecoveryBenchAdapter


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: Iterable[dict[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as destination:
        for value in values:
            destination.write(json.dumps(value, ensure_ascii=False) + "\n")


def _git_head(repository: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "--short", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _harbor_version(executable: Path) -> str:
    completed = subprocess.run(
        [str(executable), "--version"], check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/day14_recovery_bench.yaml")
    parser.add_argument("--output-root", default="runs/day14")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()

    started_at = datetime.now(timezone.utc)
    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("Unsupported Day 14 configuration")
    if bool(config.get("include_raw_content")):
        raise ValueError("Day 14 formal demo must not copy raw historical content")
    if bool(config.get("checkpoint_restore_supported")):
        raise ValueError("Recovery-Bench Docker restore support cannot be claimed")

    run_id = validate_identifier(
        args.run_id
        or f"day14_{started_at.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}",
        "run_id",
    )
    output_directory = Path(args.output_root) / run_id
    output_directory.mkdir(parents=True, exist_ok=False)

    repository = Path(str(config["recovery_bench_root"])).resolve(strict=True)
    harbor_executable = Path(str(config["harbor_executable"])).resolve(strict=True)
    git_head = _git_head(repository)
    harbor_version = _harbor_version(harbor_executable)
    adapter = RecoveryBenchAdapter(
        config["trials_root"], supported_atif_schemas=config["supported_atif_schemas"]
    )
    selections = config.get("selected_trials")
    if not isinstance(selections, list) or not selections:
        raise ValueError("At least one Recovery-Bench trial must be selected")

    source_files: list[Path] = []
    for selection in selections:
        if not isinstance(selection, dict):
            raise ValueError("Selected trial entries must be mappings")
        trial_directory = adapter.trial_directory(str(selection["trial_directory"]))
        source_files.extend(
            (
                trial_directory / "result.json",
                trial_directory / "config.json",
                trial_directory / "agent" / "trajectory.json",
                trial_directory / "verifier" / "reward.txt",
            )
        )
    before = {str(path): _sha256(path) for path in source_files}

    trials = []
    expected_match = []
    for selection in selections:
        trial = adapter.load_trial(
            str(selection["trial_directory"]),
            expected_task_checksum=str(selection["task_checksum"]),
            include_content=False,
        )
        trials.append(trial)
        expected_match.append(
            trial.task_name == selection["task_name"]
            and trial.status == selection["expected_status"]
        )
    after = {str(path): _sha256(path) for path in source_files}

    trial_records = [trial.to_dict() for trial in trials]
    tool_records = [
        {"trial_name": trial.trial_name, **tool_call.to_dict()}
        for trial in trials
        for tool_call in trial.tool_calls
    ]
    observation_records = [
        {"trial_name": trial.trial_name, **observation.to_dict()}
        for trial in trials
        for observation in trial.observations
    ]
    source_manifest = {
        "schema_version": 1,
        "source_repository": str(repository),
        "source_git_head": git_head,
        "source_artifacts": [
            {"path": path, "sha256_before": before[path], "sha256_after": after[path]}
            for path in sorted(before)
        ],
    }
    _write_jsonl(output_directory / "selected_trials.jsonl", trial_records)
    _write_jsonl(output_directory / "normalized_tool_calls.jsonl", tool_records)
    _write_jsonl(output_directory / "normalized_observations.jsonl", observation_records)
    _write_json(output_directory / "source_artifact_manifest.json", source_manifest)

    checks = {
        "recovery_bench_git_head_matches": git_head == str(config["recovery_bench_git_head"]),
        "harbor_version_matches": harbor_version.endswith(str(config["harbor_version"])),
        "selected_trial_count_matches": len(trials) == len(selections),
        "selected_trials_unique": len({trial.trial_name for trial in trials}) == len(trials),
        "selection_identity_and_status_match": all(expected_match),
        "supported_atif_schema": all(
            trial.atif_schema_version in config["supported_atif_schemas"] for trial in trials
        ),
        "config_result_consistent": all(trial.config_matches_result for trial in trials),
        "verifier_reward_files_consistent": all(
            trial.verifier_reward_file_matches is True for trial in trials
        ),
        "tool_calls_available": all(trial.tool_call_count > 0 for trial in trials),
        "observations_available": all(trial.observation_result_count > 0 for trial in trials),
        "raw_arguments_redacted": all(
            call.arguments is None for trial in trials for call in trial.tool_calls
        ),
        "raw_observations_redacted": all(
            observation.content is None
            for trial in trials
            for observation in trial.observations
        ),
        "source_artifacts_unchanged": before == after,
        "checkpoint_restore_not_claimed": all(
            not trial.checkpoint_restore_supported for trial in trials
        ),
        "branch_utility_not_claimed": all(not trial.branch_utility_eligible for trial in trials),
        "status_coverage": {trial.status for trial in trials}
        == {"success", "verifier_failure", "agent_error"},
    }
    summary = {
        "schema_version": 1,
        "experiment_id": config["experiment_id"],
        "run_id": run_id,
        "created_at": started_at.isoformat(),
        "mode": "read_only_existing_artifacts",
        "source_repository": str(repository),
        "source_git_head": git_head,
        "harbor_version": harbor_version,
        "atif_schemas": sorted({trial.atif_schema_version for trial in trials}),
        "selected_trial_count": len(trials),
        "status_counts": dict(sorted(Counter(trial.status for trial in trials).items())),
        "success_count": sum(trial.success for trial in trials),
        "step_count": sum(trial.step_count for trial in trials),
        "tool_call_count": sum(trial.tool_call_count for trial in trials),
        "observation_result_count": sum(
            trial.observation_result_count for trial in trials
        ),
        "unmatched_observation_result_count": sum(
            trial.unmatched_observation_result_count for trial in trials
        ),
        "input_tokens": sum(trial.input_tokens for trial in trials),
        "cache_tokens": sum(trial.cache_tokens for trial in trials),
        "output_tokens": sum(trial.output_tokens for trial in trials),
        "cost_usd": sum(trial.cost_usd for trial in trials),
        "checkpoint_restore_supported": False,
        "branch_utility_experiment_executed": False,
        "branch_utility_block_reason": trials[0].branch_ineligibility_reason,
        "raw_historical_content_copied": False,
        "checks": checks,
        "passed": all(checks.values()),
        "artifacts": {
            "selected_trials": str((output_directory / "selected_trials.jsonl").resolve()),
            "tool_calls": str(
                (output_directory / "normalized_tool_calls.jsonl").resolve()
            ),
            "observations": str(
                (output_directory / "normalized_observations.jsonl").resolve()
            ),
            "source_manifest": str(
                (output_directory / "source_artifact_manifest.json").resolve()
            ),
        },
    }
    _write_json(output_directory / "recovery_bench_demo_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
