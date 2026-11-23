"""Build and audit the additive P2-D1 task catalog without model or remote access."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from risk_agent.faults import (
    FaultConfig,
    FaultInjector,
    FaultType,
    InjectedFaultError,
    RetrySafetyEvaluator,
    describe_operation,
)
from risk_agent.tools.task_tool_registry import TaskToolRegistry

from .environment import TaskEnvironment
from .runner import ReferenceTaskRunner
from .schema import DataSplit, Domain, TaskDefinitionError, TaskSpec, TaskTemplate
from .step_references import resolve_step_value, step_dependencies
from .task_generator import generate_task_instances, task_to_dict
from .template_loader import load_task_templates
from .verifier import DeterministicVerifier


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _template_record(template: TaskTemplate, *, inherited: bool) -> dict[str, Any]:
    parameters = dict(template.parameters)
    control = {
        key: parameters.pop(key)
        for key in ("fault_plan", "interventions")
        if key in parameters
    }
    return {
        "template_id": template.template_id,
        "domain": template.domain.value,
        "split": template.split.value,
        "family": template.family,
        "base_seed": template.base_seed,
        "inherited_v1": inherited,
        "agent_parameters": parameters,
        "control": control,
    }


def _agent_task_record(task: TaskSpec) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "template_id": task.template_id,
        "instance_id": task.instance_id,
        "domain": task.domain.value,
        "split": task.split.value if task.split is not None else None,
        "seed": task.seed,
        "prompt": task.prompt,
    }


def _environment_task_record(task: TaskSpec) -> dict[str, Any]:
    return {
        "files": [asdict(item) for item in task.files],
        "inventory": [dict(item) for item in task.inventory],
        "orders": [dict(item) for item in task.orders],
    }


def _control_task_record(task: TaskSpec) -> dict[str, Any]:
    return {
        "reference_steps": [asdict(item) for item in task.reference_steps],
        "expected": dict(task.expected),
    }


def _fault_config(template: TaskTemplate, task: TaskSpec) -> FaultConfig:
    raw = template.parameters.get("fault_plan")
    if not isinstance(raw, Mapping):
        raise TaskDefinitionError(f"{template.template_id} has no fault_plan mapping")
    return FaultConfig(
        fault_id=str(raw["fault_id"]),
        fault_type=FaultType(str(raw["fault_type"])),
        seed=task.seed + 1_000_000,
        target_tool=str(raw["target_tool"]),
        call_indices=tuple(int(value) for value in raw.get("call_indices", ())),
        probability=float(raw.get("probability", 0.0)),
        parameters=dict(raw.get("parameters", {})),
    )


def _template_structure_audit(
    template: TaskTemplate, tasks: tuple[TaskSpec, ...]
) -> dict[str, Any]:
    plan = template.parameters["fault_plan"]
    target_step = int(plan["target_step"])
    target_tool = str(plan["target_tool"])
    fault_type = FaultType(str(plan["fault_type"]))
    records: list[dict[str, Any]] = []
    for task in tasks:
        if not 3 <= len(task.reference_steps) <= 8:
            raise TaskDefinitionError(f"{task.task_id} must contain 3-8 steps")
        if target_step > len(task.reference_steps):
            raise TaskDefinitionError(f"{task.task_id} fault target is out of range")
        if task.reference_steps[target_step - 1].tool != target_tool:
            raise TaskDefinitionError(f"{task.task_id} fault target tool/step mismatch")
        prior_tools: Counter[str] = Counter()
        steps: list[dict[str, Any]] = []
        for index, step in enumerate(task.reference_steps, start=1):
            dependencies = sorted(step_dependencies(step.arguments))
            if any(dependency >= index for dependency in dependencies):
                raise TaskDefinitionError(
                    f"{task.task_id} step {index} contains a forward reference"
                )
            intentional_repeat = (
                fault_type == FaultType.F5_REPEATED_ACTION
                and index == target_step
                and index > 1
                and step.tool == task.reference_steps[index - 2].tool
                and dict(step.arguments) == dict(task.reference_steps[index - 2].arguments)
            )
            intentional_refresh = (
                fault_type == FaultType.F4_STALE_OBSERVATION
                and index == target_step + 1
                and index > 1
                and step.tool == task.reference_steps[index - 2].tool
                and dict(step.arguments) == dict(task.reference_steps[index - 2].arguments)
            )
            if (
                index > 1
                and not dependencies
                and not intentional_repeat
                and not intentional_refresh
            ):
                raise TaskDefinitionError(
                    f"{task.task_id} step {index} is not dependent on prior state"
                )
            prior_tools[step.tool] += 1
            steps.append(
                {
                    "step": index,
                    "tool": step.tool,
                    "dependencies": dependencies,
                    "tool_call_index": prior_tools[step.tool],
                    "intentional_repeat": intentional_repeat,
                    "intentional_state_refresh": intentional_refresh,
                }
            )
        expected_call_indices = tuple(int(value) for value in plan.get("call_indices", ()))
        actual_target_call = steps[target_step - 1]["tool_call_index"]
        if expected_call_indices and actual_target_call not in expected_call_indices:
            raise TaskDefinitionError(
                f"{task.task_id} target call index does not match its fault plan"
            )
        records.append({"task_id": task.task_id, "steps": steps})
    return {
        "template_id": template.template_id,
        "fault_type": fault_type.value,
        "target_step": target_step,
        "target_tool": target_tool,
        "tasks": records,
    }


def _event_record(event: Any) -> dict[str, Any]:
    return {
        "fault_id": event.fault_id,
        "fault_type": event.fault_type.value,
        "seed": event.seed,
        "tool_name": event.tool_name,
        "call_index": event.call_index,
        "triggered": event.triggered,
        "phase": event.phase,
        "commit_status": event.commit_status.value,
        "response_received": event.response_received,
        "error_type": event.error_type,
        "arguments_hash": event.arguments_hash,
    }


def _fault_probe(
    task: TaskSpec,
    template: TaskTemplate,
    base_directory: Path,
) -> dict[str, Any]:
    environment = TaskEnvironment(base_directory, task)
    initial_hash = environment.reset()
    registry = TaskToolRegistry(environment)
    config = _fault_config(template, task)
    injector = FaultInjector(config)
    outputs: dict[str, Any] = {}
    final_result: Any = None
    execution_error: str | None = None
    target_operation = None
    agent_visible: dict[str, Any] | None = None
    for index, step in enumerate(task.reference_steps, start=1):
        try:
            arguments = resolve_step_value(step.arguments, outputs)
            if not isinstance(arguments, dict):
                raise TaskDefinitionError("Resolved step arguments must be a mapping")
            operation = describe_operation(step.tool, arguments)
            before_count = len(injector.events)
            final_result = injector.execute(
                operation,
                arguments,
                lambda step=step, arguments=arguments: registry.execute(
                    task.domain, step.tool, arguments
                ),
            )
            outputs[f"step_{index}"] = final_result
            if len(injector.events) > before_count and injector.events[-1].triggered:
                target_operation = operation
                agent_visible = {"kind": "result", "value": final_result}
        except InjectedFaultError as exc:
            target_operation = operation
            execution_error = type(exc).__name__
            public_error_types = {
                FaultType.F1_TIMEOUT: "TimeoutError",
                FaultType.F2_API_503: "ServiceUnavailableError",
                FaultType.F3_INVALID_ARGUMENT: "InvalidArgumentError",
                FaultType.F6_PARTIAL_FAILURE: "ToolExecutionError",
            }
            agent_visible = {
                "kind": "error",
                "error_type": public_error_types[config.fault_type],
                "message": str(exc),
            }
            break
        except Exception as exc:  # downstream failure caused by a visible stale result
            execution_error = type(exc).__name__
            break
    triggered = [event for event in injector.events if event.triggered]
    verifier_result: bool | None = None
    if execution_error is None:
        verifier_result = DeterministicVerifier().verify(
            task, environment, final_result
        ).passed
    fault_type = config.fault_type
    if fault_type == FaultType.F0_NO_FAULT:
        if triggered or verifier_result is not True:
            raise AssertionError(f"F0 probe failed for {task.task_id}")
        safety = None
        agent_visible = {"kind": "result", "value": final_result}
    else:
        if len(triggered) != 1 or target_operation is None:
            raise AssertionError(f"Fault did not trigger exactly once for {task.task_id}")
        safety = RetrySafetyEvaluator().evaluate(triggered[0], target_operation)
    final_state = environment.state_manifest()
    f6_state_check: dict[str, Any] | None = None
    if fault_type == FaultType.F6_PARTIAL_FAILURE:
        event = triggered[0]
        expected_status = str(template.parameters["new_status"])
        observed_status = str(final_state["orders"][0]["status"])
        configured_commit = bool(config.parameters.get("commit", True))
        f6_state_check = {
            "configured_commit": configured_commit,
            "observed_status": observed_status,
            "write_effect_observed": observed_status == expected_status,
            "idempotency_record_count": len(final_state["idempotency_keys"]),
            "status_matches_effect": (observed_status == expected_status)
            == configured_commit,
            "blind_retry_blocked_when_uncertain_or_committed": (
                safety.retry_allowed is False
                if event.commit_status.value in {"unknown", "committed"}
                else True
            ),
            "known_uncommitted_idempotent_retry_allowed": (
                safety.retry_allowed is True
                if event.commit_status.value == "not_committed"
                else True
            ),
        }
        if not all(
            value
            for key, value in f6_state_check.items()
            if key
            in {
                "status_matches_effect",
                "blind_retry_blocked_when_uncertain_or_committed",
                "known_uncommitted_idempotent_retry_allowed",
            }
        ):
            raise AssertionError(f"F6 safety probe failed for {task.task_id}")
    restored_hash = environment.reset()
    if restored_hash != initial_hash:
        raise AssertionError(f"Fault probe reset failed for {task.task_id}")
    return {
        "task_id": task.task_id,
        "template_id": task.template_id,
        "domain": task.domain.value,
        "split": task.split.value if task.split is not None else None,
        "fault_type": fault_type.value,
        "agent_visible": agent_visible,
        "internal_fault_metadata": {
            "events": [_event_record(event) for event in injector.events],
            "triggered_event": _event_record(triggered[0]) if triggered else None,
        },
        "verifier_label": {
            "status": "evaluated" if verifier_result is not None else "not_evaluated",
            "passed": verifier_result,
        },
        "execution_error": execution_error,
        "verifier_passed": verifier_result,
        "safety_decision": safety.to_dict() if safety is not None else None,
        "f6_state_check": f6_state_check,
        "reset_verified": restored_hash == initial_hash,
    }


def _negative_verifier_probe(task: TaskSpec, base_directory: Path) -> bool:
    environment = TaskEnvironment(base_directory, task)
    environment.reset()
    return not DeterministicVerifier().verify(
        task, environment, {"deliberately": "incorrect"}
    ).passed


def _counts(values: Any) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def build_p2_d1_catalog(
    *,
    base_templates_path: str | Path,
    v2_templates_path: str | Path,
    output_directory: str | Path,
) -> dict[str, Any]:
    """Validate the catalog and atomically publish a new append-only run directory."""

    base_path = Path(base_templates_path).resolve()
    v2_path = Path(v2_templates_path).resolve()
    output = Path(output_directory).resolve()
    if output.exists():
        raise FileExistsError(f"P2-D1 output already exists: {output}")

    base_source_before = _file_hash(base_path)
    base_templates = load_task_templates(base_path)
    templates = load_task_templates(v2_path)
    if len(base_templates) != 40 or len(templates) != 80:
        raise TaskDefinitionError("P2-D1 requires exactly 40 inherited and 40 new templates")
    if templates[: len(base_templates)] != base_templates:
        raise TaskDefinitionError("The inherited v1 template objects changed")
    new_templates = templates[len(base_templates) :]
    tasks = generate_task_instances(templates, instances_per_template=2)
    tasks_by_template: dict[str, tuple[TaskSpec, ...]] = {
        template.template_id: tuple(
            task for task in tasks if task.template_id == template.template_id
        )
        for template in templates
    }
    new_template_ids = {template.template_id for template in new_templates}
    new_tasks = tuple(task for task in tasks if task.template_id in new_template_ids)

    new_domain_split = {
        domain.value: {
            split.value: sum(
                item.domain == domain and item.split == split for item in new_templates
            )
            for split in DataSplit
        }
        for domain in Domain
    }
    structure_audits = [
        _template_structure_audit(template, tasks_by_template[template.template_id])
        for template in new_templates
    ]

    template_faults = {
        template.template_id: str(template.parameters["fault_plan"]["fault_type"])
        for template in new_templates
    }
    fault_by_split: dict[str, Counter[str]] = defaultdict(Counter)
    fault_by_domain: dict[str, Counter[str]] = defaultdict(Counter)
    fault_by_domain_split: dict[str, dict[str, Counter[str]]] = defaultdict(
        lambda: defaultdict(Counter)
    )
    for template in new_templates:
        fault = template_faults[template.template_id]
        fault_by_split[template.split.value][fault] += 1
        fault_by_domain[template.domain.value][fault] += 1
        fault_by_domain_split[template.domain.value][template.split.value][fault] += 1
    all_faults = {item.value for item in FaultType}

    with tempfile.TemporaryDirectory(prefix="p2-d1-reference-") as temporary:
        temporary_root = Path(temporary)
        reference_root = temporary_root / "reference"
        reference_results = [
            ReferenceTaskRunner(reference_root).run(task).to_dict() for task in tasks
        ]
        negative_results = [
            {
                "task_id": task.task_id,
                "incorrect_result_rejected": _negative_verifier_probe(
                    task, temporary_root / "negative"
                ),
            }
            for task in tasks
        ]
        fault_probes = [
            _fault_probe(
                task,
                next(
                    template
                    for template in new_templates
                    if template.template_id == task.template_id
                ),
                temporary_root / "faults",
            )
            for task in new_tasks
        ]

    f6_statuses = {
        probe["internal_fault_metadata"]["triggered_event"]["commit_status"]
        for probe in fault_probes
        if probe["fault_type"] == FaultType.F6_PARTIAL_FAILURE.value
    }
    checks = {
        "base_v1_objects_preserved": templates[:40] == base_templates,
        "base_v1_source_unchanged_during_build": _file_hash(base_path)
        == base_source_before,
        "eighty_unique_templates": len(templates) == len({x.template_id for x in templates}) == 80,
        "one_hundred_sixty_unique_tasks": len(tasks)
        == len({x.task_id for x in tasks})
        == 160,
        "twenty_templates_per_domain": _counts(x.domain.value for x in templates)
        == {domain.value: 20 for domain in Domain},
        "template_split_48_16_16": _counts(x.split.value for x in templates)
        == {"test": 16, "train": 48, "validation": 16},
        "new_template_domain_split_6_2_2": all(
            counts == {"train": 6, "validation": 2, "test": 2}
            for counts in new_domain_split.values()
        ),
        "two_instances_per_template": set(
            Counter(x.template_id for x in tasks).values()
        )
        == {2},
        "new_tasks_have_3_to_8_steps": all(
            3 <= len(task.reference_steps) <= 8 for task in new_tasks
        ),
        "new_steps_are_backward_dependent": bool(structure_audits),
        "every_split_covers_f0_to_f6": all(
            set(counts) == all_faults for counts in fault_by_split.values()
        )
        and set(fault_by_split) == {"train", "validation", "test"},
        "f6_three_commit_states_reachable": f6_statuses
        == {"committed", "not_committed", "unknown"},
        "all_reference_runs_pass": all(
            result["passed"] and result["reset_verified"]
            for result in reference_results
        ),
        "all_negative_results_rejected": all(
            result["incorrect_result_rejected"] for result in negative_results
        ),
        "all_fault_targets_reached_or_f0_completed": all(
            (
                probe["fault_type"] == FaultType.F0_NO_FAULT.value
                and probe["verifier_passed"] is True
            )
            or probe["internal_fault_metadata"]["triggered_event"] is not None
            for probe in fault_probes
        ),
        "all_fault_probe_resets_verified": all(
            probe["reset_verified"] for probe in fault_probes
        ),
        "f6_blind_retry_guard_verified": all(
            probe["f6_state_check"] is None
            or (
                probe["f6_state_check"]["status_matches_effect"]
                and probe["f6_state_check"][
                    "blind_retry_blocked_when_uncertain_or_committed"
                ]
                and probe["f6_state_check"][
                    "known_uncommitted_idempotent_retry_allowed"
                ]
            )
            for probe in fault_probes
        ),
        "agent_view_excludes_control_keys": all(
            not (
                {
                    "fault_plan",
                    "interventions",
                    "reference_steps",
                    "expected",
                    "files",
                    "inventory",
                    "orders",
                }
                & set(_agent_task_record(task))
            )
            for task in tasks
        ),
    }
    if not all(checks.values()):
        failed = sorted(name for name, passed in checks.items() if not passed)
        raise AssertionError(f"P2-D1 acceptance failed: {failed}")

    task_records = [
        {
            "agent_view": _agent_task_record(task),
            "environment_setup": _environment_task_record(task),
            "evaluation_control": _control_task_record(task),
        }
        for task in tasks
    ]
    task_catalog = {
        "schema_version": 2,
        "catalog_version": "minitoolbench-r-v2",
        "separation_policy": (
            "agent_view is model-visible; environment_setup, evaluation_control, "
            "and fault artifacts are evaluator-only"
        ),
        "counts": {
            "templates": len(templates),
            "tasks": len(tasks),
            "new_templates": len(new_templates),
            "new_tasks": len(new_tasks),
        },
        "templates": [
            _template_record(template, inherited=template.template_id not in new_template_ids)
            for template in templates
        ],
        "tasks": task_records,
    }
    split_manifest = {
        "schema_version": 1,
        "split_is_template_level": True,
        "template_counts": _counts(item.split.value for item in templates),
        "task_counts": _counts(item.split.value for item in tasks),
        "new_template_domain_split": new_domain_split,
        "templates": [
            {
                "template_id": template.template_id,
                "domain": template.domain.value,
                "split": template.split.value,
                "task_ids": [
                    task.task_id for task in tasks_by_template[template.template_id]
                ],
            }
            for template in templates
        ],
    }
    missing_cells: list[dict[str, str]] = []
    for domain in Domain:
        for fault in FaultType:
            if fault.value not in fault_by_domain[domain.value]:
                missing_cells.append(
                    {
                        "domain": domain.value,
                        "fault_type": fault.value,
                        "reason": (
                            "F6 is restricted to the idempotent update_order write path"
                            if fault == FaultType.F6_PARTIAL_FAILURE
                            else "Ten-template domain budget; all-split F0-F6 coverage was prioritized"
                        ),
                    }
                )
    fault_matrix = {
        "schema_version": 1,
        "scope": "new P2-D1 templates and their two deterministic instances",
        "fault_types": sorted(all_faults),
        "template_counts_by_split": {
            split: dict(sorted(counts.items()))
            for split, counts in sorted(fault_by_split.items())
        },
        "template_counts_by_domain": {
            domain: dict(sorted(counts.items()))
            for domain, counts in sorted(fault_by_domain.items())
        },
        "template_counts_by_domain_split": {
            domain: {
                split: dict(sorted(counts.items()))
                for split, counts in sorted(split_counts.items())
            }
            for domain, split_counts in sorted(fault_by_domain_split.items())
        },
        "template_ids_by_fault": {
            fault.value: sorted(
                template.template_id
                for template in new_templates
                if template_faults[template.template_id] == fault.value
            )
            for fault in FaultType
        },
        "missing_domain_fault_cells": missing_cells,
        "structure_audits": structure_audits,
        "fault_probes": fault_probes,
        "f6_commit_states_observed": sorted(f6_statuses),
    }
    reference_validation = {
        "schema_version": 1,
        "runner": "ReferenceTaskRunner",
        "model_calls": 0,
        "remote_calls": 0,
        "summary": {
            "reference_total": len(reference_results),
            "reference_passed": sum(
                bool(result["passed"]) for result in reference_results
            ),
            "reset_verified": sum(
                bool(result["reset_verified"]) for result in reference_results
            ),
            "negative_total": len(negative_results),
            "negative_rejected": sum(
                bool(result["incorrect_result_rejected"])
                for result in negative_results
            ),
        },
        "reference_results": reference_results,
        "negative_results": negative_results,
    }

    output.mkdir(parents=True, exist_ok=False)
    artifacts = {
        "task_catalog.json": task_catalog,
        "split_manifest.json": split_manifest,
        "fault_coverage_matrix.json": fault_matrix,
        "reference_validation.json": reference_validation,
    }
    for name, document in artifacts.items():
        _write_json(output / name, document)
    summary = {
        "schema_version": 1,
        "stage": "P2-D1",
        "status": "PASS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "output_directory": output.as_posix(),
        "source_sha256": {
            base_path.as_posix(): base_source_before,
            v2_path.as_posix(): _file_hash(v2_path),
        },
        "catalog_sha256": _canonical_hash(
            [task_to_dict(task) for task in tasks]
        ),
        "base_v1_catalog_sha256": _canonical_hash(
            [
                task_to_dict(task)
                for task in generate_task_instances(base_templates, 5)
            ]
        ),
        "checks": checks,
        "counts": task_catalog["counts"],
        "reference_summary": reference_validation["summary"],
        "no_remote_or_model_execution": True,
    }
    _write_json(output / "run_manifest.json", summary)
    manifest = {
        "schema_version": 1,
        "files": [
            {
                "path": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": _file_hash(path),
            }
            for path in sorted(output.iterdir())
            if path.is_file() and path.name != "artifact_manifest.json"
        ],
    }
    _write_json(output / "artifact_manifest.json", manifest)
    return summary
