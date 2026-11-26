from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from risk_agent.collection import tls_evidence_capture as capture
from scripts.test_p2_d2_tls_evidence_capture import main


ARGV = ['/usr/bin/openssl', 's_client', '-connect', '127.0.0.1:8000',
        '-servername', 'localhost', '-verify_hostname', 'localhost',
        '-verify_return_error', '-CAfile', '/root/.config/risk-agent/tls/ca.crt',
        '-brief', '-state', '-msg']


@pytest.fixture(autouse=True)
def no_process_or_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Stage A attempted a real process or network connection')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)


class FakeClock(capture.Clock):
    value = 100.0
    def monotonic(self):
        return self.value
    def utc(self):
        return f'fixture-utc-{self.value:.6f}'


class MockProcess:
    pid = 12345
    start_identity = 'fixture-owned-start'
    executable_path = '/usr/bin/openssl'
    def __init__(self, clock, events=(), *, code=0, mode='normal', termination='success', owned=True):
        self.clock = clock
        self.events = list(events)
        self.code, self.mode, self.termination, self.owned = code, mode, termination, owned
        self.running = True
        self.waits = self.terminates = self.kills = 0
        self.closed = False
    def read(self, timeout):
        self.clock.value += max(timeout, .001)
        if self.events:
            event = self.events.pop(0)
            if isinstance(event, Exception):
                raise event
            return [event]
        if self.mode == 'normal' or not self.running:
            self.running = False
            return [('stdout', None), ('stderr', None)]
        return []
    def poll(self):
        return None if self.running else self.code
    def owns(self, identity):
        return self.owned and identity == self.start_identity
    def terminate(self):
        self.terminates += 1
        if self.termination == 'error':
            raise OSError('synthetic termination failure')
        if self.termination == 'success':
            self.running, self.code = False, -15
    def kill(self):
        self.kills += 1
        self.running, self.code = False, -9
    def wait(self, timeout):
        self.waits += 1
        if self.running:
            self.clock.value += min(timeout, .02)
            raise subprocess.TimeoutExpired('fixture only', timeout)
        return self.code
    def close(self):
        self.closed = True


def run_mock(tmp_path, events=(), *, run_id='case', limit=8*1024*1024, **settings):
    clock = FakeClock()
    process = MockProcess(clock, events, **settings)
    calls = []
    def factory(argv):
        # These have already been created and fsynced before command submission.
        directory = tmp_path / run_id
        assert all((directory / name).exists() for name in (
            'stdout.bin', 'stderr.bin', 'lifecycle.jsonl', 'process_result.json',
            'combined_observed_timeline.jsonl'))
        assert json.loads((directory / 'lifecycle.jsonl').read_text().splitlines()[0])['event'] == 'capture_prepared'
        assert argv == ARGV
        calls.append(argv)
        return process
    result = capture.capture_process(tmp_path, run_id, ARGV, process_factory=factory,
                                     max_output_bytes=limit, clock=clock)
    assert len(calls) == 1 and process.waits >= 1 and process.closed
    return tmp_path / run_id, process, result


@pytest.mark.parametrize('stdout,stderr', [
    (b'normal stdout\n', b''), (b'', b'normal stderr\n'),
    (b'one\r\n\r\nTWO\n', b'errors\r\n\n'), (b'', b''),
    (bytes(range(256)), b'\xff\xfe\x00\r\n'),
])
def test_exact_raw_bytes_empty_streams_sizes_hashes_and_modes(tmp_path, stdout, stderr):
    root, _, result = run_mock(tmp_path, [('stdout', stdout), ('stderr', stderr)])
    assert result['status'] == 'CAPTURE_COMPLETE'
    manifest = capture.verify_manifest(root)
    assert (root / 'stdout.bin').read_bytes() == stdout
    assert (root / 'stderr.bin').read_bytes() == stderr
    for name, data in (('stdout.bin', stdout), ('stderr.bin', stderr)):
        item = next(a for a in manifest['artifacts'] if a['path'] == name)
        assert item['sha256'] == hashlib.sha256(data).hexdigest()
        assert item['size_bytes'] == len(data)
        assert (root / name).stat().st_mode & 0o777 == 0o600
    assert root.stat().st_mode & 0o777 == 0o700


