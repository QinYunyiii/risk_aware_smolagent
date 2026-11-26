from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
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
from scripts.test_p2_d2_ssh_evidence_transport import main


NONCE = 'offline-session-nonce'
RUN = 'offline_case'


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail('Offline SSH adapter attempted a real network connection')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket.socket, 'connect_ex', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


class Clock(cap.Clock):
    def __init__(self):self.now=100.
    def monotonic(self):return self.now
    def utc(self):return 'fixture-utc'


class MockSSH:
    pid=987654321
    start_identity='owned-mock'
    executable_path='/fixture/fake-ssh'
    def __init__(self, clock, wire=b'', stderr=b'', *, code=0, hang=False, sticky=False):
        self.clock=clock;self.code=code;self.hang=hang;self.sticky=sticky
        self.chunks=[]
        for i in range(0,max(len(wire),len(stderr)),32768):
            if i<len(wire):self.chunks.append(('stdout',wire[i:i+32768]))
            if i<len(stderr):self.chunks.append(('stderr',stderr[i:i+32768]))
        self.running=True;self.waited=0;self.terminated=0;self.killed=0;self.closed=False
    def read(self,timeout):
        self.clock.now+=max(timeout,.01)
        if self.chunks:return [self.chunks.pop(0)]
        if self.hang and self.running:return []
        self.running=False
        return [('stdout',None),('stderr',None)]
    def poll(self):return None if self.running else self.code
    def owns(self,identity):return identity==self.start_identity
    def terminate(self):
        self.terminated+=1
        if not self.sticky:self.running=False;self.code=-15
    def kill(self):self.killed+=1;self.running=False;self.code=-9
    def wait(self,timeout):
        self.waited+=1
        if self.running:
            self.clock.now+=min(timeout,.02)
            raise subprocess.TimeoutExpired('fixture',timeout)
        return self.code
    def close(self):self.closed=True


def wire(stdout=b'',stderr=b'',code=0,run=RUN,nonce=NONCE):
    frames=[ssh.encode_frame(run,nonce,1,'start')]
    sequence=1
    for stream,data in [('stdout',stdout),('stderr',stderr)]:
        for offset in range(0,len(data),16384):
            sequence+=1
            frames.append(ssh.data_frame(run,nonce,sequence,stream,data[offset:offset+16384],offset))
    frames.append(ssh.exit_frame(run,nonce,sequence+1,returncode=code,
        stdout_bytes=len(stdout),stderr_bytes=len(stderr),stdout_sha256=hashlib.sha256(stdout).hexdigest(),
        stderr_sha256=hashlib.sha256(stderr).hexdigest()))
    return b''.join(frames)


def run_mock(tmp_path,stdout=b'out',stderr=b'err',*,ssh_code=0,remote_code=0,
             local_stderr=b'',debug=b'client\r\n\n',raw_wire=None,run=RUN,limit=8*1024*1024,**proc_settings):
    clock=Clock();child=MockSSH(clock,wire(stdout,stderr,remote_code,run) if raw_wire is None else raw_wire,
                             local_stderr,code=ssh_code,**proc_settings)
    launched=[]
    def factory(argv):
        launched.append(argv)
        path=Path(argv[argv.index('-E')+1])
        assert path.exists() and path.stat().st_mode&0o777==0o600
        assert path.parent.joinpath('artifact_manifest.pending').exists()
        with path.open('ab') as f:f.write(debug)
        return child
    result=ssh.capture_ssh_transport(tmp_path,run,process_factory=factory,nonce=NONCE,
                                     clock=clock,max_output_bytes=limit,local_timeout_seconds=40)
    assert len(launched)==1 and child.waited and child.closed
    return tmp_path/run,child,result


def test_argv_no_forward_or_shell_unique_debug_paths(tmp_path):
    a=ssh.ssh_argv(tmp_path/'a/ssh_client.log.raw');b=ssh.ssh_argv(tmp_path/'b/ssh_client.log.raw')
    assert a[a.index('-E')+1]!=b[b.index('-E')+1]
    assert a[-2:]==['risk-agent-remote','python3 -B -']
    assert all(flag in a for flag in ['BatchMode=yes','StrictHostKeyChecking=yes',
        'ClearAllForwardings=yes','PasswordAuthentication=no','IdentitiesOnly=yes','ControlMaster=no'])
    assert not set(('-L','-R','-D','-F')).intersection(a)
    assert a[a.index('-p')+1]=='4035'


@pytest.mark.parametrize('stdout,stderr',[(b'out',b'err'),(b'',b''),(b'',b'error'),(b'output',b''),
    (bytes(range(256)),b'\xff\xfe\r\n\n'),('跨块\r\n'.encode(),b'\x00binary')])
