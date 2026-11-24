from __future__ import annotations

import dataclasses
import json
import ssl
import threading
from pathlib import Path

import pytest

from risk_agent.monitoring.connection_lifecycle import (
    ConnectionIdentity, ConnectionLifecycle, SSHProcess, diagnose,
)


class FakeProcess:
    def __init__(self, platform="WSL/OpenSSH", run="run", pid=100):
        self.identity = ConnectionIdentity(run, "worker-0", "tunnel", platform, 8000,
            "explicit-target", pid, "start-a", "owned.sock", "2026-11-25T00:00:00Z")
        self.code = None
        self.listening = True
        self.closed = False

    def snapshot(self):
        return {"process_state": "PROCESS_ALIVE" if self.code is None else "PROCESS_EXITED",
                "exit_code": self.code,
                "listener_state": "LOCAL_PORT_LISTENING" if self.listening else "LOCAL_PORT_CLOSED",
                "source": "fake_adapter"}

    def close(self, expected):
        if expected != self.identity:
            raise RuntimeError("ownership mismatch")
        self.closed = True
        self.code = 0 if self.code is None else self.code


def records(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_stable_lifecycle_and_active_cleanup_guard(tmp_path):
    path = tmp_path / "connections.jsonl"
    c = ConnectionLifecycle("run", path)
    p = FakeProcess()
    c.add(p)
    c.event("connection_ready", "worker-0")
    c.request_started("worker-0", "request")
    with pytest.raises(RuntimeError, match="active"):
        c.close()
    assert not p.closed
    c.request_finished("worker-0", "request")
    prefix = path.read_bytes()
    c.close()
    assert p.closed
    assert path.read_bytes().startswith(prefix)
    assert [r['event'] for r in records(path)] == [
        "connection_created", "connection_ready", "provider_request_started",
        "provider_request_finished", "connection_cleanup_started", "connection_cleanup_finished"]


def test_exit_observed_during_request_has_code_and_request_lineage(tmp_path):
    c = ConnectionLifecycle("run", tmp_path / "audit", interval=0.001)
    p = FakeProcess()
    c.add(p)
    c.request_started("worker-0", "req")
    p.code = 255
    c.start_monitor()
    for _ in range(100):
        if "worker-0" in c.exits:
            break
        threading.Event().wait(0.005)
    c.request_finished("worker-0", "req", ConnectionError("closed"))
    c.close()
    lost = next(r for r in records(c.path) if r["event"] == "connection_lost_observed")
    assert lost["exit_code"] == 255
    assert lost["provider_request_id"] == "req"
    assert lost["timestamp"]
    assert lost["identity"]["process_start_identity"] == "start-a"


@pytest.mark.parametrize("alive,listening,category", [
    (True, False, "local_forward_unavailable"),
    (True, True, "provider_connection_failure"),
    (False, False, "ssh_tunnel_exited_observed"),
])
def test_process_and_listener_are_separate_observations(alive, listening, category):
    p = FakeProcess()
    p.code = None if alive else 255
    p.listening = listening
    d = diagnose(ConnectionError("closed"), [p.snapshot()])
    assert d["connection_diagnostic"] == category
    assert d["root_cause_confidence"] == "UNDETERMINED"


@pytest.mark.parametrize("exc,expected", [
    (TimeoutError("read timeout"), "provider_read_timeout"),
    (ssl.SSLCertVerificationError("certificate verify failed"), "tls_connection_failure"),
    (ValueError("unknown"), "connection_failure_unknown"),
])
def test_exception_categories_remain_external_to_research_failure(exc, expected):
    d = diagnose(exc, [])
    assert d["connection_diagnostic"] == expected
    assert d["research_failure_category"] == "infrastructure_failure"
    assert d["exception_chain"][0]["type"] == type(exc).__name__


@pytest.mark.parametrize("order", ["exit_first", "api_first", "api_only"])
def test_temporal_observations_do_not_claim_causality(tmp_path, order):
    c = ConnectionLifecycle("run", tmp_path / "events")
    p = FakeProcess()
    c.add(p)
    c.request_started("worker-0", "req")
    if order == "exit_first":
        p.code = 255
        c.event("connection_lost_observed", "worker-0", "req")
    c.request_finished("worker-0", "req", ConnectionError("failed"))
    if order == "api_first":
        p.code = 255
        c.event("connection_lost_observed", "worker-0")
    diagnosis = next(r['diagnosis'] for r in records(c.path) if r['event'] == 'connection_failure_diagnosed')
    assert diagnosis['root_cause_confidence'] == 'UNDETERMINED'
    assert diagnosis['connection_diagnostic'] == (
        'ssh_tunnel_exited_observed' if order == 'exit_first' else 'provider_connection_failure')
    c.close()


@pytest.mark.parametrize("change", ["pid_reused", "foreign_socket", "already_exited"])
def test_cleanup_never_targets_foreign_identity(tmp_path, change):
    c = ConnectionLifecycle("run", tmp_path / "audit")
    p = FakeProcess()
    c.add(p)
    if change == "pid_reused":
        p.identity = dataclasses.replace(p.identity, process_start_identity="start-b")
    elif change == "foreign_socket":
        p.identity = dataclasses.replace(p.identity, run_id="another-run")
    else:
        p.code = 255
    if change == "already_exited":
        c.close()
        assert p.code == 255
    else:
        with pytest.raises(RuntimeError, match="cleanup incomplete"):
            c.close()
        assert not p.closed


@pytest.mark.parametrize("platform,pid", [("WSL/OpenSSH", 1), ("Windows/external-owner", 700)])
def test_platform_adapter_identity_and_parent_exit_simulation(tmp_path, platform, pid):
    c = ConnectionLifecycle("run", tmp_path / "audit")
    p = FakeProcess(platform=platform, pid=pid)
    c.add(p)
    p.code = 1
    r = c.event("connection_lost_observed", "worker-0")
    assert r['identity']['platform'] == platform
    assert r['identity']['pid'] == pid
    assert r['process_state'] == 'PROCESS_EXITED'
    c.close()


def test_ssh_launch_uses_independent_foreground_child_no_shell(monkeypatch, tmp_path):
    captured = {}
    class Child:
        pid = 345
        def poll(self): return 255
    def popen(command, **kwargs):
        captured.update(command=command, **kwargs)
        return Child()
    monkeypatch.setattr("subprocess.Popen", popen)
    p = SSHProcess(run_id="run", worker_id="worker-0", stderr_path=tmp_path / "stderr",
        spec=dict(local_port=8000, remote_port=8000, target="user@example.invalid",
                  ssh_port=2222, identity_file="explicit-key", known_hosts_file="known-hosts"))
    assert captured['start_new_session'] is True
    assert '-f' not in captured['command']
    assert 'StrictHostKeyChecking=yes' in captured['command']
    assert 'ControlMaster=no' in captured['command']
    p.close(p.identity)


def test_unmanaged_connection_is_unknown_and_secrets_are_redacted(tmp_path):
    c = ConnectionLifecycle("run", tmp_path / "events")
    c.request_started("worker-1", "req")
    c.request_finished("worker-1", "req", ConnectionError("Bearer secret-token"))
    events = records(c.path)
    assert events[0]['process_state'] == 'UNKNOWN'
    assert events[0]['listener_state'] == 'UNKNOWN'
    assert 'secret-token' not in c.path.read_text()
    c.close()


def test_two_worker_launch_and_partial_startup_cleanup(monkeypatch, tmp_path):
    class ClosedSocket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def connect_ex(self, target): return 1
    monkeypatch.setattr('socket.socket', ClosedSocket)
    created = []
    def factory(**kwargs):
        p = FakeProcess()
        p.identity = dataclasses.replace(p.identity, worker_id=kwargs['worker_id'])
        created.append(p)
        return p
    c = ConnectionLifecycle('run', tmp_path / 'audit')
    specs = {f'worker-{i}': {'local_port': 8000+i, 'remote_port': 8000+i} for i in (0,1)}
    c.launch(specs, factory=factory)
    assert len(c.adapters) == 2
    c.close()
    assert all(p.closed for p in created)
    assert len([r for r in records(c.path) if r['event']=='connection_ready']) == 2

    partial = ConnectionLifecycle('run', tmp_path / 'partial')
    def failing_factory(**kwargs):
        if kwargs['worker_id'] == 'worker-1':
            raise OSError('synthetic launch failure')
        return factory(**kwargs)
    with pytest.raises(OSError):
        partial.launch(specs, factory=failing_factory)
    partial.close()
    assert created[-1].closed


def test_existing_port_never_adopted_or_cleaned(monkeypatch, tmp_path):
    class Occupied:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def connect_ex(self, target): return 0
    monkeypatch.setattr('socket.socket', Occupied)
    c = ConnectionLifecycle('run', tmp_path / 'audit')
    with pytest.raises(RuntimeError, match='refusing adoption'):
        c.launch({f'worker-{i}': {'local_port':8000+i,'remote_port':8000+i} for i in (0,1)},
                 factory=lambda **k: pytest.fail('must not launch'))
    c.close()
    assert c.adapters == {}
