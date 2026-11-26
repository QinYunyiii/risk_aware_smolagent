"""Byte-preserving evidence capture. No command is launched by this module.

The caller must supply a factory returning an exclusively owned process handle.
The Stage A CLI supplies no such factory. Raw files are private, unredacted evidence;
they must never be included in ordinary logs or automatically published.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import threading
import time
from typing import Any, Callable, Protocol

from risk_agent.monitoring.provider_response_audit import sanitize_audit_value

TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
STREAMS = ('stdout', 'stderr')


class EvidenceError(RuntimeError):
    """Evidence cannot be trusted; any already written files must be retained."""


class StorageLimitExceeded(EvidenceError):
    """Output or observation metadata exceeded its declared storage limit."""


class Clock:
    def monotonic(self) -> float:
        return time.monotonic()

    def utc(self) -> str:
        return datetime.now(timezone.utc).isoformat()


class OwnedProcess(Protocol):
    pid: int
    start_identity: str | None
    executable_path: str
    def read(self, timeout: float) -> list[tuple[str, bytes | None]]: ...
    def poll(self) -> int | None: ...
    def owns(self, start_identity: str | None) -> bool: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float) -> int: ...
    def close(self) -> None: ...


class PopenPipeProcess:
    """Wrap a caller-owned binary Popen; construction never launches a command.

    Future factory must use shell=False, binary PIPEs, stdin=DEVNULL, and an
    environment without credentials. If factory setup fails after launch it is
    responsible for reclaiming that child before propagating its exception.
    """
    def __init__(self, process: subprocess.Popen[bytes]):
        if process.text_mode or process.stdout is None or process.stderr is None:
            raise EvidenceError('Binary stdout/stderr pipes required')
        self.process = process
        self.pid = process.pid
        self.start_identity = self._identity()
        try:
            self.executable_path = os.readlink(f'/proc/{self.pid}/exe')
        except OSError:
            self.executable_path = 'unavailable: process exited before executable sampling'
        self.selector = selectors.DefaultSelector()
        for stream in STREAMS:
            pipe = getattr(process, stream)
            os.set_blocking(pipe.fileno(), False)
            self.selector.register(pipe, selectors.EVENT_READ, stream)

    def _identity(self) -> str | None:
        try:
            return Path(f'/proc/{self.pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
        except (OSError, IndexError):
            return None

    def owns(self, start_identity: str | None) -> bool:
        return (start_identity is not None and self.start_identity == start_identity
                and self._identity() == start_identity)

    def read(self, timeout: float) -> list[tuple[str, bytes | None]]:
        events = []
        for key, _ in self.selector.select(timeout):
            try:
                data = os.read(key.fd, 65536)
            except OSError as exc:
                raise EvidenceError(f'{key.data} read failed') from exc
            if not data:
                self.selector.unregister(key.fileobj)
            events.append((key.data, data if data else None))
            break  # Timestamp the observed chunk before reading another pipe.
        return events

    def poll(self) -> int | None:
        return self.process.poll()

    def terminate(self) -> None:
        self.process.terminate()

    def kill(self) -> None:
        self.process.kill()

    def wait(self, timeout: float) -> int:
        return self.process.wait(timeout=timeout)

    def close(self) -> None:
        self.selector.close()
        self.process.stdout.close()
        self.process.stderr.close()


def _safe_name(name: str) -> None:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', name) or '..' in name:
        raise EvidenceError('Unsafe directory or artifact name')


def _directory(base: Path, run_id: str) -> tuple[Path, int]:
    """Traverse with dirfds/O_NOFOLLOW, then exclusively create a single run."""
    _safe_name(run_id)
    if '..' in Path(base).parts:
        raise EvidenceError('Unsafe base path')
    base = Path(os.path.abspath(base))
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in base.parts[1:]:
            if part in ('.', '..'):
                raise EvidenceError('Unsafe base path')
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        os.mkdir(run_id, 0o700, dir_fd=fd)  # Never adopt or overwrite an existing run.
        result = os.open(run_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            os.fsync(fd)
        except BaseException:
            os.close(result)
            raise
        return base / run_id, result
    finally:
        os.close(fd)


def validate_argv(argv: list[str]) -> list[str]:
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
        raise EvidenceError('An argv array is required')
    if not Path(argv[0]).is_absolute():
        raise EvidenceError('Explicit executable path required')
    for arg in argv:
        if ('\x00' in arg or '\n' in arg or '\r' in arg
                or sanitize_audit_value(arg) != arg
                or re.search(r'(?i)^--?(?:api[-_]?key|password|token|secret|key|pass|private[-_]?key|keylogfile|ssl[-_]?keyfile)(?:=|$)', arg)
                or re.search(r'://[^/\s]*@', arg)):
            raise EvidenceError('Sensitive or invalid argv refused; values withheld')
    return list(argv)


class EvidenceStore:
    """Only a private new run; fsync raw bytes before their timeline receipt."""
    def __init__(self, base: Path, run_id: str, clock: Clock, clock_domain: str, limit: int,
                 *, stream_files: dict[str, str] | None = None, external_streams: tuple[str, ...] = ()):
        self.stream_files = dict(stream_files or {s: s + '.bin' for s in STREAMS})
        if len(set(self.stream_files.values())) != len(self.stream_files):
            raise EvidenceError('Stream filenames must be independent')
        for stream, name in self.stream_files.items():
            _safe_name(stream); _safe_name(name)
            if name in ('lifecycle.jsonl', 'process_result.json', 'combined_observed_timeline.jsonl',
                        'artifact_manifest.pending', 'artifact_manifest.json'):
                raise EvidenceError('Reserved artifact filename')
        if not set(external_streams) <= self.stream_files.keys():
            raise EvidenceError('Unknown external stream')
        self.external_streams = frozenset(external_streams)
        self.root, self.dirfd = _directory(base, run_id)
        self.clock, self.domain, self.limit = clock, clock_domain, limit
        self.fds: dict[str, int] = {}
        self.counts = dict.fromkeys(self.stream_files, 0)
        self.received_counts = dict.fromkeys(self.stream_files, 0)
        self.sequence = 0
        try:
            for name in (*self.stream_files.values(), 'combined_observed_timeline.jsonl',
                         'lifecycle.jsonl', 'process_result.json'):
                self.fds[name] = self.create(name)
                os.fsync(self.fds[name])
            os.fsync(self.dirfd)
        except BaseException:
            self.close()
            raise

    def create(self, name: str) -> int:
        _safe_name(name)
        return os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                       0o600, dir_fd=self.dirfd)

    def write(self, name: str, data: bytes) -> None:
        if name not in self.stream_files.values():
            metadata_limit = max(65536, min(self.limit * 8, 8 * 1024 * 1024))
            if os.fstat(self.fds[name]).st_size + len(data) > metadata_limit:
                raise StorageLimitExceeded('Metadata storage limit exceeded; capture is incomplete')
        view = memoryview(data)
        while view:
            written = os.write(self.fds[name], view)
            if written <= 0:
                raise EvidenceError('Artifact write made no progress')
            view = view[written:]
        os.fsync(self.fds[name])

    def record(self, name: str, value: dict[str, Any]) -> None:
        safe = sanitize_audit_value(value)
        self.write(name, json.dumps(safe, ensure_ascii=False).encode('utf-8') + b'\n')

    def event(self, event: str, **values: Any) -> None:
        self.record('lifecycle.jsonl', dict(values, event=event, utc=self.clock.utc(),
                    monotonic_seconds=self.clock.monotonic(), clock_domain=self.domain))

    def chunk(self, stream: str, data: bytes) -> None:
        observed_utc, observed_mono = self.clock.utc(), self.clock.monotonic()
        if stream not in self.stream_files or not isinstance(data, bytes):
            raise EvidenceError('Invalid stream or non-binary chunk')
        self.received_counts[stream] += len(data)
        if sum(self.counts.values()) + len(data) > self.limit:
            raise StorageLimitExceeded('Output storage limit exceeded; capture is incomplete')
        offset = self.counts[stream]
        name = self.stream_files[stream]
        if stream in self.external_streams:
            if os.fstat(self.fds[name]).st_size < offset + len(data):
                raise EvidenceError('External stream receipt exceeds file size')
            os.fsync(self.fds[name])  # The producer already wrote these exact bytes.
        else:
            self.write(name, data)
        self.counts[stream] += len(data)
        self.sequence += 1
        self.record('combined_observed_timeline.jsonl', dict(
            sequence=self.sequence, monotonic_seconds=observed_mono, utc=observed_utc,
            monotonic_ns=int(observed_mono * 1_000_000_000), utc_timestamp=observed_utc,
            clock_domain=self.domain, stream=stream, offset=offset, chunk_bytes=len(data),
            byte_count=len(data), cumulative_bytes=self.counts[stream],
            cumulative_stream_bytes=self.counts[stream], cumulative_total_bytes=sum(self.counts.values()),
            chunk_sha256=hashlib.sha256(data).hexdigest(),
            ordering='parent observed read order; not child write order'))

    def sync_raw(self) -> None:
        for name in self.stream_files.values():
            os.fsync(self.fds[name])

    def prepare_manifest(self) -> None:
        """Reserve and sync manifest storage before an external producer starts."""
        self.fds['artifact_manifest.pending'] = self.create('artifact_manifest.pending')
        os.fsync(self.fds['artifact_manifest.pending'])
        os.fsync(self.dirfd)

    def manifest(self) -> dict[str, Any]:
        entries = []
        for name in sorted(self.fds):
            if name == 'artifact_manifest.pending':
                continue
            os.fsync(self.fds[name])
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.dirfd)
            try:
                sha, size = hashlib.sha256(), 0
                while data := os.read(fd, 65536):
                    sha.update(data)
                    size += len(data)
                entries.append(dict(path=name, sha256=sha.hexdigest(), size_bytes=size))
            finally:
                os.close(fd)
        value = dict(schema_version=1, artifacts=entries, stream_files=self.stream_files,
                     raw_authority=list(self.stream_files.values()), raw_is_unredacted=True,
                     automatic_publish_allowed=False)
        # Publish a fully fsynced manifest with an atomic no-clobber hard link.
        # A failed write retains the private pending file and never exposes a
        # partial final manifest. Neither link nor create can replace history.
        pending = 'artifact_manifest.pending'
        if pending not in self.fds:
            self.fds[pending] = self.create(pending)
        self.record(pending, value)
        os.link(pending, 'artifact_manifest.json', src_dir_fd=self.dirfd,
                dst_dir_fd=self.dirfd, follow_symlinks=False)
        os.unlink(pending, dir_fd=self.dirfd)
        self.fds['artifact_manifest.json'] = self.fds.pop(pending)
        os.fsync(self.dirfd)
        return value

    def close(self) -> None:
        for fd in self.fds.values():
            os.close(fd)
        os.close(self.dirfd)


def verify_manifest(root: Path) -> dict[str, Any]:
    """Verify sizes, hashes and chunk reconstruction before downstream parsing."""
    root = Path(root)
    if root.is_symlink():
        raise EvidenceError('Symlink evidence directory refused')
    if (root / 'artifact_manifest.json').is_symlink():
        raise EvidenceError('Symlink manifest refused')
    value = json.loads((root / 'artifact_manifest.json').read_bytes())
    names = set()
    for entry in value['artifacts']:
        name = entry['path']
        _safe_name(name)
        if name in names or (root / name).is_symlink():
            raise EvidenceError('Duplicate or symlink artifact refused')
        names.add(name)
        data = (root / name).read_bytes()
        if len(data) != entry['size_bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise EvidenceError('Artifact size or SHA-256 mismatch')
    stream_files = value.get('stream_files', {s: s + '.bin' for s in STREAMS})
    for stream, name in stream_files.items():
        _safe_name(stream); _safe_name(name)
    required = {*stream_files.values(), 'combined_observed_timeline.jsonl', 'lifecycle.jsonl', 'process_result.json'}
    if not required <= names:
        raise EvidenceError('Required evidence missing from manifest')
    result = json.loads((root / 'process_result.json').read_bytes())
    blobs = {stream: (root / name).read_bytes() for stream, name in stream_files.items()}
    offsets = dict.fromkeys(stream_files, 0)
    previous_mono = float('-inf')
    for seq, line in enumerate((root / 'combined_observed_timeline.jsonl').read_bytes().splitlines(), 1):
        event = json.loads(line)
        stream = event['stream']
        if stream not in stream_files:
            raise EvidenceError('Invalid observed stream')
        data = blobs[stream]
        start, size = event['offset'], event['chunk_bytes']
        if (event['sequence'] != seq or start != offsets[stream]
                or not isinstance(size, int) or size < 0):
            raise EvidenceError('Invalid observed timeline sequence/offset')
        chunk = data[start:start + size]
        if len(chunk) != size or hashlib.sha256(chunk).hexdigest() != event['chunk_sha256']:
            raise EvidenceError('Observed chunk SHA-256 mismatch')
        offsets[stream] += size
        if (event['cumulative_stream_bytes'] != offsets[stream]
                or event['cumulative_total_bytes'] != sum(offsets.values())
                or event['clock_domain'] != result['clock_domain']
                or event['monotonic_seconds'] < previous_mono):
            raise EvidenceError('Inconsistent observed timeline metadata')
        previous_mono = event['monotonic_seconds']
    sizes = result.get('stream_bytes', {s: result.get(s + '_bytes') for s in STREAMS})
    if any(sizes.get(s) is not None and sizes[s] != len(blobs[s]) for s in stream_files):
        raise EvidenceError('Final stream size differs from process receipt')
    if result['capture_complete'] and any(offsets[s] != len(blobs[s]) for s in stream_files):
        raise EvidenceError('Complete capture has unaccounted bytes')
    return value


def verified_raw_streams(root: Path) -> dict[str, Any]:
    """A downstream classifier gets exact bytes AND lifecycle, never text alone."""
    verify_manifest(root)
    result = json.loads((Path(root) / 'process_result.json').read_bytes())
    if not result['capture_complete'] or not result['reaped'] or result['errors']:
        raise EvidenceError('Incomplete evidence must not be classified as TLS success')
    return dict(stdout=(Path(root) / 'stdout.bin').read_bytes(),
                stderr=(Path(root) / 'stderr.bin').read_bytes(), process=result)


def capture_process(base: Path, run_id: str, argv: list[str], *,
                    process_factory: Callable[[list[str]], OwnedProcess],
                    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
                    clock: Clock | None = None, clock_domain: str = 'local_parent',
                    process_timeout_seconds: float = TIMEOUT_SECONDS,
                    store_factory: Callable[..., EvidenceStore] = EvidenceStore,
                    on_chunk: Callable[[EvidenceStore, str, bytes], None] | None = None,
                    on_finish: Callable[[EvidenceStore, dict[str, Any]], None] | None = None,
                    on_finalize: Callable[[EvidenceStore, dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Capture one explicit owned process. Never retry and never classify TLS.

    A returned CAPTURE_COMPLETE describes persistence, not TLS success. All critical
    files and a durable prepared event exist before invoking the supplied factory.
    On persistence failure this raises, retains evidence, and reclaims only its child.
    """
    argv = validate_argv(argv)
    if max_output_bytes <= 0:
        raise EvidenceError('Positive output storage limit required')
    if not 0 < process_timeout_seconds <= 3600:
        raise EvidenceError('Invalid explicit process timeout')
    clock = clock or Clock()
    store = store_factory(base, run_id, clock, clock_domain, max_output_bytes)
    process = None
    timer = None
    started = clock.monotonic()
    result: dict[str, Any] = dict(argv=argv, executable_path=None, pid=None, process_start_identity=None,
        started_at_utc=clock.utc(), started_monotonic_seconds=started, timeout_seconds=process_timeout_seconds,
        start_time_basis='factory submission UTC; process identity sampled separately; not exact exec time',
        process_exit_observed_at_utc=None, process_exit_observed_monotonic_seconds=None,
        timed_out=False, terminate_called=False, kill_called=False, reaped=False, returncode=None,
        capture_complete=False, stdout_bytes=None, stderr_bytes=None, termination_signal=None,
        clock_domain=clock_domain, errors=[], ownership_confirmed=None)
    stop_lock = threading.Lock()

    def exit_observed() -> None:
        result['process_exit_observed_at_utc'] = clock.utc()
        result['process_exit_observed_monotonic_seconds'] = clock.monotonic()

    def stop_owned() -> None:
        if process is None:
            return
        with stop_lock:
            if process.poll() is not None:
                result['returncode'] = process.wait(.25)
                result['reaped'] = True
                exit_observed()
                return
            if not process.owns(result['process_start_identity']):
                result['ownership_confirmed'] = False
                raise EvidenceError('Ownership unconfirmed; no signal sent')
            result['ownership_confirmed'] = True
            try:
                store.sync_raw()  # Already read bytes persist before signalling.
            except OSError as exc:
                result['errors'].append(dict(type=type(exc).__name__, phase='pre_termination_fsync'))
            result['terminate_called'] = True
            try:
                process.terminate()
                result['returncode'] = process.wait(.25)
            except (OSError, subprocess.TimeoutExpired):
                if process.poll() is None:
                    if not process.owns(result['process_start_identity']):
                        raise EvidenceError('Ownership changed; no kill sent')
                    result['kill_called'] = True
                    process.kill()
                result['returncode'] = process.wait(1.0)
            result['reaped'] = True
            exit_observed()

    def deadline() -> None:
        result['timed_out'] = True
        try:
            stop_owned()
        except Exception as exc:
            result['errors'].append(dict(type=type(exc).__name__, phase='deadline_cleanup'))

    failure: BaseException | None = None
    try:
        store.event('capture_prepared', argv=argv, raw_is_private=True, maximum_bytes=max_output_bytes)
        # Durability preparation precedes the unchanged five-second process
        # budget; the factory submission itself is included, like Popen setup.
        started = clock.monotonic()
        result.update(started_at_utc=clock.utc(), started_monotonic_seconds=started)
        process = process_factory(argv)
        result.update(pid=process.pid, process_start_identity=process.start_identity,
                      executable_path=process.executable_path, process_received_at_utc=clock.utc(),
                      process_received_monotonic_seconds=clock.monotonic())
        timer = threading.Timer(max(0, process_timeout_seconds - (clock.monotonic() - started)), deadline)
        timer.daemon = True
        timer.start()
        store.event('owned_process_received', pid=process.pid, process_start_identity=process.start_identity)
        eof = set()
        pipe_streams = set(getattr(process, 'streams', STREAMS))
        if not pipe_streams or not pipe_streams <= store.stream_files.keys():
            raise EvidenceError('Unknown producer streams')
        drain_deadline = None
        while eof != pipe_streams:
            if clock.monotonic() - started >= process_timeout_seconds and not result['timed_out']:
                deadline()
            if result['timed_out']:
                if drain_deadline is None:
                    drain_deadline = clock.monotonic() + .5
                if clock.monotonic() >= drain_deadline:
                    raise EvidenceError('Output EOF unavailable after owned cleanup')
            for stream, data in process.read(.02):
                if stream not in pipe_streams or stream in eof:
                    raise EvidenceError('Invalid or post-EOF stream event')
                if data is None:
                    eof.add(stream)
                else:
                    store.chunk(stream, data)
                    if on_chunk:
                        on_chunk(store, stream, data)
        while process.poll() is None:
            if clock.monotonic() - started >= process_timeout_seconds and not result['timed_out']:
                deadline()
            if result['timed_out']:
                if not result['reaped']:
                    raise EvidenceError('Process could not be safely reaped')
                break
            try:
                process.wait(.02)
            except subprocess.TimeoutExpired:
                pass
        result['returncode'] = process.wait(.25)
        result['reaped'] = True
        exit_observed()
        if on_finish:
            on_finish(store, result)
        result['capture_complete'] = True
    except BaseException as exc:
        failure = exc
        result['errors'].append(dict(type=type(exc).__name__, phase='capture'))
        try:
            stop_owned()
        except Exception as cleanup:
            result['errors'].append(dict(type=type(cleanup).__name__, phase='cleanup'))
    finally:
        if timer is not None:
            timer.cancel()
            timer.join()
        if process is not None:
            try:
                process.close()
            except Exception as exc:
                result['errors'].append(dict(type=type(exc).__name__, phase='pipe_close'))
        result.update(ended_at_utc=clock.utc(), ended_monotonic_seconds=clock.monotonic(),
                      elapsed_seconds=clock.monotonic() - started)
        if process is not None:
            # Final sizes come from actual files, including any partial write.
            result['stream_bytes'] = {s: os.fstat(store.fds[name]).st_size for s, name in store.stream_files.items()}
            result['stdout_bytes'] = result['stream_bytes'].get('stdout')
            result['stderr_bytes'] = result['stream_bytes'].get('stderr')
        result['received_stream_bytes'] = dict(store.received_counts)
        result['stored_stream_bytes'] = dict(store.counts)
        result['maximum_output_bytes'] = max_output_bytes
        code = result['returncode']
        if code is not None and code < 0:
            result['termination_signal'] = -code
        result['capture_complete'] &= not result['errors'] and result['reaped']
        try:
            if on_finalize:
                on_finalize(store, result)
            store.event('capture_finished', **result)
            store.record('process_result.json', result)
            store.manifest()
        except BaseException as exc:
            failure = failure or exc
        finally:
            store.close()
    if failure is not None:
        # Untrusted exception text can itself contain credentials; only types are
        # persisted and the safe public exception suppresses automatic chaining.
        raise EvidenceError('Capture failed; private partial evidence retained') from None
    verify_manifest(store.root)
    return dict(status='CAPTURE_COMPLETE' if result['capture_complete'] else 'CAPTURE_FAILED',
                evidence_directory=str(store.root), process=result)


def offline_preflight() -> dict[str, Any]:
    return dict(mode='offline_preflight', network_requests=0, process_launches=0,
                timeout_seconds=TIMEOUT_SECONDS, raw_files=['stdout.bin', 'stderr.bin'],
                raw_is_unredacted=True, raw_requires_private_storage=True,
                stage_b_enabled=False, real_transport='not wired; separate authorization required')
