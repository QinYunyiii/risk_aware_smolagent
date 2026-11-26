"""Standalone remote-loopback TLS diagnostic; importing never opens a connection."""
from __future__ import annotations

import inspect
import json
from pathlib import Path
import shlex
import subprocess
import time
from typing import Any

from risk_agent.monitoring.provider_response_audit import sanitize_audit_value

CA_PATH = "/root/.config/risk-agent/tls/ca.crt"
CA_SHA256 = "97e07749f90c2d418a8dce22148c2e1fc77a14adbf913d6c74665e477c4b5bef"
REQUIRED_FLAGS = ("-connect", "-servername", "-verify_hostname", "-verify_return_error",
                  "-CAfile", "-brief", "-state", "-msg")


def bounded_process(command, limit, on_line, stdin_payload=None, env=None):
    """Own exactly this child. Total process budget, not a TCP-only timeout.

    Kept stdlib-only so the same tested function can run over SSH stdin, without files.
    """
    import os
    import datetime
    import selectors
    import subprocess
    import threading
    import time
    start = time.monotonic()
    start_ns=time.monotonic_ns()
    start_utc=datetime.datetime.now(datetime.timezone.utc).isoformat()
    process = subprocess.Popen(command, stdin=subprocess.PIPE if stdin_payload is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, env=env)
    try:
        with open('/proc/{}/stat'.format(process.pid)) as handle:
            start_ticks=handle.read().rsplit(')',1)[1].split()[19]
    except OSError: start_ticks='unavailable: owned child exited before sampling'
    stdin_error = []
    writer = None
    if stdin_payload is not None:
        def deliver():
            try:
                process.stdin.write(stdin_payload)
                process.stdin.close()
            except (OSError, ValueError) as exc: stdin_error.append(type(exc).__name__)
        writer = threading.Thread(target=deliver, daemon=True)
        writer.start()
    deadline_fired = threading.Event()
    def expire_owned_child():
        if process.poll() is None:
            deadline_fired.set()
            process.terminate()
            try: process.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=2)
    watchdog=threading.Timer(max(0,limit-(time.monotonic()-start)),expire_owned_child)
    watchdog.daemon=True
    watchdog.start()
    buffers = {}
    selector = selectors.DefaultSelector()
    for label, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, label)
        buffers[label] = b""
    try:
        while selector.get_map():
            remaining = limit - (time.monotonic() - start)
            # Watchdog enforces the deadline even if on_line/audit output blocks.
            for key, _ in selector.select(timeout=max(0.001, min(0.05, remaining))):
                chunk = os.read(key.fd, 65536)
                label = key.data
                if not chunk:
                    if buffers[label]: on_line(label, buffers[label].decode("utf-8", "replace"))
                    selector.unregister(key.fileobj)
                    continue
                buffers[label] += chunk
                while b"\n" in buffers[label]:
                    line, buffers[label] = buffers[label].split(b"\n", 1)
                    on_line(label, line.decode("utf-8", "replace"))
        code = process.wait(timeout=1)
        return {"returncode": code, "timed_out": deadline_fired.is_set(),
                "elapsed_seconds": time.monotonic() - start, "owned_pid": process.pid,
                "deadline_seconds": limit, "stdin_delivery_error": stdin_error,
                "started_at_utc":start_utc,"started_monotonic_ns":start_ns,
                "ended_at_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "ended_monotonic_ns":time.monotonic_ns(),"process_start_ticks":start_ticks,
                "owned_child_reaped":process.poll() is not None}
    finally:
        # Covers audit/parser exceptions; never targets a discovered PID or service.
        watchdog.cancel()
        watchdog.join(timeout=3)
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=2)
        selector.close()
        if writer: writer.join(timeout=1)
        process.stdout.close(); process.stderr.close()


