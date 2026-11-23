"""Load and validate immutable v1 and additive v2 template catalogs."""

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
    source = Path(path).expanduser().resolve()
    document = yaml.safe_load(source.read_text(encoding="utf-8"))
    root = _mapping(document, "template document")
    version = root.get("version")
    if version not in {1, 2}:
        raise TaskDefinitionError("Only template schema versions 1 and 2 are supported")
    expected_instances = 5 if version == 1 else 2
    if root.get("instances_per_template") != expected_instances:
        raise TaskDefinitionError(
            f"Template schema v{version} requires exactly {expected_instances} instances"
        )
    inherited: tuple[TaskTemplate, ...] = ()
    if version == 2:
        extends = root.get("extends")
        if not isinstance(extends, str) or not extends:
            raise TaskDefinitionError("Template schema v2 requires a sibling extends path")
        inherited_path = (source.parent / extends).resolve()
        if inherited_path.parent != source.parent or inherited_path == source:
            raise TaskDefinitionError("Template schema v2 extends must reference a sibling file")
        inherited = load_task_templates(inherited_path)
    raw_templates = root.get("templates")
    if not isinstance(raw_templates, list) or not raw_templates:
        raise TaskDefinitionError("templates must be a non-empty list")
    templates: list[TaskTemplate] = list(inherited)
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