def test_three_raw_streams_exact_hash_and_manifest(tmp_path,stdout,stderr):
    debug=b'local\r\n\r\n\x80'
    root,_,result=run_mock(tmp_path,stdout,stderr,debug=debug,local_stderr=b'local bootstrap?\xff')
    manifest=cap.verify_manifest(root)
    for name,data in [('remote_stdout.raw',stdout),('remote_stderr.raw',stderr),('ssh_client.log.raw',debug),
                      ('ssh_stderr_unclassified.raw',b'local bootstrap?\xff')]:
        assert (root/name).read_bytes()==data
        entry=next(e for e in manifest['artifacts'] if e['path']==name)
        assert entry['sha256']==hashlib.sha256(data).hexdigest() and entry['size_bytes']==len(data)
    assert result['transport']['remote_exit_status']==0
    assert result['transport']['remote_exit_status_observed']
    assert 'UNCLASSIFIED' in result['transport']['local_stderr_provenance']
    events=[json.loads(x) for x in (root/'combined_observed_timeline.jsonl').read_text().splitlines()]
    assert {'stdout','stderr','debug','remote_stdout','remote_stderr'} >= {e['stream'] for e in events}
    assert all({'sequence','monotonic_ns','utc_timestamp','stream','byte_count','cumulative_bytes','chunk_sha256'}<=e.keys() for e in events)


def test_large_interleaved_output_and_complete_receipt(tmp_path):
    stdout=b'x\xff'*500000;stderr=b'err\x00'*200000
    root,_,_=run_mock(tmp_path,stdout,stderr)
    assert (root/'remote_stdout.raw').read_bytes()==stdout
    assert (root/'remote_stderr.raw').read_bytes()==stderr


@pytest.mark.parametrize('remote_code,ssh_code,observed',[(7,0,True),(255,0,True),(255,255,True),(-9,0,True)])
def test_ssh_and_remote_exit_are_independent(tmp_path,remote_code,ssh_code,observed):
    root,_,result=run_mock(tmp_path,remote_code=remote_code,ssh_code=ssh_code)
    status=result['transport']
    assert status['ssh_process_return_code']==ssh_code
    assert status['remote_exit_status']==remote_code and status['remote_exit_status_observed']==observed
    if ssh_code==255:assert status['ssh_transport_status']=='SSH_255_AMBIGUOUS'
    if remote_code<0:assert status['remote_termination_status']=='SIGNALED'


@pytest.mark.parametrize('ssh_code',[0,255,1])
def test_missing_receipt_never_fills_remote_exit_zero(tmp_path,ssh_code):
    with pytest.raises(cap.EvidenceError):run_mock(tmp_path,raw_wire=b'',ssh_code=ssh_code,local_stderr=b'Permission denied?')
    root=tmp_path/RUN
    status=json.loads((root/'transport_status.json').read_text())
    assert status['ssh_process_return_code']==ssh_code
    assert status['remote_exit_status'] is None and not status['remote_exit_status_observed']
    assert status['remote_termination_status']=='UNKNOWN'
    cap.verify_manifest(root)


@pytest.mark.parametrize('sticky',[False,True])
def test_local_timeout_partial_bytes_preserved_and_remote_unknown(tmp_path,sticky):
    clock=Clock();raw=ssh.encode_frame(RUN,NONCE,1,'start')
    child=MockSSH(clock,raw,b'partial stderr\xff',hang=True,sticky=sticky)
    def factory(argv):return child
    with pytest.raises(cap.EvidenceError):
        ssh.capture_ssh_transport(tmp_path,RUN,nonce=NONCE,process_factory=factory,clock=clock,local_timeout_seconds=.15)
    root=tmp_path/RUN
    assert (root/'ssh_stdout_transport.raw').read_bytes()==raw
    assert (root/'ssh_stderr_unclassified.raw').read_bytes()==b'partial stderr\xff'
    result=json.loads((root/'process_result.json').read_text())
    assert result['timed_out'] and result['reaped'] and child.terminated==1
    assert bool(child.killed)==sticky
    status=json.loads((root/'transport_status.json').read_text())
    assert status['local_timeout'] and status['remote_termination_status']=='UNKNOWN'


@pytest.mark.parametrize('name',['ssh_client.log.raw','remote_stdout.raw','remote_stderr.raw',
                                'artifact_manifest.pending','transport_status.json'])
