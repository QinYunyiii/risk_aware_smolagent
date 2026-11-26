"""Production CLI E2E with generated remote code and an in-memory Fake SSH.

No remote process, OpenSSL executable, TLS or socket operation is performed.
"""
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import shutil
from types import SimpleNamespace

import pytest

from scripts.run_p2_d2_tls_evidence_capture_stage_b import main
from risk_agent.collection import tls_stage_b as stage
from risk_agent.collection import ssh_evidence_transport as ssh
from risk_agent.collection import tls_evidence_capture as cap
from risk_agent.collection import remote_local_tls_isolation as tls
from test_tls_preflight_metadata import metadata as legacy_metadata

RUN = 'production_entry_fake'
SESSION = 'production-session'
STDOUT = b'>>> TLS 1.3, Handshake [length 0100], ClientHello\r\n<<< TLS 1.3, Handshake [length 0034], Finished\n'
STDERR = (b'CONNECTION ESTABLISHED\nProtocol version: TLSv1.3\n'
          b'Ciphersuite: TLS_AES_256_GCM_SHA384\nVerification: OK\n')
CA = b'PUBLIC_FAKE_CA_FOR_OFFLINE_REPLAY_ONLY'


def policy():
    return dict(worker_pid=382420, worker_process_identity='519951147',
        worker_command_identity='vllm-worker-0', worker_command_fingerprint=hashlib.sha256(b'SYNTHETIC_COMMAND').hexdigest(),
        worker_endpoint='127.0.0.1:8000', openssl_version='OpenSSL 3.0.2 15 Mar 2022')


class FakePreflight:
    def __init__(self, expected, failure=None): self.expected = expected;self.failure = failure
    def identity(self):
        return dict(hostname='bad-container' if self.failure == 'container' else '36f825bcc5da',
                    uid=0, ubuntu=True, version='22.04')
    def tools(self):
        return self.expected['openssl_executable'], self.expected['openssl_version'], '\n'.join(tls.REQUIRED_FLAGS)
    def ca_hash(self): return '0'*64 if self.failure == 'ca' else self.expected['ca_sha256']
    def worker(self):
        e = self.expected
        return dict(worker_pid=e['worker_pid']+1 if self.failure == 'worker' else e['worker_pid'],
            worker_process_identity=e['worker_process_identity'], worker_command_identity=e['worker_command_identity'],
            worker_command_fingerprint=e['worker_command_fingerprint'], worker_endpoint=e['worker_endpoint'], alive=True)


class PipeChild:
    """Anonymous byte pipes and a Mock wait receipt; not an executable process."""
    pid = 987654321
    text_mode = False
    def __init__(self, stdout, stderr, code):
        self.code = code
        for stream, data in [('stdout', stdout), ('stderr', stderr)]:
            read, write = os.pipe()
            os.write(write, data);os.close(write)
            setattr(self, stream, os.fdopen(read, 'rb'))
    def poll(self): return self.code
    def wait(self, timeout=None): return self.code
    def terminate(self): raise AssertionError('Completed Fake child should not be signalled')
    def kill(self): raise AssertionError('Completed Fake child should not be signalled')


class MemorySSH:
    pid = 987654322
    start_identity = 'offline-memory-ssh'
    executable_path = '/offline/fake-ssh-no-exec'
    def __init__(self, wire, code):
        self.parts = [wire[i:i+503] for i in range(0, len(wire), 503)]
        self.code = code;self.running = True;self.waited = False;self.closed = False;self.terminated = False
    def read(self, timeout):
        if self.parts: return [('stdout', self.parts.pop(0))]
        self.running = False
        return [('stdout', None), ('stderr', None)]
    def poll(self): return None if self.running else self.code
    def owns(self, identity): return identity == self.start_identity
    def wait(self, timeout):
        if self.running: raise subprocess.TimeoutExpired('offline-memory', timeout)
        self.waited = True;return self.code
    def terminate(self): self.terminated = True;self.running = False;self.code = -15
    def kill(self): raise AssertionError('Fake memory SSH requires no kill')
    def close(self): self.closed = True