def remote_entry(runner=None, emit=None, expected_ca="97e07749f90c2d418a8dce22148c2e1fc77a14adbf913d6c74665e477c4b5bef"):
    """The actual remote program, also callable with fully offline fake runners."""
    import datetime
    import hashlib
    import json
    import os
    import re
    import subprocess
    import time
    start_ns = time.monotonic_ns()
    if emit is None:
        def emit(record): print(json.dumps(record, ensure_ascii=False), flush=True)
    def event(name, **fields):
        emit(dict(event=name, utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  monotonic_ns=time.monotonic_ns(), clock_domain="remote_python",
                  elapsed_seconds=(time.monotonic_ns()-start_ns)/1e9, **fields))
    class Runner:
        def identity(self): return dict(hostname=os.uname().nodename,uid=os.getuid())
        def tool_info(self):
            version = subprocess.run(["openssl", "version"], capture_output=True, text=True, timeout=3, check=True)
            help_result = subprocess.run(["openssl", "s_client", "-help"], capture_output=True, text=True, timeout=3)
            return version.stdout.strip(), help_result.stdout + help_result.stderr
        def read_ca(self):
            path = "/root/.config/risk-agent/tls/ca.crt"
            if not os.path.isfile(path): raise FileNotFoundError("existing remote CA missing")
            if not os.access(path, os.R_OK): raise PermissionError("existing remote CA unreadable")
            with open(path, "rb") as handle: return handle.read()
        def probe(self, command, budget, callback): return bounded_process(command, budget, callback)
    if runner is None: runner = Runner()
    event("remote_command_started")
    try:
        identity=runner.identity()
        event('remote_identity_observed',**identity)
        if identity.get('hostname')!='36f825bcc5da' or identity.get('uid')!=0:
            raise ValueError('Remote identity differs from authorized historical target; stop for review')
        version_text, help_text = runner.tool_info()
        needed = ("-connect", "-servername", "-verify_hostname", "-verify_return_error", "-CAfile", "-brief", "-state", "-msg")
        missing = [flag for flag in needed if not re.search(r"(?<!\S)"+re.escape(flag)+r"(?=\s|$)", help_text)]
        supported_version = bool(re.match(r"OpenSSL 3\.", version_text))
        event("openssl_capability_checked", version=version_text, missing_flags=missing,
              supported=supported_version and not missing)
        if not supported_version or missing:
            raise ValueError("Unsupported or unverified OpenSSL version/options")
        ca = runner.read_ca()
        actual_hash = hashlib.sha256(ca).hexdigest()
        if actual_hash != expected_ca: raise ValueError("Remote CA identity mismatch")
        event("remote_ca_verified", path="/root/.config/risk-agent/tls/ca.crt", sha256=actual_hash)
        command = ["openssl", "s_client", "-connect", "127.0.0.1:8000", "-servername", "localhost",
                   "-verify_hostname", "localhost", "-verify_return_error", "-CAfile",
                   "/root/.config/risk-agent/tls/ca.crt", "-brief", "-state", "-msg"]
        state = dict(tcp=False, hello=False, verified=False, complete=False, certificate_error=False, tcp_error=False,
                     connection_established=False, protocol=None, ciphersuite=None, fatal_alert=False,
                     finished_sent=False, finished_received=False)
        def line(source, text):
            # Only state/message summaries; never persist hex dumps, tickets or session secrets.
            stripped = text.strip()
            name = None
            if "CONNECTED(" in text:
                state["tcp"] = True; name = "tcp_connect_complete_observed"
            elif "SSL_connect:" in text:
                if not state["tcp"]:
                    state["tcp"] = True
                    event("tcp_connect_complete_inferred", source="openssl_ssl_connect_state", time_is_observation=True)
                name = "tls_state"
                if re.fullmatch(r"SSL_connect:\s*SSL negotiation finished successfully", stripped):
                    state["complete"] = True; name = "tls_handshake_complete"
            if re.search(r">>> .*Handshake.*ClientHello", text):
                state["hello"] = True; name = "client_hello_sent_observed"
            elif re.search(r"<<< .*Handshake.*ServerHello", text): name = "server_hello_received_observed"
            elif re.search(r"<<< .*Handshake.*Certificate(?:\s|$)", text): name = "certificate_received_observed"
            elif re.search(r">>> .*Handshake.*\bFinished\s*$", stripped):
                state["finished_sent"] = True; name = "client_finished_sent_observed"
            elif re.search(r"<<< .*Handshake.*\bFinished\s*$", stripped):
                state["finished_received"] = True; name = "server_finished_received_observed"
            elif stripped == "CONNECTION ESTABLISHED":
                state["tcp"] = True; state["connection_established"] = True
                name = "tls_connection_established_summary"
            elif re.fullmatch(r"Protocol version:\s*TLSv1(?:\.[0-3])?", stripped):
                state["protocol"] = stripped.split(":", 1)[1].strip(); name = "tls_protocol_summary"
            elif re.fullmatch(r"Ciphersuite:\s*\S+", stripped):
                cipher = stripped.split(":", 1)[1].strip()
                state["ciphersuite"] = (cipher if re.fullmatch(r"[A-Za-z0-9_-]+", cipher)
                                        and cipher.upper() != "NONE" and "NULL" not in cipher.upper() else None)
                name = "tls_ciphersuite_summary"
            elif re.fullmatch(r"Verification:\s*OK|Verify return code:\s*0 \(ok\)", stripped):
                state["verified"] = True; name = "certificate_verification_ok"
            elif "verify error:" in text or "certificate verify failed" in text or re.search(r"Verification error:", text):
                state["certificate_error"] = True; name = "certificate_verification_failed"
            elif any(token in text for token in ("Connection refused", "Network is unreachable")) or re.search(r"connect:errno=[1-9][0-9]*",text):
                state["tcp_error"] = True; name = "remote_tcp_failure"
            elif re.search(r"\balert\b", text, re.IGNORECASE):
                if re.search(r"\bfatal\b|\balert number\s+\d+", text, re.IGNORECASE):
                    state["fatal_alert"] = True
                name = "tls_alert"
            if name:
                event(name, stream=source, summary=stripped[:1000])
        event("tcp_connect_attempt_started", scope="openssl_process_start_not_exact_socket_connect", budget_seconds=5)
        outcome = runner.probe(command, 5.0, line)
        # A complete brief connection summary is an alternative explicit completion
        # report. Verify OK, Finished state names or exit 0 alone never set complete.
        if (not state["complete"] and state["connection_established"]
                and state["protocol"] and state["ciphersuite"]):
            state["complete"] = True
            event("tls_handshake_complete", source="openssl_brief_connection_summary",
                  protocol=state["protocol"], ciphersuite=state["ciphersuite"])
        if state["certificate_error"]:
            category = "REMOTE_CERTIFICATE_VERIFICATION_FAILURE"
        elif state["tcp_error"] and not state["tcp"]:
            category = "REMOTE_TCP_FAILURE"
        elif outcome["timed_out"] and state["tcp"] and not state["complete"]:
            category = "REMOTE_TLS_HANDSHAKE_TIMEOUT"
        elif (outcome["returncode"] == 0 and not outcome["timed_out"]
              and state["complete"] and state["verified"] and not state["fatal_alert"]):
            category = "REMOTE_TLS_SUCCESS"
        else:
            category = "DIAGNOSTIC_INCONCLUSIVE"
        if outcome["timed_out"]:
            event("owned_openssl_deadline_expired", tcp_observed=state["tcp"], handshake_complete=state["complete"], process_outcome=outcome)
        event("tls_process_closed", process_outcome=outcome)
        result = dict(category=category, state=state, outcome=outcome,
                      tcp_connect_exact_duration="unavailable", tls_handshake_only_budget="unavailable; shared total process budget <=5 seconds")
    except Exception as exc:
        chain=[]; current=exc; seen=set()
        while current is not None and id(current) not in seen:
            seen.add(id(current)); chain.append(dict(type=type(current).__name__,message=str(current)))
            current=current.__cause__ or current.__context__
        result = dict(category="DIAGNOSTIC_INCONCLUSIVE", error_type=type(exc).__name__, error_message=str(exc),exception_chain=chain)
    event("remote_command_finished", **result)
    return result


