from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from risk_agent.benchmark import (
    ReferenceTaskRunner,
    generate_task_instances,
    load_task_templates,
    task_to_dict,
    validate_task_catalog,
)
from risk_agent.benchmark.schema import DataSplit, Domain


ROOT = Path(__file__).parents[1]
TEMPLATE_CONFIG = ROOT / "configs" / "task_templates.yaml"


def test_day7_catalog_has_required_domain_and_split_counts() -> None:
    templates = load_task_templates(TEMPLATE_CONFIG)
    tasks = generate_task_instances(templates)
    validation = validate_task_catalog(templates, tasks)
    assert validation.passed
    assert validation.template_count == 40
    assert validation.instance_count == 200
    assert Counter(template.domain for template in templates) == {
        Domain.DATA_ANALYSIS: 10,
        Domain.FILE_RETRIEVAL: 10,
        Domain.API_WORKFLOW: 10,
        Domain.CALCULATION_LOGIC: 10,
    }
    assert Counter(template.split for template in templates) == {
        DataSplit.TRAIN: 24,
        DataSplit.VALIDATION: 8,
        DataSplit.TEST: 8,
    }
    assert Counter(task.split for task in tasks) == {
        DataSplit.TRAIN: 120,
        DataSplit.VALIDATION: 40,
        DataSplit.TEST: 40,
    }
    assert all(
        counts == {"train": 6, "validation": 2, "test": 2}
        for counts in validation.templates_by_domain_split.values()
    )
    assert all(
        counts == {"train": 30, "validation": 10, "test": 10}
        for counts in validation.instances_by_domain_split.values()
    )


def test_generation_is_seeded_reproducible_unique_and_template_grouped() -> None:
    templates = load_task_templates(TEMPLATE_CONFIG)
    first = generate_task_instances(templates)
    second = generate_task_instances(templates)
    assert [task_to_dict(task) for task in first] == [task_to_dict(task) for task in second]
    assert len({task.seed for task in first}) == 200
    assert len({task.task_id for task in first}) == 200
    grouped_splits: dict[str, set[DataSplit | None]] = defaultdict(set)
    grouped_counts: Counter[str] = Counter()
    for task in first:
        grouped_splits[task.template_id].add(task.split)
        grouped_counts[task.template_id] += 1
    assert len(grouped_counts) == 40
    assert set(grouped_counts.values()) == {5}
    assert all(len(splits) == 1 and None not in splits for splits in grouped_splits.values())


def test_all_200_tasks_execute_validate_and_reset(tmp_path) -> None:
    templates = load_task_templates(TEMPLATE_CONFIG)
    tasks = generate_task_instances(templates)
    runner = ReferenceTaskRunner(tmp_path / "environments")
    results = [runner.run(task) for task in tasks]
    assert len(results) == 200
    assert all(result.passed for result in results)
    assert all(result.checks for result in results)
    assert all(result.reset_verified for result in results)
    assert all(result.initial_state_hash == result.restored_state_hash for result in results)