def test_prelaunch_artifact_failure_starts_zero_ssh(tmp_path,monkeypatch,name):
    original=cap.EvidenceStore.create;calls=[]
    def failed(self,file):
        if file==name:raise OSError('synthetic persistence unavailable')
        return original(self,file)
    monkeypatch.setattr(cap.EvidenceStore,'create',failed)
    with pytest.raises(OSError):
        ssh.capture_ssh_transport(tmp_path,RUN,process_factory=lambda a:calls.append(a))
    assert not calls


def test_prelaunch_fsync_failure_starts_zero_ssh(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(os,'fsync',lambda fd:(_ for _ in ()).throw(OSError('synthetic fsync')))
    with pytest.raises(OSError):ssh.capture_ssh_transport(tmp_path,RUN,process_factory=lambda a:calls.append(a))
    assert not calls


@pytest.mark.parametrize('bad',['../escape','/escape','a/b','a\\b'])
def test_run_path_rejected_before_launcher(tmp_path,bad):
    with pytest.raises(cap.EvidenceError):
        ssh.capture_ssh_transport(tmp_path,bad,process_factory=lambda a:pytest.fail('launched'))


def test_output_limit_preserves_received_count_and_reaps(tmp_path):
    clock=Clock();data=wire(b'123456',b'error')
    child=MockSSH(clock,data)
    with pytest.raises(cap.EvidenceError):
        ssh.capture_ssh_transport(tmp_path,RUN,process_factory=lambda a:child,nonce=NONCE,clock=clock,max_output_bytes=3)
    record=json.loads((tmp_path/RUN/'process_result.json').read_text())
    assert record['received_stream_bytes']['stdout']==len(data)
    assert record['maximum_output_bytes']==3 and not record['capture_complete']
    assert record['reaped'] and child.terminated==1


def test_manifest_tamper_detected(tmp_path):
    root,_,_=run_mock(tmp_path)
    (root/'ssh_client.log.raw').write_bytes(b'modified')
    with pytest.raises(cap.EvidenceError):cap.verify_manifest(root)


def test_sensitive_argv_refused_and_raw_not_filtered(tmp_path):
    secret=b'Authorization: Bearer SYNTHETIC_SECRET\n'
    root,_,_=run_mock(tmp_path,secret)
    assert (root/'remote_stdout.raw').read_bytes()==secret
    assert b'SYNTHETIC_SECRET' not in (root/'transport_status.json').read_bytes()
    assert b'SYNTHETIC_SECRET' not in (root/'combined_observed_timeline.jsonl').read_bytes()
    settings=replace(ssh.SSHSettings(),identity_file='/private/Authorization: Bearer synthetic')
    with pytest.raises(cap.EvidenceError):ssh.ssh_argv(tmp_path/'log',settings)


@pytest.mark.parametrize('malformation',['wrong_nonce','wrong_sequence','corrupt_payload','truncated','unframed','extra_after_exit'])
def test_frame_validation_rejects_ambiguous_evidence(tmp_path,malformation):
    data=wire(b'binary\xff')
    if malformation=='wrong_nonce':data=data.replace(NONCE.encode(),b'wrong')
    elif malformation=='wrong_sequence':data=data.replace(b'"sequence":2',b'"sequence":5')
    elif malformation=='corrupt_payload':data=data.replace(b'YmluYXJ5/w==',b'YmluYXJ5/g==')
    elif malformation=='truncated':data=data[:-20]
    elif malformation=='unframed':data=b'raw unqualified stdout\xff'
    else:data+=ssh.encode_frame(RUN,NONCE,4,'start')
    with pytest.raises(cap.EvidenceError):run_mock(tmp_path,raw_wire=data)
    status=json.loads((tmp_path/RUN/'transport_status.json').read_text())
    assert not status['remote_exit_status_observed']


def test_all_wire_split_boundaries_preserve_utf8_and_non_utf8():
    data=wire('多字节'.encode()+b'\xff\x00',b'\r\n')
    for split in range(len(data)+1):
        decoder=ssh.FrameDecoder(RUN,NONCE);chunks=[]
        sink=lambda frame,raw:chunks.append((frame.get('stream'),raw)) if frame['kind']=='data' else None
        decoder.feed(data[:split],sink);decoder.feed(data[split:],sink);decoder.finish()
        assert b''.join(raw for stream,raw in chunks if stream=='stdout')=='多字节'.encode()+b'\xff\x00'


def test_concurrent_runs_isolated(tmp_path):
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run_mock,tmp_path,b'bytes',run=name) for name in ['a','b']]
        roots=[f.result()[0] for f in futures]
    assert roots[0]!=roots[1]
    for root in roots:cap.verify_manifest(root)


