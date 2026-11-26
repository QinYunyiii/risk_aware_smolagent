#!/usr/bin/env python3
"""Independent, opt-in Worker-0 diagnostic. Importing this module never uses network."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import queue
import re
import ssl
import subprocess
import threading
import time
import traceback
from typing import Any
import uuid

import httpx2

from risk_agent.agent.worker_router import RemoteModelSettings
from risk_agent.collection.day5_gate import create_unique_run_directory, sha256_file
from risk_agent.monitoring.connection_lifecycle import ConnectionIdentity, ConnectionLifecycle, diagnose, now
from risk_agent.monitoring.provider_response_audit import sanitize_audit_value
from risk_agent.monitoring.trajectory import JsonlTraceWriter

try:
    from scripts.run_p2_d2_connection_smoke import specs_from_host, write_json, CONFIGS
except ModuleNotFoundError:  # direct CLI
    from run_p2_d2_connection_smoke import specs_from_host, write_json, CONFIGS


URL = "https://localhost:8000/v1/models"
EXPECTED_CONFIG_HASHES={
    CONFIGS[0]:"3d3c5f161f9fa13c518e00a193eb54e74452a43c3ecb36f029d8df2986a5d3b4",
    CONFIGS[1]:"4731cb56c128e3c150712745e25b9881fd8c19d64a37fd87a97f48ae3a5a57a5",
}


class Audit:
    """One clock for SSH reader receipt timestamps and real HTTPCore trace callbacks."""
    def __init__(self, root: Path, run_id: str):
        self.root, self.run_id = root, run_id
        self.origin_ns = time.monotonic_ns()
        self.request_ns: int | None = None
        self.request_id="tls-diagnostic-"+uuid.uuid4().hex
        self.lock = threading.RLock()
        self.events: list[dict[str, Any]] = []
        self.peer: dict[str, Any] = {}
        for name in ("ssh_process_audit.jsonl","ssh_channel_trace.jsonl","tcp_tls_trace.jsonl","worker_probe_audit.jsonl"):
            (root/name).touch(exist_ok=False,mode=0o600)

    def stamp(self) -> dict[str, Any]:
        ns = time.monotonic_ns()
        return dict(utc=now(), monotonic_ns=ns, elapsed_seconds=(ns-self.origin_ns)/1e9,
                    request_elapsed_seconds=None if self.request_ns is None else (ns-self.request_ns)/1e9)

    def emit(self, filename: str, event: str, stamp: dict | None = None, **fields: Any) -> None:
        with self.lock:
            record = sanitize_audit_value(dict(run_id=self.run_id, worker_id="worker-0",
                request_id=self.request_id,
                **(stamp or self.stamp()), event=event, **fields))
            JsonlTraceWriter(self.root/filename).append(record)
            self.events.append(record)

    def trace(self, event: str, info: dict[str, Any]) -> None:
        # Never serialize the request, raw info dict, headers or TLS key material.
        fields: dict[str, Any] = {}
        if event.endswith("connect_tcp.started"):
            fields.update(host=info.get("host"), port=info.get("port"), timeout=info.get("timeout"))
        if event.endswith("start_tls.started"):
            ctx = info.get("ssl_context")
            fields.update(server_hostname=info.get("server_hostname"), timeout=info.get("timeout"),
                          check_hostname=getattr(ctx,"check_hostname",None),
                          verify_mode=getattr(ctx,"verify_mode",None))
        if event.endswith(("connect_tcp.complete", "start_tls.complete")):
            stream=info.get("return_value")
            fields.update(server_addr=stream.get_extra_info("server_addr"),
                          client_addr=stream.get_extra_info("client_addr"))
            self.peer.update(fields)
            tls=stream.get_extra_info("ssl_object")
            if tls is not None:
                fields.update(tls_version=tls.version(),cipher=tls.cipher(),alpn=tls.selected_alpn_protocol())
        error=info.get("exception")
        if event.endswith("receive_response_headers.complete"):
            returned=info.get("return_value")
            if isinstance(returned,tuple) and len(returned)>1:
                fields['http_status']=returned[1]  # never copy response headers
        if isinstance(error, BaseException):
            fields.update(exception_type=type(error).__module__+"."+type(error).__name__,
                          exception_message=str(error),exception_chain=diagnose(error,[])['exception_chain'],
                          traceback="".join(traceback.TracebackException.from_exception(error).format()))
        self.emit("tcp_tls_trace.jsonl",event,**fields)


def proc_identity(pid: int) -> tuple[str,list[str]]:
    stat=Path(f"/proc/{pid}/stat").read_text()
    ticks=stat.rsplit(")",1)[1].split()[19]
    command=Path(f"/proc/{pid}/cmdline").read_bytes().rstrip(b"\0").decode().split("\0")
    return ticks,command


def owned_tcp(pid: int) -> list[dict[str, Any]]:
    """Passive IPv4 /proc observation: never connects to a forwarding listener."""
    inodes=set()
    try:
        for fd in Path(f"/proc/{pid}/fd").iterdir():
            try:
                target=fd.readlink().as_posix()
                if target.startswith("socket:["): inodes.add(target[8:-1])
            except OSError: pass
    except OSError:
        return []
    def address(value: str) -> list[Any]:
        host,port=value.split(":")
        return [".".join(str(x) for x in bytes.fromhex(host)[::-1]),int(port,16)]
    result=[]
    for line in Path("/proc/net/tcp").read_text().splitlines()[1:]:
        columns=line.split()
        if columns[9] in inodes:
            result.append(dict(local=address(columns[1]),peer=address(columns[2]),
                               tcp_state=columns[3],source="owned_fd_inode_and_proc_net_tcp"))
    return result


class SSHLogCollector:
    """Drain stderr to a queue independently of disk fsync; no pipe backpressure from IO."""
    def __init__(self, pipe: Any, audit: Audit):
        self.pipe,self.audit=pipe,audit
        self.queue: queue.SimpleQueue = queue.SimpleQueue()
        self.error: BaseException | None=None
        self.channel_failed=False
        self.forward_target: dict[str, Any] | None=None
        self.reader=threading.Thread(target=self._read,daemon=True)
        self.writer=threading.Thread(target=self._write,daemon=True)
        self.writer.start(); self.reader.start()

    def _read(self) -> None:
        try:
            for line in self.pipe:
                self.queue.put((self.audit.stamp(),line.rstrip()))
        except BaseException as exc: self.error=exc
        finally: self.queue.put(None)

    def record_line(self, stamp: dict, line: str) -> None:
        self.audit.emit("ssh_process_audit.jsonl","ssh_stderr_received",stamp,line=line)
        fields: dict[str,Any]={"line":line,"source":"stderr_reader_receipt"}
        match=re.search(r"Connection to port (\d+) forwarding to (\S+) port (\d+) requested",line)
        if match:
            self.forward_target=dict(local_port=int(match[1]),destination=match[2],destination_port=int(match[3]))
            self.audit.emit("ssh_channel_trace.jsonl","forward_request",stamp,**fields,**self.forward_target)
        channel=re.search(r"channel (\d+): (.*)",line)
        event=None
        if "Authenticated to " in line: event="ssh_authentication_complete"
        elif "Local forwarding listening on " in line: event="local_forward_listener_ready"
        elif channel:
            fields["channel_id"]=int(channel[1])
            text=channel[2]
            if "new direct-tcpip" in text: event="direct_tcpip_channel_created"
            elif "send open" in text: event="channel_open_requested"
            elif "open confirm" in text: event="channel_open_confirmed"
            elif "open failed" in text:
                event="channel_open_failed"; self.channel_failed=True
            elif "close" in text or "free:" in text: event="channel_close_observed"
            if self.forward_target:
                fields.update(self.forward_target,association="preceding_single_forward_request_log")
        if event: self.audit.emit("ssh_channel_trace.jsonl",event,stamp,**fields)

    def _write(self) -> None:
        while True:
            item=self.queue.get()
            if item is None: return
            try: self.record_line(*item)
            except BaseException as exc: self.error=exc  # keep draining even if storage fails

    def join(self) -> None:
        self.reader.join(timeout=5); self.writer.join(timeout=5)
        if self.reader.is_alive() or self.writer.is_alive():
            raise RuntimeError("SSH stderr collection incomplete")
        if self.error: raise RuntimeError("SSH stderr audit failure") from self.error


class DiagnosticSSH:
    """Independent debug adapter; existing production SSHProcess is unchanged."""
    def __init__(self, spec: dict[str,Any], audit: Audit):
        self.audit=audit
        self.command=["ssh","-F","/dev/null","-vvv","-N","-T","-i",spec["identity_file"],
            "-p",str(spec["ssh_port"]),"-o","BatchMode=yes","-o","IdentitiesOnly=yes",
            "-o","PasswordAuthentication=no","-o","StrictHostKeyChecking=yes",
            "-o","UserKnownHostsFile="+spec["known_hosts_file"],"-o","ExitOnForwardFailure=yes",
            "-o","ConnectTimeout=15","-o","ServerAliveInterval=15","-o","ServerAliveCountMax=3",
            "-o","ControlMaster=no","-o","ControlPath=none","-L","127.0.0.1:8000:127.0.0.1:8000",spec["target"]]
        self.process=subprocess.Popen(self.command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,text=True,encoding="utf-8",errors="replace",start_new_session=True)
        self.identity=ConnectionIdentity(audit.run_id,"worker-0",uuid.uuid4().hex,"WSL/OpenSSH",8000,
            spec["target"],self.process.pid,"unavailable",None,now())
        try:
            self.start_ticks,self.actual_command=proc_identity(self.process.pid)
            from dataclasses import replace
            self.identity=replace(self.identity,process_start_identity=self.start_ticks)
            self.audit.emit("ssh_process_audit.jsonl","ssh_process_started",identity=asdict(self.identity),command=self.actual_command)
            self.logs=SSHLogCollector(self.process.stderr,audit)
        except BaseException:
            # This freshly-created, retained child handle is the ownership proof.
            if self.process.poll() is None:
                self.process.terminate(); self.process.wait(timeout=10)
            if self.process.stderr: self.process.stderr.close()
            raise
        self.last_sockets: list[dict[str,Any]]=[]

    def snapshot(self) -> dict[str,Any]:
        code=self.process.poll()
        sockets=owned_tcp(self.process.pid) if code is None else []
        if code is None:
            ticks,command=proc_identity(self.process.pid)
            if ticks!=self.start_ticks or command!=self.actual_command:
                raise RuntimeError("Owned SSH process identity changed")
            self.last_sockets=sockets
        return dict(process_state="PROCESS_ALIVE" if code is None else "PROCESS_EXITED",exit_code=code,
            listener_state="LOCAL_PORT_LISTENING" if any(s["local"]==["127.0.0.1",8000] and s["tcp_state"]=="0A" for s in sockets) else "LOCAL_PORT_CLOSED",
            source="passive_owned_process_proc_socket_table")

    def close(self, expected: ConnectionIdentity) -> None:
        if expected!=self.identity: raise RuntimeError("SSH ownership mismatch")
        if self.process.poll() is None:
            self.snapshot()  # start ticks and command line verification, without probing a port
            self.process.terminate(); self.process.wait(timeout=10)
        self.logs.join()
        self.audit.emit("ssh_process_audit.jsonl","ssh_process_exited",exit_code=self.process.returncode,
                        identity=asdict(self.identity))


def make_client(context: ssl.SSLContext) -> httpx2.Client:
    return httpx2.Client(transport=httpx2.HTTPTransport(verify=context,retries=0,trust_env=False),
        timeout=httpx2.Timeout(10,connect=5),follow_redirects=False,trust_env=False)


def check(adapter: DiagnosticSSH) -> None:
    if adapter.logs.error: raise RuntimeError("SSH audit failed") from adapter.logs.error
    if adapter.logs.channel_failed: raise ConnectionError("SSH channel open failed")
    state=adapter.snapshot()
    if state["process_state"]!="PROCESS_ALIVE" or state["listener_state"]!="LOCAL_PORT_LISTENING":
        raise ConnectionError("Owned SSH forwarding unavailable")


def request_once(client: httpx2.Client, api_key: str, audit: Audit,
                 health_check: Any=lambda:None) -> dict[str,Any]:
    health_check()  # Do not submit a request after an already-observed audit/channel failure.
    audit.request_ns=time.monotonic_ns()
    result=dict(url=URL,http_status=None,model_ids=[],actual_client_peer="unavailable")
    audit.emit("worker_probe_audit.jsonl","https_request_started")
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(client.get,URL,headers={"Authorization":"Bearer "+api_key},extensions={"trace":audit.trace})
            while not future.done():
                health_check(); threading.Event().wait(0.05)
            response=future.result()
        result['http_status']=response.status_code
        response.raise_for_status()
        result['model_ids']=[v['id'] for v in response.json().get('data',[])]
        if "Qwen/Qwen3-8B" not in result["model_ids"]: raise ValueError("Model identity mismatch")
        result["success"]=True
    except BaseException as exc:
        result.update(success=False,diagnosis=diagnose(exc,[]),
            traceback="".join(traceback.TracebackException.from_exception(exc).format()))
    result["actual_client_peer"]=audit.peer or "unavailable"
    result["total_seconds"]=(time.monotonic_ns()-audit.request_ns)/1e9
    audit.emit("worker_probe_audit.jsonl","https_request_finished",**result)
    return result


def main(argv: list[str] | None=None) -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--authorize-stage-b",action="store_true")
    parser.add_argument("--run-id",required=True)
    args=parser.parse_args(argv)
    if not args.authorize_stage_b:
        raise RuntimeError("Stage B requires separate explicit user authorization")
    # This CLI is intentionally not run during Stage A.
    settings=RemoteModelSettings.from_yaml(CONFIGS[1])
    key,ca=settings.resolve_api_key(),settings.resolve_ca_bundle()
    hashes={p:sha256_file(p) for p in CONFIGS}
    if hashes!=EXPECTED_CONFIG_HASHES: raise RuntimeError('Frozen configuration changed')
    if ca.resolve()!=Path('/home/three/.config/risk-aware-smolagents/pki/ca.crt') or sha256_file(ca)!='97e07749f90c2d418a8dce22148c2e1fc77a14adbf913d6c74665e477c4b5bef':
        raise RuntimeError('Approved CA path or identity changed')
    spec=specs_from_host()["worker-0"]
    if any(s["local"][1]==8000 and s["tcp_state"]=="0A" for s in all_local_tcp()):
        raise RuntimeError("Local port 8000 occupied; refusing adoption")
    root=create_unique_run_directory("runs/phase2_p2_d2_tls_diagnostic",args.run_id)
    audit=Audit(root,args.run_id)
    manager=ConnectionLifecycle(args.run_id,root/"connection_audit.jsonl")
    write_json(root/"run_manifest.json",dict(run_id=args.run_id,created_at=now(),
        git_commit=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),config_sha256=hashes,
        ca_sha256=sha256_file(ca),versions={n:version(n) for n in ("httpx2","httpcore2","openai","smolagents")},
        implementation_sha256=sha256_file(__file__),connect_timeout=5,read_timeout=10,retries=0,
        worker_id="worker-0",completion_requests=0,maximum_get_attempts=1,
        listener_observation="passive proc; differs from prior Smoke active connect probes"))
    summary=dict(status="TLS_DIAGNOSTIC_BLOCKED",get_attempts=0)
    release=dict(passed=False,remote_worker_state="unavailable; not modified or remotely queried")
    adapter=None
    try:
        adapter=DiagnosticSSH(spec,audit); manager.add(adapter)
        deadline=time.monotonic()+30
        while adapter.snapshot()["listener_state"]!="LOCAL_PORT_LISTENING":
            if adapter.process.poll() is not None or adapter.logs.error: raise RuntimeError("SSH startup failed")
            if time.monotonic()>=deadline: raise TimeoutError("SSH listener not ready")
            time.sleep(0.05)
        manager.start_monitor()
        def health() -> None:
            if manager.error: raise RuntimeError('Connection monitor failed') from manager.error
            check(adapter)
        with make_client(ssl.create_default_context(cafile=str(ca))) as client:
            summary["get_attempts"]=1
            summary["result"]=request_once(client,key,audit,health)
        summary["status"]="TLS_DIAGNOSTIC_PASS" if summary["result"]["success"] else "TLS_DIAGNOSTIC_BLOCKED"
    except BaseException as exc:
        summary["error"]=diagnose(exc,[])
    finally:
        try:
            manager.close()
            release.update(passed=True,owned_sockets_before_close=adapter.last_sockets if adapter else [],
                           ssh_control_connection_peer="unavailable" if not adapter else [s for s in adapter.last_sockets if s["tcp_state"]=="01" and s["peer"][1]==4035],
                           remote_https_actual_peer="unavailable",exit_code=None if not adapter else adapter.process.returncode)
        except BaseException as exc:
            release["error"]=diagnose(exc,[]); summary["status"]="TLS_DIAGNOSTIC_BLOCKED"
        release["port_8000_released"]=not any(s["local"][1]==8000 and s["tcp_state"]=="0A" for s in all_local_tcp())
        if not release["port_8000_released"]: summary["status"]="TLS_DIAGNOSTIC_BLOCKED"
        summary["configs_unchanged"]=hashes=={p:sha256_file(p) for p in CONFIGS}
        if not summary["configs_unchanged"]: summary["status"]="TLS_DIAGNOSTIC_BLOCKED"
        names={e['event'] for e in audit.events}
        summary['phase_classification']=classify_phase(audit.events,summary.get('result',{}))
        summary["audit_completeness"]=dict(
            httpx_tcp_peer=bool(audit.peer.get('server_addr') and audit.peer.get('client_addr')),
            ssh_control_peer=bool(release.get('ssh_control_connection_peer') not in (None,[],"unavailable")),
            ssh_channel_open_confirmed='channel_open_confirmed' in names,
            ssh_authentication='ssh_authentication_complete' in names,
            tls_trace='connection.start_tls.complete' in names,
            data_forwarding_detail="unavailable: SSH debug does not prove TLS payload delivery",
            remote_https_actual_peer="unavailable")
        if summary['status']=='TLS_DIAGNOSTIC_PASS' and not all(
            v for k,v in summary['audit_completeness'].items() if isinstance(v,bool)):
            summary['status']='TLS_DIAGNOSTIC_INCONCLUSIVE'
        write_json(root/"resource_release.json",release)
        write_json(root/"gate_summary.json",summary)
        write_json(root/"artifact_manifest.json",dict(artifacts=[dict(path=p.name,sha256=sha256_file(p)) for p in sorted(root.iterdir()) if p.is_file()]))
    print(json.dumps(sanitize_audit_value(summary),indent=2))
    return 0 if summary["status"]=="TLS_DIAGNOSTIC_PASS" else 1


def classify_phase(events: list[dict[str,Any]], result: dict[str,Any]) -> str:
    names={e['event'] for e in events}
    diagnosis=result.get('diagnosis',{})
    chains=diagnosis.get('exception_chain',[])+[
        item for event in events for item in event.get('exception_chain',[])]
    if any('SSLCertVerificationError' in item.get('type','') for item in chains):
        return 'D_CERTIFICATE_VERIFICATION_ERROR'
    if diagnosis.get('connection_diagnostic')=='tls_connection_failure': return 'TLS_ERROR_NOT_PROVEN_CERTIFICATE_FAILURE'
    if result.get('success'): return 'E_REQUEST_SUCCESS'
    if 'connection.start_tls.complete' in names:
        return 'C_HTTP_STAGE_FAILURE'
    if 'connection.connect_tcp.complete' in names: return 'B_TCP_COMPLETE_TLS_INCOMPLETE'
    if 'connection.connect_tcp.started' in names: return 'A_TCP_INCOMPLETE'
    return 'UNAVAILABLE_REQUEST_NOT_OBSERVED'


def all_local_tcp() -> list[dict[str,Any]]:
    # Passive occupancy check, including IPv6 wildcard listeners; never open a socket.
    result=[]
    for name in ("tcp","tcp6"):
        for line in Path('/proc/net/'+name).read_text().splitlines()[1:]:
            cols=line.split()
            result.append(dict(local=[cols[1].split(':')[0],int(cols[1].split(':')[1],16)],tcp_state=cols[3]))
    return result


if __name__=="__main__":
    raise SystemExit(main())
