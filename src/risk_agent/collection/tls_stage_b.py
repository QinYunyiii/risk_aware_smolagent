"""Single-session TLS production orchestration using the existing V1 transport.

Importing, preparing a program, and the default CLI mode never connect. Real
execution requires an explicit authorization flag; tests inject the SSH boundary.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import threading
import uuid
from typing import Any, Callable

from . import remote_local_tls_isolation as isolation
from . import ssh_evidence_transport as transport
from . import tls_evidence_capture as capture


def frozen_tls_argv() -> list[str]:
    tree = ast.parse(inspect.getsource(isolation.remote_entry))
    return next(ast.literal_eval(node.value) for node in ast.walk(tree)
                if isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == 'command' for target in node.targets))


def load_worker_evidence(path: Path) -> dict[str, Any]:
    """Read a checksum-verified historical owner, never a live remote query."""
    blob = path.read_bytes()
    manifest = json.loads((path.parent / 'artifact_manifest.json').read_bytes())
    entries = [a for a in manifest['artifacts'] if a['path'] == path.name]
    if len(entries) != 1 or hashlib.sha256(blob).hexdigest() != entries[0]['sha256']:
        raise capture.EvidenceError('Worker ownership source Manifest mismatch')
    events = [json.loads(line).get('remote_event', {}) for line in blob.splitlines()]
    snapshots = [e['snapshot'] for e in events if e.get('event') == 'worker0_service_preflight']
    versions = [e['version'] for e in events if e.get('event') == 'openssl_capability_checked'
                and e.get('supported') is True]
    if len(snapshots) != 1 or len(versions) != 1 or not snapshots[0].get('passed'):
        raise capture.EvidenceError('Unambiguous historical Worker ownership required')
    owners = [p for p in snapshots[0]['owners'] if p.get('running')
              and p.get('vllm_command_observed') and p.get('port_argument_matches')]
    if len(owners) != 1:
        raise capture.EvidenceError('Ambiguous Worker owner')
    owner = owners[0]
    return dict(worker_pid=owner['pid'], worker_process_identity=owner['process_start_ticks'],
                worker_command_identity='vllm-worker-0', worker_command_fingerprint=owner['command_sha256'],
                worker_endpoint='127.0.0.1:8000', openssl_version=versions[0],
                evidence_source_sha256=hashlib.sha256(blob).hexdigest())


def expected_metadata(evidence: dict[str, Any], run_id: str, session_id: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    record = dict(run_id=run_id, session_id=session_id, worker_id='worker-0',
        remote_container_id='36f825bcc5da', remote_hostname='36f825bcc5da', remote_user='root',
        endpoint='127.0.0.1:8000', openssl_version=evidence['openssl_version'],
        openssl_executable='/usr/bin/openssl', ca_path=isolation.CA_PATH, ca_sha256=isolation.CA_SHA256,
        tls_parameter_fingerprint=hashlib.sha256(json.dumps(
            frozen_tls_argv(), separators=(',', ':')).encode('ascii')).hexdigest(),
        connect_timeout=15, handshake_timeout=5, preflight_timestamp=now, preflight_status='PASS',
        ownership_verified=True, ownership_evidence_timestamp=now, preflight_error=None)
    for key in ('worker_pid', 'worker_process_identity', 'worker_command_identity',
                'worker_command_fingerprint', 'worker_endpoint'):
        record[key] = evidence[key]
    return json.loads(transport.preflight_metadata_bytes(record, run_id, session_id))


def remote_preflight(expected, runner=None):
    """Same preflight in the real stdin program and offline injected runners.

    Only the original owner's /proc entry is inspected. Raw cmdline stays in
    memory; the evidence contains a whole-command digest and a fixed safe label.
    """
    import datetime
    import hashlib
    import os
    from pathlib import Path
    import re
    import shutil
    import subprocess
    class System:
        def identity(self):
            release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines()
                           if '=' in line)
            return dict(hostname=os.uname().nodename, uid=os.getuid(),
                        ubuntu=release.get('ID', '').strip('"') == 'ubuntu',
                        version=release.get('VERSION_ID', '').strip('"'))
        def tools(self):
            executable = shutil.which('openssl')
            if executable != expected['openssl_executable']:
                raise ValueError('executable mismatch')
            env = {k: os.environ[k] for k in ('PATH', 'LANG', 'LC_ALL') if k in os.environ}
            version = subprocess.run([executable, 'version'], capture_output=True, timeout=3,
                                     check=True, env=env).stdout.decode('ascii').strip()
            help_result = subprocess.run([executable, 's_client', '-help'], capture_output=True,
                                         timeout=3, env=env)
            return executable, version, (help_result.stdout + help_result.stderr).decode('ascii')
        def ca_hash(self):
            return hashlib.sha256(Path(expected['ca_path']).read_bytes()).hexdigest()
        def worker(self):
            pid = expected['worker_pid']; base = Path('/proc') / str(pid)
            stat_before = (base / 'stat').read_text().rsplit(')', 1)[1].split()
            raw = (base / 'cmdline').read_bytes()
            args = raw.split(b'\x00')
            port_ok = b'--port=8000' in args or any(
                a == b'--port' and i+1 < len(args) and args[i+1] == b'8000' for i, a in enumerate(args))
            command_ok = any(b'vllm' in a for a in args) and port_ok
            listeners = set()
            for line in Path('/proc/net/tcp').read_text().splitlines()[1:]:
                fields = line.split()
                if fields[1] == '0100007F:1F40' and fields[3] == '0A': listeners.add(fields[9])
            owned = set()
            for fd in (base / 'fd').iterdir():
                try: link = os.readlink(fd)
                except OSError: continue
                if link.startswith('socket:[') and link[8:-1] in listeners: owned.add(link[8:-1])
            stat_after = (base / 'stat').read_text().rsplit(')', 1)[1].split()
            return dict(worker_pid=pid, worker_process_identity=stat_after[19],
                worker_command_identity='vllm-worker-0' if command_ok else None,
                worker_command_fingerprint=hashlib.sha256(raw).hexdigest(),
                worker_endpoint='127.0.0.1:8000' if owned else None,
                alive=stat_after[0] not in ('Z', 'X') and stat_before[19] == stat_after[19])
    system = runner if runner is not None else System()
    result = dict(expected)
    result.update(preflight_status='FAIL', ownership_verified=None, preflight_error='IDENTITY_REJECTED')
    for key in ('remote_container_id', 'remote_hostname', 'remote_user', 'openssl_version',
                'openssl_executable', 'ca_sha256', 'worker_pid', 'worker_process_identity',
                'worker_command_identity', 'worker_command_fingerprint', 'worker_endpoint'):
        result[key] = None
    try:
        identity = system.identity()
        result.update(remote_container_id=identity['hostname'], remote_hostname=identity['hostname'],
                      remote_user='root' if identity['uid'] == 0 else str(identity['uid']))
        if (identity['hostname'] != expected['remote_hostname'] or identity['uid'] != 0
                or not identity['ubuntu'] or identity['version'] != '22.04'):
            raise ValueError('identity rejected')
        result['preflight_error'] = 'OPENSSL_REJECTED'
        executable, version, help_text = system.tools()
        result.update(openssl_executable=executable, openssl_version=version)
        flags = ('-connect', '-servername', '-verify_hostname', '-verify_return_error',
                 '-CAfile', '-brief', '-state', '-msg')
        if (executable != expected['openssl_executable'] or version != expected['openssl_version']
                or any(not re.search(r'(?<!\S)' + re.escape(f) + r'(?=\s|$)', help_text) for f in flags)):
            raise ValueError('openssl rejected')
        result['preflight_error'] = 'CA_REJECTED'
        result['ca_sha256'] = system.ca_hash()
        if result['ca_sha256'] != expected['ca_sha256']: raise ValueError('ca rejected')
        result['preflight_error'] = 'WORKER_REJECTED'
        worker = system.worker()
        for key in ('worker_pid', 'worker_process_identity', 'worker_command_identity',
                    'worker_command_fingerprint', 'worker_endpoint'): result[key] = worker.get(key)
        result['ownership_verified'] = bool(worker.get('alive') and all(
            result[key] == expected[key] for key in ('worker_pid', 'worker_process_identity',
                'worker_command_identity', 'worker_command_fingerprint', 'worker_endpoint')))
        if not result['ownership_verified']: raise ValueError('worker rejected')
        result.update(preflight_status='PASS', preflight_error=None)
    except Exception:
        # Fixed error codes only. Exceptions or cmdline contents may contain secrets.
        pass
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    result.update(preflight_timestamp=now, ownership_evidence_timestamp=now)
    return result


def remote_program(expected: dict[str, Any]) -> bytes:
    command = frozen_tls_argv()
    command[0] = expected['openssl_executable']  # Same resolved executable, unchanged TLS flags.
    source = transport.remote_wrapper_source(command, expected['run_id'], expected['session_id'])
    source += '\n' + inspect.getsource(remote_preflight)
    source += (f'\nEXPECTED={expected!r}\n'
        'def remote_main(preflight_runner=None):\n'
        '    metadata=remote_preflight(EXPECTED,preflight_runner)\n'
        '    try:\n'
        '        outcome=authorized_launch_after_preflight(metadata)\n'
        '    except EvidenceError:\n'
        '        return 90\n'
        '    if not outcome["reaped"] or outcome["timed_out"]: return 89\n'
        '    code=outcome["returncode"]\n'
        '    return code if code>=0 else 128-code\n'
        'if __name__=="__main__": raise SystemExit(remote_main())\n')
    compile(source, 'tls_stage_b_remote', 'exec')
    return source.encode('utf-8')


def read_local_ca() -> bytes:
    value = Path(os.environ['RISK_AGENT_CA_BUNDLE']).read_bytes()
    if hashlib.sha256(value).hexdigest() != isolation.CA_SHA256:
        raise capture.EvidenceError('Local CA differs from frozen identity')
    return value


class SourceProcess(capture.PopenPipeProcess):
    """Deliver stdin while the existing transport supervises the owned SSH."""
    def __init__(self, child: subprocess.Popen[bytes], source: bytes):
        super().__init__(child)
        self.delivery_error = None
        def send():
            try:
                view = memoryview(source)
                while view:
                    count = os.write(child.stdin.fileno(), view)
                    if count <= 0: raise OSError('stdin made no progress')
                    view = view[count:]
            except (OSError, ValueError) as exc:
                self.delivery_error = type(exc).__name__
            finally: child.stdin.close()
        self.sender = threading.Thread(target=send, daemon=True)
        self.sender.start()

    def close(self) -> None:
        self.sender.join(timeout=1)
        if self.sender.is_alive():
            self.process.stdin.close(); self.sender.join(timeout=1)
        super().close()


def _write(root: Path, name: str, value: Any) -> None:
    capture._safe_name(name)
    data = value if isinstance(value, bytes) else (json.dumps(value, indent=2) + '\n').encode()
    fd = os.open(root/name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(data); handle.flush(); os.fsync(handle.fileno())
    directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(directory_fd)
    finally: os.close(directory_fd)


def _publish(root: Path, name: str) -> None:
    entries = []
    for path in sorted(root.iterdir()):
        if path.is_file():
            with path.open('rb') as handle: os.fsync(handle.fileno())
            entries.append(dict(path=path.name, size_bytes=path.stat().st_size,
                                sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    pending = name + '.pending'
    _write(root, pending, dict(run_id=root.name, raw_is_unredacted=True,
                            automatic_publish_allowed=False, artifacts=entries))
    os.link(root/pending, root/name); (root/pending).unlink()
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)
    for entry in entries:
        data = (root/entry['path']).read_bytes()
        if len(data) != entry['size_bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise capture.EvidenceError('Published evidence Manifest mismatch')


def classify_evidence(evidence: dict[str, Any], receipt: dict[str, Any], ca: bytes) -> dict[str, Any]:
    """Replay verified raw lines into the unchanged classifier; never a new request."""
    metadata = evidence['preflight_metadata']
    class Replay:
        def identity(self): return dict(hostname=metadata['remote_hostname'], uid=0)
        def tool_info(self): return metadata['openssl_version'], '\n'.join(isolation.REQUIRED_FLAGS)
        def read_ca(self): return ca
        def probe(self, command, budget, callback):
            if command != frozen_tls_argv() or budget != 5: raise capture.EvidenceError('Frozen TLS drift')
            for stream in ('stdout', 'stderr'):
                for line in evidence[stream].splitlines(): callback(stream, line.decode('utf-8', 'replace'))
            return dict(returncode=receipt['returncode'], timed_out=receipt['timed_out'],
                        **{k:v for k,v in receipt.get('lifecycle', {}).items()
                           if k not in ('returncode', 'timed_out')})
    events = []
    result = isolation.remote_entry(Replay(), events.append, hashlib.sha256(ca).hexdigest())
    state = result.get('state', {})
    verified = bool(state.get('verified') and not state.get('certificate_error'))
    return dict(result=result, replay_events=events, classification_source='VERIFIED_RAW_OFFLINE_REPLAY',
                tls_handshake_completed=True if state.get('complete') else None,
                certificate_chain_verified=True if verified else None,
                hostname_verified=True if verified else None)


def run_stage_b(base: Path, run_id: str, ownership: dict[str, Any] | Path, *,
                authorize_real: bool = False, session_id: str | None = None,
                ssh_factory: Callable[[list[str], bytes], Any] | None = None,
                preflight_provider: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> dict[str, Any]:
    """The production entry and its injected Fake E2E share this complete path.

    Local validation/durability failures precede SSH. Remote-only failures cannot
    precede the connection that observes them; they prohibit the TLS child.
    """
    capture._safe_name(run_id)
    root = Path(base).absolute()/run_id
    if root.exists(): raise FileExistsError('Run already exists; no reuse or retry')
    if ssh_factory is None and not authorize_real:
        raise capture.EvidenceError('Separate Stage B real execution authorization required')
    nonce = session_id or uuid.uuid4().hex
    launched = 0; result = None; error = None; expected = None; source = None
    receipt = None; phase = 'PREPARE_METADATA'; failure_code = 'OWNERSHIP_POLICY_INVALID'
    try:
        if isinstance(ownership, Path): ownership = load_worker_evidence(ownership)
        expected = expected_metadata(ownership, run_id, nonce)
        phase = 'LOCAL_CA'; failure_code = 'LOCAL_CA_REJECTED'
        ca = read_local_ca()
        if preflight_provider is not None:
            phase = 'LOCAL_PREFLIGHT'; failure_code = 'PREPARED_METADATA_INVALID'
            supplied = preflight_provider(dict(expected))
            canonical = transport.preflight_metadata_bytes(supplied['remote_preflight'], run_id, nonce)
            failure_code = 'METADATA_HASH_MISMATCH'
            if hashlib.sha256(canonical).hexdigest() != supplied['remote_preflight_sha256']:
                raise capture.EvidenceError('Prepared preflight metadata hash mismatch')
            observed = json.loads(canonical)
            keys = set(expected) - {'preflight_timestamp', 'ownership_evidence_timestamp'}
            failure_code = 'WORKER_BINDING_MISMATCH'
            if any(observed[k] != expected[k] for k in keys):
                raise capture.EvidenceError('Prepared Worker preflight differs from approved expectation')
        source = remote_program(expected)
        phase = 'TRANSPORT'; failure_code = 'TRANSPORT_OR_REMOTE_PREFLIGHT_REJECTED'
        def factory(argv):
            nonlocal launched
            if launched: raise capture.EvidenceError('Second SSH launch forbidden')
            argv[argv.index('ConnectTimeout=10')] = 'ConnectTimeout=15'
            _write(root, 'remote_stdin_program.py', source)
            _write(root, 'run_manifest.json', dict(run_id=run_id, session_id=nonce,
                execution='INJECTED_OFFLINE_FAKE' if ssh_factory else 'EXPLICITLY_AUTHORIZED_REAL',
                formal_experiment=False, actual_ssh_argv=argv,
                source_sha256=hashlib.sha256(source).hexdigest(),
                implementation_sha256={Path(module.__file__).name:hashlib.sha256(
                    Path(module.__file__).read_bytes()).hexdigest() for module in (transport, capture, isolation)},
                ownership_source_sha256=ownership.get('evidence_source_sha256'),
                frozen_tls_argv=frozen_tls_argv(), resolved_tls_argv=[expected['openssl_executable'], *frozen_tls_argv()[1:]],
                ca_sha256=expected['ca_sha256'], ssh_overall_timeout=30, tls_timeout=5,
                maximum_ssh_attempts=1, maximum_tls_handshakes=1, automatic_retries=0, worker_failover=0))
            launched += 1
            if ssh_factory is not None: return ssh_factory(argv, source)
            env = {k:os.environ[k] for k in ('PATH', 'LANG', 'LC_ALL') if k in os.environ}
            child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     shell=False, start_new_session=True, bufsize=0, env=env)
            try: return SourceProcess(child, source)
            except BaseException:
                if child.poll() is None:
                    child.terminate()
                    try: child.wait(timeout=.25)
                    except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=1)
                child.stdin.close(); child.stdout.close(); child.stderr.close()
                raise
        result = transport.capture_ssh_transport(base, run_id, nonce=nonce, process_factory=factory,
                    preflight_metadata=expected, require_preflight_metadata=True, local_timeout_seconds=30)
        evidence = transport.verified_remote_evidence(root)
        decoder = transport.FrameDecoder(run_id, nonce)
        wire = (root/'ssh_stdout_transport.raw').read_bytes()
        decoder.feed(wire, lambda *args: None); decoder.finish()
        receipt = decoder.receipt
        phase = 'CLASSIFY_VERIFIED_RAW'; failure_code = 'CLASSIFICATION_OR_ARTIFACT_FAILURE'
        classification = classify_evidence(evidence, receipt, ca)
        for alias, original in dict(openssl_stdout='remote_stdout.raw', openssl_stderr='remote_stderr.raw',
                                    ssh_wire_stdout='ssh_stdout_transport.raw',
                                    ssh_process_stderr='ssh_stderr_unclassified.raw').items():
            _write(root, alias+'.raw', (root/original).read_bytes())
        _write(root, 'tls_preflight_metadata.json', (root/'remote_preflight.json').read_bytes())
        line = next(line for line in wire.splitlines(keepends=True)
                    if json.loads(line[len(transport.PREFIX):]).get('kind') == 'exit')
        _write(root, 'remote_exit_receipt.raw', line)
        _write(root, 'tls_classification.json', classification)
        passed = (result['transport']['ssh_transport_status'] == 'SUCCESS'
                  and classification['result']['category'] == 'REMOTE_TLS_SUCCESS'
                  and evidence['preflight_metadata']['ownership_verified'] is True)
        gate = 'TLS_EVIDENCE_CAPTURE_STAGE_B_PASS' if passed else 'TLS_EVIDENCE_CAPTURE_STAGE_B_INCONCLUSIVE'
    except Exception as exc:
        chain = []; current = exc; seen = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current)); chain.append(type(current).__name__)
            current = current.__cause__ or current.__context__
        error = dict(type=type(exc).__name__, phase=phase, reason=failure_code, exception_chain_types=chain)
        gate = 'TLS_EVIDENCE_CAPTURE_STAGE_B_BLOCKED' if not launched else 'TLS_EVIDENCE_CAPTURE_STAGE_B_INCONCLUSIVE'
        if root.exists() and (root/'remote_preflight.json').is_file():
            try:
                if json.loads((root/'remote_preflight.json').read_bytes())['preflight_status'] == 'FAIL':
                    gate = 'TLS_EVIDENCE_CAPTURE_STAGE_B_BLOCKED'
            except (ValueError, KeyError): pass
        if not root.exists():
            _, fd = capture._directory(base, run_id); os.close(fd)
        _write(root, 'failure_reason.json', error)
    # Preserve partial raw streams with the public artifact names as well. These
    # copies never substitute for a complete capture or a validated exit receipt.
    for alias, original in dict(openssl_stdout='remote_stdout.raw', openssl_stderr='remote_stderr.raw',
                                ssh_wire_stdout='ssh_stdout_transport.raw',
                                ssh_process_stderr='ssh_stderr_unclassified.raw').items():
        if (root/original).is_file() and not (root/(alias+'.raw')).exists():
            _write(root, alias+'.raw', (root/original).read_bytes())
    if (root/'remote_preflight.json').is_file() and not (root/'tls_preflight_metadata.json').exists():
        _write(root, 'tls_preflight_metadata.json', (root/'remote_preflight.json').read_bytes())
    if not (root/'tls_classification.json').exists():
        _write(root, 'tls_classification.json', dict(status='NOT_RUN_INCOMPLETE_EVIDENCE',
            tls_handshake_completed=None, certificate_chain_verified=None, hostname_verified=None))
    local = json.loads((root/'process_result.json').read_bytes()) if (root/'process_result.json').is_file() and (root/'process_result.json').stat().st_size else None
    status = json.loads((root/'transport_status.json').read_bytes()) if (root/'transport_status.json').is_file() and (root/'transport_status.json').stat().st_size else None
    _write(root, 'process_lifecycle.json', dict(local_ssh=local, remote_openssl=receipt,
        remote_receipt_observed=bool(status and status['remote_exit_status_observed'])))
    _write(root, 'exit_status_audit.json', status or dict(ssh_process_return_code=None,
        remote_exit_status=None, remote_exit_status_observed=False, remote_termination_status='UNKNOWN'))
    _write(root, 'resource_release.json', dict(local_ssh_started=bool(launched), local_ssh_reaped=local.get('reaped') if local else None,
        local_ssh_pid=local.get('pid') if local else None, tunnels_created=0, worker_operations=0,
        remote_termination_status=status.get('remote_termination_status') if status else 'UNKNOWN'))
    _write(root, 'transport_verification.json', dict(ssh_factory_calls=launched, error=error,
        complete=result is not None, actual_network_mode='OFFLINE_INJECTED' if ssh_factory else 'AUTHORIZED_REAL',
        automatic_retries=0, worker_failover=0))
    summary = dict(run_id=run_id, session_id=nonce, gate=gate, ssh_factory_calls=launched,
                   real_ssh_attempts=launched if ssh_factory is None else 0, http_requests=0, qwen_requests=0,
                   legacy_summary_path_called=False, error=error)
    _write(root, 'gate_summary.json', summary)
    _publish(root, 'artifact_manifest_post_analysis.json' if (root/'artifact_manifest.json').exists() else 'artifact_manifest.json')
    return summary


def cli_main(argv=None, *, ssh_factory=None, preflight_provider=None, ownership=None):
    """CLI composition root, with the same injection boundary for offline E2E."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('prepare', 'execute'), default='prepare')
    parser.add_argument('--authorize-stage-b', action='store_true')
    parser.add_argument('--run-id')
    parser.add_argument('--session-id')
    parser.add_argument('--output-base', type=Path, default=Path('runs/phase2_p2_d2_tls_evidence_capture_stage_b'))
    parser.add_argument('--ownership-evidence', type=Path, default=Path(
        'runs/phase2_p2_d2_remote_local_tls_isolation/p2_d2_remote_local_tls_isolation_20261125_001/ssh_remote_command_audit.jsonl'))
    args = parser.parse_args(argv)
    if args.mode == 'prepare':
        print(json.dumps(dict(entry='tls_stage_b', real_execution=False, transport='existing V1',
                              real_ssh_attempts=0, tls_handshakes=0)))
        return 0
    if not args.run_id or (ssh_factory is None and not args.authorize_stage_b):
        raise RuntimeError('Separate Stage B authorization and unique Run ID required')
    policy = ownership if ownership is not None else args.ownership_evidence
    result = run_stage_b(args.output_base, args.run_id, policy, authorize_real=args.authorize_stage_b,
        session_id=args.session_id, ssh_factory=ssh_factory, preflight_provider=preflight_provider)
    print(json.dumps(result, indent=2))
    return 0 if result['gate'] == 'TLS_EVIDENCE_CAPTURE_STAGE_B_PASS' else 1
