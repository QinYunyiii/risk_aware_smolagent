"""Offline repair regression; fake local SSH exercises the real adapter path."""
from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import random
import socket
import subprocess
import sys

import pytest

from risk_agent.collection import ssh_evidence_transport as ssh
from risk_agent.collection import tls_evidence_capture as cap
from test_ssh_evidence_transport import NONCE, RUN, MockSSH, Clock, run_mock, wire


@pytest.fixture(autouse=True)
def forbid_network_and_real_remote_tools(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail('Offline compatibility test attempted a real connection')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny)
    monkeypatch.setattr(socket, 'create_connection', deny)
    original = subprocess.Popen
    def local_only(argv, *args, **kwargs):
        assert Path(argv[0]).name not in ('ssh', 'openssl', 'curl', 'wget')
        return original(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', local_only)


def receipt(code):
    return dict(returncode=code, reaped=True, timed_out=False)


@pytest.mark.parametrize('local,remote,transport,command', [
    (0,0,'SUCCESS','COMPLETED'), (23,23,'SUCCESS','COMPLETED_NONZERO'),
    (1,1,'SUCCESS','COMPLETED_NONZERO'), (255,None,'FAILED_OR_UNKNOWN','UNKNOWN'),
    (None,None,'UNKNOWN','UNKNOWN'), (0,23,'SUCCESS','COMPLETED_NONZERO'),
    (23,1,'FAILED_OR_UNKNOWN','COMPLETED_NONZERO'), (1,0,'FAILED_OR_UNKNOWN','COMPLETED'),
])
def test_required_exit_semantics_matrix(local,remote,transport,command):
    result=ssh.exit_semantics(local,False,None if remote is None else receipt(remote),evidence_valid=remote is not None)
    assert result['ssh_process_return_code']==local
    assert result['remote_exit_status']==remote
    assert result['remote_exit_status_observed']==(remote is not None)
    assert result['ssh_transport_status']==transport
    assert result['remote_command_status']==command


@pytest.mark.parametrize('bad',[None,{},dict(returncode=23),dict(returncode='23',reaped=True,timed_out=False),
    dict(returncode=True,reaped=True,timed_out=False),dict(returncode=23,reaped=False,timed_out=False),
    dict(returncode=23,reaped=True,timed_out='false')])
def test_missing_or_damaged_receipt_never_infers_exit_from_ssh(bad):
    result=ssh.exit_semantics(23,False,bad,evidence_valid=True)
    assert result['remote_exit_status'] is None
    assert result['remote_command_status']=='UNKNOWN'
    assert result['remote_termination_status']=='UNKNOWN'
    assert result['ssh_transport_status']!='SUCCESS'


def test_invalid_evidence_or_timeout_does_not_imply_transport_success():
    result=ssh.exit_semantics(23,False,receipt(23),evidence_valid=False)
    assert not result['remote_exit_status_observed'] and result['remote_exit_status'] is None
    result=ssh.exit_semantics(-15,True,None,evidence_valid=False)
    assert result['local_timeout'] and result['remote_termination_status']=='UNKNOWN'
    assert result['remote_command_status']=='UNKNOWN'


def test_reliable_remote_255_stays_independent_of_ssh_255_ambiguity():
    result=ssh.exit_semantics(255,False,receipt(255),evidence_valid=True)
    assert result['remote_exit_status']==255 and result['remote_exit_status_observed']
    assert result['ssh_transport_status']!='SUCCESS'
    assert result['ssh_transport_detail']=='SSH_255_AMBIGUOUS'


@pytest.mark.parametrize('code',[0,1,23])
def test_matching_codes_in_actual_adapter_not_only_status_function(tmp_path,code):
    root,_,result=run_mock(tmp_path,stdout=ssh.BYTE_PROBE_STDOUT,stderr=ssh.BYTE_PROBE_STDERR,
                           ssh_code=code,remote_code=code)
    assert result['transport']['ssh_transport_status']=='SUCCESS'
    assert result['transport']['remote_command_status']==('COMPLETED' if code==0 else 'COMPLETED_NONZERO')
    assert (root/'remote_stdout.raw').read_bytes()==ssh.BYTE_PROBE_STDOUT
    assert (root/'remote_stderr.raw').read_bytes()==ssh.BYTE_PROBE_STDERR
    cap.verify_manifest(root)


def encode(stdout,stderr):
    encoder=ssh.FrameEncoder(RUN,NONCE)
    frames=[encoder.start()]
    frames.extend(encoder.data('stdout',stdout))
    frames.extend(encoder.data('stderr',stderr))
    frames.append(encoder.finish(returncode=23,reaped=True,timed_out=False))
    return b''.join(frames)


def decode(data,parts):
    decoder=ssh.FrameDecoder(RUN,NONCE);out=dict(stdout=bytearray(),stderr=bytearray())
    def sink(frame,payload):
        if frame['kind']=='data':out[frame['stream']].extend(payload)
    offset=0
    for length in parts:
        decoder.feed(data[offset:offset+length],sink);offset+=length
    decoder.feed(data[offset:],sink);decoder.finish()
    return {s:bytes(v) for s,v in out.items()},decoder.receipt


@pytest.mark.parametrize('stdout,stderr',[(b'',b''),(b'ASCII\r\n\n',b'line\n'),
    (b'\x00',b'\x00\x00'),(bytes(range(256)),b'\xff\x80\xfe\x81'),
    ('多字节跨块'.encode(),b'\x00STDERR\r\n')])
def test_authoritative_encoder_decoder_roundtrip_arbitrary_chunks(stdout,stderr):
    data=encode(stdout,stderr)
    for sizes in ([1]*len(data),[2,3,7,17,19],[len(data)//2]):
        out,ending=decode(data,sizes)
        assert out==dict(stdout=stdout,stderr=stderr)
        assert ending['returncode']==23


def test_oversize_payload_becomes_many_compatible_frames_without_truncation(tmp_path):
    stdout=bytes(range(256))*3000;stderr=b'\xfferr'*10000
    data=encode(stdout,stderr)
    assert all(len(line)+1<=ssh.MAX_FRAME_BYTES for line in data.splitlines())
    rng=random.Random(20261125)
    out,ending=decode(data,[rng.randint(1,17000) for _ in range(150)])
    assert out==dict(stdout=stdout,stderr=stderr)
    root,_,result=run_mock(tmp_path,stdout,stderr,raw_wire=data,ssh_code=23)
    assert result['transport']['ssh_transport_status']=='SUCCESS'
    assert (root/'remote_stdout.raw').read_bytes()==stdout
    cap.verify_manifest(root)


@pytest.mark.parametrize('damage',['incomplete','header','length','offset','sha','bad_stream','bad_base64','missing_exit'])
def test_invalid_frames_fail_closed_in_real_adapter(tmp_path,damage):
    data=encode(b'payload\x00\xff',b'errors')
    lines=data.splitlines(keepends=True)
    if damage=='incomplete':data=data[:-2]
    elif damage=='header':data=data.replace(ssh.PREFIX,b'INVALID ',1)
    elif damage=='missing_exit':data=b''.join(lines[:-1])
    else:
        item=json.loads(lines[1][len(ssh.PREFIX):])
        if damage=='length':item['byte_count']+=1
        elif damage=='offset':item['offset']=1
        elif damage=='sha':item['chunk_sha256']='0'*64
        elif damage=='bad_stream':item['stream']='merged'
        else:item['payload']='not!base64'
        lines[1]=ssh.PREFIX+json.dumps(item).encode()+b'\n';data=b''.join(lines)
    with pytest.raises(cap.EvidenceError):run_mock(tmp_path,raw_wire=data,ssh_code=23)
    status=json.loads((tmp_path/RUN/'transport_status.json').read_text())
    assert status['remote_exit_status'] is None and status['remote_command_status']=='UNKNOWN'
    assert status['ssh_transport_status']!='SUCCESS'


def test_single_oversize_low_level_frame_refused_by_same_wire_limit():
    with pytest.raises(cap.EvidenceError,match='V1 receiver limit'):
        ssh.data_frame(RUN,NONCE,2,'stdout',b'x'*ssh.MAX_FRAME_BYTES,0)


@pytest.mark.parametrize('damage',['exit_hash','exit_length','exit_code_type','exit_not_reaped','exit_timeout_type'])
def test_corrupted_exit_receipt_cannot_confirm_remote_status(tmp_path,damage):
    lines=encode(b'payload',b'errors').splitlines(keepends=True)
    item=json.loads(lines[-1][len(ssh.PREFIX):])
    if damage=='exit_hash':item['stdout_sha256']='0'*64
    elif damage=='exit_length':item['stderr_bytes']+=1
    elif damage=='exit_code_type':item['returncode']='23'
    elif damage=='exit_not_reaped':item['reaped']=False
    else:item['timed_out']='false'
    lines[-1]=ssh.PREFIX+json.dumps(item).encode()+b'\n'
    with pytest.raises(cap.EvidenceError):run_mock(tmp_path,raw_wire=b''.join(lines),ssh_code=23)
    status=json.loads((tmp_path/RUN/'transport_status.json').read_text())
    assert not status['remote_exit_status_observed']
    assert status['remote_exit_status'] is None and status['remote_termination_status']=='UNKNOWN'


def test_multibyte_character_split_across_payload_frames_is_not_decoded():
    payload=b'x'*(ssh.FRAME_CHUNK_BYTES-1)+'字'.encode()
    encoder=ssh.FrameEncoder(RUN,NONCE)
    start=encoder.start();parts=list(encoder.data('stdout',payload))
    assert len(parts)==2
    first=json.loads(parts[0][len(ssh.PREFIX):]);second=json.loads(parts[1][len(ssh.PREFIX):])
    assert base64.b64decode(first['payload']).endswith('字'.encode()[:1])
    assert base64.b64decode(second['payload'])=='字'.encode()[1:]
    ending=encoder.finish(returncode=23,reaped=True,timed_out=False)
    out,_=decode(start+b''.join(parts)+ending,[7,11,3,4096])
    assert out['stdout']==payload


def test_encoder_state_machine_does_not_synthesize_a_second_session():
    encoder=ssh.FrameEncoder(RUN,NONCE)
    with pytest.raises(cap.EvidenceError):list(encoder.data('stdout',b'pre-start'))
    encoder.start()
    with pytest.raises(cap.EvidenceError):encoder.start()
    encoder.finish(returncode=0,reaped=True,timed_out=False)
    with pytest.raises(cap.EvidenceError):list(encoder.data('stderr',b'post-exit'))


def test_v1_historical_wire_fixture_keeps_original_payload_and_expected_exit():
    fixture=json.loads((Path(__file__).parent/'fixtures/ssh/v1_stage_a_sample.json').read_text())
    data=base64.b64decode(fixture['wire_base64'])
    assert hashlib.sha256(data).hexdigest()==fixture['wire_sha256']
    decoder=ssh.FrameDecoder(fixture['run_id'],fixture['nonce']);out={'stdout':b'','stderr':b''}
    def sink(frame,payload):
        if frame['kind']=='data':out[frame['stream']]+=payload
    decoder.feed(data,sink);decoder.finish()
    assert out['stdout']==base64.b64decode(fixture['stdout_base64'])
    assert out['stderr']==base64.b64decode(fixture['stderr_base64'])
    assert decoder.receipt['returncode']==fixture['expected_remote_exit']==7
    # Existing low-level helpers still reproduce the original byte-for-byte wire.
    assert wire(out['stdout'],out['stderr'],7,fixture['run_id'],fixture['nonce'])==data


def run_prepared_fake(base,*,wrong_identity=False):
    run='prepared_fake';nonce='offline-prepared'
    plan=ssh.prepare_byte_probe(run,nonce,expected_hostname='not-this-host' if wrong_identity else os.uname().nodename,
                               expected_uid=os.getuid())
    program=base/'prepared_remote_source.py';program.write_text(plan.source)
    fake=base/'fake_ssh.py'
    fake.write_text('import pathlib,sys\n'
        'path=sys.argv[sys.argv.index("-E")+1]\n'
        'with open(path,"ab") as f:f.write(b"fake-client\\r\\n\\n")\n'
        f'source=pathlib.Path({str(program)!r}).read_text()\n'
        'exec(compile(source,"prepared_remote_source","exec"),{"__name__":"__main__"})\n')
    children=[]
    def factory(argv):
        child=subprocess.Popen([sys.executable,str(fake),*argv[1:]],stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,shell=False,start_new_session=True,
            env={k:os.environ[k] for k in ('PATH','LANG','LC_ALL') if k in os.environ})
        children.append(child);return cap.PopenPipeProcess(child)
    settings=replace(ssh.SSHSettings(),executable=str(fake))
    result=ssh.capture_ssh_transport(base,run,nonce=nonce,settings=settings,process_factory=factory)
    return base/run,plan,children,result


def test_prepared_byte_probe_fake_ssh_end_to_end_exit_23(tmp_path):
    root,plan,children,result=run_prepared_fake(tmp_path)
    assert len(children)==1 and children[0].poll()==23
    assert result['capture']['process']['returncode']==23
    assert result['transport']['ssh_transport_status']=='SUCCESS'
    assert result['transport']['remote_exit_status']==23
    assert result['transport']['remote_exit_status_observed']
    assert result['transport']['remote_command_status']=='COMPLETED_NONZERO'
    assert result['transport']['remote_termination_status']=='EXITED'
    assert (root/'remote_stdout.raw').read_bytes()==plan.expected_stdout==ssh.BYTE_PROBE_STDOUT
    assert (root/'remote_stderr.raw').read_bytes()==plan.expected_stderr==ssh.BYTE_PROBE_STDERR
    assert (root/'ssh_client.log.raw').read_bytes()==b'fake-client\r\n\n'
    assert (root/'ssh_stderr_unclassified.raw').read_bytes()==b''
    assert (root/'ssh_stdout_transport.raw').read_bytes()!=plan.expected_stdout
    manifest=cap.verify_manifest(root)
    for name,data in [('remote_stdout.raw',plan.expected_stdout),('remote_stderr.raw',plan.expected_stderr)]:
        item=next(a for a in manifest['artifacts'] if a['path']==name)
        assert item['sha256']==hashlib.sha256(data).hexdigest() and item['size_bytes']==len(data)
    decoder=ssh.FrameDecoder(root.name,result['nonce'])
    decoder.feed((root/'ssh_stdout_transport.raw').read_bytes(),lambda *args:None);decoder.finish()
    actual=decoder.receipt['lifecycle']
    assert actual['reaped'] and actual['returncode']==23
    assert actual['pid']>0 and actual['process_start_identity']


def test_prepared_identity_mismatch_outputs_no_payload_or_fabricated_receipt(tmp_path):
    with pytest.raises(cap.EvidenceError):run_prepared_fake(tmp_path,wrong_identity=True)
    root=tmp_path/'prepared_fake'
    assert (root/'ssh_stdout_transport.raw').read_bytes()==b''
    assert (root/'remote_stdout.raw').read_bytes()==b''
    assert (root/'remote_stderr.raw').read_bytes()==b''
    status=json.loads((root/'transport_status.json').read_text())
    assert status['ssh_process_return_code']==90
    assert status['remote_exit_status'] is None and not status['remote_exit_status_observed']


def test_prepared_plan_building_never_executes_and_uses_remote_interpreter(monkeypatch):
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**k:pytest.fail('Prepared plan launched a process'))
    plan=ssh.prepare_byte_probe('future_run','future_nonce')
    assert plan.expected_exit_status==23 and plan.protocol==ssh.PROTOCOL
    assert len(plan.expected_stdout)==55 and len(plan.expected_stderr)==14
    assert '[sys.executable, "-B", "-c",' in plan.source
    assert '36f825bcc5da' in plan.source and 'raise SystemExit(90)' in plan.source
    assert '/home/three' not in plan.source
