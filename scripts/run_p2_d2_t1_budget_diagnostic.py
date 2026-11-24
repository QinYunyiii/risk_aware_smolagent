#!/usr/bin/env python3
"""Run the versioned P2-D2 T1 diagnostic with a strict 1024-token cap."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from risk_agent.agent.agent_factory import RemoteAPIClient, verify_tls_endpoint
from risk_agent.agent.worker_router import RemoteModelSettings, WorkerConfig
from risk_agent.agent.agent_state_adapter import canonical_hash
from risk_agent.benchmark import generate_task_instances, load_task_templates
from risk_agent.benchmark.schema import validate_identifier
from risk_agent.collection.day5_gate import create_unique_run_directory, sha256_file
from risk_agent.collection.p2_d2_completion_budget import (
    DIAGNOSTIC_MODE_1024,
    EXPERIMENT_CONFIG_VERSION_1024,
    EXPERIMENT_ID_1024,
    GENERATION_CONFIG_VERSION_1024,
    T1_TASK_ID,
    load_completion_budget_profile,
    validate_completion_budget_settings,
)
from risk_agent.collection.p2_d2_real_qwen_gate import (
    P2D2RealQwenGateRunner,
    RESEARCH_FLAGS,
)
try:  # Direct script execution puts ``scripts/`` rather than the repo root on sys.path.
    from scripts.run_p2_d2_real_qwen_gate import (
        _artifact_manifest,
        _catalog_audit,
        _git_head,
        _read_json,
        _validate_remote_evidence,
        _write_json,
    )
except ModuleNotFoundError:  # pragma: no cover - exercised by CLI subprocess tests
    from run_p2_d2_real_qwen_gate import (
        _artifact_manifest,
        _catalog_audit,
        _git_head,
        _read_json,
        _validate_remote_evidence,
        _write_json,
    )


REMOTE_MODE = DIAGNOSTIC_MODE_1024
DEFAULT_OUTPUT_ROOT = "runs/phase2_p2_d2_t1_budget_1024"


def _default_run_id(mode: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"p2_d2_t1_1024_{mode.replace('-', '_')}_{stamp}"


def _worker_zero(settings: RemoteModelSettings) -> WorkerConfig:
    workers = tuple(worker for worker in settings.workers if worker.worker_id == "worker-0")
    if len(workers) != 1:
        raise RuntimeError("1024-token T1 diagnostic requires exactly one worker-0")
    return workers[0]


def _api_preflight_worker_zero(
    settings: RemoteModelSettings, worker: WorkerConfig
) -> dict[str, object]:
    tls_latency = verify_tls_endpoint(settings, worker)
    client = RemoteAPIClient(settings, worker)
    try:
        model_ids = client.list_model_ids()
    finally:
        client.close()
    if settings.model_id not in model_ids:
        raise RuntimeError("worker-0 does not serve the frozen model ID")
    return {
        "worker_id": worker.worker_id,
        "endpoint": worker.base_url,
        "tls_verified": True,
        "tls_latency_seconds": tls_latency,
        "model_ids": model_ids,
        "expected_model_present": True,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("local-preflight", REMOTE_MODE),
        default="local-preflight",
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--output-root", default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--experiment-config", default="configs/p2_d2_t1_budget_1024.yaml"
    )
    parser.add_argument(
        "--remote-config", default="configs/remote_model_p2_d2_1024.yaml"
    )
    parser.add_argument("--remote-evidence", default=None)
    parser.add_argument("--authorize-remote", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode == REMOTE_MODE and not args.authorize_remote:
        raise RuntimeError(
            "1024-token remote diagnostic requires explicit approval and "
            "--authorize-remote"
        )

    profile = load_completion_budget_profile(args.experiment_config)
    settings = RemoteModelSettings.from_yaml(args.remote_config)
    validate_completion_budget_settings(
        settings,
        candidate_config=args.remote_config,
    )
    worker = _worker_zero(settings)
    templates = load_task_templates(profile.catalog_path)
    tasks = generate_task_instances(templates, 2)
    catalog_audit = _catalog_audit(tasks, templates)
    if catalog_audit["canonical_catalog_hash"] != profile.canonical_catalog_hash:
        raise RuntimeError("Canonical P2-D1 catalog hash mismatch")
    if catalog_audit["fixed_tasks"][0]["task_id"] != T1_TASK_ID:
        raise RuntimeError("Catalog audit did not select the frozen T1 first")

    run_id = validate_identifier(args.run_id or _default_run_id(args.mode), "run_id")
    run_directory = create_unique_run_directory(args.output_root, run_id)
    disk = shutil.disk_usage(run_directory)
    manifest = {
        "schema_version": 1,
        "protocol_id": profile.protocol_id,
        "experiment_id": profile.experiment_id,
        "experiment_config_version": profile.experiment_config_version,
        "generation_config_version": profile.generation_config_version,
        "run_id": run_id,
        "mode": args.mode,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_head(),
        "catalog_audit": catalog_audit,
        "model": {
            "model_id": settings.model_id,
            "model_version": settings.model_version,
            "prompt_version": settings.prompt_version,
            "generation_config_version": GENERATION_CONFIG_VERSION_1024,
            "inference": settings.inference,
            "generation_config_hash": canonical_hash(settings.inference),
            "workers": [
                {"worker_id": worker.worker_id, "endpoint": worker.base_url}
            ],
        },
        "changed_factor": {
            "name": "max_completion_tokens",
            "baseline": 256,
            "diagnostic": 1024,
        },
        "diagnostic_scope": {
            "task_ids": [T1_TASK_ID],
            "worker_ids": ["worker-0"],
            "max_attempts": 1,
            "automatic_retries": 0,
            "replacement_tasks": 0,
            "t2_t5_allowed": False,
        },
        "limits": dict(profile.limits),
        "versions": {
            "smolagents": version("smolagents"),
            "openai": version("openai"),
            "httpx2": version("httpx2"),
        },
        "config_sha256": {
            "experiment_1024": sha256_file(args.experiment_config),
            "remote_1024": sha256_file(args.remote_config),
            "baseline_gate_256": profile.baseline_gate_config_sha256,
            "baseline_remote_256": profile.baseline_remote_model_sha256,
            "catalog": sha256_file(profile.catalog_path),
        },
        "implementation_sha256": {
            "runner": sha256_file(
                "src/risk_agent/collection/p2_d2_real_qwen_gate.py"
            ),
            "entrypoint": sha256_file(
                "scripts/run_p2_d2_t1_budget_diagnostic.py"
            ),
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
            "diagnostic_task_ids": [T1_TASK_ID],
            "diagnostic_worker_ids": ["worker-0"],
            "experiment_id": EXPERIMENT_ID_1024,
            "experiment_config_version": EXPERIMENT_CONFIG_VERSION_1024,
        },
    )
    try:
        if not manifest["local_disk_at_least_20gb"]:
            raise RuntimeError("Local disk has less than 20 GiB free")
        if args.mode == "local-preflight":
            summary = {
                "run_id": run_id,
                "mode": args.mode,
                "experiment_id": EXPERIMENT_ID_1024,
                "experiment_config_version": EXPERIMENT_CONFIG_VERSION_1024,
                "generation_config_version": GENERATION_CONFIG_VERSION_1024,
                "completion_token_limit": 1024,
                "network_requests_made": 0,
                "passed": True,
                "status": "PASS",
                **RESEARCH_FLAGS,
            }
            _write_json(run_directory / "gate_summary.json", summary)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        if args.remote_evidence is None:
            raise RuntimeError("Remote diagnostic requires --remote-evidence")
        evidence = _validate_remote_evidence(_read_json(args.remote_evidence))
        remote_preflight = {
            "evidence": evidence,
            "api_workers": [_api_preflight_worker_zero(settings, worker)],
            "strict_tls": True,
            "api_key_recorded": False,
            "service_changes_made": False,
            "remote_processes_started": 0,
            **RESEARCH_FLAGS,
        }
        _write_json(run_directory / "remote_preflight.json", remote_preflight)
        runner = P2D2RealQwenGateRunner(
            settings=settings,
            run_directory=run_directory,
            run_id=run_id,
            catalog_hash=catalog_audit["canonical_catalog_hash"],
            split_hash=catalog_audit["split_manifest_hash"],
            verifier_content_hash=sha256_file("src/risk_agent/benchmark/verifier.py"),
            experiment_id=EXPERIMENT_ID_1024,
            experiment_config_version=EXPERIMENT_CONFIG_VERSION_1024,
            diagnostic_mode=DIAGNOSTIC_MODE_1024,
        )
        summary = runner.run_single_t1(tasks=tasks, worker=worker)
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