def test_multibyte_utf8_across_chunks_and_observed_timeline(tmp_path):
    data = '证据\r\n\n'.encode()
    events = [('stdout', data[:1]), ('stderr', b'early?'), ('stdout', data[1:4]),
              ('stderr', b'\x80\x00'), ('stdout', data[4:])]
    root, _, _ = run_mock(tmp_path, events)
    assert (root / 'stdout.bin').read_bytes() == data
    timeline = [json.loads(line) for line in (root / 'combined_observed_timeline.jsonl').read_text().splitlines()]
    assert [e['sequence'] for e in timeline] == list(range(1, 6))
    assert [e['stream'] for e in timeline] == [e[0] for e in events]
    assert all('not child write order' in e['ordering'] for e in timeline)
    assert all('utc' in e and 'monotonic_seconds' in e for e in timeline)
    assert [e['monotonic_seconds'] for e in timeline] == sorted(e['monotonic_seconds'] for e in timeline)
    assert timeline[-1]['cumulative_total_bytes'] == len(data) + 8
    capture.verify_manifest(root)


def test_large_chunked_output_no_truncation(tmp_path):
    chunks = [bytes([i % 256]) * 65536 for i in range(20)]
    root, _, result = run_mock(tmp_path, [('stdout', c) for c in chunks])
    assert result['process']['stdout_bytes'] == 20 * 65536
    assert (root / 'stdout.bin').read_bytes() == b''.join(chunks)


@pytest.mark.parametrize('code', [0, 7, -11])
def test_lifecycle_exit_status_and_signals_are_not_tls_classification(tmp_path, code):
    root, process, result = run_mock(tmp_path, code=code)
    record = result['process']
    assert record['returncode'] == code and record['reaped']
    assert record['termination_signal'] == (11 if code == -11 else None)
    assert not record['terminate_called'] and not record['kill_called']
    assert record['argv'] == ARGV and record['pid'] == process.pid
    assert record['process_start_identity'] == process.start_identity
    assert record['executable_path'] == '/usr/bin/openssl'
    assert record['ended_monotonic_seconds'] >= record['started_monotonic_seconds']
    assert 'started_at_utc' in record and 'ended_at_utc' in record
    assert 'category' not in record  # Capture completeness never invents TLS success.
    capture.verify_manifest(root)


@pytest.mark.parametrize('termination,killed', [('success', False), ('sticky', True), ('error', True)])
def test_timeout_persists_partial_output_then_terminate_kill_and_reap(tmp_path, termination, killed):
    root, process, result = run_mock(tmp_path, [('stdout', b'partial\xff')],
                                     mode='hang', termination=termination)
    record = result['process']
    assert record['timed_out'] and record['timeout_seconds'] == 5
    assert record['terminate_called'] and record['kill_called'] == killed
    assert record['reaped'] and process.waits >= 1 and not process.running
    assert (root / 'stdout.bin').read_bytes() == b'partial\xff'
    assert record['returncode'] == (-9 if killed else -15)
    capture.verify_manifest(root)


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
def test_stream_read_failure_preserves_prior_bytes_and_reaps(tmp_path, stream):
    clock = FakeClock()
    process = MockProcess(clock, [(stream, b'already persisted'), OSError(f'{stream} synthetic read failure')])
    with pytest.raises(capture.EvidenceError):
        capture.capture_process(tmp_path, 'readfail', ARGV, process_factory=lambda a:process, clock=clock)
    root = tmp_path / 'readfail'
    assert (root / (stream+'.bin')).read_bytes() == b'already persisted'
    record = json.loads((root / 'process_result.json').read_text())
    assert not record['capture_complete'] and record['reaped']
    assert process.terminates == 1 and process.closed


