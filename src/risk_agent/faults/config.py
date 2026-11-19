"""Strict YAML loader for Day 4 fault profiles."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from .schema import FaultConfig, FaultType


class FaultConfigurationError(ValueError):
    pass


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise FaultConfigurationError(f"{field} must be a mapping")
    return value


def load_fault_configs(path: str | Path) -> tuple[FaultConfig, ...]:
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    root = _mapping(document, "fault document")
    if root.get("version") != 1:
        raise FaultConfigurationError("Only fault schema version 1 is supported")
    profiles = root.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise FaultConfigurationError("profiles must be a non-empty list")
    configs: list[FaultConfig] = []
    try:
        for item in profiles:
            raw = _mapping(item, "fault profile")
            indices = raw.get("call_indices", [])
            if not isinstance(indices, list):
                raise FaultConfigurationError("call_indices must be a list")
            parameters = _mapping(raw.get("parameters", {}), "parameters")
            configs.append(
                FaultConfig(
                    fault_id=str(raw["fault_id"]),
                    fault_type=FaultType(str(raw["fault_type"])),
                    seed=int(raw["seed"]),
                    target_tool=str(raw.get("target_tool", "*")),
                    call_indices=tuple(int(index) for index in indices),
                    probability=float(raw.get("probability", 0.0)),
                    parameters=dict(parameters),
                )
            )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, FaultConfigurationError):
            raise
        raise FaultConfigurationError(f"Invalid fault profile: {exc}") from exc
    ids = [config.fault_id for config in configs]
    if len(ids) != len(set(ids)):
        raise FaultConfigurationError("fault_id values must be unique")
    return tuple(configs)
