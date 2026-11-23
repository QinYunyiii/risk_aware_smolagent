"""Resolve deterministic backward references between reference tool steps."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .schema import TaskDefinitionError


_REFERENCE = re.compile(r"^step_([1-9][0-9]*)(?:\.(.+))?$")
_TEMPLATE_REFERENCE = re.compile(r"\{(step_[1-9][0-9]*(?:\.[A-Za-z0-9_-]+)*)\}")


def _lookup(reference: str, outputs: Mapping[str, Any]) -> Any:
    match = _REFERENCE.fullmatch(reference)
    if match is None:
        raise TaskDefinitionError(f"Invalid step reference: {reference!r}")
    step_key = f"step_{match.group(1)}"
    if step_key not in outputs:
        raise TaskDefinitionError(f"Step reference is not backward/resolved: {reference!r}")
    value = outputs[step_key]
    tail = match.group(2)
    if tail is None:
        return value
    for component in tail.split("."):
        if isinstance(value, Mapping):
            if component not in value:
                raise TaskDefinitionError(
                    f"Step reference component is absent: {reference!r}"
                )
            value = value[component]
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            if not component.isdigit() or int(component) >= len(value):
                raise TaskDefinitionError(
                    f"Step reference index is invalid: {reference!r}"
                )
            value = value[int(component)]
        else:
            raise TaskDefinitionError(
                f"Step reference traverses a scalar: {reference!r}"
            )
    return value


def resolve_step_value(value: Any, outputs: Mapping[str, Any]) -> Any:
    """Resolve nested reference objects without mutating the task definition."""

    if isinstance(value, Mapping):
        if set(value) == {"$ref"}:
            reference = value["$ref"]
            if not isinstance(reference, str):
                raise TaskDefinitionError("$ref must be a string")
            return _lookup(reference, outputs)
        if set(value) == {"$template"}:
            template = value["$template"]
            if not isinstance(template, str):
                raise TaskDefinitionError("$template must be a string")
            return _TEMPLATE_REFERENCE.sub(
                lambda match: str(_lookup(match.group(1), outputs)), template
            )
        return {key: resolve_step_value(item, outputs) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_step_value(item, outputs) for item in value]
    if isinstance(value, tuple):
        return tuple(resolve_step_value(item, outputs) for item in value)
    return value


def step_dependencies(value: Any) -> frozenset[int]:
    """Return every referenced one-based step number in an argument tree."""

    dependencies: set[int] = set()
    if isinstance(value, Mapping):
        if set(value) == {"$ref"} and isinstance(value["$ref"], str):
            match = _REFERENCE.fullmatch(value["$ref"])
            if match is None:
                raise TaskDefinitionError(f"Invalid step reference: {value['$ref']!r}")
            dependencies.add(int(match.group(1)))
        elif set(value) == {"$template"} and isinstance(value["$template"], str):
            for match in _TEMPLATE_REFERENCE.finditer(value["$template"]):
                reference_match = _REFERENCE.fullmatch(match.group(1))
                if reference_match is None:  # pragma: no cover - regexes share a grammar
                    raise TaskDefinitionError(
                        f"Invalid step reference: {match.group(1)!r}"
                    )
                dependencies.add(int(reference_match.group(1)))
        else:
            for item in value.values():
                dependencies.update(step_dependencies(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            dependencies.update(step_dependencies(item))
    return frozenset(dependencies)