@pytest.mark.parametrize('failure', ['create', 'fsync', 'prepared_event'])
def test_critical_audit_failure_before_launch_prevents_factory(tmp_path, monkeypatch, failure):
    calls = []
    if failure == 'create':
        monkeypatch.setattr(capture.EvidenceStore, 'create', lambda *a:(_ for _ in ()).throw(OSError('disk full')))
    elif failure == 'fsync':
        monkeypatch.setattr(os, 'fsync', lambda *a:(_ for _ in ()).throw(OSError('fsync failed')))
    else:
        original = capture.EvidenceStore.event
        def fail(self, event, **kwargs):
            if event == 'capture_prepared':raise OSError('prepared audit failed')
            return original(self, event, **kwargs)
        monkeypatch.setattr(capture.EvidenceStore, 'event', fail)
    with pytest.raises((OSError, capture.EvidenceError)):
        capture.capture_process(tmp_path, 'no_launch', ARGV, process_factory=lambda a:calls.append(a))
    assert calls == []


@pytest.mark.parametrize('failure', ['write', 'fsync', 'timeline'])
def test_mid_capture_io_failure_stops_owned_child_and_retains_partial(tmp_path, monkeypatch, failure):
    clock = FakeClock()
    process = MockProcess(clock, [('stdout', b'first'), ('stdout', b'second')])
    original = capture.EvidenceStore.write
    count = 0
    def fail(self, name, data):
        nonlocal count
        if name == 'stdout.bin':
            count += 1
            if count == 2:
                if failure == 'write':raise OSError('synthetic write failed')
                if failure == 'fsync':
                    os.write(self.fds[name], data)
                    raise OSError('synthetic raw fsync failed')
        if failure == 'timeline' and name == 'combined_observed_timeline.jsonl' and count == 2:
            raise OSError('synthetic audit write failed')
        return original(self,name,data)
    monkeypatch.setattr(capture.EvidenceStore,'write',fail)
    with pytest.raises(capture.EvidenceError):
        capture.capture_process(tmp_path,'mid',ARGV,process_factory=lambda a:process,clock=clock)
    assert process.terminates == 1 and not process.running and process.closed
    assert (tmp_path/'mid/stdout.bin').read_bytes().startswith(b'first')
    record=json.loads((tmp_path/'mid/process_result.json').read_text())
    assert not record['capture_complete'] and record['reaped']


def test_storage_limit_failure_is_explicit_and_retains_prefix(tmp_path):
    clock=FakeClock(); process=MockProcess(clock,[('stdout',b'1234'),('stderr',b'5678')])
    with pytest.raises(capture.EvidenceError):
        capture.capture_process(tmp_path,'limit',ARGV,process_factory=lambda a:process,clock=clock,max_output_bytes=6)
    root=tmp_path/'limit'
    assert (root/'stdout.bin').read_bytes()==b'1234'
    record=json.loads((root/'process_result.json').read_text())
    assert not record['capture_complete'] and record['stderr_bytes']==0
    assert any(e['type']=='StorageLimitExceeded' for e in record['errors'])
    assert process.waits and not process.running


def test_ownership_loss_refuses_signals_and_records_unreaped(tmp_path):
    clock=FakeClock(); process=MockProcess(clock,mode='hang',owned=False)
    with pytest.raises(capture.EvidenceError):
        capture.capture_process(tmp_path,'foreign',ARGV,process_factory=lambda a:process,clock=clock)
    record=json.loads((tmp_path/'foreign/process_result.json').read_text())
    assert process.terminates==process.kills==0
    assert not record['reaped'] and record['ownership_confirmed'] is False
    assert not record['capture_complete']


