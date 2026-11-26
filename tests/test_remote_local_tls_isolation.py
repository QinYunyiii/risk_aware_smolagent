from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from risk_agent.collection import remote_local_tls_isolation as isolation
from scripts.run_p2_d2_remote_local_tls_isolation import main


CA=b'public CA fixture, not a secret'
HELP='\n'.join(isolation.REQUIRED_FLAGS)


class FakeOpenSSL:
    def __init__(self,case='success'):
        self.case=case; self.probes=[]
    def identity(self):return dict(hostname='36f825bcc5da',uid=0)
    def tool_info(self):
        if self.case=='unsupported':return 'OpenSSL 4.0.0',HELP
        if self.case=='flag_missing':return 'OpenSSL 3.0.13',HELP.replace('-verify_hostname','')
        return 'OpenSSL 3.0.13',HELP
    def read_ca(self):
        if self.case=='missing_ca':raise FileNotFoundError('fixture missing CA')
        if self.case=='unreadable_ca':raise PermissionError('fixture unreadable CA')
        return b'different public CA' if self.case=='wrong_ca' else CA
    def probe(self,command,budget,callback):
        self.probes.append((command,budget))
        lines={
            'tcp':['connect:errno=111','Connection refused'],
            'timeout':['SSL_connect:before SSL initialization','>>> TLS 1.3, Handshake [length 0100], ClientHello'],
            'unobserved_timeout':[],
            'certificate':['SSL_connect:before SSL initialization','verify error:num=62:hostname mismatch'],
            'success':['SSL_connect:before SSL initialization',
                '>>> TLS 1.3, Handshake [length 0100], ClientHello',
                '<<< TLS 1.3, Handshake [length 0050], ServerHello',
                '<<< TLS 1.3, Handshake [length 0200], Certificate',
                'Verification: OK','SSL_connect:SSL negotiation finished successfully',
                'Master-Key: MUST_NOT_BE_PERSISTED','    00 11 secret ticket data',
                '<<< TLS 1.3, Alert [length 0002], warning close_notify'],
            'verify_without_finish':['Verification: OK'],
        }[self.case]
        for line in lines:callback('stderr',line)
        return dict(returncode=0 if self.case in ('success','verify_without_finish') else -15,
            timed_out=self.case in ('timeout','unobserved_timeout'),elapsed_seconds=5 if 'timeout' in self.case else .05)


@pytest.mark.parametrize('case,expected',[
    ('tcp','REMOTE_TCP_FAILURE'),('timeout','REMOTE_TLS_HANDSHAKE_TIMEOUT'),
    ('certificate','REMOTE_CERTIFICATE_VERIFICATION_FAILURE'),('success','REMOTE_TLS_SUCCESS'),
    ('unobserved_timeout','DIAGNOSTIC_INCONCLUSIVE'),('verify_without_finish','DIAGNOSTIC_INCONCLUSIVE'),
])
def test_tls_classification_without_network(case,expected):
    runner=FakeOpenSSL(case); events=[]
    result=isolation.remote_entry(runner,events.append,hashlib.sha256(CA).hexdigest())
    assert result['category']==expected
    assert len(runner.probes)==1
    command,budget=runner.probes[0]
    assert budget==5
    assert command[:4]==['openssl','s_client','-connect','127.0.0.1:8000']
    assert '-verify_hostname' in command and '-verify_return_error' in command
    assert '-CAfile' in command and '-servername' in command
    assert '-verify' not in command and '-L' not in command
    assert 'MUST_NOT_BE_PERSISTED' not in json.dumps(events)
    assert all(e['clock_domain']=='remote_python' for e in events)


@pytest.mark.parametrize('case,error',[
    ('unsupported','ValueError'),('flag_missing','ValueError'),('missing_ca','FileNotFoundError'),
    ('unreadable_ca','PermissionError'),('wrong_ca','ValueError'),
])
def test_metadata_and_ca_failure_never_start_handshake(case,error):
    runner=FakeOpenSSL(case); events=[]
    result=isolation.remote_entry(runner,events.append,hashlib.sha256(CA).hexdigest())
    assert result['category']=='DIAGNOSTIC_INCONCLUSIVE'
    assert result['error_type']==error
    assert not runner.probes


@pytest.mark.parametrize('code,message,expected',[
    (255,'Permission denied (publickey).','SSH_AUTHENTICATION_FAILURE'),
    (2,'python3: unavailable','SSH_REMOTE_COMMAND_FAILURE'),
    (0,'','DIAGNOSTIC_INCONCLUSIVE'),
])
def test_mock_ssh_failure_classification(code,message,expected):
    assert isolation.classify_ssh(code,[],message)==expected


def test_owned_process_timeout_and_audit_exception_cleanup():
    result=isolation.bounded_process([sys.executable,'-c','import time; time.sleep(10)'],.05,lambda *a:None)
    assert result['timed_out'] and result['returncode']!=0
    assert result['elapsed_seconds']<2
    assert result['owned_pid'] and result['started_monotonic_ns']<result['ended_monotonic_ns']


