"""Load and validate the Day 7 template catalog."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from .schema import DataSplit, Domain, TaskDefinitionError, TaskTemplate


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise TaskDefinitionError(f"{field} must be a mapping")
    return value


def load_task_templates(path: str | Path) -> tuple[TaskTemplate, ...]:
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    root = _mapping(document, "template document")
    if root.get("version") != 1:
        raise TaskDefinitionError("Only template schema version 1 is supported")
    if root.get("instances_per_template") != 5:
        raise TaskDefinitionError("Day 7 requires exactly five instances per template")
    raw_templates = root.get("templates")
    if not isinstance(raw_templates, list) or not raw_templates:
        raise TaskDefinitionError("templates must be a non-empty list")
    templates: list[TaskTemplate] = []
    for raw_value in raw_templates:
        raw = _mapping(raw_value, "template")
        try:
            templates.append(
                TaskTemplate(
                    template_id=str(raw["template_id"]),
                    domain=Domain(str(raw["domain"])),
                    split=DataSplit(str(raw["split"])),
                    family=str(raw["family"]),
                    base_seed=int(raw["base_seed"]),
                    parameters=dict(_mapping(raw.get("parameters", {}), "parameters")),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, TaskDefinitionError):
                raise
            raise TaskDefinitionError(f"Invalid task template: {exc}") from exc
    template_ids = [template.template_id for template in templates]
    if len(template_ids) != len(set(template_ids)):
        raise TaskDefinitionError("template_id values must be unique")
    base_seeds = [template.base_seed for template in templates]
    if len(base_seeds) != len(set(base_seeds)):
        raise TaskDefinitionError("base_seed values must be unique")
    return tuple(templates)
