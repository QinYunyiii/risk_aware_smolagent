"""Offline-only metadata integration, including the actual serialized producer."""
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from risk_agent.collection import ssh_evidence_transport as ssh
from risk_agent.collection import tls_evidence_capture as cap
from risk_agent.collection import remote_local_tls_isolation as tls
from test_ssh_evidence_transport import Clock, MockSSH, wire

RUN = 'metadata_case'
SESSION = 'metadata-session'


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail('Metadata test attempted external network access')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny)
    monkeypatch.setattr(socket, 'create_connection', deny)
    popen = subprocess.Popen
    def local_python_only(argv, *args, **kwargs):
        assert Path(argv[0]).name not in ('ssh', 'openssl', 'curl', 'wget', 'docker', 'nvidia-smi')
        return popen(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', local_python_only)


def metadata(run=RUN, session=SESSION):
    return dict(run_id=run, session_id=session, worker_id='worker-0',
        remote_container_id='36f825bcc5da', remote_hostname='36f825bcc5da', remote_user='root',
        endpoint='127.0.0.1:8000', openssl_version='OpenSSL 3.0.2 15 Mar 2022',
        openssl_executable='/usr/bin/openssl', ca_path=tls.CA_PATH, ca_sha256=tls.CA_SHA256,
        tls_parameter_fingerprint=hashlib.sha256(b'SYNTHETIC_TLS_PARAMETERS_NOT_EXECUTED').hexdigest(),
        connect_timeout=15, handshake_timeout=5,
        preflight_timestamp='2026-11-25T07:00:00+00:00', preflight_status='PASS')


def producer_wire(record=None, stdout=ssh.BYTE_PROBE_STDOUT, stderr=ssh.BYTE_PROBE_STDERR):
    record = record or metadata()
    clock = Clock()
    process = MockSSH(clock, stdout, stderr, code=23)
    frames = []
    def factory():
        assert frames and json.loads(frames[0][len(ssh.PREFIX):])['remote_preflight'] == record
        return process
    result = ssh.produce_session(None, record['run_id'], record['session_id'], frames.append,
                                 clock=clock, preflight_metadata=record, process_factory=factory)
    assert result['reaped'] and result['returncode'] == 23
    return b''.join(frames)


def capture(base, *, record=None, raw=None, expected=None, run=RUN, session=SESSION):
    record = record or metadata(run, session)
    raw = producer_wire(record) if raw is None else raw
    expected = record if expected is None else expected
    clock = Clock()
    child = MockSSH(clock, raw, code=23)
    calls = []
    def factory(argv):
        calls.append(argv)
        log = Path(argv[argv.index('-E')+1])
        assert (log.parent/'preflight_expectation.json').is_file()
        assert (log.parent/'remote_preflight.json').read_bytes() == b''
        log.write_bytes(b'FAKE SSH CLIENT ONLY\r\n')
        return child
    result = ssh.capture_ssh_transport(base, run, nonce=session, clock=clock,
        process_factory=factory, preflight_metadata=expected, require_preflight_metadata=True)
    return base/run, child, calls, result


def test_normalization_and_producer_ordering_without_payload_changes():
    record = metadata()
    before = copy.deepcopy(record)
    canonical = ssh.preflight_metadata_bytes(record, RUN, SESSION)
    assert json.loads(canonical) == record == before
    assert canonical == ssh.preflight_metadata_bytes(dict(reversed(list(record.items()))), RUN, SESSION)
    raw = producer_wire(record)
    decoder = ssh.FrameDecoder(RUN, SESSION)
    payloads = {'stdout': b'', 'stderr': b''}
    def sink(frame, payload):
        if frame['kind'] == 'data': payloads[frame['stream']] += payload
    for offset in range(0, len(raw), 7): decoder.feed(raw[offset:offset+7], sink)
    decoder.finish()
    assert decoder.preflight_metadata == record
    assert payloads == {'stdout': ssh.BYTE_PROBE_STDOUT, 'stderr': ssh.BYTE_PROBE_STDERR}
    assert decoder.receipt['returncode'] == 23
    assert 'remote_preflight' not in decoder.receipt


def test_independent_persistence_hashes_and_classifier_input(tmp_path):
    root, child, calls, result = capture(tmp_path)
    assert len(calls) == 1 and child.closed and child.waited
    assert result['transport']['ssh_transport_status'] == 'SUCCESS'
    evidence = ssh.verified_remote_evidence(root)
    assert evidence['preflight_metadata'] == metadata()
    assert evidence['stdout'] == ssh.BYTE_PROBE_STDOUT
    assert evidence['stderr'] == ssh.BYTE_PROBE_STDERR
    assert (root/'remote_preflight.json').read_bytes() == ssh.preflight_metadata_bytes(metadata(), RUN, SESSION)+b'\n'
    assert (root/'ssh_client.log.raw').read_bytes() == b'FAKE SSH CLIENT ONLY\r\n'
    manifest = cap.verify_manifest(root)
    entry = next(a for a in manifest['artifacts'] if a['path'] == 'remote_preflight.json')
    assert entry['sha256'] == hashlib.sha256((root/'remote_preflight.json').read_bytes()).hexdigest()
    assert (root/'remote_preflight.json').stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('field', list(metadata()))
def test_each_required_field_missing_prevents_ssh_start(tmp_path, field):
    record = metadata(); del record[field]
    calls = []
    with pytest.raises(cap.EvidenceError):
        ssh.capture_ssh_transport(tmp_path, RUN, nonce=SESSION,
            process_factory=lambda argv: calls.append(argv), preflight_metadata=record)
    assert calls == [] and not (tmp_path/RUN).exists()


@pytest.mark.parametrize('field,value', [
    ('run_id', 'other_run'), ('session_id', 'other_session'), ('worker_id', 'worker-1'),
    ('endpoint', '127.0.0.1:8001'), ('remote_user', 'nobody'), ('remote_hostname', 'different'),
    ('ca_sha256', 'not-a-hash'), ('tls_parameter_fingerprint', 'f'*63),
    ('handshake_timeout', 6), ('connect_timeout', 30), ('connect_timeout', True),
    ('handshake_timeout', float('nan')), ('preflight_timestamp', '2026-11-25T00:00:00'),
    ('preflight_status', 'UNKNOWN'), ('openssl_version', 'OpenSSL 1.1.1'),
    ('openssl_executable', '/usr/bin/other'), ('ca_path', '/tmp/../private'),
    ('openssl_version', object()),
])
def test_invalid_or_unserializable_metadata_fails_before_factory(field, value):
    record = metadata(); record[field] = value
    calls = []
    with pytest.raises(cap.EvidenceError):
        ssh.produce_session(None, RUN, SESSION, lambda b: calls.append('emit'),
            preflight_metadata=record, process_factory=lambda: calls.append('launch'))
    assert calls == []


@pytest.mark.parametrize('field', ['api_key', 'password', 'private_key', 'certificate', 'extra'])
def test_unknown_or_sensitive_fields_rejected_without_values(field):
    record = metadata(); record[field] = 'SYNTHETIC_DO_NOT_PERSIST_VALUE'
    with pytest.raises(cap.EvidenceError) as exc:
        ssh.preflight_metadata_bytes(record, RUN, SESSION)
    assert record[field] not in str(exc.value)


def test_required_local_metadata_absent_starts_zero_ssh(tmp_path):
    calls = []
    with pytest.raises(cap.EvidenceError):
        ssh.capture_ssh_transport(tmp_path, RUN, nonce=SESSION,
            process_factory=lambda argv: calls.append(argv), require_preflight_metadata=True)
    assert calls == []


@pytest.mark.parametrize('point', ['create', 'write', 'fsync', 'audit'])
def test_metadata_storage_preparation_failure_starts_zero_ssh(tmp_path, monkeypatch, point):
    calls = []
    if point == 'create':
        original = cap.EvidenceStore.create
        def fail(self, name):
            if name == 'remote_preflight.json': raise OSError('synthetic reserve failure')
            return original(self, name)
        monkeypatch.setattr(cap.EvidenceStore, 'create', fail)
    elif point in ('write', 'audit'):
        original = cap.EvidenceStore.record
        def fail(self, name, value):
            if (point == 'write' and name == 'preflight_expectation.json') or (
                    point == 'audit' and value.get('event') == 'ssh_transport_prepared'):
                raise OSError('synthetic write/audit failure')
            return original(self, name, value)
        monkeypatch.setattr(cap.EvidenceStore, 'record', fail)
    else:
        original = os.fsync
        def fail(fd):
            if os.readlink(f'/proc/self/fd/{fd}').endswith('remote_preflight.json'):
                raise OSError('synthetic metadata fsync failure')
            return original(fd)
        monkeypatch.setattr(os, 'fsync', fail)
    with pytest.raises(OSError):
        ssh.capture_ssh_transport(tmp_path, RUN, nonce=SESSION,
            process_factory=lambda argv: calls.append(argv), preflight_metadata=metadata())
    assert calls == []


def test_emit_failure_happens_before_supervised_task_launch():
    calls = []
    def broken(frame): raise BrokenPipeError('synthetic wire closed')
    with pytest.raises(BrokenPipeError):
        ssh.produce_session(None, RUN, SESSION, broken, preflight_metadata=metadata(),
                            process_factory=lambda: calls.append('task'))
    assert calls == []


def test_failed_local_expectation_starts_zero_ssh(tmp_path):
    record = metadata(); record['preflight_status'] = 'FAIL';calls = []
    with pytest.raises(cap.EvidenceError):
        ssh.capture_ssh_transport(tmp_path, RUN, nonce=SESSION, preflight_metadata=record,
                                  process_factory=lambda argv: calls.append(argv))
    assert calls == [] and not (tmp_path/RUN).exists()


def test_failed_preflight_emitted_but_never_starts_task():
    record = metadata(); record['preflight_status'] = 'FAIL'
    frames = []; calls = []
    with pytest.raises(cap.EvidenceError):
        ssh.produce_session(None, RUN, SESSION, frames.append, preflight_metadata=record,
                            process_factory=lambda: calls.append('task'))
    assert calls == [] and len(frames) == 1
    decoder = ssh.FrameDecoder(RUN, SESSION);decoder.feed(frames[0], lambda *args: None)
    assert decoder.preflight_metadata['preflight_status'] == 'FAIL'
    assert decoder.receipt is None


@pytest.mark.parametrize('damage', ['hash', 'absent', 'worker', 'run', 'session', 'expectation', 'failed'])
def test_bad_remote_metadata_never_classifies_success(tmp_path, damage):
    lines = producer_wire().splitlines(keepends=True)
    first = json.loads(lines[0][len(ssh.PREFIX):])
    if damage == 'hash': first['remote_preflight_sha256'] = '0'*64
    elif damage == 'absent':
        del first['remote_preflight'];del first['remote_preflight_sha256']
    else:
        field, value = {
            'worker': ('worker_id', 'worker-1'), 'run': ('run_id', 'other'),
            'session': ('session_id', 'other'), 'expectation': ('ca_sha256', '0'*64),
            'failed': ('preflight_status', 'FAIL')}[damage]
        first['remote_preflight'][field] = value
        data = json.dumps(first['remote_preflight'], sort_keys=True, separators=(',', ':')).encode()
        first['remote_preflight_sha256'] = hashlib.sha256(data).hexdigest()
    lines[0] = ssh.PREFIX+json.dumps(first).encode()+b'\n'
    with pytest.raises(cap.EvidenceError):capture(tmp_path, raw=b''.join(lines))
    root = tmp_path/RUN
    assert not json.loads((root/'process_result.json').read_bytes())['capture_complete']
    with pytest.raises(cap.EvidenceError):ssh.verified_remote_evidence(root)
    if damage in ('expectation', 'failed'):
        assert json.loads((root/'remote_preflight.json').read_bytes()) == first['remote_preflight']


def test_runtime_metadata_write_failure_reaps_owned_ssh(tmp_path, monkeypatch):
    original = cap.EvidenceStore.write
    children = []
    def fail(self, name, data):
        if name == 'remote_preflight.json': raise OSError('synthetic receive-time disk failure')
        return original(self, name, data)
    monkeypatch.setattr(cap.EvidenceStore, 'write', fail)
    clock = Clock();child = MockSSH(clock, producer_wire());children.append(child)
    with pytest.raises(cap.EvidenceError):
        ssh.capture_ssh_transport(tmp_path, RUN, nonce=SESSION, clock=clock,
            preflight_metadata=metadata(), process_factory=lambda argv: child)
    assert child.terminated == 1 and child.waited and child.closed
    assert not json.loads((tmp_path/RUN/'process_result.json').read_bytes())['capture_complete']


def test_metadata_tamper_detected_after_capture(tmp_path):
    root, _, _, _ = capture(tmp_path)
    (root/'remote_preflight.json').write_bytes(b'{}\n')
    with pytest.raises(cap.EvidenceError):ssh.verified_remote_evidence(root)


@pytest.mark.parametrize('missing', ['preflight_expectation.json', 'remote_preflight.json'])
def test_metadata_must_be_listed_in_manifest_not_only_present_on_disk(tmp_path, missing):
    root, _, _, _ = capture(tmp_path)
    path = root/'artifact_manifest.json'
    manifest = json.loads(path.read_bytes())
    manifest['artifacts'] = [a for a in manifest['artifacts'] if a['path'] != missing]
    path.write_text(json.dumps(manifest))
    cap.verify_manifest(root)
    with pytest.raises(cap.EvidenceError, match='missing from Manifest'):
        ssh.verified_remote_evidence(root)


def test_observed_metadata_survives_missing_exit_receipt(tmp_path):
    lines = producer_wire().splitlines(keepends=True)
    with pytest.raises(cap.EvidenceError):capture(tmp_path, raw=b''.join(lines[:-1]))
    root = tmp_path/RUN
    assert json.loads((root/'remote_preflight.json').read_bytes()) == metadata()
    cap.verify_manifest(root)
    assert not json.loads((root/'transport_status.json').read_bytes())['remote_exit_status_observed']
    with pytest.raises(cap.EvidenceError):ssh.verified_remote_evidence(root)


def test_independent_metadata_is_checked_against_wire_not_just_updated_manifest(tmp_path):
    root, _, _, _ = capture(tmp_path)
    record = metadata(); record['preflight_timestamp'] = '2026-11-25T08:00:00+00:00'
    path = root/'remote_preflight.json'
    path.write_bytes(ssh.preflight_metadata_bytes(record, RUN, SESSION)+b'\n')
    manifest = json.loads((root/'artifact_manifest.json').read_bytes())
    entry = next(a for a in manifest['artifacts'] if a['path'] == path.name)
    entry.update(sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size_bytes=path.stat().st_size)
    (root/'artifact_manifest.json').write_text(json.dumps(manifest))
    cap.verify_manifest(root)
    with pytest.raises(cap.EvidenceError, match='original Wire'):
        ssh.verified_preflight_metadata(root)


def test_receive_time_metadata_fsync_failure_reaps_ssh_and_never_publishes_manifest(tmp_path, monkeypatch):
    original = os.fsync
    def fail(fd):
        if (os.readlink(f'/proc/self/fd/{fd}').endswith('remote_preflight.json')
                and os.fstat(fd).st_size):
            raise OSError('synthetic receive-time fsync failure')
        return original(fd)
    monkeypatch.setattr(os, 'fsync', fail)
    clock = Clock(); child = MockSSH(clock, producer_wire())
    with pytest.raises(cap.EvidenceError):
        ssh.capture_ssh_transport(tmp_path, RUN, nonce=SESSION, clock=clock,
            preflight_metadata=metadata(), process_factory=lambda argv: child)
    assert child.terminated == 1 and child.waited and child.closed
    assert not (tmp_path/RUN/'artifact_manifest.json').exists()


@pytest.mark.parametrize('lines', [
    [('stderr', 'CONNECTION ESTABLISHED'), ('stderr', 'Protocol version: TLSv1.3'),
     ('stderr', 'Ciphersuite: TLS_AES_256_GCM_SHA384'), ('stderr', 'Verification: OK')],
    [('stderr', 'Verification: OK')],
    [('stdout', '<<< TLS 1.3, Alert [length 0002], fatal handshake_failure')],
])
def test_frozen_classifier_receives_identical_streams_and_outcomes(tmp_path, lines):
    from test_tls_evidence_classifier import classify
    stdout = b''.join((text+'\r\n').encode() for stream, text in lines if stream == 'stdout')
    stderr = b''.join((text+'\r\n').encode() for stream, text in lines if stream == 'stderr')
    root, _, _, _ = capture(tmp_path, raw=producer_wire(metadata(), stdout, stderr))
    evidence = ssh.verified_remote_evidence(root)
    replay = [(stream, line) for stream in ('stdout', 'stderr')
              for line in evidence[stream].decode('ascii').splitlines()]
    direct = [(stream, line) for stream, data in [('stdout', stdout), ('stderr', stderr)]
              for line in data.decode('ascii').splitlines()]
    # The deliberate command exit 23 must also remain unchanged at classification.
    before, _ = classify(direct, code=23)
    after, _ = classify(replay, code=evidence['transport']['remote_exit_status'])
    assert before == after


def test_separate_sessions_and_concurrent_sessions_do_not_mix(tmp_path):
    def execute(i):
        run = f'metadata_{i}';session = f'session_{i}'
        root, _, _, _ = capture(tmp_path, run=run, session=session)
        return ssh.verified_remote_evidence(root)['preflight_metadata']
    with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(execute, [0, 1]))
    assert results == [metadata('metadata_0', 'session_0'), metadata('metadata_1', 'session_1')]


