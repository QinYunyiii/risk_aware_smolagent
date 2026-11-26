from __future__ import annotations

import io
import json
from pathlib import Path
import ssl
import subprocess
import sys
import threading

import httpcore2
import httpx2
import pytest
from httpcore2._backends.base import NetworkBackend, NetworkStream

from scripts import run_p2_d2_worker0_tls_diagnostic as diag


class FakeStream(NetworkStream):
    def __init__(self, failure=None):
        self.failure=failure
        self.writes=[]
        self.tls_args=None
        self.read_timeout=None
        body=b'{"data":[{"id":"Qwen/Qwen3-8B"}]}'
        self.response=b'HTTP/1.1 200 OK\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body
    def start_tls(self,ssl_context,server_hostname=None,timeout=None):
        self.tls_args=(ssl_context,server_hostname,timeout)
        if self.failure=='tls':
            raise httpcore2.ConnectTimeout('synthetic handshake timeout') from TimeoutError('synthetic handshake timeout')
        if self.failure=='certificate':
            raise httpcore2.ConnectError('certificate verify failed') from ssl.SSLCertVerificationError('synthetic certificate')
        return self
    def read(self,max_bytes,timeout=None):
        self.read_timeout=timeout
        if self.failure=='http':raise httpcore2.ReadTimeout('synthetic read timeout')
        result,self.response=self.response,b''
        return result
    def write(self,buffer,timeout=None):self.writes.append(buffer)
    def close(self):pass
    def get_extra_info(self,info):
        return {'server_addr':('127.0.0.1',8000),'client_addr':('127.0.0.1',54321),'is_readable':False}.get(info)


class FakeBackend(NetworkBackend):
    def __init__(self,failure=None):
        self.failure=failure
        self.stream=FakeStream(failure)
        self.calls=[]
    def connect_tcp(self,**kwargs):
        self.calls.append(kwargs)
        if self.failure=='tcp':raise httpcore2.ConnectTimeout('synthetic TCP timeout')
        return self.stream
    def sleep(self,value):pytest.fail('Retry is forbidden')


def run_fake(root,failure=None):
    audit=diag.Audit(root,'offline_fake')
    context=ssl.create_default_context()
    backend=FakeBackend(failure)
    with diag.make_client(context) as client:
        # Real HTTPX/HTTPCore event machinery, fake socket/TLS backend: no network.
        client._transport._pool=httpcore2.ConnectionPool(ssl_context=context,network_backend=backend,retries=0)
        assert client._mounts=={}
        assert client.trust_env is False
        assert client.timeout.as_dict()==dict(connect=5,read=10,write=10,pool=10)
        result=diag.request_once(client,'fixture-only-secret',audit)
    return audit,backend,result


def test_actual_trace_extension_events_with_fake_network_backend(tmp_path):
    audit,backend,result=run_fake(tmp_path)
    assert result['success']
    names=[e['event'] for e in audit.events]
    for event in ('connect_tcp.started','connect_tcp.complete','start_tls.started','start_tls.complete'):
        assert 'connection.'+event in names
    stamps=[e['monotonic_ns'] for e in audit.events]
    assert stamps==sorted(stamps) and len(set(stamps))==len(stamps)
    tls=next(e for e in audit.events if e['event']=='connection.start_tls.started')
    assert tls['check_hostname'] is True and tls['verify_mode']==ssl.CERT_REQUIRED
    assert tls['timeout']==5 and tls['server_hostname']=='localhost'
    assert backend.stream.tls_args[1:]==('localhost',5)
    assert backend.stream.read_timeout==10
    assert len(backend.calls)==1
    assert result['actual_client_peer']['server_addr']==('127.0.0.1',8000)
    assert result['actual_client_peer']['client_addr']==('127.0.0.1',54321)
    assert b'GET /v1/models HTTP/1.1' in b''.join(backend.stream.writes)
    for file in tmp_path.iterdir():
        assert 'fixture-only-secret' not in file.read_text()
        assert 'Authorization' not in file.read_text()


