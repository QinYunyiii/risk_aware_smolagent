#!/usr/bin/env python3
"""Run the independently versioned P2-D2 1024-token five-task Gate."""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from risk_agent.agent.agent_state_adapter import canonical_hash
from risk_agent.agent.worker_router import RemoteModelSettings, WorkerConfig
from risk_agent.benchmark import generate_task_instances, load_task_templates, task_to_dict
from risk_agent.benchmark.schema import validate_identifier
from risk_agent.collection.day5_gate import create_unique_run_directory, sha256_file
from risk_agent.collection.p2_d2_completion_budget import (
    FULL_GATE_EXPERIMENT_CONFIG_VERSION_1024,
    FULL_GATE_EXPERIMENT_ID_1024,
    FULL_GATE_MODE_1024,
    FULL_GATE_WORKER_SCHEDULE,
    GENERATION_CONFIG_VERSION_1024,
    load_full_gate_1024_profile,
    load_exact_identifier_prompt_profile,
    validate_completion_budget_settings,
)
from risk_agent.collection.p2_d2_real_qwen_gate import (
    FIXED_TASK_IDS,
    P2D2RealQwenGateRunner,
    RESEARCH_FLAGS,
)
from risk_agent.monitoring.connection_lifecycle import ConnectionLifecycle

try:  # Direct execution puts ``scripts/`` rather than the repo root on sys.path.
    from scripts.run_p2_d2_real_qwen_gate import (
        _api_preflight,
        _artifact_manifest,
        _catalog_audit,
        _git_head,
        _read_json,
        _validate_remote_evidence,
        _write_json,
    )
except ModuleNotFoundError:  # pragma: no cover - exercised by CLI subprocess tests
    from run_p2_d2_real_qwen_gate import (
        _api_preflight,
        _artifact_manifest,
        _catalog_audit,
        _git_head,
        _read_json,
        _validate_remote_evidence,
        _write_json,
    )


REMOTE_MODE = FULL_GATE_MODE_1024
DEFAULT_OUTPUT_ROOT = "runs/phase2_p2_d2_full_gate_1024"


