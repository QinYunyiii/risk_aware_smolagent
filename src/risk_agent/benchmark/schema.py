"""Validated, immutable schemas for benchmark tasks and template splits."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Mapping


_SAFE_ID = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")


class TaskDefinitionError(ValueError):
    """Raised when a task definition is unsafe or malformed."""


class Domain(StrEnum):
    DATA_ANALYSIS = "data_analysis"
    FILE_RETRIEVAL = "file_retrieval"
    API_WORKFLOW = "api_workflow"
    CALCULATION_LOGIC = "calculation_logic"


class DataSplit(StrEnum):
    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"


def validate_identifier(value: str, field: str) -> str:
    if not _SAFE_ID.fullmatch(value):
        raise TaskDefinitionError(f"{field} must match {_SAFE_ID.pattern!r}: {value!r}")
    return value


def validate_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise TaskDefinitionError(f"Unsafe fixture path: {value!r}")
    return str(path)


@dataclass(frozen=True, slots=True)
class FileFixture:
    path: str
    content: str

    def __post_init__(self) -> None:
        validate_relative_path(self.path)


@dataclass(frozen=True, slots=True)
class ToolStep:
    tool: str
    arguments: Mapping[str, Any]

    def __post_init__(self) -> None:
        validate_identifier(self.tool, "tool")


@dataclass(frozen=True, slots=True)
class TaskSpec:
    task_id: str
    template_id: str
    instance_id: str
    domain: Domain
    seed: int
    prompt: str
    files: tuple[FileFixture, ...]
    inventory: tuple[Mapping[str, Any], ...]
    orders: tuple[Mapping[str, Any], ...]
    reference_steps: tuple[ToolStep, ...]
    expected: Mapping[str, Any]
    dataset_role: str = "day3_smoke"
    split: DataSplit | None = None

    def __post_init__(self) -> None:
        validate_identifier(self.task_id, "task_id")
        validate_identifier(self.template_id, "template_id")
        validate_identifier(self.instance_id, "instance_id")
        if self.seed < 0:
            raise TaskDefinitionError("seed must be non-negative")
        if not self.prompt.strip():
            raise TaskDefinitionError("prompt must not be empty")
        if not self.reference_steps:
            raise TaskDefinitionError("reference_steps must not be empty")
        paths = [fixture.path for fixture in self.files]
        if len(paths) != len(set(paths)):
            raise TaskDefinitionError(f"Task {self.task_id!r} has duplicate fixture paths")
        if self.dataset_role not in {"day3_smoke", "day7_catalog"}:
            raise TaskDefinitionError("Unsupported dataset_role")
        if self.dataset_role == "day3_smoke" and self.split is not None:
            raise TaskDefinitionError("Day 3 smoke tasks must not be assigned to a formal split")
        if self.dataset_role == "day7_catalog" and self.split is None:
            raise TaskDefinitionError("Day 7 catalog tasks require a template-level split")


@dataclass(frozen=True, slots=True)
class TaskTemplate:
    template_id: str
    domain: Domain
    split: DataSplit
    family: str
    base_seed: int
    parameters: Mapping[str, Any]

    def __post_init__(self) -> None:
        validate_identifier(self.template_id, "template_id")
        validate_identifier(self.family, "family")
        if self.base_seed < 0:
            raise TaskDefinitionError("base_seed must be non-negative")
