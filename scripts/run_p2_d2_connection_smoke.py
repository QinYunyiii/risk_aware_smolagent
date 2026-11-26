#!/usr/bin/env python3
"""Authorized sequential two-GET connection smoke; no Agent imports."""
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

if __package__ in (None, ""):
    import manage_remote_vllm as deployment
else:
    from . import manage_remote_vllm as deployment

from risk_agent.agent.worker_router import RemoteModelSettings
from risk_agent.collection.day5_gate import create_unique_run_directory, sha256_file
from risk_agent.monitoring.connection_lifecycle import ConnectionLifecycle, diagnose, now, windows_tcp_proxy_command
from risk_agent.monitoring.provider_response_audit import sanitize_audit_value
from risk_agent.monitoring.trajectory import JsonlTraceWriter


DURATION = 90.0
INTERVAL = 0.25
WORKERS = ("worker-0", "worker-1")
CONFIGS = ("configs/p2_d2_full_gate_1024.yaml", "configs/remote_model_p2_d2_1024.yaml")


def write_json(path: Path, value: Any) -> None:
    from risk_agent.collection.tls_stage_b import _write
    _write(path.parent, path.name, sanitize_audit_value(value))


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
    for worker, adapter in list(connections.adapters.items()):
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
    trace_writer = JsonlTraceWriter(writer.path.parent / (worker + '_tcp_tls_trace.jsonl'))
    def trace(event: str, info: dict[str, Any]) -> None:
        # Never serialize a request, headers, TLS key material or the raw info map.
        fields = dict(event=event, timestamp=now(), worker_id=worker, request_id=request_id)
        if event.endswith('start_tls.started'):
            ctx = info.get('ssl_context')
            fields.update(check_hostname=getattr(ctx, 'check_hostname', None),
                          verify_mode=getattr(ctx, 'verify_mode', None), timeout=info.get('timeout'))
        error = info.get('exception')
        if isinstance(error, BaseException): fields['diagnosis'] = diagnose(error, [])
        trace_writer.append(sanitize_audit_value(fields))
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(client.get, url, headers={"Authorization": "Bearer " + api_key},
                                 extensions={'trace': trace})
            while not future.done():
                observe(connections)
                threading.Event().wait(INTERVAL)
            response = future.result()
        record.update(http_status=response.status_code, tls_state="TLS_VALID")
        from risk_agent.collection.tls_stage_b import _write
        import hashlib
        raw = response.content
        _write(writer.path.parent, worker + '_response.raw', raw)
        record.update(response_bytes=len(raw), response_sha256=hashlib.sha256(raw).hexdigest(),
                      response_usage=None, usage_applicability='NOT_A_COMPLETION',
                      provider_request_id=response.headers.get('x-request-id'))
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
        try:
            record.update(ended_at=now(), latency_seconds=time.monotonic()-started,
                          process_after=connections.adapters[worker].snapshot())
            writer.append(sanitize_audit_value(record))
        finally:
            connections.request_finished(worker, request_id, failure)


def observation_window(connections: ConnectionLifecycle, probe: Callable[[str], Any],
                       *, clock: Callable[[], float] = time.monotonic,
                       wait: Callable[[float], Any] = time.sleep) -> float:
    # Historical 90-second API only; the production CLI never invokes this plan.
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


def strict_context(ca: Path) -> ssl.SSLContext:
    from risk_agent.collection.remote_local_tls_isolation import CA_SHA256
    if not ca.is_file() or sha256_file(ca) != CA_SHA256:
        raise ValueError("Frozen CA fingerprint mismatch")
    context = ssl.create_default_context(cafile=str(ca))
    if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
        raise ValueError("Strict CA and hostname verification required")
    return context


