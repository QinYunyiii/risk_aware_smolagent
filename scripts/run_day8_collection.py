#!/usr/bin/env python3
"""Collect resumable Day 8 real-Qwen trajectories with local WSL tools."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import yaml

from risk_agent.agent import RemoteModelSettings, WorkerRouter
from risk_agent.agent.agent_factory import RemoteAPIClient, verify_tls_endpoint
from risk_agent.benchmark import generate_task_instances, load_task_templates, task_to_dict
from risk_agent.benchmark.schema import validate_identifier
from risk_agent.collection import (
    InitialTrajectoryRunner,
    is_retryable_pretool_infra,
    log_completeness,
    select_initial_tasks,
)
from risk_agent.monitoring import JsonlTraceWriter


TOOL_REQUIRED_FIELDS = (
    "experiment_id",
    "run_id",
    "task_id",
    "template_id",
    "instance_id",
    "seed",
    "model_id",
    "model_version",
    "prompt_version",
    "tool_version",
    "step_id",
    "tool_name",
    "tool_arguments",
    "tool_result",
    "error_type",
    "timestamp",
    "token_usage",
    "latency_seconds",
    "worker_id",
    "environment_id",
    "execution_location",
)


def default_run_id(started_at: datetime, smoke: bool) -> str:
    prefix = "day8_smoke" if smoke else "day8"
    return f"{prefix}_{started_at.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def catalog_hash(tasks: tuple) -> str:
    canonical = json.dumps(
        [task_to_dict(task) for task in tasks],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--collection-config", default="configs/day8_collection.yaml")
    parser.add_argument("--remote-config", default="configs/remote_model.yaml")
    parser.add_argument("--templates", default="configs/task_templates.yaml")
    parser.add_argument("--output-root", default="runs/day08")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()

    started_at = datetime.now(timezone.utc)
    config = yaml.safe_load(Path(args.collection_config).read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("Unsupported Day 8 collection configuration")
    run_id = validate_identifier(args.run_id or default_run_id(started_at, args.smoke), "run_id")
    run_directory = Path(args.output_root) / run_id
    if args.run_id is None:
        run_directory.mkdir(parents=True, exist_ok=False)
    else:
        run_directory.mkdir(parents=True, exist_ok=True)

    templates = load_task_templates(args.templates)
    all_tasks = generate_task_instances(templates)
    actual_catalog_hash = catalog_hash(all_tasks)
    expected_catalog_hash = str(config["catalog_sha256"])
    if actual_catalog_hash != expected_catalog_hash:
        raise RuntimeError(
            f"Catalog hash mismatch: expected {expected_catalog_hash}, got {actual_catalog_hash}"
        )
    selected = select_initial_tasks(all_tasks)
    if args.smoke:
        selected = selected[:2]

    settings = RemoteModelSettings.from_yaml(args.remote_config)
    router = WorkerRouter(settings.workers)
    preflight: list[dict[str, object]] = []
    for worker in settings.workers:
        tls_latency = verify_tls_endpoint(settings, worker)
        client = RemoteAPIClient(settings, worker)
        try:
            models = client.list_model_ids()
        finally:
            client.close()
        if settings.model_id not in models:
            raise RuntimeError(f"{worker.worker_id} does not serve {settings.model_id}")
        preflight.append(
            {
                "worker_id": worker.worker_id,
                "endpoint": worker.base_url,
                "tls_verified": True,
                "tls_latency_seconds": tls_latency,
                "model_id_present": True,
            }
        )
    (run_directory / "preflight.json").write_text(
        json.dumps(
            {
                "created_at": started_at.isoformat(),
                "catalog_sha256": actual_catalog_hash,
                "workers": preflight,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    task_path = run_directory / "task_trajectories.jsonl"
    task_writer = JsonlTraceWriter(task_path)
    existing = read_jsonl(task_path)
    attempts_by_task = Counter(str(record["task_id"]) for record in existing)
    latest_existing: dict[str, dict[str, object]] = {}
    for record in existing:
        latest_existing[str(record["task_id"])] = record
    completed_ids = {
        task_id
        for task_id, record in latest_existing.items()
        if not is_retryable_pretool_infra(record)
    }
    runner = InitialTrajectoryRunner(
        settings=settings,
        run_directory=run_directory,
        experiment_id=str(config["experiment_id"]),
        run_id=run_id,
        catalog_sha256=actual_catalog_hash,
        max_steps=int(config["agent"]["max_steps"]),
    )
    interrupted_before_tool = False
    for index, task in enumerate(selected):
        if task.task_id in completed_ids:
            print(f"skip completed task={task.task_id}", flush=True)
            continue
        worker = router.select(settings.workers[index % len(settings.workers)].worker_id)
        print(
            f"start task={index + 1}/{len(selected)} task_id={task.task_id} worker={worker.worker_id}",
            flush=True,
        )
        attempt_index = attempts_by_task[task.task_id] + 1
        record = runner.run_task(
            task=task, worker=worker, attempt_index=attempt_index
        )
        task_writer.append(record)
        attempts_by_task[task.task_id] += 1
        if not is_retryable_pretool_infra(record):
            completed_ids.add(task.task_id)
        print(
            f"done task_id={task.task_id} worker={worker.worker_id} "
            f"status={record['task_status']} validation={record['validation_passed']} "
            f"tools={record['tool_call_count']}",
            flush=True,
        )
        if is_retryable_pretool_infra(record):
            interrupted_before_tool = True
            print(
                "pre-tool infrastructure failure detected; stopping batch so the "
                "same run_id can resume safely after endpoint recovery",
                flush=True,
            )
            break

    all_attempt_records = read_jsonl(task_path)
    selected_ids = {task.task_id for task in selected}
    latest_by_task: dict[str, dict[str, object]] = {}
    for record in all_attempt_records:
        if record.get("task_id") in selected_ids:
            latest_by_task[str(record["task_id"])] = record
    records = [
        latest_by_task[task.task_id]
        for task in selected
        if task.task_id in latest_by_task
    ]
    logical_results_path = run_directory / "logical_task_results.json"
    logical_results_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": run_id,
                "catalog_sha256": actual_catalog_hash,
                "record_count": len(records),
                "records": records,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    tool_records = read_jsonl(run_directory / "tool_trajectories.jsonl")
    worker_requests = Counter(str(record["worker_id"]) for record in records)
    worker_tool_calls = Counter(str(record["worker_id"]) for record in tool_records)
    domains_with_tools = {
        str(record["domain"])
        for record in records
        if int(record.get("tool_call_count", 0)) > 0
    }
    task_completeness = log_completeness(records)
    tool_completeness = log_completeness(tool_records, TOOL_REQUIRED_FIELDS) if tool_records else 0.0
    expected_count = 2 if args.smoke else int(config["selection"]["expected_task_count"])
    checks = {
        "catalog_hash_matches": actual_catalog_hash == expected_catalog_hash,
        "expected_unique_task_count": len(records) == expected_count
        and len({record["task_id"] for record in records}) == expected_count,
        "tls_verified_both_workers": len(preflight) == 2
        and all(item["tls_verified"] for item in preflight),
        "both_workers_have_real_task_requests": all(
            worker_requests[worker.worker_id] > 0 for worker in settings.workers
        ),
        "both_workers_have_local_tool_calls": all(
            worker_tool_calls[worker.worker_id] > 0 for worker in settings.workers
        ),
        "failure_origin_recorded": all(bool(record.get("failure_origin")) for record in records),
        "task_log_completeness_at_least_95_percent": task_completeness
        >= float(config["collection"]["minimum_log_completeness"]),
        "tool_log_completeness_at_least_95_percent": tool_completeness
        >= float(config["collection"]["minimum_log_completeness"]),
        "tool_execution_is_local_wsl": all(
            record.get("execution_location") == "local_wsl" for record in tool_records
        ),
    }
    if not args.smoke:
        checks["all_four_domains_have_tool_calls"] = domains_with_tools == {
            "data_analysis",
            "file_retrieval",
            "api_workflow",
            "calculation_logic",
        }
    summary = {
        "schema_version": 1,
        "experiment_id": config["experiment_id"],
        "run_id": run_id,
        "smoke": args.smoke,
        "created_at": started_at.isoformat(),
        "catalog_sha256": actual_catalog_hash,
        "selected_task_count": len(selected),
        "record_count": len(records),
        "raw_attempt_record_count": len(all_attempt_records),
        "interrupted_before_tool": interrupted_before_tool,
        "worker_request_counts": dict(sorted(worker_requests.items())),
        "worker_tool_call_counts": dict(sorted(worker_tool_calls.items())),
        "status_counts": dict(sorted(Counter(str(record["task_status"]) for record in records).items())),
        "validation_passed": sum(bool(record["validation_passed"]) for record in records),
        "task_log_completeness": task_completeness,
        "tool_log_completeness": tool_completeness,
        "checks": checks,
        "passed": all(checks.values()),
        "preflight": preflight,
        "artifacts": {
            "tasks": str(task_path.resolve()),
            "tools": str((run_directory / "tool_trajectories.jsonl").resolve()),
            "preflight": str((run_directory / "preflight.json").resolve()),
            "logical_tasks": str(logical_results_path.resolve()),
        },
    }
    (run_directory / "collection_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
