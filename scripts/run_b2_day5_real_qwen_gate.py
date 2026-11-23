#!/usr/bin/env python3
"""Run the explicitly authorized B2 Day 5 Qwen checkpoint gate."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any

import yaml

from risk_agent.agent import RemoteModelSettings, WorkerRouter
from risk_agent.agent.agent_factory import RemoteAPIClient, verify_tls_endpoint
from risk_agent.benchmark import generate_task_instances, load_task_templates
from risk_agent.benchmark.schema import validate_identifier
from risk_agent.collection.day5_gate import (
    Day5GateRunner,
    EXPERIMENT_ID,
    NoEligibleCheckpointError,
    PROTOCOL_ID,
    RUN_RESEARCH_FLAGS,
    canonical_task_catalog_hash,
    create_unique_run_directory,
    select_day5_candidates,
    sha256_file,
    split_manifest_hash,
)
from risk_agent.monitoring import JsonlTraceWriter


REMOTE_MODES = {"remote-preflight", "single-checkpoint", "five-checkpoint-gate"}


def default_run_id(started_at: datetime, mode: str) -> str:
    normalized = mode.replace("-", "_")
    return f"b2_day5_{normalized}_{started_at.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _load_remote_evidence(path: str | None) -> dict[str, Any] | None:
    if path is None:
        return None
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Remote evidence must be a JSON object")
    allowed = {
        "remote_operations_authorized",
        "https_service_authorized",
        "model_version_verified",
        "gpu_mapping_verified",
        "vllm_version",
        "model_weight_identity",
        "checked_at",
        "worker_gpu_mapping",
        "service_ownership",
        "resource_check_evidence_path",
    }
    extra = set(value) - allowed
    if extra:
        raise ValueError(f"Remote evidence contains unsupported fields: {sorted(extra)}")
    return value


def _require_execution_evidence(value: dict[str, Any] | None) -> None:
    required_true = {
        "remote_operations_authorized",
        "https_service_authorized",
        "model_version_verified",
        "gpu_mapping_verified",
    }
    if value is None or any(value.get(field) is not True for field in required_true):
        raise RuntimeError(
            "single/five execution requires --remote-evidence with explicit authorization, "
            "HTTPS service ownership, model-version and GPU-mapping verification"
        )


def _manifest(
    *,
    args: argparse.Namespace,
    run_id: str,
    settings: RemoteModelSettings,
    catalog_hash: str,
    split_hash: str,
    remote_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    config_paths = {
        "remote_model": Path(args.config),
        "collection": Path(args.collection_config),
        "task_templates": Path(args.templates),
        "checkpoint_schema": Path("docs/phase2/checkpoint_schema_v2.json"),
    }
    disk = shutil.disk_usage(Path(args.output_root).expanduser().resolve().parent)
    return {
        "schema_version": 1,
        "protocol_id": PROTOCOL_ID,
        "experiment_id": EXPERIMENT_ID,
        "run_id": run_id,
        "mode": args.mode,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_head(),
        "model": {
            "model_id": settings.model_id,
            "configured_model_version": settings.model_version,
            "prompt_version": settings.prompt_version,
            "generation_config": dict(settings.inference),
            "workers": [
                {"worker_id": worker.worker_id, "endpoint": worker.base_url}
                for worker in settings.workers
            ],
        },
        "versions": {
            "smolagents": version("smolagents"),
            "openai": version("openai"),
            "httpx2": version("httpx2"),
            "vllm": remote_evidence.get("vllm_version") if remote_evidence else None,
        },
        "config_sha256": {
            name: sha256_file(path) for name, path in config_paths.items()
        },
        "catalog_hash": catalog_hash,
        "split_manifest_hash": split_hash,
        "local_disk_free_bytes": disk.free,
        "local_disk_at_least_20gb": disk.free >= 20 * 1024**3,
        "context_budget_observability": {
            "max_context_tokens": 8_192,
            "exact_pre_request_count_available": False,
            "reason": "provider tokenizer count is not exposed before delegation",
            "silent_truncation_or_summarization_enabled": False,
        },
        "remote_evidence": remote_evidence,
        **RUN_RESEARCH_FLAGS,
    }


def _validate_frozen_config(
    *, settings: RemoteModelSettings, collection_config: str | Path
) -> dict[str, bool]:
    collection = yaml.safe_load(Path(collection_config).read_text(encoding="utf-8"))
    if not isinstance(collection, dict):
        raise ValueError("Collection configuration must be a mapping")
    limits = collection.get("limits", {})
    checks = {
        "protocol_id": collection.get("protocol_id") == PROTOCOL_ID,
        "protocol_frozen": collection.get("protocol_status") == "frozen",
        "model_id": settings.model_id == "Qwen/Qwen3-8B",
        "model_version": settings.model_version == "Qwen3-8B-modelscope-snapshot",
        "seed": settings.inference.get("seed") == 20261118,
        "temperature": settings.inference.get("temperature") == 0.0,
        "max_completion_tokens": settings.inference.get("max_tokens") == 256,
        "thinking_disabled": settings.inference.get("chat_template_kwargs")
        == {"enable_thinking": False},
        "router": settings.router_strategy == "round_robin",
        "two_workers": len(settings.workers) == 2,
        "agent_steps": limits.get("max_agent_steps") == 10,
        "model_calls": limits.get("max_model_calls") == 10,
        "tool_calls": limits.get("max_tool_calls") == 12,
        "total_tokens": limits.get("max_total_tokens") == 50_000,
        "context_tokens": limits.get("max_context_tokens") == 8_192,
        "wall_clock": limits.get("task_wall_clock_timeout_seconds") == 900,
        "request_timeout": limits.get("model_request_timeout_seconds") == 120,
    }
    if not all(checks.values()):
        failed = sorted(name for name, passed in checks.items() if not passed)
        raise RuntimeError(f"Frozen Day 5 configuration mismatch: {failed}")
    return checks


def _remote_preflight(
    settings: RemoteModelSettings,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for worker in settings.workers:
        tls_latency = verify_tls_endpoint(settings, worker)
        client = RemoteAPIClient(settings, worker)
        try:
            models = client.list_model_ids()
        finally:
            client.close()
        if settings.model_id not in models:
            raise RuntimeError(f"{worker.worker_id} does not serve {settings.model_id}")
        records.append(
            {
                "worker_id": worker.worker_id,
                "endpoint": worker.base_url,
                "tls_verified": True,
                "tls_latency_seconds": tls_latency,
                "model_id_present": True,
            }
        )
    return records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=(
            "local-preflight",
            "remote-preflight",
            "single-checkpoint",
            "five-checkpoint-gate",
        ),
        default="local-preflight",
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--output-root", default="runs/phase2_b2_day5")
    parser.add_argument("--config", default="configs/remote_model.yaml")
    parser.add_argument("--collection-config", default="configs/phase2_risk_collection.yaml")
    parser.add_argument("--templates", default="configs/task_templates.yaml")
    parser.add_argument("--remote-evidence", default=None)
    parser.add_argument(
        "--authorize-remote",
        action="store_true",
        help="Acknowledge separate user authorization for network/remote operations.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode in REMOTE_MODES and not args.authorize_remote:
        raise RuntimeError(
            "Remote modes are disabled until --authorize-remote is supplied after explicit user approval"
        )

    started_at = datetime.now(timezone.utc)
    run_id = validate_identifier(
        args.run_id or default_run_id(started_at, args.mode),
        "run_id",
    )
    settings = RemoteModelSettings.from_yaml(args.config)
    frozen_config_checks = _validate_frozen_config(
        settings=settings,
        collection_config=args.collection_config,
    )
    tasks = generate_task_instances(load_task_templates(args.templates))
    candidates = select_day5_candidates(tasks)
    catalog_hash = canonical_task_catalog_hash(tasks)
    split_hash = split_manifest_hash(tasks)
    remote_evidence = _load_remote_evidence(args.remote_evidence)
    if args.mode in {"single-checkpoint", "five-checkpoint-gate"}:
        _require_execution_evidence(remote_evidence)

    run_directory = create_unique_run_directory(args.output_root, run_id)
    manifest = _manifest(
        args=args,
        run_id=run_id,
        settings=settings,
        catalog_hash=catalog_hash,
        split_hash=split_hash,
        remote_evidence=remote_evidence,
    )
    _write_json(run_directory / "run_manifest.json", manifest)

    if args.mode == "local-preflight":
        summary = {
            "mode": args.mode,
            "run_id": run_id,
            "network_requests_made": 0,
            "candidate_task_count": len(candidates),
            "first_five_candidates": [task.task_id for task in candidates[:5]],
            "research_flags": RUN_RESEARCH_FLAGS,
            "frozen_config_checks": frozen_config_checks,
            "passed": bool(manifest["local_disk_at_least_20gb"])
            and all(frozen_config_checks.values()),
        }
        _write_json(run_directory / "local_preflight.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["passed"] else 1

    preflight = _remote_preflight(settings)
    _write_json(
        run_directory / "remote_preflight.json",
        {
            "workers": preflight,
            "api_identity_only": True,
            "configured_model_version_is_not_remote_weight_proof": True,
            "remote_evidence": remote_evidence,
        },
    )
    if args.mode == "remote-preflight":
        print(json.dumps({"run_id": run_id, "workers": preflight}, ensure_ascii=False, indent=2))
        return 0

    router = WorkerRouter(settings.workers)
    runner = Day5GateRunner(
        settings=settings,
        run_directory=run_directory,
        run_id=run_id,
        catalog_hash=catalog_hash,
        split_hash=split_hash,
        verifier_content_hash=sha256_file("src/risk_agent/benchmark/verifier.py"),
    )
    attempts = JsonlTraceWriter(run_directory / "source_attempts.jsonl")
    failures = JsonlTraceWriter(run_directory / "failures.jsonl")
    checkpoint_index = JsonlTraceWriter(run_directory / "checkpoint_index.jsonl")
    restore_audit = JsonlTraceWriter(run_directory / "restore_audit.jsonl")
    model_audit = JsonlTraceWriter(run_directory / "model_request_audit.jsonl")
    budget_audit = JsonlTraceWriter(run_directory / "budget_audit.jsonl")
    target = 1 if args.mode == "single-checkpoint" else 5
    passed_records: list[dict[str, Any]] = []
    attempt_count = 0
    for task in candidates:
        attempt_count += 1
        worker = router.select(settings.workers[(attempt_count - 1) % len(settings.workers)].worker_id)
        try:
            record = runner.run_task(
                task=task,
                worker=worker,
                attempt_index=1,
            )
            attempts.append(record)
            checkpoint_index.append(
                {
                    "run_id": run_id,
                    "task_run_id": record["task_run_id"],
                    "task_id": record["task_id"],
                    "checkpoint_id": record["checkpoint_id"],
                    "checkpoint_path": record["checkpoint_path"],
                    "hashes": record["checkpoint_hashes"],
                }
            )
            restore_audit.append(
                {
                    "run_id": run_id,
                    "task_run_id": record["task_run_id"],
                    "checkpoint_id": record["checkpoint_id"],
                    "hashes_match": record["hashes_match"],
                    "tool_calls_added": record["restore_tool_calls_added"],
                }
            )
            model_audit.append(
                {
                    "run_id": run_id,
                    "task_run_id": record["task_run_id"],
                    "worker_id": record["worker_id"],
                    "source_input_hash": record["source_next_input_hash"],
                    "checkpoint_input_hash": record["checkpoint_next_input_hash"],
                    "restored_input_hash": record["restored_actual_input_hash"],
                    "source_input_canonical": record["source_next_input_canonical"],
                    "checkpoint_input_canonical": record[
                        "checkpoint_next_input_canonical"
                    ],
                    "restored_input_canonical": record[
                        "restored_actual_input_canonical"
                    ],
                    "controlled_response": record["controlled_response"],
                    "controlled_request_usage": record["controlled_request_usage"],
                }
            )
            budget_audit.append(
                {
                    "run_id": run_id,
                    "task_run_id": record["task_run_id"],
                    "source_model_calls": record["source_model_calls"],
                    "restored_model_calls": record["restored_model_calls"],
                    "source_tool_calls": record["source_tool_calls"],
                    "restore_tool_calls_added": record["restore_tool_calls_added"],
                    "source_request_usage": record["source_request_usage"],
                    "controlled_request_usage": record["controlled_request_usage"],
                }
            )
            if record["passed"]:
                passed_records.append(record)
        except NoEligibleCheckpointError as exc:
            failure = {
                "protocol_id": PROTOCOL_ID,
                "run_id": run_id,
                "task_id": task.task_id,
                "worker_id": worker.worker_id,
                "error_type": type(exc).__name__,
                "failure_reason": str(exc),
                "outcome_status": "checkpoint_rejected",
                **RUN_RESEARCH_FLAGS,
            }
            failures.append(failure)
            attempts.append({**failure, "record_type": "b2_day5_attempt", "passed": False})
            continue
        except Exception as exc:
            failure = {
                "protocol_id": PROTOCOL_ID,
                "run_id": run_id,
                "task_id": task.task_id,
                "worker_id": worker.worker_id,
                "error_type": type(exc).__name__,
                "failure_reason": str(exc),
                **RUN_RESEARCH_FLAGS,
            }
            failures.append(failure)
            attempts.append({**failure, "record_type": "b2_day5_attempt", "passed": False})
            break
        if not record["passed"]:
            break
        if len(passed_records) >= target:
            break

    summary = {
        "protocol_id": PROTOCOL_ID,
        "run_id": run_id,
        "mode": args.mode,
        "target": target,
        "attempt_count": attempt_count,
        "passed_count": len(passed_records),
        "passed_task_ids": [record["task_id"] for record in passed_records],
        "passed": len(passed_records) == target,
        "status": "PASS" if len(passed_records) == target else "BLOCKED",
        **RUN_RESEARCH_FLAGS,
    }
    _write_json(run_directory / "gate_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