@pytest.mark.parametrize('run_id',['../escape','/absolute','a/b','..','a\\b'])
def test_path_traversal_prevents_launch(tmp_path,run_id):
    with pytest.raises(capture.EvidenceError):
        capture.capture_process(tmp_path,run_id,ARGV,process_factory=lambda a:pytest.fail('launched'))


def test_symlink_base_and_duplicate_run_refused(tmp_path):
    target=tmp_path/'target';target.mkdir();link=tmp_path/'link';link.symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        capture.capture_process(link,'run',ARGV,process_factory=lambda a:pytest.fail('launched'))
    run_mock(tmp_path,run_id='existing')
    before=(tmp_path/'existing/stdout.bin').read_bytes()
    with pytest.raises(FileExistsError):
        capture.capture_process(tmp_path,'existing',ARGV,process_factory=lambda a:pytest.fail('launched'))
    assert (tmp_path/'existing/stdout.bin').read_bytes()==before


def test_concurrent_unique_runs_are_isolated(tmp_path):
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run_mock,tmp_path,[('stdout',name.encode())],run_id=name) for name in ('run_a','run_b')]
        results=[f.result() for f in futures]
    assert {root.name for root,_,_ in results}=={'run_a','run_b'}
    assert (tmp_path/'run_a/stdout.bin').read_bytes()==b'run_a'
    assert (tmp_path/'run_b/stdout.bin').read_bytes()==b'run_b'


def test_manifest_tamper_blocks_use(tmp_path):
    root,_,_=run_mock(tmp_path,[('stdout',b'original')])
    (root/'stdout.bin').write_bytes(b'tampered')
    with pytest.raises(capture.EvidenceError,match='mismatch'):
        capture.verify_manifest(root)


def test_raw_is_unchanged_and_private_while_metadata_redacts_and_refuses_secrets(tmp_path):
    secret=b'Authorization: Bearer SYNTHETIC_ONLY_SECRET\r\nMaster-Key: fixture-only\n'
    root,_,_=run_mock(tmp_path,[('stdout',secret)])
    assert (root/'stdout.bin').read_bytes()==secret
    assert secret not in (root/'process_result.json').read_bytes()
    assert b'SYNTHETIC_ONLY_SECRET' not in (root/'combined_observed_timeline.jsonl').read_bytes()
    assert not capture.verify_manifest(root)['automatic_publish_allowed']
    for argv in (ARGV+['--api-key','fixture-only-secret'], ARGV+['Authorization: Bearer fixture'], ARGV+['-keylogfile','x']):
        with pytest.raises(capture.EvidenceError):
            capture.capture_process(tmp_path,'denied',argv,process_factory=lambda a:pytest.fail('secret command'))
    assert not (tmp_path/'denied').exists()


def test_no_default_factory_and_cli_cannot_enable_stage_b(tmp_path,monkeypatch,capsys):
    monkeypatch.chdir(tmp_path)
    assert main([])==0
    assert json.loads(capsys.readouterr().out)['stage_b_enabled'] is False
    assert not (tmp_path/'runs').exists()
    with pytest.raises(SystemExit):main(['--mode','remote_tls_diagnostic'])
    with pytest.raises(TypeError):capture.capture_process(tmp_path,'no_factory',ARGV)


def test_factory_failure_has_unknown_process_fields_not_fabricated_zero(tmp_path):
    def fail(argv):raise RuntimeError('Authorization: Bearer SYNTHETIC_ONLY_SECRET')
    with pytest.raises(capture.EvidenceError) as exc:
        capture.capture_process(tmp_path,'factory_failure',ARGV,process_factory=fail)
    assert 'SYNTHETIC_ONLY_SECRET' not in str(exc.value)
    record=json.loads((tmp_path/'factory_failure/process_result.json').read_text())
    assert record['pid'] is record['returncode'] is record['stdout_bytes'] is None
    assert b'SYNTHETIC_ONLY_SECRET' not in (tmp_path/'factory_failure/process_result.json').read_bytes()