@pytest.mark.parametrize('failure,missing,last',[
    ('tcp','connection.connect_tcp.complete','connection.connect_tcp.failed'),
    ('tls','connection.start_tls.complete','connection.start_tls.failed'),
    ('certificate','connection.start_tls.complete','connection.start_tls.failed'),
    ('http','http11.receive_response_headers.complete','http11.receive_response_headers.failed'),
])
def test_stage_failures_record_real_trace_callbacks_without_retry(tmp_path,failure,missing,last):
    audit,backend,result=run_fake(tmp_path,failure)
    assert result['success'] is False
    names=[e['event'] for e in audit.events]
    assert missing not in names and last in names
    assert len(backend.calls)==1
    assert diag.classify_phase(audit.events,result)=={
        'tcp':'A_TCP_INCOMPLETE','tls':'B_TCP_COMPLETE_TLS_INCOMPLETE',
        'certificate':'D_CERTIFICATE_VERIFICATION_ERROR','http':'C_HTTP_STAGE_FAILURE'}[failure]
    if failure in ('tcp','tls','certificate'):
        assert not backend.stream.writes  # HTTP not sent before a complete handshake
    if failure=='certificate':
        assert result['diagnosis']['connection_diagnostic']=='tls_connection_failure'


def test_ssh_reader_does_not_wait_for_audit_disk_writer(tmp_path,monkeypatch):
    audit=diag.Audit(tmp_path,'drain_fake')
    entered,release=threading.Event(),threading.Event()
    def slow_emit(*args,**kwargs):
        entered.set()
        assert release.wait(5)
    monkeypatch.setattr(audit,'emit',slow_emit)
    # Local Python subprocess only. More bytes than a pipe buffer, no SSH/network.
    child=subprocess.Popen([sys.executable,'-c','import sys; sys.stderr.write("debug synthetic\\n"*20000)'],stderr=subprocess.PIPE,text=True)
    collector=diag.SSHLogCollector(child.stderr,audit)
    try:
        assert entered.wait(2)
        assert child.wait(timeout=3)==0
        collector.reader.join(timeout=2)
        assert not collector.reader.is_alive()
    finally:
        release.set()
        collector.join()


def test_ssh_channel_log_parser_receipt_clock_and_redaction(tmp_path):
    audit=diag.Audit(tmp_path,'logs_fake')
    text='\n'.join([
        'Authenticated to fixture using "publickey".',
        'Local forwarding listening on 127.0.0.1 port 8000.',
        'Connection to port 8000 forwarding to 127.0.0.1 port 8000 requested.',
        'debug1: channel 2: new direct-tcpip [direct-tcpip]',
        'debug2: channel 2: send open',
        'debug2: channel 2: open confirm rwindow 2097152 rmax 32768',
        'debug2: channel 2: rcvd close',
        'Authorization: Bearer fixture-hidden-token',
    ])+'\n'
    collector=diag.SSHLogCollector(io.StringIO(text),audit)
    collector.join()
    events=[e for e in audit.events if e['event']=='channel_open_confirmed']
    assert events[0]['channel_id']==2
    assert events[0]['destination']=='127.0.0.1' and events[0]['destination_port']==8000
    assert events[0]['monotonic_ns']
    assert 'fixture-hidden-token' not in (tmp_path/'ssh_process_audit.jsonl').read_text()


def test_trace_does_not_serialize_arbitrary_info_or_headers(tmp_path):
    audit=diag.Audit(tmp_path,'safe_fake')
    audit.trace('http11.send_request_headers.started',{
        'request':httpx2.Request('GET',diag.URL,headers={'Authorization':'Bearer fixture-hidden'}),
        'private_key':'fixture-private-data'})
    assert 'fixture-hidden' not in (tmp_path/'tcp_tls_trace.jsonl').read_text()
    assert 'fixture-private-data' not in (tmp_path/'tcp_tls_trace.jsonl').read_text()


def test_passive_tcp_parser_does_not_connect(tmp_path,monkeypatch):
    def forbidden(*args,**kwargs):pytest.fail('Network attempted during passive inspection')
    monkeypatch.setattr('socket.socket',forbidden)
    assert isinstance(diag.all_local_tcp(),list)
    assert isinstance(diag.owned_tcp(99999999),list)


def test_cli_denies_stage_b_before_any_launch(monkeypatch):
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**k:pytest.fail('Process started'))
    with pytest.raises(RuntimeError,match='separate explicit user authorization'):
        diag.main(['--run-id','must_not_run'])