def test_cli_is_offline_and_no_execute_switch(tmp_path,monkeypatch,capsys):
    monkeypatch.chdir(tmp_path)
    assert main([])==0
    info=json.loads(capsys.readouterr().out)
    assert not any(info[k] for k in ['network_enabled','ssh_enabled','remote_command_enabled','real_qwen_enabled'])
    assert info['fake_ssh_executable'] is None
    with pytest.raises(SystemExit):main(['--execute'])
    assert not list(tmp_path.iterdir())


def test_remote_wrapper_is_serializable_not_executed_and_reuses_pipe_adapter():
    source=ssh.remote_wrapper_source(['/usr/bin/openssl','version'],RUN,NONCE)
    compile(source,'future_remote_stdin','exec')
    namespace={};exec(source,namespace)
    assert callable(namespace['authorized_launch_after_preflight'])
    assert 'shell=False' in source and 'PopenPipeProcess(child)' in source
    assert 'def capture_process' not in source and 'mkdir' not in source


def test_mock_remote_producer_roundtrip_and_termination_receipt():
    clock=Clock();process=MockSSH(clock,b'raw\xff',b'errors\r\n',code=255)
    frames=[];result=ssh.produce_session(process,RUN,NONCE,frames.append,clock=clock)
    assert result['reaped'] and process.waited
    decoder=ssh.FrameDecoder(RUN,NONCE);out=[]
    decoder.feed(b''.join(frames),lambda frame,raw:out.append((frame.get('stream'),raw)) if frame['kind']=='data' else None)
    decoder.finish()
    assert decoder.receipt['returncode']==255
    assert b''.join(raw for stream,raw in out if stream=='stdout')==b'raw\xff'


def test_fake_ssh_executable_binary_pipes_and_debug_file(tmp_path):
    # Python fake, never OpenSSH; it writes only its inherited binary pipes and -E file.
    data=wire(b'concurrent stdout\xff',b'remote stderr\x80',run='actual_fake')
    fake=tmp_path/'fake_ssh.py'
    fake.write_text('import os,sys,threading\n'
        'path=sys.argv[sys.argv.index("-E")+1]\n'
        'with open(path,"ab") as f:f.write(b"debug\\r\\n\\n")\n'
        f'wire={data!r}\n'
        'a=threading.Thread(target=lambda:os.write(1,wire))\n'
        'b=threading.Thread(target=lambda:os.write(2,b"local bootstrap?\\xff"))\n'
        'a.start();b.start();a.join();b.join()\n')
    children=[]
    def factory(argv):
        env={k:os.environ[k] for k in ['PATH','LANG'] if k in os.environ}
        child=subprocess.Popen([sys.executable,str(fake),*argv[1:]],stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE,shell=False,start_new_session=True,env=env)
        children.append(child)
        return cap.PopenPipeProcess(child)
    result=ssh.capture_ssh_transport(tmp_path,'actual_fake',process_factory=factory,nonce=NONCE,
        settings=ssh.SSHSettings(executable=str(fake)))
    # This fake's wire must bind to the chosen run id.
    assert result['transport']['remote_exit_status_observed']
    assert children[0].poll()==0


def test_large_fake_concurrent_pipe_outputs_do_not_deadlock(tmp_path):
    stdout=b'\xffout'*150000;stderr=b'\x80err'*120000
    data=wire(stdout,stderr,run='big_fake')
    fake=tmp_path/'large_fake.py'
    payload=tmp_path/'wire_payload';payload.write_bytes(data)
    fake.write_text('import os,sys,threading\n'
        'path=sys.argv[sys.argv.index("-E")+1]\n'
        'with open(path,"ab") as f:f.write(b"large debug\\n")\n'
        f'wire=open({str(payload)!r},"rb").read()\n'
        'a=threading.Thread(target=lambda:os.write(1,wire))\n'
        'b=threading.Thread(target=lambda:os.write(2,b"unattributed"*10000))\n'
        'a.start();b.start();a.join();b.join()\n')
    children=[]
    def factory(argv):
        child=subprocess.Popen([sys.executable,str(fake),*argv[1:]],stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,shell=False,start_new_session=True,
            env={'PATH':os.environ.get('PATH','')})
        children.append(child);return cap.PopenPipeProcess(child)
    ssh.capture_ssh_transport(tmp_path,'big_fake',process_factory=factory,nonce=NONCE,
                             settings=ssh.SSHSettings(executable=str(fake)))
    assert children[0].poll()==0
    assert (tmp_path/'big_fake/remote_stdout.raw').read_bytes()==stdout
    assert (tmp_path/'big_fake/remote_stderr.raw').read_bytes()==stderr
    assert (tmp_path/'big_fake/ssh_stderr_unclassified.raw').stat().st_size==120000


