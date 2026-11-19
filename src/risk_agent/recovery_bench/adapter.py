"""Read Harbor 0.4.0 / ATIF artifacts without modifying Recovery-Bench."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from .schema import NormalizedObservation, NormalizedToolCall, RecoveryBenchTrial


class RecoveryBenchArtifactError(ValueError):
    """Raised when a source artifact is absent, unsafe, or schema-incompatible."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha256(encoded)


def _read_json(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise RecoveryBenchArtifactError(f"Cannot read artifact: {path}") from error
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RecoveryBenchArtifactError(f"Invalid JSON artifact: {path}") from error
    if not isinstance(value, dict):
        raise RecoveryBenchArtifactError(f"Expected JSON object: {path}")
    return value, _sha256(raw)


def _require_string(value: Mapping[str, Any], key: str, source: Path) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise RecoveryBenchArtifactError(f"Missing string {key!r} in {source}")
    return item


def _duration_seconds(started_at: str, finished_at: str) -> float:
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        finish = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise RecoveryBenchArtifactError("Invalid trial timestamp") from error
    duration = (finish - start).total_seconds()
    if duration < 0:
        raise RecoveryBenchArtifactError("Trial finish time precedes start time")
    return duration


def _reward(result: Mapping[str, Any]) -> float | None:
    verifier = result.get("verifier_result")
    if not isinstance(verifier, Mapping):
        return None
    rewards = verifier.get("rewards")
    if not isinstance(rewards, Mapping):
        return None
    reward = rewards.get("reward")
    if isinstance(reward, bool) or not isinstance(reward, (int, float)):
        return None
    return float(reward)


def _status(result: Mapping[str, Any], reward: float | None) -> tuple[str, str | None]:
    exception = result.get("exception_info")
    exception_type: str | None = None
    if isinstance(exception, Mapping) and isinstance(exception.get("exception_type"), str):
        exception_type = str(exception["exception_type"])
    if exception_type:
        lowered = exception_type.lower()
        if "agent" in lowered:
            return "agent_error", exception_type
        if "verifier" in lowered:
            return "verifier_error", exception_type
        if "environment" in lowered or "docker" in lowered:
            return "environment_error", exception_type
        return "trial_error", exception_type
    if reward is None:
        return "incomplete", None
    return ("success", None) if reward > 0 else ("verifier_failure", None)


class RecoveryBenchAdapter:
    """Normalize existing trial artifacts through a path-confined, read-only API."""

    def __init__(
        self,
        trials_root: str | Path,
        *,
        supported_atif_schemas: Iterable[str] = ("ATIF-v1.6",),
    ) -> None:
        self.trials_root = Path(trials_root).resolve(strict=True)
        if not self.trials_root.is_dir():
            raise RecoveryBenchArtifactError("Recovery-Bench trials root is not a directory")
        self.supported_atif_schemas = frozenset(str(item) for item in supported_atif_schemas)
        if not self.supported_atif_schemas:
            raise RecoveryBenchArtifactError("At least one ATIF schema must be supported")

    def trial_directory(self, trial_name: str) -> Path:
        if not trial_name or Path(trial_name).name != trial_name:
            raise RecoveryBenchArtifactError("Trial name must be one path component")
        candidate = (self.trials_root / trial_name).resolve(strict=True)
        try:
            candidate.relative_to(self.trials_root)
        except ValueError as error:
            raise RecoveryBenchArtifactError("Trial path escapes configured root") from error
        if not candidate.is_dir():
            raise RecoveryBenchArtifactError("Trial path is not a directory")
        return candidate

    def list_trial_names(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                path.name
                for path in self.trials_root.iterdir()
                if path.is_dir()
                and (path / "result.json").is_file()
                and (path / "config.json").is_file()
            )
        )

    def load_trial(
        self,
        trial_name: str,
        *,
        expected_task_checksum: str | None = None,
        include_content: bool = False,
    ) -> RecoveryBenchTrial:
        trial_directory = self.trial_directory(trial_name)
        result_path = trial_directory / "result.json"
        config_path = trial_directory / "config.json"
        trajectory_path = trial_directory / "agent" / "trajectory.json"
        result, result_digest = _read_json(result_path)
        config, config_digest = _read_json(config_path)
        trajectory, trajectory_digest = _read_json(trajectory_path)

        result_config = result.get("config")
        if not isinstance(result_config, Mapping):
            raise RecoveryBenchArtifactError("result.json has no object config")
        config_matches_result = dict(result_config) == config
        if not config_matches_result:
            raise RecoveryBenchArtifactError("config.json differs from result.json config")

        atif_schema = _require_string(trajectory, "schema_version", trajectory_path)
        if atif_schema not in self.supported_atif_schemas:
            raise RecoveryBenchArtifactError(f"Unsupported ATIF schema: {atif_schema}")
        steps = trajectory.get("steps")
        if not isinstance(steps, list) or not all(isinstance(step, Mapping) for step in steps):
            raise RecoveryBenchArtifactError("ATIF steps must be a list of objects")

        task_checksum = _require_string(result, "task_checksum", result_path)
        if expected_task_checksum is not None and task_checksum != expected_task_checksum:
            raise RecoveryBenchArtifactError("Task checksum does not match configured selection")

        reward = _reward(result)
        status, exception_type = _status(result, reward)
        reward_file_matches = self._reward_file_matches(trial_directory, reward)
        task_id = result.get("task_id") if isinstance(result.get("task_id"), Mapping) else {}
        agent_info = (
            result.get("agent_info") if isinstance(result.get("agent_info"), Mapping) else {}
        )
        model_info = (
            agent_info.get("model_info")
            if isinstance(agent_info.get("model_info"), Mapping)
            else {}
        )
        agent_result = (
            result.get("agent_result")
            if isinstance(result.get("agent_result"), Mapping)
            else {}
        )
        environment = (
            config.get("environment") if isinstance(config.get("environment"), Mapping) else {}
        )
        tool_calls, observations, unmatched_count = self._normalize_trace(
            steps, include_content=include_content
        )
        started_at = _require_string(result, "started_at", result_path)
        finished_at = _require_string(result, "finished_at", result_path)
        reason = (
            "Harbor Docker trials provide fresh reset and command replay, but no "
            "normalized intermediate-state snapshot, hash-verified restore, or isolated "
            "same-checkpoint branch protocol"
        )
        return RecoveryBenchTrial(
            schema_version=1,
            source_run=self.trials_root.name,
            source_trial_directory=str(trial_directory),
            result_sha256=result_digest,
            config_sha256=config_digest,
            trajectory_sha256=trajectory_digest,
            trial_id=_require_string(result, "id", result_path),
            task_name=_require_string(result, "task_name", result_path),
            trial_name=_require_string(result, "trial_name", result_path),
            source=_require_string(result, "source", result_path),
            task_checksum=task_checksum,
            task_git_url=str(task_id["git_url"]) if isinstance(task_id.get("git_url"), str) else None,
            task_git_commit_id=(
                str(task_id["git_commit_id"])
                if isinstance(task_id.get("git_commit_id"), str)
                else None
            ),
            task_path=str(task_id["path"]) if isinstance(task_id.get("path"), str) else None,
            atif_schema_version=atif_schema,
            agent_name=str(agent_info.get("name", "unknown")),
            agent_version=str(agent_info.get("version", "unknown")),
            model_name=str(model_info.get("name", "unknown")),
            environment_type=str(environment.get("type", "unknown")),
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=_duration_seconds(started_at, finished_at),
            status=status,
            success=status == "success",
            verifier_reward=reward,
            exception_type=exception_type,
            input_tokens=int(agent_result.get("n_input_tokens", 0)),
            cache_tokens=int(agent_result.get("n_cache_tokens", 0)),
            output_tokens=int(agent_result.get("n_output_tokens", 0)),
            cost_usd=float(agent_result.get("cost_usd", 0.0)),
            step_count=len(steps),
            tool_call_count=len(tool_calls),
            observation_result_count=len(observations),
            unmatched_observation_result_count=unmatched_count,
            config_matches_result=config_matches_result,
            verifier_reward_file_matches=reward_file_matches,
            checkpoint_restore_supported=False,
            branch_utility_eligible=False,
            branch_ineligibility_reason=reason,
            tool_calls=tool_calls,
            observations=observations,
        )

    @staticmethod
    def _reward_file_matches(trial_directory: Path, reward: float | None) -> bool | None:
        reward_path = trial_directory / "verifier" / "reward.txt"
        if not reward_path.is_file() or reward is None:
            return None
        try:
            reward_file = float(reward_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError) as error:
            raise RecoveryBenchArtifactError("Invalid verifier/reward.txt") from error
        return abs(reward_file - reward) <= 1e-12

    @staticmethod
    def _normalize_trace(
        steps: list[Mapping[str, Any]], *, include_content: bool
    ) -> tuple[
        tuple[NormalizedToolCall, ...], tuple[NormalizedObservation, ...], int
    ]:
        normalized: list[NormalizedToolCall] = []
        normalized_observations: list[NormalizedObservation] = []
        unmatched_count = 0
        for step in steps:
            calls = step.get("tool_calls") or []
            if not isinstance(calls, list) or not all(isinstance(call, Mapping) for call in calls):
                raise RecoveryBenchArtifactError("ATIF tool_calls must be a list of objects")
            observation = step.get("observation")
            results: list[Mapping[str, Any]] = []
            if isinstance(observation, Mapping):
                candidate_results = observation.get("results") or []
                if not isinstance(candidate_results, list) or not all(
                    isinstance(item, Mapping) for item in candidate_results
                ):
                    raise RecoveryBenchArtifactError("ATIF observation results are invalid")
                results = candidate_results
            known_call_ids = {
                str(call.get("tool_call_id")) for call in calls if call.get("tool_call_id")
            }
            result_ids = [
                str(item.get("source_call_id"))
                for item in results
                if item.get("source_call_id") is not None
            ]
            unmatched_count += sum(result_id not in known_call_ids for result_id in result_ids)
            no_ids = all(item.get("source_call_id") is None for item in results)
            ambiguous_step_results = bool(results) and no_ids and len(calls) != len(results)
            if ambiguous_step_results:
                unmatched_count += len(results)

            step_id = str(step.get("step_id", ""))
            timestamp = str(step["timestamp"]) if isinstance(step.get("timestamp"), str) else None
            for index, item in enumerate(results):
                source_call_id = (
                    str(item["source_call_id"])
                    if item.get("source_call_id") is not None
                    else None
                )
                if source_call_id is not None and source_call_id in known_call_ids:
                    observation_association = "source_call_id"
                elif no_ids and len(calls) == len(results) and index < len(calls):
                    observation_association = "positional"
                else:
                    observation_association = "step_level_unlinked"
                content = str(item.get("content", ""))
                normalized_observations.append(
                    NormalizedObservation(
                        step_id=step_id,
                        timestamp=timestamp,
                        source_call_id=source_call_id,
                        association=observation_association,
                        content_sha256=_sha256(content.encode("utf-8")),
                        content=content if include_content else None,
                    )
                )

            metrics = step.get("metrics") if isinstance(step.get("metrics"), Mapping) else {}
            for index, call in enumerate(calls):
                call_id = str(call.get("tool_call_id", ""))
                function_name = str(call.get("function_name", ""))
                if not call_id or not function_name:
                    raise RecoveryBenchArtifactError("ATIF tool call lacks id or function name")
                arguments_value = call.get("arguments")
                arguments = dict(arguments_value) if isinstance(arguments_value, Mapping) else {}
                matching = [item for item in results if item.get("source_call_id") == call_id]
                if matching:
                    association = "source_call_id"
                elif no_ids and len(calls) == len(results) and index < len(results):
                    matching = [results[index]]
                    association = "positional"
                elif not results:
                    association = "none"
                else:
                    association = "step_level_unlinked"
                contents = tuple(str(item.get("content", "")) for item in matching)
                normalized.append(
                    NormalizedToolCall(
                        step_id=step_id,
                        timestamp=timestamp,
                        model_name=(
                            str(step["model_name"])
                            if isinstance(step.get("model_name"), str)
                            else None
                        ),
                        tool_call_id=call_id,
                        function_name=function_name,
                        argument_keys=tuple(sorted(arguments)),
                        arguments_sha256=_canonical_sha256(arguments),
                        observation_count=len(matching),
                        observation_sha256=tuple(
                            _sha256(content.encode("utf-8")) for content in contents
                        ),
                        result_association=association,
                        step_prompt_tokens=int(metrics.get("prompt_tokens", 0)),
                        step_completion_tokens=int(metrics.get("completion_tokens", 0)),
                        step_cost_usd=float(metrics.get("cost_usd", 0.0)),
                        arguments=arguments if include_content else None,
                        observation_contents=contents if include_content else None,
                    )
                )
        return tuple(normalized), tuple(normalized_observations), unmatched_count