def test_historical_fixture_is_unchanged_and_remains_inconclusive():
    from risk_agent.collection import remote_local_tls_isolation as isolation
    path=Path(__file__).parent/'fixtures/tls/remote_loopback_openssl_3_0_2_retained.json'
    original=path.read_bytes(); fixture=json.loads(original)
    class Fake:
        def identity(self):return dict(hostname='36f825bcc5da',uid=0)
        def tool_info(self):return fixture['version'],'\n'.join(isolation.REQUIRED_FLAGS)
        def read_ca(self):return b'public fixture'
        def probe(self,command,budget,callback):
            for line in fixture['lines']:callback(line['stream'],line['text'])
            return fixture['outcome']
    result=isolation.remote_entry(Fake(),lambda e:None,hashlib.sha256(b'public fixture').hexdigest())
    assert result['category']=='DIAGNOSTIC_INCONCLUSIVE'
    assert path.read_bytes()==original


def test_binary_pipe_adapter_reads_without_newline_or_text_conversion(tmp_path):
    out_r,out_w=os.pipe();err_r,err_w=os.pipe()
    os.write(out_w,b'\xff\r\n\n\xe8');os.close(out_w)
    os.write(err_w,b'\x80\x00raw');os.close(err_w)
    fake=SimpleNamespace(pid=987654321,text_mode=False,
                         stdout=os.fdopen(out_r,'rb'),stderr=os.fdopen(err_r,'rb'))
    process=capture.PopenPipeProcess(fake)
    data={'stdout':b'','stderr':b''};eof=set()
    try:
        while len(eof)<2:
            for stream,chunk in process.read(.1):
                if chunk is None:eof.add(stream)
                else:data[stream]+=chunk
        assert data=={'stdout':b'\xff\r\n\n\xe8','stderr':b'\x80\x00raw'}
        assert process.start_identity is None
        assert not process.owns(None)
    finally:process.close()


def test_text_mode_adapter_is_rejected():
    with pytest.raises(capture.EvidenceError,match='Binary'):
        capture.PopenPipeProcess(SimpleNamespace(text_mode=True))


def test_same_owned_identity_required_before_kill_after_terminate_timeout(tmp_path):
    clock=FakeClock();process=MockProcess(clock,mode='hang',termination='sticky')
    process.owns=lambda identity:process.terminates==0
    with pytest.raises(capture.EvidenceError):
        capture.capture_process(tmp_path,'changed_owner',ARGV,process_factory=lambda a:process,clock=clock)
    assert process.terminates==1 and process.kills==0
    result=json.loads((tmp_path/'changed_owner/process_result.json').read_text())
    assert not result['capture_complete'] and not result['reaped']


def test_raw_fsync_failure_during_cleanup_is_failed_even_if_child_reaped(tmp_path,monkeypatch):
    clock=FakeClock();process=MockProcess(clock,mode='hang')
    def fail(self):raise OSError('synthetic fsync failure')
    monkeypatch.setattr(capture.EvidenceStore,'sync_raw',fail)
    result=capture.capture_process(tmp_path,'sync_failure',ARGV,process_factory=lambda a:process,clock=clock)
    assert result['status']=='CAPTURE_FAILED'
    assert result['process']['reaped'] and result['process']['errors']
    assert process.terminates==1


def test_pending_manifest_is_not_published_after_failed_write(tmp_path,monkeypatch):
    clock=FakeClock();process=MockProcess(clock)
    original=capture.EvidenceStore.write
    def fail(self,name,data):
        if name=='artifact_manifest.pending':
            os.write(self.fds[name],b'{partial')
            raise OSError('synthetic manifest write failure')
        return original(self,name,data)
    monkeypatch.setattr(capture.EvidenceStore,'write',fail)
    with pytest.raises(capture.EvidenceError):
        capture.capture_process(tmp_path,'manifest_failure',ARGV,process_factory=lambda a:process,clock=clock)
    assert not (tmp_path/'manifest_failure/artifact_manifest.json').exists()
    assert (tmp_path/'manifest_failure/artifact_manifest.pending').read_bytes()==b'{partial'
    assert process.waits and not process.running