def test_debug_adapter_owns_one_worker_and_refuses_identity_change(tmp_path,monkeypatch):
    audit=diag.Audit(tmp_path,'process_fake')
    captured={}
    class Child:
        pid=12345
        returncode=None
        stderr=io.StringIO('Authenticated to fake using "publickey".\n')
        def poll(self):return self.returncode
        def terminate(self):self.returncode=0
        def wait(self,timeout=None):return self.returncode
    def popen(command,**kwargs):
        captured.update(command=command,kwargs=kwargs)
        return Child()
    monkeypatch.setattr(subprocess,'Popen',popen)
    monkeypatch.setattr(diag,'proc_identity',lambda pid:('123',captured['command']))
    monkeypatch.setattr(diag,'owned_tcp',lambda pid:[dict(local=['127.0.0.1',8000],peer=['0.0.0.0',0],tcp_state='0A')])
    adapter=diag.DiagnosticSSH(dict(identity_file='fixture-key-path',ssh_port=4035,
        known_hosts_file='fixture-known-hosts',target='root@fixture.invalid'),audit)
    assert adapter.snapshot()['listener_state']=='LOCAL_PORT_LISTENING'
    command=captured['command']
    assert command.count('-L')==1
    assert '127.0.0.1:8000:127.0.0.1:8000' in command
    assert '8001' not in ' '.join(command)
    assert '-vvv' in command and '-F' in command and '/dev/null' in command
    assert 'ClearAllForwardings=yes' not in command
    assert 'StrictHostKeyChecking=yes' in command
    assert captured['kwargs']['start_new_session'] is True
    monkeypatch.setattr(diag,'proc_identity',lambda pid:('reused',captured['command']))
    with pytest.raises(RuntimeError,match='identity changed'):
        adapter.close(adapter.identity)
    assert adapter.process.poll() is None
    monkeypatch.setattr(diag,'proc_identity',lambda pid:('123',captured['command']))
    adapter.close(adapter.identity)
    assert adapter.process.returncode==0


def test_partial_adapter_setup_audit_failure_cleans_fresh_child(tmp_path,monkeypatch):
    audit=diag.Audit(tmp_path,'setup_failure_fake')
    class Child:
        pid=54321
        returncode=None
        stderr=io.StringIO('')
        def poll(self):return self.returncode
        def terminate(self):self.returncode=0
        def wait(self,timeout=None):return self.returncode
    child=Child()
    monkeypatch.setattr(subprocess,'Popen',lambda *args,**kwargs:child)
    monkeypatch.setattr(diag,'proc_identity',lambda pid:('123',['fake-ssh']))
    def failed(*args,**kwargs):raise OSError('synthetic durable audit failure')
    monkeypatch.setattr(audit,'emit',failed)
    with pytest.raises(OSError):
        diag.DiagnosticSSH(dict(identity_file='fixture-key',ssh_port=4035,
            known_hosts_file='fixture-hosts',target='root@fixture.invalid'),audit)
    assert child.returncode==0 and child.stderr.closed


def test_known_health_failure_prevents_get_submission(tmp_path):
    audit=diag.Audit(tmp_path,'health_fail_fake')
    class NoClient:
        def get(self,*args,**kwargs):pytest.fail('GET submitted after known connection failure')
    def failed():raise ConnectionError('synthetic known channel failure')
    with pytest.raises(ConnectionError):
        diag.request_once(NoClient(),'fixture',audit,failed)


def test_changed_ca_blocks_before_any_ssh_launch(tmp_path,monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(diag.RemoteModelSettings,'from_yaml',lambda path:SimpleNamespace(
        resolve_api_key=lambda:'fixture',resolve_ca_bundle=lambda:tmp_path/'different-ca'))
    monkeypatch.setattr(diag,'specs_from_host',lambda:pytest.fail('SSH config consulted after CA mismatch'))
    with pytest.raises(RuntimeError,match='Approved CA'):
        diag.main(['--authorize-stage-b','--run-id','must_not_start'])


def test_control_peer_is_decoded_from_owned_socket_not_configured_target(monkeypatch):
    real_path=Path
    class FakeFD:
        def readlink(self):return real_path('socket:[123]')
    class FakePath:
        def __init__(self,value):self.value=value
        def iterdir(self):return iter([FakeFD()])
        def read_text(self):
            return 'header\n0: 0200000A:BEEF 0100000A:0FC3 01 0:0 0:0 0 1000 0 123\n1: 0300000A:BEEF 0400000A:0FC3 01 0:0 0:0 0 1000 0 999\n'
    monkeypatch.setattr(diag,'Path',FakePath)
    sockets=diag.owned_tcp(55)
    assert len(sockets)==1
    assert sockets[0]['local']==['10.0.0.2',48879]
    assert sockets[0]['peer']==['10.0.0.1',4035]