def test_legacy_v1_stays_accepted_without_metadata():
    raw = wire(b'\x00\xff', b'\x80', 23, RUN, SESSION)
    decoder = ssh.FrameDecoder(RUN, SESSION);decoder.feed(raw, lambda *args: None);decoder.finish()
    assert decoder.preflight_metadata is None and decoder.receipt['returncode'] == 23


def test_real_local_fake_ssh_uses_serialized_session_producer_end_to_end(tmp_path):
    record = metadata();payload = (
        'import sys;'
        f'sys.stdout.buffer.write({ssh.BYTE_PROBE_STDOUT!r});sys.stdout.buffer.flush();'
        f'sys.stderr.buffer.write({ssh.BYTE_PROBE_STDERR!r});sys.stderr.buffer.flush();'
        'raise SystemExit(23)')
    source = ssh.remote_wrapper_source([sys.executable, '-B', '-c', payload], RUN, SESSION)
    source += (f'\nresult=authorized_launch_after_preflight({record!r})\n'
               'raise SystemExit(result["returncode"])\n')
    script = tmp_path/'fake_ssh.py'
    script.write_text('import sys\n'
        'with open(sys.argv[sys.argv.index("-E")+1],"ab") as f:f.write(b"LOCAL_FAKE_CLIENT_ONLY\\n")\n'
        f'exec(compile({source!r},"serialized_producer","exec"),{{"__name__":"__main__"}})\n')
    children = []
    def factory(argv):
        child = subprocess.Popen([sys.executable, '-B', str(script), *argv[1:]],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            shell=False, start_new_session=True, env={'PATH': os.environ.get('PATH', '')})
        children.append(child)
        return cap.PopenPipeProcess(child)
    result = ssh.capture_ssh_transport(tmp_path, RUN, nonce=SESSION,
        process_factory=factory, preflight_metadata=record, require_preflight_metadata=True)
    assert len(children) == 1 and children[0].poll() == 23
    assert result['transport']['ssh_transport_status'] == 'SUCCESS'
    evidence = ssh.verified_remote_evidence(tmp_path/RUN)
    assert evidence['preflight_metadata'] == record
    assert evidence['stdout'] == ssh.BYTE_PROBE_STDOUT
    assert evidence['stderr'] == ssh.BYTE_PROBE_STDERR
    assert (tmp_path/RUN/'ssh_stderr_unclassified.raw').read_bytes() == b''
    assert (tmp_path/RUN/'ssh_client.log.raw').read_bytes() == b'LOCAL_FAKE_CLIENT_ONLY\n'
    decoder = ssh.FrameDecoder(RUN, SESSION)
    decoder.feed((tmp_path/RUN/'ssh_stdout_transport.raw').read_bytes(), lambda *args: None)
    decoder.finish()
    assert decoder.receipt['reaped'] and decoder.receipt['returncode'] == 23
    assert decoder.receipt['lifecycle']['pid'] > 0
    cap.verify_manifest(tmp_path/RUN)
