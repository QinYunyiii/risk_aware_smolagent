"""Run-owned local connection lifecycle; never retries or probes a Provider.

Process adapters own a live child handle, not a recovered PID. Windows external
owners can implement the same snapshot/close interface; native support is not claimed.
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from .provider_response_audit import AuditPersistenceError, sanitize_audit_value


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ConnectionIdentity:
    run_id: str
    worker_id: str
    tunnel_id: str
    platform: str
    local_port: int
    remote_target: str
    pid: int
    process_start_identity: str
    control_socket: str | None
    launch_timestamp: str
    owner: str = "run_connection_manager"
    cleanup_responsibility: str = "run_connection_manager"


class ProcessAdapter(Protocol):
    identity: ConnectionIdentity

    def snapshot(self) -> dict[str, Any]: ...
    def close(self, expected: ConnectionIdentity) -> None: ...


def diagnose(exc: BaseException, observations: list[dict[str, Any]]) -> dict[str, Any]:
    chain = []
    exceptions = []
    seen = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        exceptions.append(current)
        chain.append({"type": type(current).__name__, "message": str(current)})
        current = current.__cause__ or current.__context__
    names = " ".join(item["type"].lower() for item in chain)
    messages = " ".join(item["message"].lower() for item in chain)
    if any(isinstance(x, ssl.SSLError) for x in exceptions) or "certificate verify" in messages or "ssl" in names:
        category, state = "tls_connection_failure", "TLS_HANDSHAKE_FAILED"
    elif "readtimeout" in names or any(
        isinstance(item, TimeoutError) and "read" in str(item).lower()
        for item in exceptions
    ):
        category, state = "provider_read_timeout", "PROVIDER_REQUEST_TIMEOUT"
    elif "timeout" in names:
        category, state = "connection_failure_unknown", "PROVIDER_REQUEST_TIMEOUT"
    elif any(getattr(item, "status_code", None) is not None for item in exceptions):
        category, state = "provider_http_error", "PROVIDER_HTTP_ERROR"
    elif any(o.get("process_state") == "PROCESS_EXITED" for o in observations):
        category, state = "ssh_tunnel_exited_observed", "PROVIDER_CONNECTION_ERROR"
    elif any(o.get("listener_state") == "LOCAL_PORT_CLOSED" for o in observations):
        category, state = "local_forward_unavailable", "PROVIDER_CONNECTION_ERROR"
    elif "connect" in names or "connection" in names:
        category, state = "provider_connection_failure", "PROVIDER_CONNECTION_ERROR"
    else:
        category, state = "connection_failure_unknown", "UNKNOWN"
    return sanitize_audit_value({
        "research_failure_category": "infrastructure_failure",
        "connection_diagnostic": category,
        "provider_state": state,
        "exception_chain": chain,
        "evidence_confidence": "CONFIRMED" if category != "connection_failure_unknown" else "UNDETERMINED",
        "root_cause_confidence": "UNDETERMINED",
        "causal_relationship": "NOT ESTABLISHED",
        "unconfirmed": ["SSH exit cause", "causal relationship to Provider failure"],
        "observations": observations,
    })


class SSHProcess:
    """Foreground OpenSSH owned by its Popen handle in an independent session."""

    def __init__(self, *, run_id: str, worker_id: str, spec: dict[str, Any], stderr_path: Path):
        self.stderr_path = stderr_path
        self._stderr = stderr_path.open("x", encoding="utf-8")
        stderr_path.chmod(0o600)
        port = int(spec["local_port"])
        target = str(spec["target"])
        command = [
            "ssh", "-F", "/dev/null", "-N", "-T", "-i", str(spec["identity_file"]),
            "-p", str(int(spec["ssh_port"])), "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "UserKnownHostsFile=" + str(spec["known_hosts_file"]),
            "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=3", "-o", "ConnectTimeout=15",
            "-o", "ControlMaster=no", "-o", "ControlPath=none",
            "-L", f"127.0.0.1:{port}:127.0.0.1:{int(spec['remote_port'])}", target,
        ]
        if not target or target.startswith("-"):
            self._stderr.close()
            raise ValueError("Invalid explicit SSH target")
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=self._stderr, start_new_session=True)
        except BaseException:
            self._stderr.close()
            raise
        self.identity = ConnectionIdentity(run_id, worker_id, uuid.uuid4().hex,
            "WSL/OpenSSH", port, target, self.process.pid, uuid.uuid4().hex,
            None, now())

    def snapshot(self) -> dict[str, Any]:
        code = self.process.poll()
        with socket.socket() as sock:
            sock.settimeout(0.1)
            listening = sock.connect_ex(("127.0.0.1", self.identity.local_port)) == 0
        return {"process_state": "PROCESS_ALIVE" if code is None else "PROCESS_EXITED",
                "exit_code": code,
                "listener_state": "LOCAL_PORT_LISTENING" if listening else "LOCAL_PORT_CLOSED",
                "source": "owned_Popen_handle_and_local_socket"}

    def close(self, expected: ConnectionIdentity) -> None:
        if expected != self.identity:
            raise RuntimeError("Connection ownership mismatch")
        # Popen.poll reaps this exact child. A reused PID cannot be targeted.
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=10)
        self._stderr.flush()
        os.fsync(self._stderr.fileno())
        self._stderr.close()


class ConnectionLifecycle:
    def __init__(self, run_id: str, path: Path, *, interval: float = 0.25):
        self.run_id, self.path, self.interval = run_id, path, interval
        self.adapters: dict[str, ProcessAdapter] = {}
        self.identities: dict[str, ConnectionIdentity] = {}
        self.active: dict[str, str] = {}
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.error: BaseException | None = None
        self.thread: threading.Thread | None = None
        self.exits: set[str] = set()
        self.closed = False

    def event(self, kind: str, worker: str, request_id: str | None = None,
              **extra: Any) -> dict[str, Any]:
        with self.lock:
            adapter = self.adapters.get(worker)
            snapshot = adapter.snapshot() if adapter else {
                "process_state": "UNKNOWN", "listener_state": "UNKNOWN", "source": "unmanaged"}
            record = sanitize_audit_value({"event": kind, "run_id": self.run_id,
                "worker_id": worker, "timestamp": now(),
                "identity": asdict(self.identities[worker]) if worker in self.identities else None,
                "provider_request_id": request_id, **snapshot, **extra})
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    os.chmod(self.path, 0o600)
                    handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception as exc:
                raise AuditPersistenceError("Connection audit persistence failed") from exc
            return record

    def add(self, adapter: ProcessAdapter) -> None:
        identity = adapter.identity
        if identity.run_id != self.run_id or identity.worker_id in self.adapters or self.closed:
            raise ValueError("Duplicate or foreign connection ownership")
        self.adapters[identity.worker_id] = adapter
        self.identities[identity.worker_id] = identity
        self.event("connection_created", identity.worker_id)

    def start_monitor(self) -> None:
        def monitor() -> None:
            try:
                while not self.stop.wait(self.interval):
                    with self.lock:
                        for worker in self.adapters:
                            record = self.event("connection_health_observed", worker, self.active.get(worker))
                            if record["process_state"] == "PROCESS_EXITED" and worker not in self.exits:
                                self.exits.add(worker)
                                self.event("connection_lost_observed", worker, self.active.get(worker))
            except BaseException as exc:
                self.error = exc
        self.thread = threading.Thread(target=monitor, daemon=True)
        self.thread.start()

    def request_started(self, worker: str, request_id: str) -> None:
        with self.lock:
            if self.closed or self.error:
                raise AuditPersistenceError("Connection observer unavailable") from self.error
            self.active[worker] = request_id
            try:
                self.event("provider_request_started", worker, request_id)
            except BaseException:
                self.active.pop(worker, None)
                raise

    def request_finished(self, worker: str, request_id: str, exc: BaseException | None = None) -> None:
        with self.lock:
            try:
                record = self.event("provider_request_failed" if exc else "provider_request_finished",
                                    worker, request_id, exception_type=type(exc).__name__ if exc else None)
            finally:
                self.active.pop(worker, None)
            if exc:
                self.event("connection_failure_diagnosed", worker, request_id,
                           diagnosis=diagnose(exc, [record]))
            if self.error:
                raise AuditPersistenceError("Connection monitor failed") from self.error

    def launch(self, specs: dict[str, Any], *, factory: Callable[..., ProcessAdapter] = SSHProcess) -> None:
        if set(specs) != {"worker-0", "worker-1"}:
            raise ValueError("Two independent frozen Workers required")
        for worker, port in (("worker-0", 8000), ("worker-1", 8001)):
            spec = specs[worker]
            if spec["local_port"] != port or spec["remote_port"] != port:
                raise ValueError("Frozen forwarding ports differ")
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    raise RuntimeError("Port already owned; refusing adoption")
            adapter = factory(run_id=self.run_id, worker_id=worker, spec=spec,
                              stderr_path=self.path.parent / f"{worker}_ssh.stderr")
            try:
                self.add(adapter)
            except BaseException:
                adapter.close(adapter.identity)
                raise
            deadline = time.monotonic() + 30
            while True:
                state = adapter.snapshot()
                if state["process_state"] == "PROCESS_EXITED":
                    self.event("connection_lost_observed", worker)
                    raise RuntimeError("Owned SSH process exited during startup")
                if state["listener_state"] == "LOCAL_PORT_LISTENING":
                    self.event("connection_ready", worker)
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Owned SSH forward readiness timeout")
                time.sleep(0.1)
        self.start_monitor()

    def close(self) -> None:
        with self.lock:
            if self.active:
                raise RuntimeError("Cannot cleanup during active Provider request")
            self.closed = True
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=5)
        errors = []
        for worker, adapter in self.adapters.items():
            try:
                self.event("connection_cleanup_started", worker)
            except Exception as exc:
                errors.append(exc)
            try:
                adapter.close(self.identities[worker])
                self.event("connection_cleanup_finished", worker)
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError("Connection cleanup incomplete; inspect owned resources") from errors[0]