def _default_run_id(mode: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"p2_d2_full_gate_1024_{mode.replace('-', '_')}_{stamp}"


def _frozen_workers(settings: RemoteModelSettings) -> tuple[WorkerConfig, ...]:
    workers = tuple(settings.workers)
    if tuple(worker.worker_id for worker in workers) != ("worker-0", "worker-1"):
        raise RuntimeError("Full Gate requires exactly worker-0 then worker-1")
    return workers


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
        "--experiment-config", default="configs/p2_d2_full_gate_1024.yaml"
    )
    parser.add_argument(
        "--remote-config", default="configs/remote_model_p2_d2_1024.yaml"
    )
    parser.add_argument("--remote-evidence", default=None)
    parser.add_argument("--authorize-remote", action="store_true")
    parser.add_argument("--connection-spec", default=None,
                        help="Local private JSON with explicit SSH connection specs for both Workers")
    parser.add_argument("--task-prompt-profile", default=None,
                        help="Separate approved T3 exact-identifier presentation profile")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode == REMOTE_MODE:
        if not args.authorize_remote:
            raise RuntimeError(
                "1024-token full Gate requires independent approval and "
                "--authorize-remote"
            )
        if args.remote_evidence is None:
            raise RuntimeError("1024-token full Gate requires --remote-evidence")
        remote_evidence = _validate_remote_evidence(_read_json(args.remote_evidence))
        if args.connection_spec is None:
            raise RuntimeError("Managed full Gate requires --connection-spec")
    else:
        remote_evidence = None

    profile = load_full_gate_1024_profile(args.experiment_config)
    settings = RemoteModelSettings.from_yaml(args.remote_config)
    validate_completion_budget_settings(
        settings,
        candidate_config=args.remote_config,
    )
    workers = _frozen_workers(settings)
    templates = load_task_templates(profile.catalog_path)
    tasks = generate_task_instances(templates, 2)
    catalog_audit = _catalog_audit(tasks, templates)
    if catalog_audit["canonical_catalog_hash"] != profile.canonical_catalog_hash:
        raise RuntimeError("Canonical P2-D1 catalog hash mismatch")
    if tuple(item["task_id"] for item in catalog_audit["fixed_tasks"]) != FIXED_TASK_IDS:
        raise RuntimeError("Catalog audit did not preserve the frozen five-task order")
    prompt_profile = (load_exact_identifier_prompt_profile(args.task_prompt_profile, tasks)
                      if args.task_prompt_profile else None)
    experiment_version = (prompt_profile["experiment_config_version"] if prompt_profile
                          else profile.experiment_config_version)
    task_prompts = {prompt_profile["task_id"]: prompt_profile["prompt"]} if prompt_profile else {}
    if prompt_profile:
        original_task = next(t for t in tasks if t.task_id == prompt_profile["task_id"])
        prompt_profile["original_task_spec_hash"] = canonical_hash(task_to_dict(original_task))
        prompt_profile["effective_task_spec_hash"] = canonical_hash(task_to_dict(
            replace(original_task, prompt=prompt_profile["prompt"])))

    run_id = validate_identifier(args.run_id or _default_run_id(args.mode), "run_id")
    run_directory = create_unique_run_directory(args.output_root, run_id)
    disk = shutil.disk_usage(run_directory)
    manifest = {
        "schema_version": 1,
        "protocol_id": profile.protocol_id,
        "checkpoint_schema_version": profile.checkpoint_schema_version,
        "experiment_id": profile.experiment_id,
        "experiment_config_version": experiment_version,
        "baseline_experiment_config_version": profile.experiment_config_version,
        "task_prompt_profile": prompt_profile,
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
                for worker in workers
            ],
        },
        "fixed_gate_scope": {
            "task_ids": list(profile.fixed_task_ids),
            "worker_schedule": list(profile.worker_schedule),
            "max_attempts_per_task": 1,
            "automatic_retries": 0,
            "replacement_tasks": 0,
            "worker_failover": False,
            "source_restore_same_worker": True,
            "stop_on_first_failure": True,
            "restored_response_executed": False,
        },
        "changed_factor": {
            "name": "max_completion_tokens",
            "baseline": 256,
            "full_gate": 1024,
        },
        "limits": dict(profile.limits),
        "execution_controls": dict(profile.execution_controls),
        "versions": {
            "smolagents": version("smolagents"),
            "openai": version("openai"),
            "httpx2": version("httpx2"),
        },
        "config_sha256": {
            "experiment_1024": sha256_file(args.experiment_config),
            "remote_1024": sha256_file(args.remote_config),
            "verified_t1_1024": profile.verified_t1_1024_config_sha256,
            "verified_remote_1024": profile.verified_remote_model_1024_sha256,
            "baseline_gate_256": profile.baseline_gate_config_sha256,
            "baseline_remote_256": profile.baseline_remote_model_sha256,
            "catalog": sha256_file(profile.catalog_path),
        },
        "implementation_sha256": {
            "runner": sha256_file(
                "src/risk_agent/collection/p2_d2_real_qwen_gate.py"
            ),
            "entrypoint": sha256_file("scripts/run_p2_d2_full_gate_1024.py"),
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
            "gate_task_ids": list(profile.fixed_task_ids),
            "gate_worker_schedule": list(profile.worker_schedule),
            "experiment_id": FULL_GATE_EXPERIMENT_ID_1024,
            "experiment_config_version": experiment_version,
        },
    )
    connections = ConnectionLifecycle(run_id, run_directory / "connection_audit.jsonl")
    try:
        if not manifest["local_disk_at_least_20gb"]:
            raise RuntimeError("Local disk has less than 20 GiB free")
        if args.mode == "local-preflight":
            summary = {
                "run_id": run_id,
                "mode": args.mode,
                "experiment_id": FULL_GATE_EXPERIMENT_ID_1024,
                "experiment_config_version": experiment_version,
                "generation_config_version": GENERATION_CONFIG_VERSION_1024,
                "completion_token_limit": 1024,
                "fixed_task_ids": list(FIXED_TASK_IDS),
                "worker_schedule": list(FULL_GATE_WORKER_SCHEDULE),
                "network_requests_made": 0,
                "passed": True,
                "status": "PASS",
                **RESEARCH_FLAGS,
            }
            _write_json(run_directory / "gate_summary.json", summary)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        connections.launch(_read_json(args.connection_spec))
        remote_preflight = {
            "evidence": remote_evidence,
            "api_workers": _api_preflight(settings, workers=workers),
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
            experiment_id=FULL_GATE_EXPERIMENT_ID_1024,
            experiment_config_version=experiment_version,
            task_prompts=task_prompts,
            gate_mode=FULL_GATE_MODE_1024,
            connection_observer=connections,
        )
        summary = runner.run_fixed_gate(tasks=tasks, workers=workers)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["passed"] else 1
    except Exception as exc:
        summary_path = run_directory / "gate_summary.json"
        if not summary_path.exists():
            _write_json(
                summary_path,
                {
                    "run_id": run_id,
                    "mode": args.mode,
                    "experiment_id": FULL_GATE_EXPERIMENT_ID_1024,
                    "experiment_config_version": experiment_version,
                    "status": "BLOCKED",
                    "passed": False,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    **RESEARCH_FLAGS,
                },
            )
        raise
    finally:
        try:
            connections.close()
        finally:
            _write_json(run_directory / "artifact_manifest.json", _artifact_manifest(run_directory))


if __name__ == "__main__":
    raise SystemExit(main())
