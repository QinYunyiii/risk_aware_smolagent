from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from risk_agent.benchmark import (
    ReferenceTaskRunner,
    build_p2_d1_catalog,
    generate_task_instances,
    load_task_templates,
    task_to_dict,
)
from risk_agent.benchmark.step_references import step_dependencies


ROOT = Path(__file__).resolve().parents[1]
BASE_TEMPLATES = ROOT / "configs/task_templates.yaml"
V2_TEMPLATES = ROOT / "configs/task_templates_v2.yaml"
EXPECTED_V1_CATALOG_SHA256 = (
    "8a76a5c2b5d826832f2c604477c606d699a19a8ceee35b923cedc336b9e9d775"
)


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_v2_extends_immutable_v1_and_has_required_counts() -> None:
    base = load_task_templates(BASE_TEMPLATES)
    combined = load_task_templates(V2_TEMPLATES)

    assert combined[:40] == base
    assert len(combined) == len({item.template_id for item in combined}) == 80
    assert Counter(item.domain.value for item in combined) == {
        "api_workflow": 20,
        "calculation_logic": 20,
        "data_analysis": 20,
        "file_retrieval": 20,
    }
    assert Counter(item.split.value for item in combined) == {
        "train": 48,
        "validation": 16,
        "test": 16,
    }
    assert _canonical_hash(
        [task_to_dict(task) for task in generate_task_instances(base, 5)]
    ) == EXPECTED_V1_CATALOG_SHA256


def test_new_templates_generate_true_backward_dependent_multistep_tasks() -> None:
    base = load_task_templates(BASE_TEMPLATES)
    combined = load_task_templates(V2_TEMPLATES)
    tasks = generate_task_instances(combined, 2)
    new_ids = {item.template_id for item in combined[len(base) :]}
    new_tasks = [item for item in tasks if item.template_id in new_ids]

    assert len(tasks) == 160
    assert len(new_tasks) == 80
    assert all(3 <= len(task.reference_steps) <= 8 for task in new_tasks)
    for task in new_tasks:
        dependency_steps = 0
        for index, step in enumerate(task.reference_steps, start=1):
            dependencies = step_dependencies(step.arguments)
            assert all(dependency < index for dependency in dependencies)
            dependency_steps += bool(dependencies)
        assert dependency_steps >= 2


def test_all_combined_tasks_pass_reference_runner(tmp_path: Path) -> None:
    tasks = generate_task_instances(load_task_templates(V2_TEMPLATES), 2)
    runner = ReferenceTaskRunner(tmp_path)

    results = [runner.run(task) for task in tasks]

    assert all(result.passed for result in results)
    assert all(result.reset_verified for result in results)


@pytest.fixture(scope="module")
def built_catalog(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, object]]:
    output = tmp_path_factory.mktemp("p2-d1") / "run"
    summary = build_p2_d1_catalog(
        base_templates_path=BASE_TEMPLATES,
        v2_templates_path=V2_TEMPLATES,
        output_directory=output,
    )
    return output, summary


def test_builder_emits_required_artifacts_and_passes(
    built_catalog: tuple[Path, dict[str, object]],
) -> None:
    output, summary = built_catalog
    required = {
        "task_catalog.json",
        "split_manifest.json",
        "fault_coverage_matrix.json",
        "reference_validation.json",
    }

    assert summary["status"] == "PASS"
    assert required <= {path.name for path in output.iterdir()}
    assert summary["counts"] == {
        "templates": 80,
        "tasks": 160,
        "new_templates": 40,
        "new_tasks": 80,
    }
    assert all(summary["checks"].values())


def test_fault_matrix_covers_every_split_and_f6_commit_states(
    built_catalog: tuple[Path, dict[str, object]],
) -> None:
    output, _ = built_catalog
    matrix = json.loads((output / "fault_coverage_matrix.json").read_text())
    expected_faults = {f"F{index}_" for index in range(7)}

    for split in ("train", "validation", "test"):
        observed = matrix["template_counts_by_split"][split]
        assert len(observed) == 7
        assert all(any(name.startswith(prefix) for name in observed) for prefix in expected_faults)
    assert matrix["f6_commit_states_observed"] == [
        "committed",
        "not_committed",
        "unknown",
    ]
    f6_probes = [
        probe
        for probe in matrix["fault_probes"]
        if probe["fault_type"] == "F6_PARTIAL_FAILURE"
    ]
    assert len(f6_probes) == 6
    assert all(probe["f6_state_check"]["status_matches_effect"] for probe in f6_probes)
    assert all(
        probe["internal_fault_metadata"]["triggered_event"] is not None
        for probe in f6_probes
    )
    assert all(
        probe["f6_state_check"][
            "blind_retry_blocked_when_uncertain_or_committed"
        ]
        for probe in f6_probes
    )


def test_agent_view_is_separated_from_evaluator_control(
    built_catalog: tuple[Path, dict[str, object]],
) -> None:
    output, _ = built_catalog
    catalog = json.loads((output / "task_catalog.json").read_text())

    assert catalog["separation_policy"].startswith("agent_view is model-visible")
    for record in catalog["tasks"]:
        assert set(record) == {
            "agent_view",
            "environment_setup",
            "evaluation_control",
        }
        assert not {
            "fault_plan",
            "interventions",
            "reference_steps",
            "expected",
            "files",
            "inventory",
            "orders",
        } & set(record["agent_view"])
        assert set(record["environment_setup"]) == {"files", "inventory", "orders"}
        assert set(record["evaluation_control"]) == {"reference_steps", "expected"}
