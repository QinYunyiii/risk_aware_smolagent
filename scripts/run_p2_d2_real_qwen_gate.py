#!/usr/bin/env python3
"""Run the explicitly authorized P2-D2 real-Qwen checkpoint Gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any

import yaml

from risk_agent.agent.agent_factory import RemoteAPIClient, verify_tls_endpoint
from risk_agent.agent.worker_router import RemoteModelSettings, WorkerConfig
from risk_agent.benchmark import generate_task_instances, load_task_templates, task_to_dict
from risk_agent.benchmark.schema import validate_identifier
from risk_agent.collection.day5_gate import (
    canonical_task_catalog_hash,
    create_unique_run_directory,
    sha256_file,
    split_manifest_hash,
)
from risk_agent.collection.p2_d2_real_qwen_gate import (
    FIXED_TASK_IDS,
    P2D2RealQwenGateRunner,
    RESEARCH_FLAGS,
    select_p2_d2_tasks,
)


REMOTE_MODES = {"remote-preflight", "gate", "single-t1-diagnostic"}
EXPECTED_WEIGHTS = {
    "config.json": "f7c4eadfbbf522470667b797a3c89be2524832d2d599797248dc304fff447c30",
    "model.safetensors.index.json": "f9fdbcb91c23971c13ec5d5f2573d2349e8f61f2f049371ec699281748fdb1bc",
    "model-00001-of-00005.safetensors": "31d6a825ae35f11fb85b195b4c42c146c051e446433125a215336abdf95cbf5f",
    "model-00002-of-00005.safetensors": "5991236cea6fe21f3d43cab0f0e84448734fbbe0789816202989f2ddc9d18282",
    "model-00003-of-00005.safetensors": "c5185c4794be2d8a9784d5753c9922db38df478ce11f9ed0b415b7304d896836",
    "model-00004-of-00005.safetensors": "b5ee7de71fbf17db3d5704e0c8f2bc7d005ca9e1d7ca2aeb19827b0cfcaa917a",
    "model-00005-of-00005.safetensors": "20c2d6366ab85c90786ccdd829cd2b9e7d30ef3b2ebbb998280e7e4014b542ff",
}


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected JSON object: {path}")
    return value


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _default_run_id(mode: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"p2_d2_{mode.replace('-', '_')}_{stamp}"


def _artifact_manifest(root: Path) -> dict[str, Any]:
    artifacts = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "artifact_manifest.json":
            continue
        artifacts.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return {"artifact_count": len(artifacts), "artifacts": artifacts}


def _load_gate_config(path: str | Path) -> dict[str, Any]:
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("P2-D2 Gate configuration must be an object")
    limits = value.get("limits", {})
    checks = {
        "protocol": value.get("protocol_id") == "risk-continue-v2.0.0",
        "experiment": value.get("experiment_id") == "p2_d2_real_qwen_gate",
        "tasks": tuple(value.get("fixed_task_ids", ())) == FIXED_TASK_IDS,
        "faults_disabled": value.get("fault_injection_enabled") is False,
        "agent_steps": limits.get("max_agent_steps") == 10,
        "model_calls": limits.get("max_model_calls") == 10,
        "tool_calls": limits.get("max_tool_calls") == 12,
        "total_tokens": limits.get("max_total_tokens") == 50_000,
        "completion_tokens": limits.get("max_completion_tokens") == 256,
        "context_tokens": limits.get("max_context_tokens") == 8_192,
        "request_timeout": limits.get("request_timeout_seconds") == 120,
        "wall_clock": limits.get("task_wall_clock_timeout_seconds") == 900,
        "research_flags": value.get("research_flags") == RESEARCH_FLAGS,
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        raise RuntimeError(f"Frozen P2-D2 configuration mismatch: {failed}")
    return value


def _catalog_audit(tasks: tuple[Any, ...], templates: tuple[Any, ...]) -> dict[str, Any]:
    selected = select_p2_d2_tasks(tasks)
    task_documents = [task_to_dict(task) for task in tasks]
    fixed_ids = [task.task_id for task in selected]
    return {
        "template_count": len(templates),
        "task_count": len(tasks),
        "template_split_counts": dict(Counter(item.split.value for item in templates)),
        "task_split_counts": dict(Counter(item.split.value for item in tasks)),
        "domain_counts": dict(Counter(item.domain.value for item in tasks)),
        "canonical_catalog_hash": canonical_task_catalog_hash(tasks),
        "generated_task_instances_hash": _canonical_hash(task_documents),
        "split_manifest_hash": split_manifest_hash(tasks),
        "fixed_task_ids_hash": _canonical_hash(fixed_ids),
        "fixed_tasks": [
            {
                "task_id": task.task_id,
                "template_id": task.template_id,
                "instance_id": task.instance_id,
                "domain": task.domain.value,
                "split": task.split.value,
                "seed": task.seed,
                "step_count": len(task.reference_steps),
            }
            for task in selected
        ],
    }


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise RuntimeError("GPU sample timestamps must include timezone")
    return parsed


def _validate_remote_evidence(value: dict[str, Any]) -> dict[str, Any]:
    required_true = (
        "remote_operations_authorized",
        "https_service_authorized",
        "service_ownership_verified",
        "gpu_mapping_verified",
        "model_version_verified",
        "weight_identity_verified",
    )
    failed = [field for field in required_true if value.get(field) is not True]
    if failed:
        raise RuntimeError(f"Remote evidence is missing required proof: {failed}")
    samples = value.get("gpu_samples_utc")
    if not isinstance(samples, list) or len(samples) < 2:
        raise RuntimeError("At least two GPU samples are required")
    times = sorted(_parse_time(str(item)) for item in samples)
    if (times[-1] - times[0]).total_seconds() < 60:
        raise RuntimeError("GPU samples must be separated by at least 60 seconds")
    mapping = value.get("worker_gpu_mapping")
    if not isinstance(mapping, dict) or set(mapping) != {"worker-0", "worker-1"}:
        raise RuntimeError("Worker/GPU mapping evidence is incomplete")
    if value.get("model_weight_identity") != EXPECTED_WEIGHTS:
        raise RuntimeError("Current model weight hashes differ from the frozen identity")
    versions = value.get("versions", {})
    if versions != {
        "vllm": "0.34.0",
        "torch": "2.14.0+cu132",
        "transformers": "5.22.2",
    }:
        raise RuntimeError("Remote model-serving versions differ from the frozen identity")
    return value


def _api_preflight(
    settings: RemoteModelSettings,
    *,
    workers: tuple[WorkerConfig, ...] | None = None,
) -> list[dict[str, Any]]:
    records = []
    for worker in workers or settings.workers:
        tls_latency = verify_tls_endpoint(settings, worker)
        client = RemoteAPIClient(settings, worker)
        try:
            model_ids = client.list_model_ids()
        finally:
            client.close()
        if settings.model_id not in model_ids:
            raise RuntimeError(f"{worker.worker_id} does not serve {settings.model_id}")
        records.append(
            {
                "worker_id": worker.worker_id,
                "endpoint": worker.base_url,
                "tls_verified": True,
                "tls_latency_seconds": tls_latency,
                "model_ids": model_ids,
                "expected_model_present": True,
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
            "gate",
            "single-t1-diagnostic",
        ),
        default="local-preflight",
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--gate-config", default="configs/p2_d2_real_qwen_gate.yaml")
    parser.add_argument("--remote-config", default="configs/remote_model.yaml")
    parser.add_argument("--remote-evidence", default=None)
    parser.add_argument("--authorize-remote", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode in REMOTE_MODES and not args.authorize_remote:
        raise RuntimeError("Remote modes require explicit approval and --authorize-remote")
    gate_config = _load_gate_config(args.gate_config)
    settings = RemoteModelSettings.from_yaml(args.remote_config)
    if settings.model_id != "Qwen/Qwen3-8B" or settings.model_version != (
        "Qwen3-8B-modelscope-snapshot"
    ):
        raise RuntimeError("Remote model identity differs from the frozen Gate")
    if settings.inference != {
        "seed": 20261118,
        "temperature": 0.0,
        "max_tokens": 256,
        "chat_template_kwargs": {"enable_thinking": False},
    }:
        raise RuntimeError("Remote inference configuration differs from the frozen Gate")
    diagnostic_workers = tuple(
        worker for worker in settings.workers if worker.worker_id == "worker-0"
    )
    if args.mode == "single-t1-diagnostic" and len(diagnostic_workers) != 1:
        raise RuntimeError("Single T1 diagnostic requires exactly one worker-0")
    templates = load_task_templates(gate_config["catalog_path"])
    tasks = generate_task_instances(templates, 2)
    catalog_audit = _catalog_audit(tasks, templates)
    if catalog_audit["canonical_catalog_hash"] != gate_config["canonical_catalog_hash"]:
        raise RuntimeError("Canonical P2-D1 catalog hash mismatch")
    run_id = validate_identifier(args.run_id or _default_run_id(args.mode), "run_id")
    output_root = args.output_root or (
        "runs/phase2_p2_d2_t1_diagnostic"
        if args.mode == "single-t1-diagnostic"
        else "runs/phase2_p2_d2_real_qwen"
    )
    run_directory = create_unique_run_directory(output_root, run_id)
    disk = shutil.disk_usage(run_directory)
    manifest = {
        "schema_version": 1,
        "protocol_id": gate_config["protocol_id"],
        "experiment_id": gate_config["experiment_id"],
        "run_id": run_id,
        "mode": args.mode,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_head(),
        "catalog_audit": catalog_audit,
        "model": {
            "model_id": settings.model_id,
            "model_version": settings.model_version,
            "prompt_version": settings.prompt_version,
            "inference": settings.inference,
            "workers": [
                {"worker_id": item.worker_id, "endpoint": item.base_url}
                for item in (
                    diagnostic_workers
                    if args.mode == "single-t1-diagnostic"
                    else settings.workers
                )
            ],
        },
        "diagnostic_scope": (
            {
                "task_ids": [FIXED_TASK_IDS[0]],
                "worker_ids": ["worker-0"],
                "max_attempts": 1,
                "automatic_retries": 0,
                "replacement_tasks": 0,
                "t2_t5_allowed": False,
            }
            if args.mode == "single-t1-diagnostic"
            else None
        ),
        "versions": {
            "smolagents": version("smolagents"),
            "openai": version("openai"),
            "httpx2": version("httpx2"),
        },
        "config_sha256": {
            "gate": sha256_file(args.gate_config),
            "remote": sha256_file(args.remote_config),
            "catalog": sha256_file(gate_config["catalog_path"]),
        },
        "implementation_sha256": {
            "runner": sha256_file(
                "src/risk_agent/collection/p2_d2_real_qwen_gate.py"
            ),
            "entrypoint": sha256_file("scripts/run_p2_d2_real_qwen_gate.py"),
            "checkpoint": sha256_file("src/risk_agent/checkpoint/v2_manager.py"),
        },
        "local_disk_free_bytes": disk.free,
        "local_disk_at_least_20gb": disk.free >= 20 * 1024**3,
        **RESEARCH_FLAGS,
    }
    _write_json(run_directory / "run_manifest.json", manifest)
    _write_json(
        run_directory / "task_selection.json",
        {
            **catalog_audit,
            **(
                {
                    "diagnostic_task_ids": [FIXED_TASK_IDS[0]],
                    "diagnostic_worker_ids": ["worker-0"],
                }
                if args.mode == "single-t1-diagnostic"
                else {}
            ),
        },
    )
    try:
        if not manifest["local_disk_at_least_20gb"]:
            raise RuntimeError("Local disk has less than 20 GiB free")
        if args.mode == "local-preflight":
            summary = {
                "run_id": run_id,
                "mode": args.mode,
                "network_requests_made": 0,
                "passed": True,
                "status": "PASS",
                **RESEARCH_FLAGS,
            }
            _write_json(run_directory / "gate_summary.json", summary)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        if args.remote_evidence is None:
            raise RuntimeError("Remote modes require --remote-evidence")
        evidence = _validate_remote_evidence(_read_json(args.remote_evidence))
        workers = _api_preflight(
            settings,
            workers=(
                diagnostic_workers
                if args.mode == "single-t1-diagnostic"
                else settings.workers
            ),
        )
        remote_preflight = {
            "evidence": evidence,
            "api_workers": workers,
            "strict_tls": True,
            "api_key_recorded": False,
            "service_changes_made": False,
            "remote_processes_started": 0,
            **RESEARCH_FLAGS,
        }
        _write_json(run_directory / "remote_preflight.json", remote_preflight)
        if args.mode == "remote-preflight":
            summary = {
                "run_id": run_id,
                "mode": args.mode,
                "passed": True,
                "status": "PASS",
                **RESEARCH_FLAGS,
            }
            _write_json(run_directory / "gate_summary.json", summary)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        runner = P2D2RealQwenGateRunner(
            settings=settings,
            run_directory=run_directory,
            run_id=run_id,
            catalog_hash=catalog_audit["canonical_catalog_hash"],
            split_hash=catalog_audit["split_manifest_hash"],
            verifier_content_hash=sha256_file("src/risk_agent/benchmark/verifier.py"),
        )
        if args.mode == "single-t1-diagnostic":
            summary = runner.run_single_t1(
                tasks=tasks,
                worker=diagnostic_workers[0],
            )
        else:
            summary = runner.run_fixed_gate(tasks=tasks, workers=settings.workers)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["passed"] else 1
    except Exception as exc:
        summary_path = run_directory / "gate_summary.json"
        if not summary_path.exists():
            _write_json(
                summary_path,
                {
                    "run_id": run_id,
                    "status": "BLOCKED",
                    "passed": False,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    **RESEARCH_FLAGS,
                },
            )
        raise
    finally:
        _write_json(run_directory / "artifact_manifest.json", _artifact_manifest(run_directory))


if __name__ == "__main__":
    raise SystemExit(main())
