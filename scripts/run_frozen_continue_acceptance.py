#!/usr/bin/env python3
"""Run the pinned v2.1 CONTINUE acceptance entry; defaults to local preflight."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from risk_agent.collection.continue_acceptance import (
    FLAGS, ContinueAcceptanceRunner, load_entry, write_json,
)
from risk_agent.collection.day5_gate import sha256_file
from risk_agent.monitoring.provider_response_audit import sanitize_audit_value


def _offline_guard() -> None:
    def deny(event, args):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto", "socket.bind"}:
            raise RuntimeError("Offline smoke forbids network operations")
        if event == "subprocess.Popen" and Path(str(args[0])).name in {"ssh", "openssl", "curl", "wget", "docker"}:
            raise RuntimeError("Offline smoke forbids remote tools")
    sys.addaudithook(deny)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("local-preflight", "offline-smoke", "real-five-task-gate"),
                        default="local-preflight")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-root", type=Path, default=Path("runs/phase2_continue_acceptance"))
    parser.add_argument("--run-id")
    parser.add_argument("--authorize-remote", action="store_true")
    parser.add_argument("--remote-evidence", type=Path)
    parser.add_argument("--connection-spec", type=Path)
    return parser


def validate_fresh_remote_evidence(value: dict, run_id: str, config: dict) -> None:
    try:
        from scripts.run_p2_d2_real_qwen_gate import _validate_remote_evidence
    except ModuleNotFoundError:
        from run_p2_d2_real_qwen_gate import _validate_remote_evidence
    _validate_remote_evidence(value)
    limits = config["preflight"]
    now = datetime.now(timezone.utc)
    ownership = value["worker_ownership"]
    for stamp in [value["checked_at"], ownership["observed_at"], *value["gpu_samples_utc"]]:
        checked = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if checked.tzinfo is None or not 0 <= (now - checked).total_seconds() <= limits["maximum_evidence_age_seconds"]:
            raise RuntimeError("Remote ownership/model/GPU evidence is stale or has an invalid timestamp")
    if value.get("acceptance_run_id") != run_id or ownership.get("passed") is not True:
        raise RuntimeError("Remote evidence is not verified and bound to this unique Run ID")
    workers = ownership["workers"]
    if len(workers) != 2 or {w["worker_id"] for w in workers} != {"worker-0", "worker-1"}:
        raise RuntimeError("Live ownership evidence must cover both frozen Workers")
    for worker in workers:
        if (worker.get("remote_container_id") != limits["expected_remote_container"]
            or worker.get("remote_user") != limits["expected_remote_user"]
            or not all(worker.get(key) is True for key in ("alive", "ownership_verified", "listener_ownership_verified"))):
            raise RuntimeError("Remote container/user/listener ownership proof does not match")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    remote = args.mode == "real-five-task-gate"
    if remote and not args.authorize_remote:
        raise RuntimeError("Real Gate requires separate user authorization and --authorize-remote")
    if remote and not all((args.run_id, args.remote_evidence, args.connection_spec)):
        raise RuntimeError("Real Gate requires explicit run-id, fresh remote-evidence and connection-spec")
    root = args.root.resolve()
    if not remote:
        _offline_guard()
    profile, config = load_entry(root)
    evidence = specs = None
    if remote:
        args.remote_evidence = args.remote_evidence.resolve()
        args.connection_spec = args.connection_spec.resolve()
        try:
            from scripts.run_p2_d2_real_qwen_gate import _api_preflight
        except ModuleNotFoundError:
            from run_p2_d2_real_qwen_gate import _api_preflight
        evidence = json.loads(args.remote_evidence.read_text())
        validate_fresh_remote_evidence(evidence, args.run_id, config)
        specs = json.loads(args.connection_spec.read_text())
    os.chdir(root)
    factory = None
    if args.mode == "offline-smoke":
        from risk_agent.collection.continue_acceptance_fixture import OfflineAcceptanceFactory
        factory = OfflineAcceptanceFactory(profile.tasks)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_id = args.run_id or f"p2_continue_{stamp}_{uuid.uuid4().hex[:8]}"
    kwargs = {"agent_factory": factory} if factory is not None else {}
    runner = ContinueAcceptanceRunner(root=root, run_id=run_id,
        run_directory=root / args.output_root / run_id, offline_fixture=not remote,
        authorize_remote=args.authorize_remote, **kwargs)
    connections = None
    summary = {"run_id": run_id, "passed": False, "gate": "CONTINUE_ACCEPTANCE_BLOCKED", **FLAGS}
    release = {"owned_tunnels": 0, "released": True, "external_processes_terminated": 0}
    try:
        disk = shutil.disk_usage(runner.directory)
        minimum_free = config["preflight"]["minimum_disk_free_gib"] * 1024**3
        write_json(runner.directory / "local_preflight.json", {
            "disk_free_bytes": disk.free, "disk_free_gate": disk.free >= minimum_free,
            "output_created_exclusively": True, "credentials_needed": remote,
            "freeze_manifest_sha256": profile.manifest_sha256, "scope": config})
        if disk.free < minimum_free:
            raise RuntimeError("Local disk free-space gate failed")
        if args.mode == "local-preflight":
            summary.update(passed=True, gate="CONTINUE_ENTRY_PREFLIGHT_PASS", real_model_calls=0,
                           formal_collection_ready=False)
            write_json(runner.directory / "gate_summary.json", summary)
        else:
            if remote:
                runner.settings.resolve_api_key()
                runner.settings.resolve_ca_bundle()
                from risk_agent.monitoring.connection_lifecycle import ConnectionLifecycle
                connections = ConnectionLifecycle(run_id, runner.directory / "connection_audit.jsonl")
                runner.audit.connection_observer = connections
                write_json(runner.directory / "remote_evidence_reference.json", {
                    "evidence_sha256": sha256_file(args.remote_evidence),
                    "checked_at": evidence["checked_at"], "acceptance_run_id": run_id})
                connections.launch(specs)
                release["owned_tunnels"] = len(connections.adapters)
                write_json(runner.directory / "api_preflight.json", _api_preflight(runner.settings))
            summary = runner.run_fixed_gate()
    except (Exception, KeyboardInterrupt) as exc:
        summary.update(passed=False, gate="CONTINUE_ACCEPTANCE_BLOCKED",
                       error_type=type(exc).__name__, error_message=str(exc))
        if not (runner.directory / "gate_summary.json").exists():
            write_json(runner.directory / "gate_summary.json", summary)
    finally:
        if connections is not None:
            release["owned_tunnels"] = len(connections.adapters)
            try:
                connections.close()
            except Exception as exc:
                release.update(released=False, error_type=type(exc).__name__)
                summary.update(passed=False, gate="CONTINUE_ACCEPTANCE_BLOCKED")
        write_json(runner.directory / "connection_resource_release.json", release)
        if factory is not None:
            write_json(runner.directory / "offline_fixture_requests.json", factory.requests)
        final = {**summary, "mode": args.mode, "connection_resources_released": release["released"]}
        write_json(runner.directory / "gate_summary_final_audit.json", final)
        write_json(runner.directory / "artifact_manifest.json", {"run_id": run_id, "files": [
            {"path": str(path.relative_to(runner.directory)), "size_bytes": path.stat().st_size,
             "sha256": sha256_file(path)}
            for path in sorted(runner.directory.rglob("*")) if path.is_file()]})
    print(json.dumps(sanitize_audit_value(final), ensure_ascii=False, indent=2))
    return 0 if final["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
