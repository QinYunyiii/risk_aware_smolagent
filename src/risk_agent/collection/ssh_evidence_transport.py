"""Offline-testable SSH evidence adapter; no default launcher or network mode.

Remote application streams are framed on SSH stdout. SSH stderr is always kept
unattributed: -E redirects client logging, not every possible bootstrap error.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import threading
import uuid
from typing import Any, Callable, Iterator

from .tls_evidence_capture import (
    Clock, DEFAULT_MAX_OUTPUT_BYTES, EvidenceError, EvidenceStore, OwnedProcess,
    PopenPipeProcess, STREAMS, capture_process, validate_argv, verify_manifest,
)

PROTOCOL = 'risk-ssh-evidence-v1'
PREFIX = b'RISK_SSH_EVIDENCE_V1 '
MAX_FRAME_BYTES = 128 * 1024
FRAME_CHUNK_BYTES = 64 * 1024  # Emission chunk size, not a new V1 decoder limit.
SSH_OUTER_TIMEOUT = 40.0  # Not the separate, unchanged remote TLS five-second budget.
STREAM_FILES = dict(stdout='ssh_stdout_transport.raw', stderr='ssh_stderr_unclassified.raw',
                    debug='ssh_client.log.raw', remote_stdout='remote_stdout.raw',
                    remote_stderr='remote_stderr.raw')


@dataclass(frozen=True)
class SSHSettings:
    executable: str = '/usr/bin/ssh'
    alias: str = 'risk-agent-remote'
    port: int = 4035
    identity_file: str = '/home/three/.ssh/risk_agent_audit'
    known_hosts_file: str = '/home/three/.ssh/known_hosts'


def ssh_argv(log_path: Path, settings: SSHSettings = SSHSettings()) -> list[str]:
    if settings.alias != 'risk-agent-remote' or settings.port != 4035:
        raise EvidenceError('Unapproved SSH identity configuration')
    if not Path(settings.identity_file).is_absolute() or not Path(settings.known_hosts_file).is_absolute():
        raise EvidenceError('Explicit absolute identity and known_hosts paths required')
    return validate_argv([
        settings.executable, '-T', '-v', '-E', str(Path(log_path).absolute()),
        '-i', settings.identity_file, '-p', str(settings.port),
        '-o', 'UserKnownHostsFile=' + settings.known_hosts_file,
        '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
        '-o', 'IdentitiesOnly=yes', '-o', 'PasswordAuthentication=no',
        '-o', 'KbdInteractiveAuthentication=no', '-o', 'ForwardAgent=no',
        '-o', 'ClearAllForwardings=yes', '-o', 'ConnectionAttempts=1',
        '-o', 'ConnectTimeout=10', '-o', 'ControlMaster=no', '-o', 'ControlPath=none',
        settings.alias, 'python3 -B -',
    ])


def encode_frame(run_id: str, nonce: str, sequence: int, kind: str, **values: Any) -> bytes:
    """Protocol encoder for a reviewed future producer; never writes or launches."""
    record = dict(protocol=PROTOCOL, run_id=run_id, nonce=nonce, sequence=sequence,
                  kind=kind, remote_monotonic_ns=values.pop('remote_monotonic_ns', None),
                  remote_utc=values.pop('remote_utc', None), **values)
    frame = PREFIX + json.dumps(record, separators=(',', ':')).encode('ascii') + b'\n'
    if len(frame) > MAX_FRAME_BYTES:
        raise EvidenceError('Encoded frame exceeds V1 receiver limit')
    return frame


def data_frame(run_id: str, nonce: str, sequence: int, stream: str, data: bytes, offset: int,
               *, remote_monotonic_ns: int | None = None, remote_utc: str | None = None) -> bytes:
    return encode_frame(run_id, nonce, sequence, 'data', stream=stream, offset=offset,
                        byte_count=len(data), chunk_sha256=hashlib.sha256(data).hexdigest(),
                        payload=base64.b64encode(data).decode('ascii'),
                        remote_monotonic_ns=remote_monotonic_ns, remote_utc=remote_utc)


def exit_frame(run_id: str, nonce: str, sequence: int, *, returncode: int,
               stdout_bytes: int, stderr_bytes: int, stdout_sha256: str, stderr_sha256: str,
               reaped: bool = True, timed_out: bool = False,
               remote_monotonic_ns: int | None = None, remote_utc: str | None = None,
               lifecycle: dict[str, Any] | None = None, **extra_lifecycle: Any) -> bytes:
    metadata = dict(lifecycle or {})
    metadata.update(extra_lifecycle)
    return encode_frame(run_id, nonce, sequence, 'exit', returncode=returncode,
                        stdout_bytes=stdout_bytes, stderr_bytes=stderr_bytes,
                        stdout_sha256=stdout_sha256, stderr_sha256=stderr_sha256,
                        reaped=reaped, timed_out=timed_out, lifecycle=metadata,
                        remote_monotonic_ns=remote_monotonic_ns, remote_utc=remote_utc)


class FrameEncoder:
    """One stateful V1 sender shared by probe and supervised producers.

    The public low-level encode_frame/data_frame/exit_frame wire format remains
    unchanged. Large input payloads become multiple frames, never truncated bytes.
    """
    def __init__(self, run_id: str, nonce: str):
        self.run_id, self.nonce = run_id, nonce
        self.sequence = 0
        self.started = False
        self.ended = False
        self.counts = dict.fromkeys(STREAMS, 0)
        self.hashes = {stream: hashlib.sha256() for stream in self.counts}

    def start(self, **timestamps: Any) -> bytes:
        if self.started:
            raise EvidenceError('Producer already started')
        frame = encode_frame(self.run_id, self.nonce, 1, 'start', **timestamps)
        self.started, self.sequence = True, 1
        return frame

    def data(self, stream: str, payload: bytes, **timestamps: Any) -> Iterator[bytes]:
        if not self.started or self.ended or stream not in self.counts or not isinstance(payload, bytes):
            raise EvidenceError('Invalid producer state/stream/payload')
        for start in range(0, len(payload), FRAME_CHUNK_BYTES):
            chunk = payload[start:start + FRAME_CHUNK_BYTES]
            frame = data_frame(self.run_id, self.nonce, self.sequence + 1,
                               stream, chunk, self.counts[stream], **timestamps)
            self.sequence += 1
            self.counts[stream] += len(chunk)
            self.hashes[stream].update(chunk)
            yield frame

    def finish(self, *, returncode: int, reaped: bool, timed_out: bool,
               lifecycle: dict[str, Any] | None = None,
               remote_monotonic_ns: int | None = None, remote_utc: str | None = None) -> bytes:
        if not self.started or self.ended or type(returncode) is not int or type(reaped) is not bool or type(timed_out) is not bool:
            raise EvidenceError('Invalid producer completion')
        frame = exit_frame(self.run_id, self.nonce, self.sequence + 1,
            returncode=returncode, reaped=reaped, timed_out=timed_out,
            stdout_bytes=self.counts['stdout'], stderr_bytes=self.counts['stderr'],
            stdout_sha256=self.hashes['stdout'].hexdigest(), stderr_sha256=self.hashes['stderr'].hexdigest(),
            remote_monotonic_ns=remote_monotonic_ns, remote_utc=remote_utc, lifecycle=lifecycle)
        self.sequence += 1
        self.ended = True
        return frame


def preflight_metadata_bytes(metadata: dict[str, Any], run_id: str, session_id: str) -> bytes:
    """Canonical application metadata carried in existing V1 start extensions.

    This is not a new frame/stream schema. Unknown fields are rejected rather
    than silently redacting a credential and changing its claimed identity.
    """
    import re
    fields = ('run_id', 'session_id', 'worker_id', 'remote_container_id', 'remote_hostname',
              'remote_user', 'endpoint', 'openssl_version', 'openssl_executable', 'ca_path',
              'ca_sha256', 'tls_parameter_fingerprint', 'connect_timeout', 'handshake_timeout',
              'preflight_timestamp', 'preflight_status')
    ownership_fields = ('worker_pid', 'worker_process_identity', 'worker_command_identity',
                        'worker_command_fingerprint', 'worker_endpoint', 'ownership_verified',
                        'ownership_evidence_timestamp', 'preflight_error')
    extended = isinstance(metadata, dict) and set(metadata) == set(fields + ownership_fields)
    failed = extended and metadata.get('preflight_status') == 'FAIL'
    if not isinstance(metadata, dict) or (set(metadata) != set(fields) and not extended):
        raise EvidenceError('Missing or unsupported preflight metadata fields; values withheld')
    for key in fields:
        value = metadata[key]
        if failed and value is None and key in ('remote_container_id', 'remote_hostname', 'remote_user',
                'openssl_version', 'openssl_executable', 'ca_path', 'ca_sha256'):
            continue
        if key in ('connect_timeout', 'handshake_timeout'):
            if type(value) not in (int, float) or value != (15 if key == 'connect_timeout' else 5):
                raise EvidenceError('Preflight timeout differs from approved budget')
        elif (not isinstance(value, str) or not value or len(value) > 512
              or re.search(r'[\x00-\x1f\x7f]|(?i:bearer|api[_-]?key|password|token|secret|private|-----|\bsk-)', value)):
            raise EvidenceError('Unsafe or non-string preflight field; values withheld')
    if metadata['run_id'] != run_id or metadata['session_id'] != session_id:
        raise EvidenceError('Preflight Run/Session binding mismatch')
    if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', metadata[k])
           or '..' in metadata[k] for k in ('run_id', 'session_id')):
        raise EvidenceError('Unsafe preflight identity')
    if metadata['worker_id'] != 'worker-0' or metadata['endpoint'] != '127.0.0.1:8000':
        raise EvidenceError('Preflight Worker/Endpoint mismatch')
    if not failed and (not re.fullmatch(r'[0-9a-f]{12,64}', metadata['remote_container_id'])
            or metadata['remote_hostname'] != metadata['remote_container_id']
            or metadata['remote_user'] != 'root'):
        raise EvidenceError('Invalid preflight container/user binding')
    if not failed and not re.fullmatch(r'OpenSSL 3\.\d+\.\d+[ A-Za-z0-9().:-]*', metadata['openssl_version']):
        raise EvidenceError('Unsupported OpenSSL version metadata')
    for key in ('openssl_executable', 'ca_path'):
        value = metadata[key]
        if failed and value is None:
            continue
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+', value) or '..' in value or '//' in value:
            raise EvidenceError('Unsafe preflight path')
    if metadata['openssl_executable'] is not None and Path(metadata['openssl_executable']).name != 'openssl':
        raise EvidenceError('Unexpected OpenSSL executable metadata')
    for key in ('ca_sha256', 'tls_parameter_fingerprint'):
        if failed and metadata[key] is None:
            continue
        if not re.fullmatch(r'[0-9a-f]{64}', metadata[key]):
            raise EvidenceError('Invalid preflight SHA-256')
    try:
        timestamp = datetime.fromisoformat(metadata['preflight_timestamp'].replace('Z', '+00:00'))
        if timestamp.utcoffset() is None or timestamp.utcoffset().total_seconds() != 0:
            raise ValueError('UTC required')
    except ValueError as exc:
        raise EvidenceError('Invalid preflight UTC timestamp') from exc
    if metadata['preflight_status'] not in ('PASS', 'FAIL'):
        raise EvidenceError('Unknown preflight status')
    if extended:
        pid = metadata['worker_pid']
        if not (failed and pid is None) and (type(pid) is not int or pid <= 0):
            raise EvidenceError('Missing or invalid Worker PID')
        verified = metadata['ownership_verified']
        if verified is not None and type(verified) is not bool:
            raise EvidenceError('Invalid ownership verification state')
        if not failed and verified is not True:
            raise EvidenceError('Worker ownership not verified')
        checks = {
            'worker_process_identity': r'[0-9]+',
            'worker_command_identity': r'vllm-worker-0',
            'worker_command_fingerprint': r'[0-9a-f]{64}',
            'worker_endpoint': r'127\.0\.0\.1:8000',
        }
        for key, pattern in checks.items():
            value = metadata[key]
            if failed and value is None:
                continue
            if not isinstance(value, str) or not re.fullmatch(pattern, value):
                raise EvidenceError('Invalid Worker ownership identity; values withheld')
        try:
            stamp = datetime.fromisoformat(metadata['ownership_evidence_timestamp'].replace('Z', '+00:00'))
            if stamp.utcoffset() is None or stamp.utcoffset().total_seconds() != 0:
                raise ValueError('UTC required')
        except (ValueError, AttributeError) as exc:
            raise EvidenceError('Invalid ownership evidence timestamp') from exc
        error = metadata['preflight_error']
        if error is not None and (not isinstance(error, str) or not re.fullmatch(r'[A-Z_]{1,64}', error)):
            raise EvidenceError('Unsafe preflight error code')
        if not failed and error is not None:
            raise EvidenceError('Successful preflight cannot contain an error')
    # Normalize numeric representation as well as key order, without textifying payloads.
    value = dict(metadata, connect_timeout=15, handshake_timeout=5)
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def preflight_start_fields(metadata: dict[str, Any], run_id: str, session_id: str) -> dict[str, Any]:
    data = preflight_metadata_bytes(metadata, run_id, session_id)
    return dict(remote_preflight=json.loads(data), remote_preflight_sha256=hashlib.sha256(data).hexdigest())


def produce_session(process, run_id, nonce, emit, clock=None, started_monotonic=None, *,
                    preflight_metadata: dict[str, Any] | None = None,
                    process_factory: Callable[[], Any] | None = None):
    """Reviewed remote producer protocol; tests inject a Mock process and sink.

    Uses the existing binary pipe adapter; writes no remote files. It frames,
    rather than persists, bytes. The local EvidenceStore remains authoritative.
    """
    clock = clock or Clock()
    start = clock.monotonic() if started_monotonic is None else started_monotonic
    utc = clock.utc()
    encoder = FrameEncoder(run_id, nonce)
    start_emitted = False
    if preflight_metadata is not None:
        # A lazy factory is mandatory: validation and control delivery precede
        # execution of the supervised task. A failed emit cannot start it.
        if process is not None or process_factory is None:
            raise EvidenceError('Preflight session requires an unstarted task factory')
        fields = preflight_start_fields(preflight_metadata, run_id, nonce)
        emit(encoder.start(remote_monotonic_ns=int(clock.monotonic()*1e9),
                           remote_utc=clock.utc(), **fields))
        if fields['remote_preflight']['preflight_status'] != 'PASS':
            raise EvidenceError('Preflight rejected; no supervised task launched')
        start_emitted = True
        start, utc = clock.monotonic(), clock.utc()
        process = process_factory()
    elif process_factory is not None or process is None:
        raise EvidenceError('Task factory requires preflight metadata')
    lock = threading.Lock()
    state = dict(timed_out=False, terminate_called=False, kill_called=False, reaped=False,
                 ownership_confirmed=None, returncode=None)
    def timestamps():
        return dict(remote_monotonic_ns=int(clock.monotonic()*1e9), remote_utc=clock.utc())
    def stop():
        with lock:
            if process.poll() is None:
                if not process.owns(process.start_identity):
                    state['ownership_confirmed'] = False
                    return
                state['ownership_confirmed'] = True
                state['terminate_called'] = True
                try:
                    process.terminate()
                    process.wait(.25)
                except (OSError, subprocess.TimeoutExpired):
                    if process.poll() is None:
                        if not process.owns(process.start_identity):
                            state['ownership_confirmed'] = False
                            return
                        state['kill_called'] = True
                        process.kill()
                    process.wait(1)
            state['returncode'] = process.wait(.25)
            state['reaped'] = True
    def expire():
        state['timed_out'] = True
        try:
            stop()
        except Exception:
            state['reaped'] = False
    timer = threading.Timer(max(0, 5.0-(clock.monotonic()-start)), expire)
    timer.daemon = True
    timer.start()
    failure = False
    try:
        if not start_emitted:
            emit(encoder.start(**timestamps()))
        eof = set()
        drain = None
        while len(eof) < 2:
            if clock.monotonic()-start >= 5 and not state['timed_out']:
                expire()
            if state['timed_out']:
                drain = drain or clock.monotonic()+.5
                if clock.monotonic() >= drain:
                    raise EvidenceError('Remote pipe EOF incomplete')
            for stream, data in process.read(.02):
                if stream not in encoder.counts or stream in eof:
                    raise EvidenceError('Invalid remote pipe event')
                if data is None:
                    eof.add(stream)
                else:
                    for frame in encoder.data(stream, data, **timestamps()):
                        emit(frame)
        while process.poll() is None:
            if clock.monotonic()-start >= 5:
                expire()
                break
            try:
                process.wait(.02)
            except subprocess.TimeoutExpired:
                pass
        if process.poll() is not None:
            state['returncode'] = process.wait(.25)
            state['reaped'] = True
    except Exception:
        failure = True
        stop()
    finally:
        timer.cancel(); timer.join()
        process.close()
    if not failure and state['reaped']:
        emit(encoder.finish(returncode=state['returncode'], reaped=True, timed_out=state['timed_out'],
             lifecycle=dict(state, pid=process.pid, process_start_identity=process.start_identity,
                            argv=getattr(process, 'argv', None),
                            executable_path=process.executable_path, started_at_utc=utc,
                            started_monotonic_seconds=start, ended_monotonic_seconds=clock.monotonic(),
                            termination_signal=-state['returncode'] if state['returncode'] < 0 else None,
                            ended_at_utc=clock.utc(), elapsed_seconds=clock.monotonic()-start), **timestamps()))
    return state


def remote_wrapper_source(argv: list[str], run_id: str, nonce: str) -> str:
    """Serialize a future producer. Merely building this string does not execute it.

    A Stage B controller must insert approved identity/CA/Worker gates BEFORE the
    launch block; this generic transport does not substitute for those TLS gates.
    """
    argv = validate_argv(argv)
    return _remote_wrapper_source(repr(argv), run_id, nonce)


def _remote_wrapper_source(argv_expression: str, run_id: str, nonce: str) -> str:
    """Internal generator; expressions are constructed here, never user shell text."""
    header = ('from __future__ import annotations\nimport os, selectors, subprocess, threading, time, hashlib, json, base64\n'
              'import sys\nfrom pathlib import Path\nfrom datetime import datetime, timezone\nfrom typing import Any, Callable, Iterator\n')
    body = '\n'.join(inspect.getsource(x) for x in (
        EvidenceError, Clock, PopenPipeProcess, encode_frame, data_frame, exit_frame, FrameEncoder,
        preflight_metadata_bytes, preflight_start_fields, produce_session))
    launch = (f'\nPROTOCOL={PROTOCOL!r}\nPREFIX={PREFIX!r}\n'
              f'STREAMS={STREAMS!r}\n'
              f'MAX_FRAME_BYTES={MAX_FRAME_BYTES!r}\nFRAME_CHUNK_BYTES={FRAME_CHUNK_BYTES!r}\n'
              'def wire_emit(data):\n'
              '    view=memoryview(data)\n'
              '    while view:\n'
              '        count=os.write(1,view)\n'
              '        if count<=0: raise OSError("wire write failed")\n'
              '        view=view[count:]\n'
              'def authorized_launch_after_preflight(preflight_metadata=None):\n'
              '    env={k:os.environ[k] for k in ("PATH","LANG","LC_ALL") if k in os.environ}\n'
              f'    argv={argv_expression}\n'
              '    started=time.monotonic()\n'
              '    def launch():\n'
              '        child=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,shell=False,start_new_session=True,env=env)\n'
              '        try:\n'
              '            adapter=PopenPipeProcess(child)\n'
              '            adapter.argv=argv\n'
              '            return adapter\n'
              '        except BaseException:\n'
              '            if child.poll() is None:\n'
              '                child.terminate()\n'
              '                try: child.wait(timeout=.25)\n'
              '                except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=1)\n'
              '            child.stdout.close(); child.stderr.close()\n'
              '            raise\n'
              '    if preflight_metadata is not None:\n'
              f'        return produce_session(None,{run_id!r},{nonce!r},wire_emit,preflight_metadata=preflight_metadata,process_factory=launch)\n'
              f'    return produce_session(launch(),{run_id!r},{nonce!r},wire_emit,started_monotonic=started)\n')
    # Intentionally defines, but does not call, the launch function. A future
    # reviewed Stage B bootstrap supplies its gates and explicit call.
    return header + body + launch


BYTE_PROBE_STDOUT = (b'SSH-EVIDENCE-V1\nHOST=36f825bcc5da\nUID=0\n'
                     + bytes.fromhex('005354444f55543aff804142430d0a'))
BYTE_PROBE_STDERR = bytes.fromhex('005354444552523afe814445460a')


@dataclass(frozen=True)
class ByteProbePlan:
    source: str
    expected_stdout: bytes = BYTE_PROBE_STDOUT
    expected_stderr: bytes = BYTE_PROBE_STDERR
    expected_exit_status: int = 23
    protocol: str = PROTOCOL


def prepare_byte_probe(run_id: str, nonce: str, *, expected_hostname: str = '36f825bcc5da',
                       expected_uid: int = 0) -> ByteProbePlan:
    """Prepare the one-command byte test without launching any process.

    A supervised Python child writes only the exact payload bytes to its two
    buffers, exits 23 and is reaped. The existing producer frames those bytes
    plus its real wait receipt; the outer command then exits with the same code.
    """
    if not isinstance(expected_hostname, str) or type(expected_uid) is not int:
        raise EvidenceError('Invalid expected probe identity')
    payload = ('import sys\n'
               f'sys.stdout.buffer.write({BYTE_PROBE_STDOUT!r})\n'
               'sys.stdout.buffer.flush()\n'
               f'sys.stderr.buffer.write({BYTE_PROBE_STDERR!r})\n'
               'sys.stderr.buffer.flush()\n'
               'raise SystemExit(23)\n')
    # sys.executable is evaluated by the executing interpreter, never substituted
    # with a guessed remote path or the local WSL virtualenv path.
    source = _remote_wrapper_source(f'[sys.executable, "-B", "-c", {payload!r}]', run_id, nonce)
    source += ('\nif __name__ == "__main__":\n'
               f'    if os.uname().nodename != {expected_hostname!r} or os.getuid() != {expected_uid!r}:\n'
               '        raise SystemExit(90)\n'
               '    outcome=authorized_launch_after_preflight()\n'
               '    if not outcome["reaped"] or outcome["timed_out"]:\n'
               '        raise SystemExit(89)\n'
               '    code=outcome["returncode"]\n'
               '    raise SystemExit(code if 0 <= code <= 255 else 128-code)\n')
    compile(source, 'prepared_byte_probe', 'exec')
    return ByteProbePlan(source=source)


class FrameDecoder:
    """Incremental bounded decoder; raw wire is persisted before invoking feed."""
    def __init__(self, run_id: str, nonce: str):
        self.run_id, self.nonce = run_id, nonce
        self.pending = b''
        self.sequence = 0
        self.started = False
        self.receipt: dict[str, Any] | None = None
        self.preflight_metadata: dict[str, Any] | None = None
        self.counts = dict.fromkeys(STREAMS, 0)
        self.hashes = {s: hashlib.sha256() for s in self.counts}

    def feed(self, data: bytes, sink: Callable[[dict[str, Any], bytes], None]) -> None:
        # Bound the unfinished frame rather than rejecting a batch of many frames.
        for piece in data.splitlines(keepends=True):
            self.pending += piece
            if len(self.pending) > MAX_FRAME_BYTES:
                raise EvidenceError('Remote frame exceeds declared limit')
            if not self.pending.endswith(b'\n'):
                continue
            line, self.pending = self.pending, b''
            if not line.startswith(PREFIX):
                raise EvidenceError('Unframed remote stdout; provenance unknown')
            try:
                item = json.loads(line[len(PREFIX):])
            except (ValueError, UnicodeError) as exc:
                raise EvidenceError('Invalid remote frame JSON') from exc
            if (not isinstance(item, dict) or item.get('protocol') != PROTOCOL
                    or item.get('run_id') != self.run_id or item.get('nonce') != self.nonce
                    or type(item.get('sequence')) is not int or item['sequence'] != self.sequence + 1
                    or self.receipt is not None):
                raise EvidenceError('Remote frame identity/order mismatch')
            self.sequence = item['sequence']
            kind = item.get('kind')
            if kind == 'start' and not self.started and self.sequence == 1:
                if 'remote_preflight' in item or 'remote_preflight_sha256' in item:
                    metadata = item.get('remote_preflight')
                    canonical = preflight_metadata_bytes(metadata, self.run_id, self.nonce)
                    if hashlib.sha256(canonical).hexdigest() != item.get('remote_preflight_sha256'):
                        raise EvidenceError('Preflight metadata SHA-256 mismatch')
                    self.preflight_metadata = json.loads(canonical)
                self.started = True
                sink(item, b'')
            elif kind == 'data' and self.started:
                stream = item.get('stream')
                if stream not in self.counts:
                    raise EvidenceError('Unknown remote stream')
                try:
                    chunk = base64.b64decode(item['payload'], validate=True)
                except (ValueError, KeyError, TypeError) as exc:
                    raise EvidenceError('Invalid remote base64') from exc
                if (type(item.get('byte_count')) is not int or len(chunk) != item['byte_count']
                        or type(item.get('offset')) is not int or item['offset'] != self.counts[stream]
                        or hashlib.sha256(chunk).hexdigest() != item.get('chunk_sha256')):
                    raise EvidenceError('Remote chunk length/offset/SHA-256 mismatch')
                sink(item, chunk)
                self.counts[stream] += len(chunk)
                self.hashes[stream].update(chunk)
            elif kind == 'exit' and self.started:
                if (type(item.get('returncode')) is not int or item.get('reaped') is not True
                        or type(item.get('timed_out')) is not bool):
                    raise EvidenceError('Invalid remote termination receipt')
                for stream in self.counts:
                    if (type(item.get(stream + '_bytes')) is not int
                            or item[stream + '_bytes'] != self.counts[stream]
                            or item.get(stream + '_sha256') != self.hashes[stream].hexdigest()):
                        raise EvidenceError('Remote final size/SHA-256 mismatch')
                self.receipt = item
                sink(item, b'')
            else:
                raise EvidenceError('Invalid remote protocol transition')

    def finish(self) -> None:
        if self.pending or not self.started or self.receipt is None:
            raise EvidenceError('Remote stream/termination evidence incomplete')


class SSHLogProcess:
    """Add a tailed -E file to an already-owned binary process adapter.

    The file is precreated by EvidenceStore; chunks are observed, never rewritten.
    Its final EOF is emitted only after the owned SSH process exits and is drained.
    """
    streams = ('stdout', 'stderr', 'debug')
    def __init__(self, process: OwnedProcess, log_path: Path):
        self.child = process
        self.pid, self.start_identity = process.pid, process.start_identity
        self.executable_path = process.executable_path
        self.logfd = os.open(log_path, os.O_RDONLY | os.O_NOFOLLOW)
        self.offset = 0
        self.pipe_eof: set[str] = set()
        self.log_eof = False
        self.prefer_log = True

    def _log(self) -> list[tuple[str, bytes | None]]:
        size = os.fstat(self.logfd).st_size
        if size < self.offset:
            raise EvidenceError('SSH debug log was truncated')
        data = os.pread(self.logfd, 65536, self.offset)
        if data:
            self.offset += len(data)
            return [('debug', data)]
        if self.child.poll() is not None and len(self.pipe_eof) == 2 and not self.log_eof:
            self.log_eof = True
            return [('debug', None)]
        return []

    def read(self, timeout: float) -> list[tuple[str, bytes | None]]:
        self.prefer_log = not self.prefer_log
        if self.prefer_log:
            if events := self._log():
                return events
        if len(self.pipe_eof) < 2:
            events = self.child.read(timeout)
            self.pipe_eof.update(s for s, data in events if data is None)
            if events:
                return events
        return self._log()

    def poll(self) -> int | None:
        return self.child.poll()

    def owns(self, identity: str | None) -> bool:
        return self.child.owns(identity)

    def terminate(self) -> None:
        self.child.terminate()

    def kill(self) -> None:
        self.child.kill()

    def wait(self, timeout: float) -> int:
        return self.child.wait(timeout)

    def close(self) -> None:
        os.close(self.logfd)
        self.child.close()


class TransportStore(EvidenceStore):
    def __init__(self, *args: Any):
        super().__init__(*args, stream_files=STREAM_FILES, external_streams=('debug',))
        try:
            for name in ('remote_observed_timeline.jsonl', 'transport_status.json'):
                self.fds[name] = self.create(name)
                os.fsync(self.fds[name])
            self.prepare_manifest()
        except BaseException:
            self.close()
            raise


class SetupFailureProcess(SSHLogProcess):
    """Deliver ownership to shared cleanup even when debug-tail setup fails."""
    streams = ('stdout', 'stderr')
    def __init__(self, child: OwnedProcess):
        self.child = child
        self.pid, self.start_identity = child.pid, child.start_identity
        self.executable_path = child.executable_path

    def read(self, timeout: float) -> list[tuple[str, bytes | None]]:
        raise EvidenceError('Cannot open precreated SSH debug log')

    def close(self) -> None:
        self.child.close()


def exit_semantics(ssh_code: int | None, local_timeout: bool,
                   receipt: dict[str, Any] | None, *, evidence_valid: bool) -> dict[str, Any]:
    # evidence_valid means the caller validated the whole envelope, including the
    # terminal sizes/hashes. A return code alone is never a reliable receipt.
    observed = (evidence_valid and isinstance(receipt, dict)
                and type(receipt.get('returncode')) is int and receipt.get('reaped') is True
                and type(receipt.get('timed_out')) is bool)
    code = receipt['returncode'] if observed else None
    detail = None
    if local_timeout:
        transport = 'LOCAL_TIMEOUT'
    elif ssh_code is None or type(ssh_code) is not int:
        transport = 'UNKNOWN'
    elif ssh_code == 255:
        # Preserve the existing ambiguity marker for an independently proven
        # remote 255; absence of a receipt must never infer remote 255.
        transport = 'SSH_255_AMBIGUOUS' if observed else 'FAILED_OR_UNKNOWN'
        detail = 'SSH_255_AMBIGUOUS'
    elif observed and (ssh_code == 0 or ssh_code == code >= 0):
        # Legacy wrappers exit 0 after reporting the child's status. The byte
        # probe propagates that status instead. Both have explicit receipts.
        transport = 'SUCCESS'
    else:
        transport = 'FAILED_OR_UNKNOWN'
        detail = 'EXIT_STATUS_MISMATCH' if observed else 'REMOTE_RECEIPT_UNAVAILABLE'
    command = ('TIMED_OUT' if receipt['timed_out'] else 'TERMINATED' if code < 0
               else 'COMPLETED' if code == 0 else 'COMPLETED_NONZERO') if observed else 'UNKNOWN'
    return dict(ssh_process_return_code=ssh_code, remote_exit_status=code,
                remote_exit_status_observed=observed, ssh_transport_status=transport,
                remote_command_status=command, ssh_transport_detail=detail,
                local_timeout=local_timeout,
                remote_termination_status=('SIGNALED' if code < 0 else 'EXITED') if observed else 'UNKNOWN',
                remote_timed_out=receipt.get('timed_out') if observed else None,
                local_stderr_provenance='UNCLASSIFIED_SSH_OR_REMOTE_BOOTSTRAP',
                debug_provenance='OPENSSH_E_CLIENT_LOG; not all local errors guaranteed redirected',
                remote_stream_provenance='VALIDATED_ENVELOPE' if evidence_valid else 'INCOMPLETE_ENVELOPE')


def capture_ssh_transport(base: Path, run_id: str, *,
                          process_factory: Callable[[list[str]], OwnedProcess],
                          settings: SSHSettings = SSHSettings(), nonce: str | None = None,
                          clock: Clock | None = None, max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
                          local_timeout_seconds: float = SSH_OUTER_TIMEOUT,
                          preflight_metadata: dict[str, Any] | None = None,
                          require_preflight_metadata: bool = False) -> dict[str, Any]:
    """Exactly one injected launcher. Stage A supplies Mock/Fake SSH only.

    There is no default real launcher or --execute flag. Future real execution
    requires independent Stage B authorization and a reviewed producer bootstrap.
    """
    nonce = nonce or uuid.uuid4().hex
    if require_preflight_metadata and preflight_metadata is None:
        raise EvidenceError('Required local preflight expectation missing; no SSH launch')
    expected = (json.loads(preflight_metadata_bytes(preflight_metadata, run_id, nonce))
                if preflight_metadata is not None else None)
    if expected is not None and expected['preflight_status'] != 'PASS':
        raise EvidenceError('Local preflight expectation rejected; no SSH launch')
    decoder = FrameDecoder(run_id, nonce)
    log = Path(base).absolute() / run_id / 'ssh_client.log.raw'
    argv = ssh_argv(log, settings)
    store_holder: list[EvidenceStore] = []
    valid = False
    def make_store(*args: Any) -> EvidenceStore:
        store = TransportStore(*args)
        try:
            if expected is not None:
                # Expectations are not remote observations. Durability checks
                # and space reservation must finish before the SSH factory.
                for name in ('preflight_expectation.json', 'remote_preflight.json'):
                    store.fds[name] = store.create(name)
                    os.fsync(store.fds[name])
                store.record('preflight_expectation.json', dict(
                    kind='LOCAL_EXPECTATION_NOT_REMOTE_OBSERVATION', metadata=expected))
                os.fsync(store.dirfd)
            store.event('ssh_transport_prepared', protocol=PROTOCOL, nonce=nonce,
                        stderr_provenance='UNCLASSIFIED_UNTIL_FRAMED', network_authorization='not provided by adapter')
        except BaseException:
            store.close()
            raise
        store_holder.append(store)
        return store
    def factory(command: list[str]) -> OwnedProcess:
        child = process_factory(command)
        try:
            return SSHLogProcess(child, log)
        except BaseException:
            # Let the shared lifecycle controller own/reclaim even setup failures.
            # Deliver the handle first; record setup error through its first read.
            return SetupFailureProcess(child)
    def on_chunk(store: EvidenceStore, stream: str, data: bytes) -> None:
        if stream != 'stdout':
            return
        def sink(frame: dict[str, Any], chunk: bytes) -> None:
            if frame['kind'] == 'start':
                observed = decoder.preflight_metadata
                if expected is not None and observed is None:
                    raise EvidenceError('Required remote preflight metadata missing')
                if observed is not None:
                    if expected is None:
                        raise EvidenceError('Unexpected metadata without prepared persistence contract')
                    store.write('remote_preflight.json',
                                preflight_metadata_bytes(observed, run_id, nonce) + b'\n')
                    # Timestamp is generated by the remote preflight. Status may
                    # reject a matching identity and must still remain recorded.
                    keys = set(expected) - {'preflight_timestamp', 'preflight_status', 'ownership_evidence_timestamp'}
                    if any(observed[key] != expected[key] for key in keys):
                        raise EvidenceError('Remote preflight differs from local expectation')
                    if observed['preflight_status'] != 'PASS':
                        raise EvidenceError('Remote preflight rejected the supervised task')
            if frame['kind'] == 'data':
                store.chunk('remote_' + frame['stream'], chunk)
            # Do not put payload bytes or arbitrary remote metadata into audit JSON.
            store.record('remote_observed_timeline.jsonl', dict(
                sequence=frame['sequence'], kind=frame['kind'], stream=frame.get('stream'),
                remote_monotonic_ns=frame.get('remote_monotonic_ns'), remote_utc=frame.get('remote_utc'),
                local_received_monotonic_seconds=store.clock.monotonic(),
                local_received_utc=store.clock.utc(), byte_count=frame.get('byte_count'),
                offset=frame.get('offset'), chunk_sha256=frame.get('chunk_sha256'),
                ordering='remote parent observed read order; separate from local receipt order'))
        decoder.feed(data, sink)
    def on_finish(store: EvidenceStore, result: dict[str, Any]) -> None:
        nonlocal valid
        decoder.finish()
        valid = True
    def finalize(store: EvidenceStore, result: dict[str, Any]) -> None:
        status = exit_semantics(result['returncode'], result['timed_out'], decoder.receipt,
                                evidence_valid=valid and result['capture_complete'])
        store.record('transport_status.json', status)
    try:
        result = capture_process(base, run_id, argv, process_factory=factory,
            clock=clock, clock_domain='local_ssh_parent', max_output_bytes=max_output_bytes,
            process_timeout_seconds=local_timeout_seconds, store_factory=make_store,
            on_chunk=on_chunk, on_finish=on_finish, on_finalize=finalize)
    except BaseException:
        # The shared controller already preserves partial raw evidence and reaps
        # the owned local child. Never infer remote cleanup from that fact.
        raise
    verify_manifest(Path(result['evidence_directory']))
    status = exit_semantics(result['process']['returncode'], result['process']['timed_out'],
                            decoder.receipt, evidence_valid=valid)
    return dict(capture=result, transport=status, protocol=PROTOCOL, nonce=nonce)


def verified_remote_evidence(root: Path) -> dict[str, Any]:
    """Only complete validated envelopes may feed a separate frozen classifier."""
    verify_manifest(root)
    result = json.loads((Path(root) / 'process_result.json').read_bytes())
    status = json.loads((Path(root) / 'transport_status.json').read_bytes())
    if not result['capture_complete'] or not status['remote_exit_status_observed']:
        raise EvidenceError('Remote evidence incomplete; no TLS success inference allowed')
    evidence = dict(stdout=(Path(root) / 'remote_stdout.raw').read_bytes(),
                    stderr=(Path(root) / 'remote_stderr.raw').read_bytes(), transport=status)
    if (Path(root) / 'preflight_expectation.json').exists():
        evidence['preflight_metadata'] = verified_preflight_metadata(root)
    return evidence


def verified_preflight_metadata(root: Path) -> dict[str, Any]:
    """Validate independent metadata against its authoritative original Wire."""
    manifest = verify_manifest(root)
    if not {'preflight_expectation.json', 'remote_preflight.json'} <= {
            item['path'] for item in manifest['artifacts']}:
        raise EvidenceError('Preflight artifacts missing from Manifest')
    expectation = json.loads((Path(root) / 'preflight_expectation.json').read_bytes())
    if expectation.get('kind') != 'LOCAL_EXPECTATION_NOT_REMOTE_OBSERVATION':
        raise EvidenceError('Invalid preflight expectation provenance')
    expected = expectation['metadata']
    expected = json.loads(preflight_metadata_bytes(expected, expected['run_id'], expected['session_id']))
    decoder = FrameDecoder(expected['run_id'], expected['session_id'])
    decoder.feed((Path(root) / STREAM_FILES['stdout']).read_bytes(), lambda *args: None)
    decoder.finish()
    observed = decoder.preflight_metadata
    if observed is None or observed['preflight_status'] != 'PASS':
        raise EvidenceError('No successful remote preflight metadata')
    canonical = preflight_metadata_bytes(observed, expected['run_id'], expected['session_id'])
    if (Path(root) / 'remote_preflight.json').read_bytes() != canonical + b'\n':
        raise EvidenceError('Persisted metadata differs from original Wire')
    if any(observed[k] != expected[k] for k in set(expected) - {
            'preflight_timestamp', 'preflight_status', 'ownership_evidence_timestamp'}):
        raise EvidenceError('Preflight expectation binding changed')
    return observed


def offline_preflight() -> dict[str, Any]:
    return dict(mode='offline_preflight', network_enabled=False, ssh_enabled=False,
                remote_command_enabled=False, real_qwen_enabled=False,
                fake_ssh_executable=None, execution='injected Mock/Fake factory only; no default launcher',
                protocol=PROTOCOL, real_remote_transport_verified=False)
