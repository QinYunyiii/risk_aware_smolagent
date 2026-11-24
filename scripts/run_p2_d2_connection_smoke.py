#!/usr/bin/env python3
"""Authorized WSL SSH + GET-only 90-second connection smoke; no Agent imports."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import threading
import time
from typing import Any, Callable
import uuid

import httpx2

from risk_agent.agent.worker_router import RemoteModelSettings
from risk_agent.collection.day5_gate import create_unique_run_directory, sha256_file
from risk_agent.monitoring.connection_lifecycle import ConnectionLifecycle, diagnose, now
from risk_agent.monitoring.provider_response_audit import sanitize_audit_value
from risk_agent.monitoring.trajectory import JsonlTraceWriter


DURATION = 90.0
INTERVAL = 0.25
WORKERS = ("worker-0", "worker-1")
CONFIGS = ("configs/p2_d2_full_gate_1024.yaml", "configs/remote_model_p2_d2_1024.yaml")


def write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(sanitize_audit_value(value), handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def specs_from_host() -> dict[str, Any]:
    # -G expands local configuration only; it makes no SSH connection.
    result = subprocess.run(["ssh", "-G", "risk-agent-remote"], check=True,
                            capture_output=True, text=True, timeout=5)
    config: dict[str, list[str]] = {}
    for line in result.stdout.splitlines():
        key, value = line.split(" ", 1)
        config.setdefault(key, []).append(value)
    if config.get("port") != ["4035"] or config.get("hostname") != ["121.48.161.243"] or config.get("user") != ["root"]:
        raise ValueError("SSH Host differs from approved target")
    if len(config.get("identityfile", [])) != 1:
        raise ValueError("Expected exactly one dedicated identity file")
    key = Path(config["identityfile"][0]).expanduser()
    hosts = Path(config["userknownhostsfile"][0]).expanduser()
    if not key.is_file() or not hosts.is_file():
        raise ValueError("Dedicated key or known_hosts missing")
    fingerprint = subprocess.run(["ssh-keygen", "-lf", str(key)], check=True,
                                 capture_output=True, text=True, timeout=5).stdout.split()[1]
    if fingerprint != "SHA256:u51mgYnzoKDgEydhLKmI07XAOnCc2jcEhfc+BTOIL7A":
        raise ValueError("Dedicated key fingerprint mismatch")
    return {worker: dict(target="root@121.48.161.243", ssh_port=4035,
        identity_file=str(key), known_hosts_file=str(hosts), local_port=8000+i,
        remote_port=8000+i) for i, worker in enumerate(WORKERS)}


def observe(connections: ConnectionLifecycle) -> None:
    if connections.error:
        raise RuntimeError("Connection monitor failed") from connections.error
    for worker in WORKERS:
        adapter = connections.adapters[worker]
        if adapter.identity != connections.identities[worker]:
            raise RuntimeError("Owned process start identity changed")
        state = connections.event("smoke_health_observed", worker,
            stderr_bytes=adapter.stderr_path.stat().st_size if hasattr(adapter, "stderr_path") else None)
        if state["process_state"] != "PROCESS_ALIVE" or state["listener_state"] != "LOCAL_PORT_LISTENING":
            raise ConnectionError("Owned tunnel exited or local forwarding port closed")


def get_models(connections: ConnectionLifecycle, worker: str, client: httpx2.Client,
               url: str, api_key: str, writer: JsonlTraceWriter) -> dict[str, Any]:
    """Exactly one GET, checked while in flight; never invokes any model wrapper."""
    expected_url = "https://localhost:{}/v1/models".format(8000 + WORKERS.index(worker))
    if url != expected_url:
        raise ValueError("Only the frozen GET /v1/models URL is allowed")
    request_id = "smoke-" + uuid.uuid4().hex
    started, stamp = time.monotonic(), now()
    before = connections.adapters[worker].snapshot()
    connections.request_started(worker, request_id)
    failure = None
    record: dict[str, Any] = dict(run_id=connections.run_id, worker_id=worker,
        request_id=request_id, method="GET", path="/v1/models", started_at=stamp,
        http_status=None, tls_state="UNKNOWN", model_ids=[], process_before=before)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(client.get, url, headers={"Authorization": "Bearer " + api_key})
            while not future.done():
                observe(connections)
                threading.Event().wait(INTERVAL)
            response = future.result()
        record.update(http_status=response.status_code, tls_state="TLS_VALID")
        response.raise_for_status()
        ids = [item["id"] for item in response.json().get("data", [])]
        record["model_ids"] = ids
        if "Qwen/Qwen3-8B" not in ids:
            raise ValueError("Expected model ID absent")
        observe(connections)
        record["state"] = "API_REQUEST_SUCCESS"
        return record
    except BaseException as exc:
        failure = exc
        record.update(state="API_REQUEST_FAILED", diagnosis=diagnose(exc, [before]))
        if record["diagnosis"]["connection_diagnostic"] == "tls_connection_failure":
            record["tls_state"] = "TLS_FAILED"
        raise
    finally:
        record.update(ended_at=now(), latency_seconds=time.monotonic()-started,
                      process_after=connections.adapters[worker].snapshot())
        try:
            writer.append(sanitize_audit_value(record))
        finally:
            connections.request_finished(worker, request_id, failure)


def observation_window(connections: ConnectionLifecycle, probe: Callable[[str], Any],
                       *, clock: Callable[[], float] = time.monotonic,
                       wait: Callable[[float], Any] = time.sleep) -> float:
    start = clock()
    for target in (0.0, 45.0, DURATION):
        while clock()-start < target:
            observe(connections)
            wait(min(INTERVAL, target-(clock()-start)))
        observe(connections)
        if target < DURATION:
            for worker in WORKERS:
                observe(connections)
                probe(worker)
    return clock()-start


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorize-remote", action="store_true")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-root", default="runs/phase2_p2_d2_connection_smoke")
    args = parser.parse_args(argv)
    if not args.authorize_remote:
        raise RuntimeError("Independent connection smoke authorization required")
    settings = RemoteModelSettings.from_yaml(CONFIGS[1])
    api_key, ca = settings.resolve_api_key(), settings.resolve_ca_bundle()
    context = ssl.create_default_context(cafile=str(ca))
    specs = specs_from_host()
    root = create_unique_run_directory(args.output_root, args.run_id)
    hashes = {path: sha256_file(path) for path in CONFIGS}
    write_json(root / "run_manifest.json", dict(run_id=args.run_id, created_at=now(),
        git_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        config_sha256=hashes, duration_seconds=DURATION, interval_seconds=INTERVAL,
        implementation_sha256={p:sha256_file(p) for p in (
            "scripts/run_p2_d2_connection_smoke.py",
            "src/risk_agent/monitoring/connection_lifecycle.py")},
        schedule_seconds=[0,45], max_get_requests=4, completion_requests=0,
        approved_parameter_substitution="-F /dev/null replaces ClearAllForwardings=yes for this Smoke only",
        formal_experiment=False, research_dataset_eligible=False))
    manager = ConnectionLifecycle(args.run_id, root / "connection_audit.jsonl")
    writer = JsonlTraceWriter(root / "worker_probe_audit.jsonl")
    summary: dict[str, Any] = dict(run_id=args.run_id, status="BLOCKED", passed=False,
        completion_requests=0, automatic_retries=0, worker_failovers=0, get_count=0)
    clients = []
    cleanup = dict(recorded_at=None, passed=False, workers={})
    try:
        manager.launch(specs)
        summary["monitoring_started_at"] = now()
        # Explicit retries=0, no redirect following, strict SSL Context and finite timeout.
        client = httpx2.Client(transport=httpx2.HTTPTransport(verify=context, retries=0, trust_env=False),
                               timeout=httpx2.Timeout(10, connect=5), follow_redirects=False, trust_env=False)
        clients.append(client)
        def probe(worker: str) -> None:
            summary["get_count"] += 1
            get_models(manager, worker, client,
                       "https://localhost:{}/v1/models".format(8000+WORKERS.index(worker)), api_key, writer)
        summary["observed_seconds"] = observation_window(manager, probe)
        summary["monitoring_ended_at"] = now()
        summary.update(status="PASS", passed=True)
    except BaseException as exc:
        summary.update(error=diagnose(exc, []), monitoring_ended_at=now())
    finally:
        for client in clients:
            client.close()
        try:
            manager.close()
            cleanup["passed"] = True
        except BaseException as exc:
            cleanup["error"] = diagnose(exc, [])
        cleanup["recorded_at"] = now()
        for worker, adapter in manager.adapters.items():
            cleanup["workers"][worker] = dict(identity=asdict(manager.identities[worker]),
                state=adapter.snapshot(), ssh_command=adapter.process.args,
                stderr_bytes=adapter.stderr_path.stat().st_size)
        cleanup["manager_closed"] = manager.closed
        cleanup["local_ports_closed"] = not any(port_open(p) for p in (8000,8001))
        cleanup["owned_processes_exited"] = all(
            a.snapshot()["process_state"]=="PROCESS_EXITED" for a in manager.adapters.values())
        cleanup["passed"] = bool(cleanup["passed"] and cleanup["owned_processes_exited"] and cleanup["local_ports_closed"])
        summary["configs_unchanged"] = hashes == {path: sha256_file(path) for path in CONFIGS}
        summary["passed"] = bool(summary["passed"] and cleanup["passed"] and summary["configs_unchanged"] and summary["get_count"]==4)
        summary["status"] = "PASS" if summary["passed"] else "BLOCKED"
        write_json(root / "resource_release.json", cleanup)
        write_json(root / "gate_summary.json", summary)
        artifacts = [dict(path=p.relative_to(root).as_posix(),size_bytes=p.stat().st_size,sha256=sha256_file(p))
                     for p in sorted(root.rglob("*")) if p.is_file()]
        write_json(root / "artifact_manifest.json", dict(artifacts=artifacts,artifact_count=len(artifacts)))
    print(json.dumps(summary, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