def remote_program() -> str:
    # Source text only, no CA/key contents, API credential or files transferred.
    return inspect.getsource(bounded_process) + "\n" + inspect.getsource(remote_entry) + "\nremote_entry()\n"


def ssh_command() -> list[str]:
    return ["ssh", "-T", "-v", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ClearAllForwardings=yes", "-o", "ForwardAgent=no", "-o", "PasswordAuthentication=no",
            "-o", "IdentitiesOnly=yes", "-o", "ConnectionAttempts=1", "-o", "ConnectTimeout=10",
            "-o", "ControlMaster=no", "-o", "ControlPath=none", "risk-agent-remote", "python3 -B -"]


def classify_ssh(returncode: int, events: list[dict[str,Any]], stderr: str) -> str:
    if returncode == 255 and any(s in stderr.lower() for s in ("permission denied", "authentication failed")):
        return "SSH_AUTHENTICATION_FAILURE"
    if returncode != 0: return "SSH_REMOTE_COMMAND_FAILURE"
    ends = [e for e in events if e.get("event") == "remote_command_finished"]
    return ends[-1]["category"] if len(ends)==1 else "DIAGNOSTIC_INCONCLUSIVE"


def offline_preflight() -> dict[str,Any]:
    """Local version/help queries only. No s_client connection or SSH invocation."""
    import re
    version_result = subprocess.run(["openssl","version"],capture_output=True,text=True,check=True,timeout=5)
    help_result = subprocess.run(["openssl","s_client","-help"],capture_output=True,text=True,timeout=5)
    help_text = help_result.stdout + help_result.stderr
    missing = [f for f in REQUIRED_FLAGS if not re.search(r"(?<!\S)"+re.escape(f)+r"(?=\s|$)",help_text)]
    return dict(local_openssl_version=version_result.stdout.strip(),local_missing_flags=missing,
                remote_openssl_compatibility="UNKNOWN; must verify in-band before any future handshake",
                remote_ca_current_state="UNKNOWN; historical path and identity only",
                stage_a_gate="REMOTE_TLS_ISOLATION_STAGE_A_BLOCKED",
                reason="Actual remote OpenSSL version/help evidence unavailable offline",
                ssh_command=ssh_command(),handshake_budget_seconds=5,network_requests=0)