def test_deadline_kills_only_owned_mock_child_even_if_audit_callback_is_slow(monkeypatch):
    import time
    real_popen=subprocess.Popen
    children=[]
    def popen(*args,**kwargs):
        p=real_popen(*args,**kwargs); children.append(p); return p
    monkeypatch.setattr(subprocess,'Popen',popen)
    def blocked_writer(*args):
        time.sleep(.15)
        assert children[0].poll() is not None
    result=isolation.bounded_process([sys.executable,'-u','-c',
        'import time; print("state",flush=True); time.sleep(10)'],.06,blocked_writer)
    assert result['timed_out'] and len(children)==1


def test_no_ssh_or_http_when_unauthorized(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**k:pytest.fail('Process launched'))
    with pytest.raises(RuntimeError,match='Separate Stage B authorization'):
        main(['--mode','remote_tls_diagnostic','--run-id','denied'])
    assert not (tmp_path/'runs').exists()


def test_ssh_command_is_forward_free_and_payload_is_stdlib_only():
    command=isolation.ssh_command()
    assert 'ClearAllForwardings=yes' in command
    assert 'ForwardAgent=no' in command and 'PasswordAuthentication=no' in command
    assert 'StrictHostKeyChecking=yes' in command and 'BatchMode=yes' in command
    assert not set(('-L','-R','-D')).intersection(command)
    assert command[-2:]==['risk-agent-remote','python3 -B -']
    payload=isolation.remote_program()
    compile(payload,'remote_stdin','exec')
    assert 'risk_agent' not in payload
    assert 'Authorization' not in payload and 'API_KEY' not in payload
    assert 'mkdir' not in payload and 'write_text' not in payload
    assert '127.0.0.1:8001' not in payload


def test_mock_ssh_single_execution_and_clock_domains(tmp_path):
    class MockSSH:
        calls=0
        def run(self,command,payload,callback):
            self.calls+=1
            callback('stderr','Authenticated to fixture using "publickey".')
            events=[]
            isolation.remote_entry(FakeOpenSSL(),events.append,hashlib.sha256(CA).hexdigest())
            for e in events:callback('stdout',json.dumps(e))
            return dict(returncode=0,timed_out=False,owned_pid=123,elapsed_seconds=.1)
    runner=MockSSH()
    result=isolation.run_authorized(tmp_path,'mock_only',runner)
    assert runner.calls==1 and result['category']=='REMOTE_TLS_SUCCESS'
    assert result['remote_tls_attempts']==1
    assert result['ssh_authentication_observed_seconds'] is not None
    events=[json.loads(x) for x in (tmp_path/'ssh_remote_command_audit.jsonl').read_text().splitlines()]
    assert all(e['clock_domain']=='local_wsl' for e in events)
    assert all(e['remote_event']['clock_domain']=='remote_python' for e in events if e['event']=='remote_event_received')


def test_audit_failure_is_fail_closed_before_ssh(tmp_path,monkeypatch):
    from risk_agent.monitoring.trajectory import JsonlTraceWriter
    def failed(*args,**kwargs):raise OSError('synthetic disk failure')
    monkeypatch.setattr(JsonlTraceWriter,'append',failed)
    class NeverSSH:
        def run(self,*args):pytest.fail('SSH invoked despite audit failure')
    with pytest.raises(OSError):isolation.run_authorized(tmp_path,'audit_failure',NeverSSH())


def test_audit_failure_while_local_mock_process_runs_terminates_owned_child(tmp_path,monkeypatch):
    from risk_agent.monitoring.trajectory import JsonlTraceWriter
    original=JsonlTraceWriter.append
    count=[0]; children=[]; popen=subprocess.Popen
    def record(self,value):
        count[0]+=1
        if count[0]>1:raise OSError('synthetic mid-stream disk failure')
        original(self,value)
    def start(*args,**kwargs):
        process=popen(*args,**kwargs); children.append(process); return process
    monkeypatch.setattr(JsonlTraceWriter,'append',record)
    monkeypatch.setattr(subprocess,'Popen',start)
    class LocalMockSSH:
        def run(self,command,payload,callback):
            return isolation.bounded_process([sys.executable,'-u','-c',
                'import time; print("{}",flush=True); time.sleep(10)'],1,callback)
    with pytest.raises(OSError):isolation.run_authorized(tmp_path,'audit_mid_failure',LocalMockSSH())
    assert len(children)==1 and children[0].poll() is not None


def test_preflight_only_queries_local_version_and_help(monkeypatch,capsys):
    calls=[]
    def run(command,**kwargs):
        calls.append(command)
        from types import SimpleNamespace
        return SimpleNamespace(stdout='OpenSSL 3.0.13' if command==['openssl','version'] else HELP,stderr='')
    monkeypatch.setattr(subprocess,'run',run)
    assert main([])==1  # Actual remote compatibility is unavailable offline: fail closed.
    result=json.loads(capsys.readouterr().out)
    assert calls==[['openssl','version'],['openssl','s_client','-help']]
    assert result['network_requests']==0
    assert result['remote_openssl_compatibility'].startswith('UNKNOWN')