def ownership_preflight(specs: dict[str, Any], root: Path, *,
                        artifact_prefix: str = '') -> dict[str, Any]:
    spec = specs['worker-0']
    base = ['ssh', '-F', '/dev/null', '-T', '-i', spec['identity_file'], '-p', str(spec['ssh_port']),
        '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
        '-o', 'UserKnownHostsFile=' + spec['known_hosts_file'], '-o', 'ClearAllForwardings=yes',
        '-o', 'ControlMaster=no', '-o', 'ControlPath=none', '-o', 'ConnectionAttempts=1',
        '-o', 'ConnectTimeout=15', spec['target']]
    if spec.get('windows_tcp_proxy'):
        base[-1:-1] = ['-o', 'ProxyCommand=' + windows_tcp_proxy_command(spec)]
    write_json(root/(artifact_prefix+'ownership_ssh_command.json'), dict(argv=base, remote_action='ownership-readonly'))
    result = deployment.remote('ownership-readonly', timeout=30, ssh_command=base)
    write_json(root/(artifact_prefix+'ownership_ssh_result.json'), dict(returncode=result.returncode,
        stderr=sanitize_audit_value(result.stderr)))
    if result.returncode != 0:
        raise RuntimeError('Read-only Worker ownership SSH failed; no retry')
    return json.loads(result.stdout)


def verify_ownership(record: dict[str, Any]) -> None:
    workers = record.get('workers', [])
    if (record.get('passed') is not True or len(workers) != 2
            or [w.get('worker_id') for w in workers] != list(WORKERS)
            or any(w.get('ownership_verified') is not True or not deployment.ownership_checks(w) for w in workers)
            or len({w['worker_pid'] for w in workers}) != 2
            or len({w['gpu_uuid'] for w in workers}) != 2):
        raise ValueError('Both current Worker ownership records must pass before tunneling')


def revalidate_worker1(specs: dict[str, Any], root: Path,
                       initial: dict[str, Any]) -> dict[str, Any]:
    """Acquire new remote evidence using the unchanged production preflight.

    The existing producer checks both Workers. Reuse it as-is, then compare
    Worker 1 against its initial snapshot; never accept PID liveness alone.
    """
    current = ownership_preflight(specs, root, artifact_prefix='worker1_revalidation_')
    write_json(root/'worker1_revalidation_ownership.json', current)
    valid = False
    try:
        verify_ownership(current)
        valid = True
    except (ValueError, TypeError, KeyError, AttributeError):
        pass
    before = initial['workers'][1]
    workers = current.get('workers', []) if isinstance(current, dict) else []
    after = workers[1] if isinstance(workers, list) and len(workers) == 2 and isinstance(workers[1], dict) else {}
    fields = ('remote_container_id', 'remote_user', 'ubuntu_version', 'worker_id',
        'worker_pid', 'alive', 'worker_process_identity', 'worker_command_identity',
        'worker_command_fingerprint', 'cuda_visible_devices', 'physical_gpu_id',
        'gpu_uuid', 'state_gpu_uuid', 'state_pid', 'state_gpu_id', 'endpoint',
        'listener_ownership_verified', 'model_id', 'model_path', 'ownership_verified')
    checks = {key: key in before and key in after and type(before[key]) is type(after[key])
              and before[key] == after[key] for key in fields}
    comparison = dict(worker_id='worker-1', recorded_at=now(), initial=before, current=after,
                      checks=checks, current_snapshot_valid=valid, passed=valid and all(checks.values()))
    write_json(root/'worker1_revalidation_comparison.json', comparison)
    return comparison


