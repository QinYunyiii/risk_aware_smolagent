#!/usr/bin/env python3
"""Offline-first B3 CONTINUE Gate entry point; remote modes require explicit authorization."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from risk_agent.agent.agent_factory import RemoteAPIClient, verify_tls_endpoint
from risk_agent.agent.worker_router import RemoteModelSettings, WorkerRouter
from risk_agent.benchmark import generate_task_instances, load_task_templates
from risk_agent.benchmark.schema import validate_identifier
from risk_agent.collection.continue_gate import (
    B3_RESEARCH_FLAGS,
    ContinueGateRunner,
    audit_checkpoint_run,
)
from risk_agent.collection.day5_gate import (
    PROTOCOL_ID,
    create_unique_run_directory,
    sha256_file,
)
from risk_agent.monitoring import JsonlTraceWriter


REMOTE_MODES = {"remote-preflight", "single-continue", "five-continue-gate"}
EXECUTION_MODES = {"single-continue", "five-continue-gate"}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _artifact_manifest(root: Path) -> dict[str, Any]:
    records = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "artifact_manifest.json":
            continue
        records.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return {"artifact_count": len(records), "artifacts": records}


def _default_run_id(mode: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"b3_{mode.replace('-', '_')}_{stamp}"


def _validate_remote_evidence(value: dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        raise RuntimeError("Remote modes require --remote-evidence from an authorized preflight")
    for field in (
        "remote_operations_authorized",
        "https_service_authorized",
        "gpu_mapping_verified",
        "model_version_verified",
    ):
        if value.get(field) is not True:
            raise RuntimeError(f"Remote evidence does not authorize/verify {field}")
    return value


def _missing_rate(values: list[Any]) -> float:
    if not values:
        return 0.0
    return sum(value is None for value in values) / len(values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=(
            "local-preflight",
            "remote-preflight",
            "single-continue",
            "five-continue-gate",
        ),
        default="local-preflight",
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--checkpoint-run",
        default="runs/phase2_b2_day5/b2_day5_five_gate_20261123_001",
    )
    parser.add_argument("--output-root", default="runs/phase2_b3_continue_gate")
    parser.add_argument("--config", default="configs/remote_model.yaml")
    parser.add_argument("--templates", default="configs/task_templates.yaml")
    parser.add_argument("--remote-evidence", default=None)
    parser.add_argument("--authorize-remote", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode in REMOTE_MODES and not args.authorize_remote:
        raise RuntimeError(
            "B3 remote modes are disabled until --authorize-remote is supplied after "
            "explicit Stage B user approval"
        )
    run_id = validate_identifier(args.run_id or _default_run_id(args.mode), "run_id")
    tasks = generate_task_instances(load_task_templates(args.templates))
    run_directory = create_unique_run_directory(args.output_root, run_id)
    audit = audit_checkpoint_run(
        checkpoint_run=args.checkpoint_run,
        tasks=tasks,
        audit_workspace=run_directory / "checkpoint_audit_workspace",
    )
    shutil.rmtree(run_directory / "checkpoint_audit_workspace")
    manifest = {
        "schema_version": 1,
        "protocol_id": PROTOCOL_ID,
        "experiment_id": "b3_model_backed_continue_gate",
        "run_id": run_id,
        "mode": args.mode,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_head(),
        "source_checkpoint_run": audit.source_run_id,
        "checkpoint_run_path": audit.checkpoint_run,
        "checkpoint_schema": "2.0.0",
        "verifier_code_sha256": sha256_file("src/risk_agent/benchmark/verifier.py"),
        **B3_RESEARCH_FLAGS,
    }
    _write_json(run_directory / "run_manifest.json", manifest)
    if args.mode == "local-preflight":
        summary = {
            "mode": args.mode,
            "run_id": run_id,
            "network_requests_made": 0,
            "checkpoint_count": len(audit.records),
            "all_hashes_verified": all(item.all_hashes_verified for item in audit.records),
            "checkpoint_ids": [item.checkpoint_id for item in audit.records],
            "workers": [item.worker_id for item in audit.records],
            "passed": audit.passed and len(audit.records) == 5,
            **B3_RESEARCH_FLAGS,
        }
        _write_json(run_directory / "checkpoint_audit.json", audit.to_dict())
        _write_json(run_directory / "local_preflight.json", summary)
        _write_json(run_directory / "artifact_manifest.json", _artifact_manifest(run_directory))
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["passed"] else 1

    remote_evidence = _validate_remote_evidence(
        _read_json(args.remote_evidence) if args.remote_evidence else None
    )
    settings = RemoteModelSettings.from_yaml(args.config)
    router = WorkerRouter(settings.workers)
    remote_checks: list[dict[str, Any]] = []
    for worker in settings.workers:
        tls_latency = verify_tls_endpoint(settings, worker)
        client = RemoteAPIClient(settings, worker)
        try:
            model_ids = client.list_model_ids()
        finally:
            client.close()
        remote_checks.append(
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
        "remote_evidence": remote_evidence,
        "workers": remote_checks,
        "service_changes_made": False,
    }
    _write_json(run_directory / "resource_audit.json", resource_audit)
    if not all(item["expected_model_present"] for item in remote_checks):
        raise RuntimeError("One or more original Workers do not serve the frozen Model ID")
    if args.mode == "remote-preflight":
        _write_json(run_directory / "artifact_manifest.json", _artifact_manifest(run_directory))
        print(json.dumps(resource_audit, ensure_ascii=False, indent=2))
        return 0

    runner = ContinueGateRunner(
        settings=settings,
        run_directory=run_directory,
        checkpoint_run=args.checkpoint_run,
        run_id=run_id,
        tasks=tasks,
    )
    attempts_writer = JsonlTraceWriter(run_directory / "branch_attempts.jsonl")
    failures_writer = JsonlTraceWriter(run_directory / "failures.jsonl")
    target = 1 if args.mode == "single-continue" else 5
    completed: list[dict[str, Any]] = []
    for audit_record in audit.records[:target]:
        worker = router.select(audit_record.worker_id)
        result = runner.run_checkpoint(
            audit=audit_record,
            worker=worker,
            branch_attempt_index=1,
        )
        attempts_writer.append(result)
        if not result["complete_chain"] and result["safe_reattempt_allowed"]:
            failures_writer.append(result)
            result = runner.run_checkpoint(
                audit=audit_record,
                worker=worker,
                branch_attempt_index=2,
            )
            attempts_writer.append(result)
        if result["complete_chain"]:
            completed.append(result)
        else:
            failures_writer.append(result)
            break

    model_rows = []
    model_path = run_directory / "model_events.jsonl"
    if model_path.exists():
        model_rows = [json.loads(line) for line in model_path.read_text().splitlines() if line]
    tool_rows = []
    tool_path = run_directory / "tool_events.jsonl"
    if tool_path.exists():
        tool_rows = [json.loads(line) for line in tool_path.read_text().splitlines() if line]
    summary = {
        "protocol_id": PROTOCOL_ID,
        "run_id": run_id,
        "mode": args.mode,
        "target": target,
        "complete_chains": len(completed),
        "passed": len(completed) == target,
        "status": "PASS" if len(completed) == target else "BLOCKED",
        "tool_replay_count": sum(bool(row["tool_replay_detected"]) for row in completed),
        "checkpoint_mutation_count": sum(not row["checkpoint_immutable"] for row in completed),
        "label_counts": {
            "1": sum(row["y_continue"] == 1 for row in completed),
            "0": sum(row["y_continue"] == 0 for row in completed),
            "null": sum(row["y_continue"] is None for row in completed),
        },
        "missing_rates": {
            "prompt_tokens": _missing_rate([row.get("prompt_tokens") for row in model_rows]),
            "completion_tokens": _missing_rate(
                [row.get("completion_tokens") for row in model_rows]
            ),
            "model_latency": _missing_rate(
                [row.get("latency_seconds") for row in model_rows]
            ),
            "tool_latency": _missing_rate(
                [row.get("latency_seconds") for row in tool_rows]
            ),
            "wall_latency": _missing_rate(
                [row.get("wall_latency_seconds") for row in completed]
            ),
        },
        **B3_RESEARCH_FLAGS,
    }
    critical_rates = summary["missing_rates"]
    if any(float(value) > 0.05 for value in critical_rates.values()):
        summary["passed"] = False
        summary["status"] = "BLOCKED"
    for required_jsonl in (
        "branch_attempts.jsonl",
        "model_events.jsonl",
        "tool_events.jsonl",
        "agent_visible_events.jsonl",
        "continue_outcomes.jsonl",
        "verifier_outcomes.jsonl",
        "budget_audit.jsonl",
        "failures.jsonl",
    ):
        (run_directory / required_jsonl).touch(exist_ok=True)
    _write_json(run_directory / "gate_summary.json", summary)
    _write_json(run_directory / "artifact_manifest.json", _artifact_manifest(run_directory))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
