#!/usr/bin/env python3
"""v2.2 engineering gate: default local preflight; real mode requires new authorization."""
from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from risk_agent.collection.continue_acceptance_v2 import FLAGS, ContinueAcceptanceRunner, load_entry, write_json
from risk_agent.collection.day5_gate import sha256_file
from risk_agent.monitoring.provider_response_audit import sanitize_audit_value
try:
    from scripts.run_frozen_continue_acceptance import build_parser, validate_fresh_remote_evidence, _offline_guard
except ModuleNotFoundError:
    from run_frozen_continue_acceptance import build_parser, validate_fresh_remote_evidence, _offline_guard


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.description = "Versioned v2.2 lossless-output engineering acceptance"
    parser.set_defaults(output_root=Path("runs/phase2_continue_acceptance_v2"))
    args = parser.parse_args(argv)
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
    summary = {"run_id": run_id, "passed": False, "engineering_passed": False, "gate": "CONTINUE_ENGINEERING_GATE_BLOCKED", **FLAGS}
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
            summary.update(passed=True, engineering_passed=True, gate="CONTINUE_ENTRY_V2_PREFLIGHT_PASS", real_model_calls=0,
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
        summary.update(passed=False, engineering_passed=False, gate="CONTINUE_ENGINEERING_GATE_BLOCKED",
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
                summary.update(passed=False, engineering_passed=False, gate="CONTINUE_ENGINEERING_GATE_BLOCKED")
        write_json(runner.directory / "connection_resource_release.json", release)
        if factory is not None:
            write_json(runner.directory / "offline_fixture_requests.json", factory.requests)
        final = {**summary, "mode": args.mode, "entry_version": config["entry_version"],
                 "engineering_passed": bool(summary.get("engineering_passed") and release["released"]),
                 "connection_resources_released": release["released"]}
        write_json(runner.directory / "gate_summary_final_audit.json", final)
        write_json(runner.directory / "artifact_manifest.json", {"run_id": run_id, "files": [
            {"path": str(path.relative_to(runner.directory)), "size_bytes": path.stat().st_size,
             "sha256": sha256_file(path)}
            for path in sorted(runner.directory.rglob("*")) if path.is_file()]})
    print(json.dumps(sanitize_audit_value(final), ensure_ascii=False, indent=2))
    return 0 if final["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