def publish_manifest(root: Path) -> None:
    # Reuse the already tested no-clobber/fsync atomic evidence publisher.
    from risk_agent.collection.tls_stage_b import _publish
    _publish(root, 'artifact_manifest.json')
    manifest = json.loads((root/'artifact_manifest.json').read_bytes())
    if {a['path'] for a in manifest['artifacts']} != {
            p.name for p in root.iterdir() if p.is_file() and p.name != 'artifact_manifest.json'}:
        raise RuntimeError('Manifest artifact set mismatch')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Sequential two-GET connection smoke; no Agent')
    parser.add_argument('--authorize-remote', action='store_true')
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--output-root', default='runs/phase2_p2_d2_connection_smoke')
    parser.add_argument('--ssh-ipqos', choices=('none',), default=None,
                        help='Explicit connection-only profile: use OS-default packet traffic class')
    parser.add_argument('--windows-tcp-proxy', action='store_true',
                        help='Use Windows TCP transport, retaining WSL SSH authentication and forwards')
    args = parser.parse_args(argv)
    if not args.authorize_remote:
        raise RuntimeError('Independent connection smoke authorization required')
    root = create_unique_run_directory(args.output_root, args.run_id)
    root.chmod(0o700)
    hashes = {path: sha256_file(path) for path in CONFIGS}
    summary: dict[str, Any] = dict(run_id=args.run_id, status='BLOCKED', passed=False,
        completion_requests=0, automatic_retries=0, worker_failovers=0, get_count=0,
        get_counts={w: 0 for w in WORKERS}, tunnel_counts={w: 0 for w in WORKERS},
        workers_passed=[], ownership_verified=False, manifest_verified=False,
        worker1_revalidation_count=0, worker1_revalidation_passed=False)
    manager = ConnectionLifecycle(args.run_id, root/'connection_audit.jsonl')
    writer = JsonlTraceWriter(root/'worker_probe_audit.jsonl')
    sequence = JsonlTraceWriter(root/'execution_sequence.jsonl')
    clients = []
    cleanup: dict[str, Any] = dict(passed=False, workers={}, client_errors=[])
    def event(name: str, worker: str | None = None) -> None:
        sequence.append(dict(event=name, worker_id=worker, timestamp=now()))
    try:
        write_json(root/'run_manifest.json', dict(run_id=args.run_id, created_at=now(),
            git_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
            implementation_sha256={p: sha256_file(p) for p in (
                'scripts/run_p2_d2_connection_smoke.py', 'scripts/manage_remote_vllm.py',
                'src/risk_agent/monitoring/connection_lifecycle.py')},
            config_sha256=hashes, max_get_requests=2, max_get_requests_per_worker=1,
            ssh_ipqos=args.ssh_ipqos or 'OPENSSH_DEFAULT',
            windows_tcp_proxy=args.windows_tcp_proxy,
            http_timeout_seconds=dict(connect=5, read=10, write=10, pool=10),
            separate_openssl_budget_seconds=5, openssl_executed=False,
            trust_env=False, http_retries=0, follow_redirects=False,
            ca_validation=True, hostname_validation=True,
            approved_parameter_substitution='-F /dev/null replaces ClearAllForwardings=yes for Smoke tunnels only',
            formal_experiment=False, research_dataset_eligible=False))
        settings = RemoteModelSettings.from_yaml(CONFIGS[1])
        api_key = settings.resolve_api_key()
        if not api_key:
            raise ValueError('Existing API credential missing')
        context = strict_context(settings.resolve_ca_bundle())
        specs = specs_from_host()
        if args.ssh_ipqos is not None:
            for spec in specs.values():
                spec['ipqos'] = args.ssh_ipqos
        if args.windows_tcp_proxy:
            for spec in specs.values():
                spec['windows_tcp_proxy'] = True
        ownership = ownership_preflight(specs, root)
        write_json(root/'worker_ownership.json', ownership)
        verify_ownership(ownership)
        summary['ownership_verified'] = True
        event('Ownership Preflight')
        for worker in WORKERS:
            if worker == 'worker-1' and summary['workers_passed'] != ['worker-0']:
                raise RuntimeError('Worker 1 forbidden until Worker 0 full PASS')
            observe(manager)
            if worker == 'worker-1':
                event('Live Ownership Revalidation', worker)
                summary['worker1_revalidation_count'] += 1
                comparison = revalidate_worker1(specs, root, ownership)
                event('Ownership Comparison', worker)
                if not comparison['passed']:
                    raise ValueError('Worker 1 live ownership differs or cannot be verified')
                summary['worker1_revalidation_passed'] = True
                event('Ownership PASS', worker)
                observe(manager)
            summary['tunnel_counts'][worker] += 1
            event('Tunnel', worker)
            manager.launch_worker(worker, specs[worker])
            if manager.thread is None:
                manager.start_monitor()
            observe(manager)
            client = httpx2.Client(transport=httpx2.HTTPTransport(verify=context, retries=0, trust_env=False),
                timeout=httpx2.Timeout(10, connect=5), follow_redirects=False, trust_env=False)
            clients.append(client)
            if summary['get_counts'][worker] or summary['get_count'] >= 2:
                raise RuntimeError('GET budget exhausted')
            summary['get_count'] += 1
            summary['get_counts'][worker] += 1
            event('GET', worker)
            result = get_models(manager, worker, client,
                f'https://localhost:{8000+WORKERS.index(worker)}/v1/models', api_key, writer)
            if (result['state'] != 'API_REQUEST_SUCCESS' or result['http_status'] != 200
                    or result['tls_state'] != 'TLS_VALID' or 'Qwen/Qwen3-8B' not in result['model_ids']):
                raise RuntimeError('Worker full validation failed')
            event('PASS', worker)
            summary['workers_passed'].append(worker)
        summary.update(status='PASS', passed=True)
    except Exception as exc:
        summary.update(error=diagnose(exc, []), status='PARTIAL' if summary['workers_passed'] else 'BLOCKED')
    finally:
        for client in clients:
            try:
                client.close()
            except Exception as exc:
                cleanup['client_errors'].append(diagnose(exc, []))
        try:
            event('Cleanup')
        except Exception as exc:
            cleanup['audit_error'] = diagnose(exc, [])
        try:
            manager.close()
            cleanup['passed'] = True
        except Exception as exc:
            cleanup['error'] = diagnose(exc, [])
        try:
            for worker, adapter in manager.adapters.items():
                cleanup['workers'][worker] = dict(identity=asdict(manager.identities[worker]),
                    state=adapter.snapshot(), ssh_command=adapter.process.args,
                    stderr_bytes=adapter.stderr_path.stat().st_size)
            cleanup['manager_closed'] = manager.closed
            # Only check ports owned by this run; never adopt or clean a foreign listener.
            cleanup['local_ports_closed'] = not any(port_open(a.identity.local_port)
                                                    for a in manager.adapters.values())
            cleanup['owned_processes_exited'] = all(
                a.snapshot()['process_state'] == 'PROCESS_EXITED' for a in manager.adapters.values())
            cleanup['passed'] = bool(cleanup['passed'] and not cleanup['client_errors']
                and 'audit_error' not in cleanup and cleanup['manager_closed']
                and cleanup['owned_processes_exited'] and cleanup['local_ports_closed'])
        except Exception as exc:
            cleanup.update(passed=False, inspection_error=diagnose(exc, []))
        cleanup['recorded_at'] = now()
        summary['configs_unchanged'] = hashes == {path: sha256_file(path) for path in CONFIGS}
        summary['passed'] = bool(summary['passed'] and cleanup['passed']
            and summary['configs_unchanged'] and summary['ownership_verified']
            and summary['worker1_revalidation_passed']
            and summary['get_counts'] == dict.fromkeys(WORKERS, 1))
        if not summary['passed'] and summary['status'] == 'PASS':
            summary['status'] = 'INCONCLUSIVE'
        try:
            write_json(root/'resource_release.json', cleanup)
            # PASS is provisional until atomic publication and re-hash both succeed.
            summary['manifest_verified'] = True
            write_json(root/'gate_summary.json', summary)
            publish_manifest(root)
        except Exception as exc:
            summary.update(passed=False, manifest_verified=False, status='INCONCLUSIVE',
                           publication_error=diagnose(exc, []))
            # Preserve the unpublished candidate and failed seal. Never leave a PASS gate.
            for name in ('gate_summary.json', 'artifact_manifest.json'):
                if (root/name).exists():
                    (root/name).rename(root/('unpublished_' + name))
            write_json(root/'gate_summary.json', summary)
    print(json.dumps(summary, indent=2))
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
