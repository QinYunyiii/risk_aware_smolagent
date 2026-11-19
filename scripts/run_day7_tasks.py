#!/usr/bin/env python3
"""Generate and validate the complete local Day 7 task catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from risk_agent.benchmark import (
    ReferenceTaskRunner,
    generate_task_instances,
    load_task_templates,
    task_to_dict,
    validate_task_catalog,
)
from risk_agent.benchmark.schema import TaskTemplate, validate_identifier


def default_run_id(started_at: datetime) -> str:
    return f"day7_{started_at.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def template_to_dict(template: TaskTemplate) -> dict[str, object]:
    raw = asdict(template)
    raw["domain"] = template.domain.value
    raw["split"] = template.split.value
    return raw


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--templates", default="configs/task_templates.yaml")
    parser.add_argument("--output-root", default="runs/day07")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()

    started_at = datetime.now(timezone.utc)
    run_id = validate_identifier(args.run_id or default_run_id(started_at), "run_id")
    run_directory = Path(args.output_root) / run_id
    run_directory.mkdir(parents=True, exist_ok=False)
    template_path = Path(args.templates)
    source_hash = hashlib.sha256(template_path.read_bytes()).hexdigest()

    templates = load_task_templates(template_path)
    tasks = generate_task_instances(templates)
    regenerated_tasks = generate_task_instances(templates)
    serialized_tasks = [task_to_dict(task) for task in tasks]
    serialized_regeneration = [task_to_dict(task) for task in regenerated_tasks]
    canonical_catalog = json.dumps(
        serialized_tasks, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    catalog_hash = hashlib.sha256(canonical_catalog.encode("utf-8")).hexdigest()
    catalog_validation = validate_task_catalog(templates, tasks)

    runner = ReferenceTaskRunner(run_directory / "environments")
    results = [runner.run(task) for task in tasks]
    passed_results = [result for result in results if result.passed and result.reset_verified]
    checks = {
        **dict(catalog_validation.checks),
        "generation_reproducible": serialized_tasks == serialized_regeneration,
        "unique_seeds": len({task.seed for task in tasks}) == len(tasks),
        "all_reference_runs_passed": len(passed_results) == 200,
        "all_resets_reproducible": all(
            result.initial_state_hash == result.restored_state_hash for result in results
        ),
        "all_results_have_verifier_checks": all(bool(result.checks) for result in results),
    }

    split_manifest: dict[str, object] = {
        "schema_version": 1,
        "dataset_role": "day7_catalog",
        "template_source": str(template_path),
        "template_source_sha256": source_hash,
        "catalog_sha256": catalog_hash,
        "splits": {},
    }
    for split in ("train", "validation", "test"):
        split_manifest["splits"][split] = {
            "template_ids": [
                template.template_id for template in templates if template.split.value == split
            ],
            "instance_ids": [
                task.instance_id for task in tasks if task.split is not None and task.split.value == split
            ],
        }

    catalog_document = {
        "schema_version": 1,
        "dataset_role": "day7_catalog",
        "formal_trajectory_data": False,
        "run_id": run_id,
        "created_at": started_at.isoformat(),
        "template_source_sha256": source_hash,
        "catalog_sha256": catalog_hash,
        "templates": [template_to_dict(template) for template in templates],
        "tasks": serialized_tasks,
    }
    results_document = {
        "schema_version": 1,
        "experiment_id": "day7_task_catalog_local",
        "run_id": run_id,
        "created_at": started_at.isoformat(),
        "dataset_role": "day7_catalog",
        "formal_trajectory_data": False,
        "catalog_sha256": catalog_hash,
        "catalog_validation": catalog_validation.to_dict(),
        "checks": checks,
        "passed": all(checks.values()),
        "task_results": [result.to_dict() for result in results],
    }
    (run_directory / "task_catalog.json").write_text(
        json.dumps(catalog_document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_directory / "split_manifest.json").write_text(
        json.dumps(split_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    output = run_directory / "task_results.json"
    output.write_text(
        json.dumps(results_document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    console_summary = {
        "run_id": run_id,
        "catalog_sha256": catalog_hash,
        "template_count": len(templates),
        "instance_count": len(tasks),
        "templates_by_domain": catalog_validation.templates_by_domain,
        "templates_by_split": catalog_validation.templates_by_split,
        "instances_by_split": catalog_validation.instances_by_split,
        "reference_runs_passed": len(passed_results),
        "reference_runs_failed": len(results) - len(passed_results),
        "checks": checks,
        "passed": results_document["passed"],
        "artifacts": {
            "catalog": str((run_directory / "task_catalog.json").resolve()),
            "split_manifest": str((run_directory / "split_manifest.json").resolve()),
            "results": str(output.resolve()),
        },
    }
    print(json.dumps(console_summary, ensure_ascii=False, indent=2))
    return 0 if results_document["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
