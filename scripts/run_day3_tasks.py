#!/usr/bin/env python3
"""Execute the eight local Day 3 smoke tasks without any remote model call."""

from __future__ import annotations

import argparse
import json
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from risk_agent.benchmark import ReferenceTaskRunner, load_tasks
from risk_agent.benchmark.schema import validate_identifier


def default_run_id(started_at: datetime) -> str:
    timestamp = started_at.strftime("%Y%m%d_%H%M%S")
    return f"day3_{timestamp}_{uuid.uuid4().hex[:8]}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="configs/tasks.yaml")
    parser.add_argument("--output-root", default="runs/day03")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()
    started_at = datetime.now(timezone.utc)
    run_id = validate_identifier(args.run_id or default_run_id(started_at), "run_id")
    run_directory = Path(args.output_root) / run_id
    run_directory.mkdir(parents=True, exist_ok=False)
    tasks = load_tasks(args.tasks)
    runner = ReferenceTaskRunner(run_directory / "environments")
    results = [runner.run(task) for task in tasks]
    domains = Counter(result.domain for result in results)
    summary = {
        "experiment_id": "day3_smoke_local",
        "run_id": run_id,
        "created_at": started_at.isoformat(),
        "dataset_role": "day3_smoke",
        "task_count": len(results),
        "domain_counts": dict(sorted(domains.items())),
        "passed": sum(result.passed and result.reset_verified for result in results),
        "failed": sum(not (result.passed and result.reset_verified) for result in results),
        "results": [result.to_dict() for result in results],
    }
    output = run_directory / "task_results.json"
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    expected_domains = {
        "api_workflow": 2,
        "calculation_logic": 2,
        "data_analysis": 2,
        "file_retrieval": 2,
    }
    return 0 if len(results) >= 8 and domains == expected_domains and summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