class FakeSSH:
    def __init__(self, stdout=STDOUT, stderr=STDERR, code=0, failure=None, damage=None):
        self.stdout = stdout;self.stderr = stderr;self.code = code;self.failure = failure;self.damage = damage
        self.calls = 0;self.child_calls = 0;self.received = [];self.handles = []
    def __call__(self, argv, source):
        self.calls += 1
        assert self.calls == 1 and 'ConnectTimeout=15' in argv
        root = Path(argv[argv.index('-E')+1]).parent
        assert (root/'preflight_expectation.json').is_file() and (root/'artifact_manifest.pending').exists()
        assert (root/'remote_stdin_program.py').read_bytes() == source
        namespace = {'__name__': 'offline_generated_remote'}
        exec(compile(source, 'generated_production_remote', 'exec'), namespace)
        frames = []
        namespace['wire_emit'] = frames.append
        def popen(command, **kwargs):
            self.child_calls += 1
            assert self.child_calls == 1
            assert command == ['/usr/bin/openssl', *stage.frozen_tls_argv()[1:]]
            assert kwargs['stdin'] == subprocess.DEVNULL and not kwargs['shell']
            return PipeChild(self.stdout, self.stderr, self.code)
        namespace['subprocess'] = SimpleNamespace(Popen=popen, TimeoutExpired=subprocess.TimeoutExpired,
                                                DEVNULL=subprocess.DEVNULL, PIPE=subprocess.PIPE)
        code = namespace['remote_main'](FakePreflight(namespace['EXPECTED'], self.failure))
        wire = b''.join(frames)
        if self.damage == 'hash':
            lines = wire.splitlines(keepends=True);item = json.loads(lines[0][len(ssh.PREFIX):])
            item['remote_preflight_sha256'] = '0'*64
            lines[0] = ssh.PREFIX+json.dumps(item).encode()+b'\n';wire = b''.join(lines)
        if self.damage == 'missing_exit': wire = b''.join(wire.splitlines(keepends=True)[:-1])
        if self.damage == 'disconnect': wire = wire[:len(wire)//2];code = 255
        (root/'ssh_client.log.raw').write_bytes(b'OFFLINE_FAKE_SSH_CLIENT_ONLY\r\n')
        self.received = frames
        process = MemorySSH(wire, code);self.handles.append(process)
        return process


@pytest.fixture(autouse=True)
def forbid_network_processes_and_legacy(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('Production E2E must stay offline')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(tls, 'run_authorized', forbidden)
    monkeypatch.setattr(tls, 'bounded_process', forbidden)
    monkeypatch.setattr(tls, 'remote_program', forbidden)
    monkeypatch.setattr(stage, 'read_local_ca', lambda: CA)  # Verified-CA reader contract, synthetic input.


def invoke(base, fake, *, provider=None, evidence=None, run=RUN, session=SESSION):
    return main(['--mode', 'execute', '--run-id', run, '--session-id', session, '--output-base', str(base)],
        ssh_factory=fake, ownership=policy() if evidence is None else evidence, preflight_provider=provider)


def prepared(expected, failure=None):
    record = stage.remote_preflight(expected, FakePreflight(expected, failure))
    return ssh.preflight_start_fields(record, expected['run_id'], expected['session_id'])


def test_formal_cli_generated_program_full_e2e(tmp_path, monkeypatch):
    counts = {'transport': 0, 'producer': 0}
    original = ssh.capture_ssh_transport
    def watched(*args, **kwargs):
        counts['transport'] += 1
        assert kwargs['require_preflight_metadata']
        return original(*args, **kwargs)
    monkeypatch.setattr(ssh, 'capture_ssh_transport', watched)
    fake = FakeSSH()
    assert invoke(tmp_path, fake, provider=prepared) == 0
    assert counts['transport'] == fake.calls == fake.child_calls == 1
    assert fake.handles[0].closed and fake.handles[0].waited
    root = tmp_path/RUN
    metadata = ssh.verified_preflight_metadata(root)
    for key, value in policy().items(): assert metadata[key] == value
    assert metadata['ownership_verified'] is True and metadata['run_id'] == RUN and metadata['session_id'] == SESSION
    assert (root/'openssl_stdout.raw').read_bytes() == STDOUT
    assert (root/'openssl_stderr.raw').read_bytes() == STDERR
    assert (root/'ssh_process_stderr.raw').read_bytes() == b''
    assert b'worker_pid' not in (root/'ssh_client.log.raw').read_bytes()
    raw_exit = (root/'remote_exit_receipt.raw').read_bytes()
    assert raw_exit == fake.received[-1] and b'ownership_verified' not in raw_exit
    classification = json.loads((root/'tls_classification.json').read_bytes())
    assert classification['result']['category'] == 'REMOTE_TLS_SUCCESS'
    assert classification['tls_handshake_completed'] and classification['hostname_verified']
    assert json.loads((root/'gate_summary.json').read_bytes())['real_ssh_attempts'] == 0
    cap.verify_manifest(root)
    verify_all(root)


def verify_all(root):
    for name in ['artifact_manifest.json', 'artifact_manifest_post_analysis.json']:
        if not (root/name).exists(): continue
        manifest = json.loads((root/name).read_bytes())
        for a in manifest['artifacts']:
            p = root/a['path'];assert p.stat().st_size == a['size_bytes']
            assert hashlib.sha256(p.read_bytes()).hexdigest() == a['sha256']


@pytest.mark.parametrize('missing', ['worker_pid', 'worker_process_identity', 'worker_command_identity',
                                    'worker_command_fingerprint', 'worker_endpoint', 'openssl_version'])
def test_missing_policy_fails_before_ssh_and_saves_reason(tmp_path, missing):
    evidence = policy();del evidence[missing];fake = FakeSSH()
    assert invoke(tmp_path, fake, evidence=evidence) == 1
    assert fake.calls == fake.child_calls == 0
    assert json.loads((tmp_path/RUN/'failure_reason.json').read_bytes())['phase'] == 'PREPARE_METADATA'
    verify_all(tmp_path/RUN)


@pytest.mark.parametrize('damage', ['missing', 'pid', 'identity', 'owner_false', 'owner_unknown',
    'run', 'session', 'worker', 'hash', 'serialization', 'missing_metadata'])
def test_invalid_prepared_metadata_zero_ssh_zero_task(tmp_path, damage):
    def provider(expected):
        envelope = prepared(expected)
        record = envelope['remote_preflight']
        if damage == 'missing': del record['worker_pid']
        elif damage == 'pid': record['worker_pid'] = None
        elif damage == 'identity': record['worker_process_identity'] = '999'
        elif damage == 'owner_false': record['ownership_verified'] = False
        elif damage == 'owner_unknown': record['ownership_verified'] = None
        elif damage == 'run': record['run_id'] = 'other'
        elif damage == 'session': record['session_id'] = 'other'
        elif damage == 'worker': record['worker_id'] = 'worker-1'
        elif damage == 'hash': envelope['remote_preflight_sha256'] = '0'*64
        elif damage == 'serialization': record['worker_command_identity'] = object()
        elif damage == 'missing_metadata': return {}
        return envelope
    fake = FakeSSH();assert invoke(tmp_path, fake, provider=provider) == 1
    assert fake.calls == fake.child_calls == 0
    assert (tmp_path/RUN/'failure_reason.json').is_file()


@pytest.mark.parametrize('point', ['write', 'fsync', 'audit'])
def test_metadata_persistence_before_launch_is_fail_closed(tmp_path, monkeypatch, point):
    fake = FakeSSH()
    if point in ('write', 'audit'):
        original = cap.EvidenceStore.record
        def fail(self, name, value):
            if (point == 'write' and name == 'preflight_expectation.json') or (
                    point == 'audit' and value.get('event') == 'ssh_transport_prepared'):
                raise OSError('offline storage failure')
            return original(self, name, value)
        monkeypatch.setattr(cap.EvidenceStore, 'record', fail)
    else:
        original = os.fsync;fired = []
        def fail(fd):
            if not fired and os.readlink(f'/proc/self/fd/{fd}').endswith('remote_preflight.json'):
                fired.append(True);raise OSError('offline fsync failure')
            return original(fd)
        monkeypatch.setattr(os, 'fsync', fail)
    assert invoke(tmp_path, fake) == 1
    assert fake.calls == fake.child_calls == 0
    assert (tmp_path/RUN/'failure_reason.json').is_file()


@pytest.mark.parametrize('case', ['container', 'worker', 'ca'])
def test_prepared_preflight_rejection_zero_launch(tmp_path, case):
    fake = FakeSSH()
    assert invoke(tmp_path, fake, provider=lambda e: prepared(e, case)) == 1
    assert fake.calls == fake.child_calls == 0


@pytest.mark.parametrize('case', ['container', 'worker', 'ca'])
def test_failure_first_observed_inside_session_prohibits_tls_and_preserves_metadata(tmp_path, case):
    fake = FakeSSH(failure=case)
    assert invoke(tmp_path, fake) == 1
    assert fake.calls == 1 and fake.child_calls == 0
    record = json.loads((tmp_path/RUN/'remote_preflight.json').read_bytes())
    assert record['preflight_status'] == 'FAIL'
    assert record['preflight_error'] == {'container':'IDENTITY_REJECTED','worker':'WORKER_REJECTED','ca':'CA_REJECTED'}[case]
    assert json.loads((tmp_path/RUN/'gate_summary.json').read_bytes())['gate'] == 'TLS_EVIDENCE_CAPTURE_STAGE_B_BLOCKED'
    assert fake.handles[0].closed


@pytest.mark.parametrize('damage', ['hash', 'missing_exit', 'disconnect'])
def test_in_session_transport_failure_not_success_or_retry(tmp_path, damage):
    fake = FakeSSH(damage=damage)
    assert invoke(tmp_path, fake) == 1
    assert fake.calls == 1 and fake.handles[0].closed
    assert json.loads((tmp_path/RUN/'gate_summary.json').read_bytes())['gate'] == 'TLS_EVIDENCE_CAPTURE_STAGE_B_INCONCLUSIVE'


@pytest.mark.parametrize('code,stderr,expected', [
    (0, b'Verification: OK\n', 'DIAGNOSTIC_INCONCLUSIVE'),
    (1, b'verify error:num=62:hostname mismatch\n', 'REMOTE_CERTIFICATE_VERIFICATION_FAILURE'),
    (0, STDERR+b'SSL3 alert read:fatal:handshake failure\n', 'DIAGNOSTIC_INCONCLUSIVE'),
])
def test_classifier_conditions_unchanged_at_formal_entry(tmp_path, code, stderr, expected):
    fake = FakeSSH(code=code, stderr=stderr)
    assert invoke(tmp_path, fake) == 1
    result = json.loads((tmp_path/RUN/'tls_classification.json').read_bytes())
    assert result['result']['category'] == expected
    assert (tmp_path/RUN/'openssl_stderr.raw').read_bytes() == stderr


def test_legacy_record_and_new_record_both_roundtrip():
    old = legacy_metadata()
    assert json.loads(ssh.preflight_metadata_bytes(old, old['run_id'], old['session_id'])) == old
    new = stage.expected_metadata(policy(), RUN, SESSION)
    assert json.loads(ssh.preflight_metadata_bytes(new, RUN, SESSION)) == new


@pytest.mark.parametrize('state', [False, None])
def test_unverified_and_unknown_ownership_are_distinct_in_failed_records(state):
    record = stage.expected_metadata(policy(), RUN, SESSION)
    record.update(preflight_status='FAIL', ownership_verified=state, preflight_error='WORKER_REJECTED')
    assert json.loads(ssh.preflight_metadata_bytes(record, RUN, SESSION))['ownership_verified'] is state


def test_default_cli_and_unauthorized_execute_never_launch(tmp_path):
    assert main([]) == 0
    with pytest.raises(RuntimeError): main(['--mode','execute','--run-id','unauthorized'])
    assert not list(tmp_path.iterdir())


def test_existing_run_is_not_reused_or_modified(tmp_path):
    fake = FakeSSH();assert invoke(tmp_path, fake) == 0
    before = {p.name:p.read_bytes() for p in (tmp_path/RUN).iterdir() if p.is_file()}
    with pytest.raises(FileExistsError): invoke(tmp_path, fake)
    assert fake.calls == 1
    assert before == {p.name:p.read_bytes() for p in (tmp_path/RUN).iterdir() if p.is_file()}


def test_old_cli_execution_redirects_to_production_entry(monkeypatch):
    from scripts import run_p2_d2_remote_local_tls_isolation as old
    calls = []
    monkeypatch.setattr(old, 'production_main', lambda args: calls.append(args) or 7)
    assert old.main(['--mode','remote_tls_diagnostic','--authorize-stage-b','--run-id','redirect']) == 7
    assert calls == [['--mode','execute','--authorize-stage-b','--run-id','redirect']]


def test_worker_metadata_is_not_in_agent_or_checkpoint_payloads(tmp_path):
    fake = FakeSSH(stdout=b'\x00\xff\x80\r\n', stderr=b'\x00\xfe\n', code=23)
    assert invoke(tmp_path, fake) == 1
    root = tmp_path/RUN
    assert (root/'openssl_stdout.raw').read_bytes() == b'\x00\xff\x80\r\n'
    assert (root/'openssl_stderr.raw').read_bytes() == b'\x00\xfe\n'
    assert not any('checkpoint' in p.name or 'memory' in p.name for p in root.iterdir())
    assert json.loads((root/'exit_status_audit.json').read_bytes())['remote_exit_status'] == 23


def test_real_preflight_collector_with_offline_proc_and_tool_fixtures(monkeypatch):
    expected = stage.expected_metadata(policy(), RUN, SESSION)
    raw_command = b'/usr/bin/vllm\x00serve\x00--port\x008000\x00--api-key\x00SYNTHETIC_PRIVATE_ARGUMENT\x00'
    expected['worker_command_fingerprint'] = hashlib.sha256(raw_command).hexdigest()
    expected['ca_sha256'] = hashlib.sha256(CA).hexdigest()
    def read_text(path, *args, **kwargs):
        name = str(path)
        if name == '/etc/os-release': return 'ID=ubuntu\nVERSION_ID="22.04"\n'
        if name.endswith('/stat'): return '382420 (vllm) '+' '.join(['S']+['0']*18+['519951147'])
        if name == '/proc/net/tcp':
            return 'header\n 0: 0100007F:1F40 00000000:0000 0A 0000 00:0000 0000 0 0 123\n'
        raise AssertionError('Unexpected fixture read')
    def read_bytes(path):
        if str(path).endswith('/cmdline'): return raw_command
        if str(path) == tls.CA_PATH: return CA
        raise AssertionError('Unexpected fixture bytes read')
    monkeypatch.setattr(Path, 'read_text', read_text)
    monkeypatch.setattr(Path, 'read_bytes', read_bytes)
    monkeypatch.setattr(Path, 'iterdir', lambda p: iter([p/'7']))
    monkeypatch.setattr(os, 'readlink', lambda p: 'socket:[123]')
    monkeypatch.setattr(os, 'uname', lambda: SimpleNamespace(nodename='36f825bcc5da'))
    monkeypatch.setattr(os, 'getuid', lambda: 0)
    monkeypatch.setattr(shutil, 'which', lambda name: '/usr/bin/openssl')
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[-1] == 'version': return SimpleNamespace(stdout=expected['openssl_version'].encode())
        return SimpleNamespace(stdout=b'', stderr='\n'.join(tls.REQUIRED_FLAGS).encode())
    monkeypatch.setattr(subprocess, 'run', run)  # Metadata subprocess boundary, no executable started.
    record = stage.remote_preflight(expected)
    assert record['ownership_verified'] is True and record['preflight_status'] == 'PASS'
    assert record['worker_process_identity'] == '519951147'
    assert b'SYNTHETIC_PRIVATE_ARGUMENT' not in json.dumps(record).encode()
    assert calls == [['/usr/bin/openssl','version'], ['/usr/bin/openssl','s_client','-help']]


def test_historical_worker_policy_loader_verifies_manifest(tmp_path):
    owner = dict(pid=382420, process_start_ticks='519951147', command_sha256=policy()['worker_command_fingerprint'],
                 running=True, vllm_command_observed=True, port_argument_matches=True)
    records = [dict(remote_event=dict(event='worker0_service_preflight', snapshot=dict(passed=True, owners=[owner]))),
               dict(remote_event=dict(event='openssl_capability_checked', supported=True, version=policy()['openssl_version']))]
    blob = b''.join(json.dumps(record).encode()+b'\n' for record in records)
    path = tmp_path/'audit.jsonl';path.write_bytes(blob)
    (tmp_path/'artifact_manifest.json').write_text(json.dumps(dict(artifacts=[
        dict(path=path.name, sha256=hashlib.sha256(blob).hexdigest())])))
    loaded = stage.load_worker_evidence(path)
    assert loaded['worker_pid'] == 382420 and loaded['worker_command_fingerprint'] == owner['command_sha256']
    path.write_bytes(blob+b'{}\n')
    with pytest.raises(cap.EvidenceError): stage.load_worker_evidence(path)


def test_missing_ownership_evidence_file_is_audited_before_ssh(tmp_path):
    fake = FakeSSH()
    result = stage.run_stage_b(tmp_path, RUN, tmp_path/'missing.jsonl', ssh_factory=fake)
    assert result['gate'] == 'TLS_EVIDENCE_CAPTURE_STAGE_B_BLOCKED' and fake.calls == 0
    assert json.loads((tmp_path/RUN/'failure_reason.json').read_bytes())['reason'] == 'OWNERSHIP_POLICY_INVALID'


def test_production_stdin_adapter_delivers_binary_source_without_process_execution():
    child = PipeChild(b'', b'', 0)
    read, write = os.pipe();child.stdin = os.fdopen(write, 'wb', buffering=0)
    received = []
    def receive():
        with os.fdopen(read, 'rb') as handle: received.append(handle.read())
    reader = threading.Thread(target=receive);reader.start()
    source = b'SYNTHETIC_STDIN\x00\xff'*2000
    adapter = stage.SourceProcess(child, source)
    adapter.close();reader.join(timeout=2)
    assert received == [source] and not reader.is_alive() and not adapter.sender.is_alive()
    assert adapter.delivery_error is None


def test_prepared_hash_failure_records_specific_safe_reason(tmp_path):
    def provider(expected):
        value = prepared(expected);value['remote_preflight_sha256'] = '0'*64;return value
    fake = FakeSSH();assert invoke(tmp_path, fake, provider=provider) == 1
    assert json.loads((tmp_path/RUN/'failure_reason.json').read_bytes())['reason'] == 'METADATA_HASH_MISMATCH'


def test_later_certificate_error_prevents_verified_wrapper_flags(tmp_path):
    fake = FakeSSH(stderr=STDERR+b'verify error:num=62:hostname mismatch\n', code=1)
    assert invoke(tmp_path, fake) == 1
    result = json.loads((tmp_path/RUN/'tls_classification.json').read_bytes())
    assert result['certificate_chain_verified'] is None and result['hostname_verified'] is None
