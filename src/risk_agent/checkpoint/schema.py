"""Checkpoint manifest schema."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class CheckpointManifest:
    schema_version: int
    checkpoint_id: str
    created_at: str
    task_id: str
    state_hash: str
    environment_hash: str
    runtime_hash: str
    task_config_hash: str
    mock_api_state_hash: str
    artifact_checksums: Mapping[str, str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CheckpointManifest":
        if int(raw["schema_version"]) != 1:
            raise ValueError("Unsupported checkpoint schema version")
        checksums = raw.get("artifact_checksums")
        if not isinstance(checksums, dict):
            raise ValueError("artifact_checksums must be a mapping")
        return cls(
            schema_version=1,
            checkpoint_id=str(raw["checkpoint_id"]),
            created_at=str(raw["created_at"]),
            task_id=str(raw["task_id"]),
            state_hash=str(raw["state_hash"]),
            environment_hash=str(raw["environment_hash"]),
            runtime_hash=str(raw["runtime_hash"]),
            task_config_hash=str(raw["task_config_hash"]),
            mock_api_state_hash=str(raw["mock_api_state_hash"]),
            artifact_checksums={str(key): str(value) for key, value in checksums.items()},
        )