def test_fsync_precedes_timeout_signal(tmp_path,monkeypatch):
    clock=FakeClock();process=MockProcess(clock,[('stdout',b'preserve-before-stop')],mode='hang')
    syncs=[];original=capture.EvidenceStore.sync_raw
    def sync(self):
        original(self);syncs.append('durable')
    monkeypatch.setattr(capture.EvidenceStore,'sync_raw',sync)
    terminate=process.terminate
    def checked_terminate():
        assert syncs and (tmp_path/'durable/stdout.bin').read_bytes()==b'preserve-before-stop'
        terminate()
    process.terminate=checked_terminate
    result=capture.capture_process(tmp_path,'durable',ARGV,process_factory=lambda a:process,clock=clock)
    assert result['process']['timed_out'] and result['process']['reaped']


def test_watchdog_stops_owned_mock_child_even_during_slow_audit(tmp_path,monkeypatch):
    # Accelerated timer tests the watchdog path without waiting five real seconds;
    # production timeout constant and recorded budget remain five seconds.
    clock=FakeClock();process=MockProcess(clock)
    timer=threading.Timer
    monkeypatch.setattr(capture.threading,'Timer',lambda delay,callback:timer(.03,callback))
    original=capture.EvidenceStore.event
    def slow(self,event,**values):
        if event=='owned_process_received':
            time.sleep(.1)
            assert not process.running
        return original(self,event,**values)
    monkeypatch.setattr(capture.EvidenceStore,'event',slow)
    result=capture.capture_process(tmp_path,'watchdog',ARGV,process_factory=lambda a:process,clock=clock)
    assert result['process']['timed_out'] and result['process']['timeout_seconds']==5
    assert result['process']['reaped'] and process.terminates==1


def test_no_overwrite_on_manifest_collision(tmp_path):
    clock=FakeClock();process=MockProcess(clock)
    def factory(argv):
        (tmp_path/'collision/artifact_manifest.json').write_bytes(b'existing-owned-test-marker')
        return process
    with pytest.raises(capture.EvidenceError):
        capture.capture_process(tmp_path,'collision',ARGV,process_factory=factory,clock=clock)
    assert (tmp_path/'collision/artifact_manifest.json').read_bytes()==b'existing-owned-test-marker'