class SSHRunner:
    def run(self, command: list[str], payload: bytes, callback: Any) -> dict[str,Any]:
        import os
        # No API credential is needed or inherited by this diagnostic child.
        env={k:os.environ[k] for k in ('PATH','LANG','LC_ALL') if k in os.environ}
        return bounded_process(command,40.0,callback,stdin_payload=payload,env=env)


def run_authorized(root: Path, run_id: str, runner: Any=None) -> dict[str,Any]:
    """Only the CLI's explicitly authorized mode calls this; single SSH, zero forwards."""
    from datetime import datetime, timezone
    from risk_agent.monitoring.trajectory import JsonlTraceWriter
    if runner is None: runner=SSHRunner()
    writer=JsonlTraceWriter(root/'ssh_remote_command_audit.jsonl')
    events=[]
    stderr=[]
    auth_ns=None
    start=time.monotonic_ns()
    def emit(kind,**values):
        record=sanitize_audit_value(dict(event=kind,run_id=run_id,utc=datetime.now(timezone.utc).isoformat(),
            monotonic_ns=time.monotonic_ns(),clock_domain='local_wsl',**values))
        writer.append(record)
    def line(source,text):
        nonlocal auth_ns
        if source=='stderr':
            stderr.append(text)
            if 'Authenticated to ' in text: auth_ns=time.monotonic_ns()
            emit('ssh_stderr_received',line=text)
        else:
            item=json.loads(text)  # unexpected/non-JSON stdout is fail-closed
            events.append(item)
            emit('remote_event_received',remote_event=item)
    emit('ssh_process_launch_requested',command=ssh_command())
    try:
        outcome=runner.run(ssh_command(),remote_program().encode(),line)
        emit('ssh_process_finished',**outcome)
        category=classify_ssh(outcome['returncode'],events,'\n'.join(stderr))
        if outcome['timed_out']: category='DIAGNOSTIC_INCONCLUSIVE'
        return dict(category=category,ssh=outcome,remote_events=events,
                    ssh_authentication_observed_seconds=None if auth_ns is None else (auth_ns-start)/1e9,
                    total_local_elapsed_seconds=(time.monotonic_ns()-start)/1e9,
                    remote_tls_attempts=sum(e.get('event')=='tcp_connect_attempt_started' for e in events))
    except BaseException as exc:
        # The process runner's finally has already terminated its owned child.
        emit('diagnostic_failed',exception_type=type(exc).__name__,exception_message=str(exc))
        return dict(category='DIAGNOSTIC_INCONCLUSIVE',error_type=type(exc).__name__,
                    error_message=str(exc),remote_events=events)


def write_json_exclusive(path: Path, value: Any) -> None:
    import os
    with path.open('x',encoding='utf-8') as handle:
        json.dump(sanitize_audit_value(value),handle,ensure_ascii=False,indent=2)
        handle.write('\n'); handle.flush(); os.fsync(handle.fileno())
