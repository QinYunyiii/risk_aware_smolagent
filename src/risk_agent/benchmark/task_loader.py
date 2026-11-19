"""YAML loader with strict conversion into TaskSpec objects."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from .schema import DataSplit, Domain, FileFixture, TaskDefinitionError, TaskSpec, ToolStep


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise TaskDefinitionError(f"{field} must be a mapping")
    return value


def _sequence(value: object, field: str) -> list[object]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TaskDefinitionError(f"{field} must be a list")
    return value


def _task_from_mapping(raw_value: object) -> TaskSpec:
    raw = _mapping(raw_value, "task")
    initial = _mapping(raw.get("initial_state", {}), "initial_state")
    files = tuple(
        FileFixture(
            path=str(_mapping(item, "file fixture")["path"]),
            content=str(_mapping(item, "file fixture")["content"]),
        )
        for item in _sequence(initial.get("files"), "initial_state.files")
    )
    steps = tuple(
        ToolStep(
            tool=str(_mapping(item, "reference step")["tool"]),
            arguments=dict(_mapping(item, "reference step").get("arguments", {})),
        )
        for item in _sequence(raw.get("reference_steps"), "reference_steps")
    )
    try:
        return TaskSpec(
            task_id=str(raw["task_id"]),
            template_id=str(raw["template_id"]),
            instance_id=str(raw["instance_id"]),
            domain=Domain(str(raw["domain"])),
            seed=int(raw["seed"]),
            prompt=str(raw["prompt"]),
            files=files,
            inventory=tuple(
                dict(_mapping(item, "inventory row"))
                for item in _sequence(initial.get("inventory"), "initial_state.inventory")
            ),
            orders=tuple(
                dict(_mapping(item, "order row"))
                for item in _sequence(initial.get("orders"), "initial_state.orders")
            ),
            reference_steps=steps,
            expected=dict(_mapping(raw.get("expected"), "expected")),
            dataset_role=str(raw.get("dataset_role", "day3_smoke")),
            split=(DataSplit(str(raw["split"])) if raw.get("split") is not None else None),
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, TaskDefinitionError):
            raise
        raise TaskDefinitionError(f"Invalid task definition: {exc}") from exc


def load_tasks(path: str | Path) -> tuple[TaskSpec, ...]:
    config_path = Path(path)
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    root = _mapping(document, "task document")
    if root.get("version") != 1:
        raise TaskDefinitionError("Only task schema version 1 is supported")
    tasks = tuple(_task_from_mapping(item) for item in _sequence(root.get("tasks"), "tasks"))
    if not tasks:
        raise TaskDefinitionError("At least one task is required")
    ids = [task.task_id for task in tasks]
    if len(ids) != len(set(ids)):
        raise TaskDefinitionError("task_id values must be unique")
    return tasks
