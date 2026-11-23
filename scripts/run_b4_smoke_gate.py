#!/usr/bin/env python3
"""Run B4 Stage A locally; remote modes require separate Stage B approval."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from risk_agent.agent.agent_factory import RemoteAPIClient, verify_tls_endpoint
from risk_agent.agent.worker_router import RemoteModelSettings
from risk_agent.benchmark import generate_task_instances, load_task_templates
from risk_agent.benchmark.schema import validate_identifier
from risk_agent.collection.b4_smoke_gate import (
    B4_RESEARCH_FLAGS,
    B4SmokeGateRunner,
    b4_run_manifest,
    load_b4_smoke_plan,
    run_b4_local_preflight,
)
from risk_agent.collection.day5_gate import create_unique_run_directory, sha256_file


REMOTE_MODES = {"remote-preflight", "smoke-gate"}


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


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _default_run_id(mode: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"b4_{mode.replace('-', '_')}_{stamp}"


def _artifact_manifest(root: Path) -> dict[str, Any]:
    rows = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "artifact_manifest.json":
            continue
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return {"artifact_count": len(rows), "artifacts": rows}


def _validate_remote_evidence(value: dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        raise RuntimeError("B4 remote modes require authorized remote evidence")
    required = (
        "remote_operations_authorized",
        "https_service_authorized",
        "gpu_mapping_verified",
        "model_version_verified",
    )
    failed = [field for field in required if value.get(field) is not True]
    if failed:
        raise RuntimeError(f"B4 remote evidence is missing approval/checks: {failed}")
    samples = value.get("gpu_samples_utc")
    if not isinstance(samples, list) or len(samples) < 2:
        raise RuntimeError("B4 requires two current GPU samples")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("local-preflight", "remote-preflight", "smoke-gate"),
        default="local-preflight",
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--output-root", default="runs/phase2_b4_smoke")
    parser.add_argument("--plan", default="configs/b4_smoke.yaml")
    parser.add_argument("--templates", default="configs/task_templates.yaml")
    parser.add_argument("--config", default="configs/remote_model.yaml")
    parser.add_argument(
        "--b2-run",
        default="runs/phase2_b2_day5/b2_day5_five_gate_20261123_001",
    )
    parser.add_argument(
        "--b3-run",
        default="runs/phase2_b3_continue_gate/b3_five_continue_gate_20261123_001",
    )
    parser.add_argument("--remote-evidence", default=None)
    parser.add_argument("--authorize-remote", action="store_true")
    parser.add_argument(
        "--single-chain",
        action="store_true",
        help=(
            "Run only the first planned task as the mandatory Stage B canary. "
            "This is an engineering preflight and does not evaluate the final B4 Gate."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode in REMOTE_MODES and not args.authorize_remote:
        raise RuntimeError(
            "B4 remote modes are disabled until explicit Stage B approval and "
            "--authorize-remote"
        )
    if args.single_chain and args.mode != "smoke-gate":
        raise RuntimeError("--single-chain is only valid with --mode smoke-gate")
    run_id = validate_identifier(args.run_id or _default_run_id(args.mode), "run_id")
    plan = load_b4_smoke_plan(args.plan)
    tasks = generate_task_instances(load_task_templates(args.templates))
    run_directory = create_unique_run_directory(args.output_root, run_id)
    manifest = b4_run_manifest(
        run_id=run_id,
        mode=args.mode,
        plan=plan,
        git_commit=_git_head(),
    )
    manifest["config_sha256"] = {
        "b4_plan": sha256_file(args.plan),
        "task_templates": sha256_file(args.templates),
    }
    manifest["implementation_sha256"] = {
        "b4_smoke_gate": sha256_file("src/risk_agent/collection/b4_smoke_gate.py"),
        "continue_gate": sha256_file("src/risk_agent/collection/continue_gate.py"),
        "day5_gate": sha256_file("src/risk_agent/collection/day5_gate.py"),
        "verifier": sha256_file("src/risk_agent/benchmark/verifier.py"),
        "entrypoint": sha256_file("scripts/run_b4_smoke_gate.py"),
    }
    manifest["local_disk_free_bytes"] = shutil.disk_usage(run_directory).free
    manifest["local_disk_at_least_20gb"] = (
        manifest["local_disk_free_bytes"] >= 20 * 1024**3
    )
    manifest["execution_scope"] = "single_chain_canary" if args.single_chain else "full"
    _write_json(run_directory / "run_manifest.json", manifest)

    if args.mode == "local-preflight":
        summary = run_b4_local_preflight(
            plan=plan,
            tasks=tasks,
            run_directory=run_directory,
            b2_gate_run=args.b2_run,
            b3_gate_run=args.b3_run,
        )
        if not manifest["local_disk_at_least_20gb"]:
            summary["passed"] = False
            summary["status"] = "BLOCKED"
            summary["checks"]["local_disk_at_least_20gb"] = False
            _write_json(run_directory / "gate_summary.json", summary)
            _write_json(run_directory / "artifact_manifest.json", _artifact_manifest(run_directory))
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["passed"] else 1

    evidence = _validate_remote_evidence(
        _read_json(args.remote_evidence) if args.remote_evidence else None
    )
    settings = RemoteModelSettings.from_yaml(args.config)
    workers = []
    for worker in settings.workers:
        tls_latency = verify_tls_endpoint(settings, worker)
        client = RemoteAPIClient(settings, worker)
        try:
            model_ids = client.list_model_ids()
        finally:
            client.close()
        workers.append(
            {
                "worker_id": worker.worker_id,
                "endpoint": worker.base_url,
                "tls_verified": True,
                "tls_latency_seconds": tls_latency,
                "model_ids": model_ids,
                "expected_model_present": settings.model_id in model_ids,
            }
        )
    resource_audit = {
        "remote_evidence": evidence,
        "workers": workers,
        "service_changes_made": False,
        "owned_remote_processes_started": 0,
        **B4_RESEARCH_FLAGS,
    }
    _write_json(run_directory / "resource_audit.json", resource_audit)
    if not all(worker["expected_model_present"] for worker in workers):
        raise RuntimeError("One or more B4 Workers do not serve the frozen Model ID")
    if args.mode == "remote-preflight":
        _write_json(run_directory / "artifact_manifest.json", _artifact_manifest(run_directory))
        print(json.dumps(resource_audit, ensure_ascii=False, indent=2))
        return 0

    runner = B4SmokeGateRunner(
        settings=settings,
        run_directory=run_directory,
        run_id=run_id,
        tasks=tasks,
        plan=plan,
    )
    summary = runner.run(
        task_limit=1 if args.single_chain else None,
        evaluate_final_gate=not args.single_chain,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