def test_manifest_rejects_path_traversal_and_symlink(tmp_path):
    root,_,_=run_mock(tmp_path)
    manifest=json.loads((root/'artifact_manifest.json').read_text())
    manifest['artifacts'][0]['path']='../escape'
    (root/'artifact_manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(capture.EvidenceError):capture.verify_manifest(root)


def test_base_traversal_is_rejected(tmp_path):
    with pytest.raises(capture.EvidenceError):
        capture.capture_process(tmp_path/'..'/'escape','run',ARGV,process_factory=lambda a:pytest.fail('launched'))


def test_short_raw_write_preserves_all_bytes(tmp_path,monkeypatch):
    original=os.write
    monkeypatch.setattr(os,'write',lambda fd,data:original(fd,data[:7]))
    root,_,_=run_mock(tmp_path,[('stdout',bytes(range(256)))])
    assert (root/'stdout.bin').read_bytes()==bytes(range(256))
    capture.verify_manifest(root)


def test_timeline_fsync_failure_is_fail_closed(tmp_path,monkeypatch):
    original=capture.EvidenceStore.record
    clock=FakeClock();process=MockProcess(clock,[('stderr',b'keep')])
    def fail(self,name,value):
        if name=='combined_observed_timeline.jsonl':raise OSError('timeline fsync failed')
        return original(self,name,value)
    monkeypatch.setattr(capture.EvidenceStore,'record',fail)
    with pytest.raises(capture.EvidenceError):
        capture.capture_process(tmp_path,'timeline_sync',ARGV,process_factory=lambda a:process,clock=clock)
    assert (tmp_path/'timeline_sync/stderr.bin').read_bytes()==b'keep'
    assert process.waits and not process.running


def test_descendant_pipe_without_eof_stops_drain_and_marks_failed(tmp_path):
    clock=FakeClock();process=MockProcess(clock,mode='hang')
    def never_eof(timeout):clock.value+=timeout;return []
    process.read=never_eof
    with pytest.raises(capture.EvidenceError):
        capture.capture_process(tmp_path,'no_eof',ARGV,process_factory=lambda a:process,clock=clock)
    record=json.loads((tmp_path/'no_eof/process_result.json').read_text())
    assert record['timed_out'] and record['reaped'] and not record['capture_complete']


def test_streams_closing_early_do_not_allow_unbounded_wait(tmp_path):
    clock=FakeClock();process=MockProcess(clock,mode='hang')
    def eof(timeout):clock.value+=timeout;return [('stdout',None),('stderr',None)]
    process.read=eof
    result=capture.capture_process(tmp_path,'eof_hang',ARGV,process_factory=lambda a:process,clock=clock)
    assert result['process']['timed_out'] and result['process']['reaped']


def test_metadata_storage_limit_is_explicit(tmp_path):
    clock=FakeClock();store=capture.EvidenceStore(tmp_path,'metadata_limit',clock,'test',1)
    try:
        with pytest.raises(capture.StorageLimitExceeded):
            store.write('lifecycle.jsonl',b'x'*65537)
    finally:store.close()


def test_downstream_verified_bytes_keep_outcome_and_refuse_incomplete(tmp_path):
    root,_,_=run_mock(tmp_path,[('stdout',b'\xffVerification: OK\r\n')],code=7)
    verified=capture.verified_raw_streams(root)
    assert verified['stdout']==b'\xffVerification: OK\r\n'
    assert verified['process']['returncode']==7
    clock=FakeClock();process=MockProcess(clock,[OSError('fixture failed read')])
    with pytest.raises(capture.EvidenceError):
        capture.capture_process(tmp_path,'incomplete',ARGV,process_factory=lambda a:process,clock=clock)
    capture.verify_manifest(tmp_path/'incomplete')  # Integrity is distinct from completeness.
    with pytest.raises(capture.EvidenceError,match='Incomplete'):
        capture.verified_raw_streams(tmp_path/'incomplete')


def test_receipt_audit_failure_stops_before_reading_and_reaps(tmp_path,monkeypatch):
    clock=FakeClock();process=MockProcess(clock)
    original=capture.EvidenceStore.event
    def fail(self,event,**values):
        if event=='owned_process_received':raise OSError('receipt audit failed')
        return original(self,event,**values)
    process.read=lambda timeout:pytest.fail('Continued capture after critical audit failure')
    monkeypatch.setattr(capture.EvidenceStore,'event',fail)
    with pytest.raises(capture.EvidenceError):
        capture.capture_process(tmp_path,'receipt_failure',ARGV,process_factory=lambda a:process,clock=clock)
    assert process.terminates==1 and process.waits and process.closed


def test_byte_write_makes_no_progress_is_failed(tmp_path,monkeypatch):
    original=os.write
    def no_progress(fd,data):
        return 0 if bytes(data)==b'RAW_FAIL' else original(fd,data)
    monkeypatch.setattr(os,'write',no_progress)
    clock=FakeClock();process=MockProcess(clock,[('stdout',b'RAW_FAIL')])
    with pytest.raises(capture.EvidenceError):
        capture.capture_process(tmp_path,'no_progress',ARGV,process_factory=lambda a:process,clock=clock)
    assert not process.running and process.waits