def test_mid_capture_audit_failure_reclaims_only_owned_child(tmp_path,monkeypatch):
    clock=Clock();child=MockSSH(clock,wire(b'content'))
    original=cap.EvidenceStore.record
    def fail(self,name,value):
        if name=='remote_observed_timeline.jsonl':raise OSError('synthetic remote audit failure')
        return original(self,name,value)
    monkeypatch.setattr(cap.EvidenceStore,'record',fail)
    with pytest.raises(cap.EvidenceError):
        ssh.capture_ssh_transport(tmp_path,RUN,process_factory=lambda a:child,nonce=NONCE,clock=clock)
    assert child.terminated==1 and child.waited and child.closed
    assert (tmp_path/RUN/'ssh_stdout_transport.raw').stat().st_size>0
    assert not json.loads((tmp_path/RUN/'transport_status.json').read_text())['remote_exit_status_observed']


@pytest.mark.parametrize('point',['artifact_manifest.pending','transport_status.json'])
def test_final_persistence_failure_does_not_publish_success(tmp_path,monkeypatch,point):
    original=cap.EvidenceStore.record
    def fail(self,name,value):
        if name==point:raise OSError('synthetic final persistence error')
        return original(self,name,value)
    monkeypatch.setattr(cap.EvidenceStore,'record',fail)
    with pytest.raises(cap.EvidenceError):run_mock(tmp_path)
    assert not (tmp_path/RUN/'artifact_manifest.json').exists()


def test_remote_producer_timeout_and_local_decode_are_separate():
    clock=Clock();child=MockSSH(clock,b'partial',hang=True,sticky=True);frames=[]
    result=ssh.produce_session(child,RUN,NONCE,frames.append,clock=clock)
    assert result['timed_out'] and result['reaped'] and child.killed==1
    decoder=ssh.FrameDecoder(RUN,NONCE);decoder.feed(b''.join(frames),lambda *a:None);decoder.finish()
    assert decoder.receipt['returncode']==-9 and decoder.receipt['timed_out']


def test_remote_producer_emission_failure_stops_owned_process():
    clock=Clock();child=MockSSH(clock,b'data')
    def failed(frame):raise BrokenPipeError('synthetic closed SSH pipe')
    result=ssh.produce_session(child,RUN,NONCE,failed,clock=clock)
    assert result['reaped'] and child.terminated==1 and child.closed


def test_remote_receipt_never_inferred_from_debug_exit_status(tmp_path):
    with pytest.raises(cap.EvidenceError):run_mock(tmp_path,raw_wire=b'',debug=b'debug1: Exit status 0\n')
    status=json.loads((tmp_path/RUN/'transport_status.json').read_text())
    assert status['remote_exit_status'] is None


def test_verified_remote_evidence_is_a_gate_before_classifier(tmp_path):
    root,_,_=run_mock(tmp_path,b'\xff',b'\x80',remote_code=7)
    evidence=ssh.verified_remote_evidence(root)
    assert evidence['stdout']==b'\xff' and evidence['stderr']==b'\x80'
    assert evidence['transport']['remote_exit_status']==7


def test_configuration_and_sensitive_changes_rejected(tmp_path):
    with pytest.raises(cap.EvidenceError):ssh.ssh_argv(tmp_path/'log',replace(ssh.SSHSettings(),port=22))
    with pytest.raises(cap.EvidenceError):ssh.ssh_argv(tmp_path/'log',replace(ssh.SSHSettings(),alias='other'))


def test_wrapper_does_not_change_frozen_tls_arguments():
    argv=['/usr/bin/openssl','s_client','-connect','127.0.0.1:8000','-servername','localhost',
          '-verify_hostname','localhost','-verify_return_error','-CAfile',
          '/root/.config/risk-agent/tls/ca.crt','-brief','-state','-msg']
    source=ssh.remote_wrapper_source(argv,RUN,NONCE)
    assert repr(argv) in source
    assert 'threading.Timer(max(0, 5.0-' in source
    assert 'authorized_launch_after_preflight()\n' not in source


def test_debug_tail_setup_failure_still_delivers_child_to_owned_cleanup(tmp_path,monkeypatch):
    clock=Clock();child=MockSSH(clock,wire())
    original=ssh.SSHLogProcess.__init__
    def fail(self,*args):raise OSError('synthetic tail setup failure')
    monkeypatch.setattr(ssh.SSHLogProcess,'__init__',fail)
    with pytest.raises(cap.EvidenceError):
        ssh.capture_ssh_transport(tmp_path,RUN,process_factory=lambda a:child,nonce=NONCE,clock=clock)
    assert child.terminated==1 and child.waited and child.closed
