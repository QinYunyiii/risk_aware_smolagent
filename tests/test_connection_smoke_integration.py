"""Formal CLI with actual lifecycle, ownership predicate and HTTP request code.

Only system boundaries are Fake: SSH deployment call/process, sockets and HTTP
transport. No alternate runner, synthetic gate, Agent or network is used.
"""
from dataclasses import replace
import json
from pathlib import Path
import ssl
import subprocess
from types import SimpleNamespace

import httpx2
import pytest

from scripts import manage_remote_vllm as deployment
from scripts import run_p2_d2_connection_smoke as smoke
from risk_agent.collection.remote_local_tls_isolation import CA_SHA256
from risk_agent.monitoring.connection_lifecycle import ConnectionIdentity, ConnectionLifecycle


class System:
    def identity(self):
        return dict(remote_container_id='36f825bcc5da',remote_user='root',ubuntu_version='22.04')
    def gpu_rows(self):
        return {i: 'GPU-' + str(i+1)*8 + '-1111-2222-3333-444444444444' for i in (0,1)}
    def worker(self,i):
        return dict(worker_pid=900+i,alive=True,worker_process_identity='current-start-'+str(i),
            worker_command_identity=f'vllm-worker-{i}',worker_command_fingerprint=str(i)*64,
            cuda_visible_devices=str(i),state_pid=900+i,state_gpu_id=i,state_gpu_uuid=self.gpu_rows()[i],
            listener_ownership_verified=True,endpoint=f'127.0.0.1:{8000+i}',
            model_id='Qwen/Qwen3-8B',model_path='/mnt/risk-aware-smolagents/remote_models/Qwen3-8B')


class ClosedSocket:
    def __enter__(self): return self
    def __exit__(self,*a): pass
    def connect_ex(self,target): return 1


class FakeSSH:
    def __init__(self, calls, failure, **kw):
        self.worker=kw['worker_id']; self.calls=calls; self.failure=failure
        calls.append(('tunnel',self.worker))
        if failure=='tunnel0' and self.worker=='worker-0': raise OSError('synthetic tunnel failure')
        self.identity=ConnectionIdentity(kw['run_id'],self.worker,self.worker,'Fake SSH',
            kw['spec']['local_port'],'fixture-target',100+int(self.worker[-1]),'owned-start',None,smoke.now())
        self.stderr_path=kw['stderr_path']; self.stderr_path.write_bytes(b'')
        self.process=SimpleNamespace(args=['fake-ssh']); self.alive=True; self.cleaned=False
    def snapshot(self):
        return dict(process_state='PROCESS_ALIVE' if self.alive else 'PROCESS_EXITED',
            listener_state='LOCAL_PORT_LISTENING' if self.alive else 'LOCAL_PORT_CLOSED',
            exit_code=None if self.alive else 0,source='Fake owned Popen')
    def close(self,expected):
        assert expected==self.identity
        self.calls.append(('cleanup',self.worker)); self.cleaned=True; self.alive=False
        if self.failure=='cleanup': raise OSError('synthetic cleanup receipt failure')


def exercise(monkeypatch,tmp_path,failure=None,*,revalidation_mutation=None):
    calls=[]; owned=[]; contexts=[]
    ca=tmp_path/'fixture-ca'; ca.write_text('public synthetic CA fixture')
    context=ssl.create_default_context()
    original_sha=smoke.sha256_file
    monkeypatch.setattr(smoke,'sha256_file',lambda p: (('0'*64 if failure=='ca' else CA_SHA256)
        if Path(p)==ca else original_sha(p)))
    monkeypatch.setattr(smoke.ssl,'create_default_context',lambda **kw: contexts.append(kw) or context)
    monkeypatch.setattr(smoke.RemoteModelSettings,'from_yaml',lambda p:SimpleNamespace(
        resolve_api_key=lambda: '' if failure=='key' else 'synthetic-credential',resolve_ca_bundle=lambda:ca))
    specs={f'worker-{i}':dict(local_port=8000+i,remote_port=8000+i,target='root@approved',
        ssh_port=4035,identity_file='dedicated-key',known_hosts_file='strict-known-hosts') for i in (0,1)}
    monkeypatch.setattr(smoke,'specs_from_host',lambda:specs)
    def remote(action,**kw):
        calls.append(('ownership',action))
        assert action=='ownership-readonly'
        command=kw['ssh_command']
        assert 'ClearAllForwardings=yes' in command and '-L' not in command
        for flag in ('BatchMode=yes','StrictHostKeyChecking=yes','ConnectionAttempts=1'):
            assert flag in command
        record=deployment.worker_ownership_preflight(System())
        if len([c for c in calls if c[0]=='ownership'])==2:
            assert ('get','worker-0') in calls and ('tunnel','worker-1') not in calls
            if failure=='revalidation_ssh':
                return subprocess.CompletedProcess(command,255,'','synthetic revalidation SSH failure')
            if failure=='revalidation_timeout': raise TimeoutError('synthetic revalidation timeout')
            if revalidation_mutation: revalidation_mutation(record)
        if failure in ('owner0','owner1'):
            # Lie in the aggregate PASS flag: the CLI must revalidate the actual fields.
            record['workers'][int(failure[-1])]['listener_ownership_verified']=False
        if failure=='ssh': return subprocess.CompletedProcess(command,255,'','synthetic SSH failure')
        return subprocess.CompletedProcess(command,0,json.dumps(record),'')
    monkeypatch.setattr(deployment,'remote',remote)
    monkeypatch.setattr(smoke.socket,'socket',lambda *a,**kw:ClosedSocket())
    monkeypatch.setattr(smoke,'port_open',lambda p:False)
    launch=ConnectionLifecycle.launch_worker
    def factory(**kw):
        adapter=FakeSSH(calls,failure,**kw); owned.append(adapter); return adapter
    monkeypatch.setattr(ConnectionLifecycle,'launch_worker',lambda self,w,s:launch(self,w,s,factory=factory))
    def transport(**kw):
        assert kw==dict(verify=context,retries=0,trust_env=False)
        assert context.check_hostname and context.verify_mode==ssl.CERT_REQUIRED
        calls.append(('tls-policy',len([x for x in calls if x[0]=='tls-policy'])))
        def handler(request):
            worker='worker-'+str(request.url.port-8000)
            calls.append(('get',worker))
            assert request.method=='GET' and request.url.path=='/v1/models'
            assert request.headers['Authorization']=='Bearer synthetic-credential'
            assert request.extensions['timeout']==dict(connect=5,read=10,write=10,pool=10)
            if failure=='get'+worker[-1]: raise httpx2.ConnectTimeout('synthetic timeout',request=request)
            if failure=='tls0' and worker=='worker-0': raise ssl.SSLCertVerificationError('synthetic certificate rejection')
            trace=request.extensions['trace']
            trace('connection.start_tls.started',dict(ssl_context=context,timeout=5))
            trace('connection.start_tls.complete',{})
            if failure=='401' and worker=='worker-0': return httpx2.Response(401,json={'error':'unauthorized'})
            if failure=='redirect' and worker=='worker-0': return httpx2.Response(302,headers={'location':'https://other.invalid'})
            model='wrong-model' if failure=='model'+worker[-1] else 'Qwen/Qwen3-8B'
            return httpx2.Response(200,json={'data':[{'id':model}]},headers={'x-request-id':'synthetic-request'})
        return httpx2.MockTransport(handler)
    monkeypatch.setattr(smoke.httpx2,'HTTPTransport',transport)
    original_append=smoke.JsonlTraceWriter.append
    def append(self, record):
        result=original_append(self, record)
        if failure=='afterpass0' and record.get('event')=='PASS' and record.get('worker_id')=='worker-0':
            owned[0].alive=False
        return result
    monkeypatch.setattr(smoke.JsonlTraceWriter,'append',append)
    original_event=ConnectionLifecycle.event
    def event(self,kind,worker,*args,**kw):
        if failure=='audit' and kind=='provider_request_finished': raise OSError('synthetic audit write failure')
        return original_event(self,kind,worker,*args,**kw)
    monkeypatch.setattr(ConnectionLifecycle,'event',event)
    original_write=smoke.write_json
    def write(path,value):
        if failure=='revalidation_audit' and path.name=='worker1_revalidation_comparison.json':
            raise OSError('synthetic revalidation audit failure')
        return original_write(path,value)
    monkeypatch.setattr(smoke,'write_json',write)
    if failure=='manifest':
        def denied(root): raise OSError('synthetic manifest write failure')
        monkeypatch.setattr(smoke,'publish_manifest',denied)
    # The production path must never enter the old four-GET observation schedule.
    monkeypatch.setattr(smoke,'observation_window',lambda *a,**kw:pytest.fail('legacy plan called'))
    code=smoke.main(['--authorize-remote','--run-id','run','--output-root',str(tmp_path/'runs')])
    root=tmp_path/'runs/run'; summary=json.loads((root/'gate_summary.json').read_text())
    assert all(a.cleaned for a in owned)
    assert len([c for c in calls if c[0]=='ownership']) <= 2
    for worker in smoke.WORKERS:
        assert len([c for c in calls if c==('get',worker)])<=1
        assert len([c for c in calls if c==('tunnel',worker)])<=1
    assert len([c for c in calls if c[0]=='get'])<=2
    if failure!='manifest':
        manifest=json.loads((root/'artifact_manifest.json').read_text())
        assert {a['path'] for a in manifest['artifacts']}=={p.name for p in root.iterdir() if p.name!='artifact_manifest.json'}
        for a in manifest['artifacts']:
            assert smoke.sha256_file(root/a['path'])==a['sha256']
            assert (root/a['path']).stat().st_size==a['size_bytes']
    for p in root.iterdir():
        if p.is_file(): assert b'synthetic-credential' not in p.read_bytes()
    return code,summary,calls,root,contexts


def test_formal_cli_exact_serial_two_get_end_to_end(monkeypatch,tmp_path):
    code,summary,calls,root,contexts=exercise(monkeypatch,tmp_path)
    assert code==0 and summary['passed'] and summary['status']=='PASS'
    assert contexts and summary['ownership_verified'] and summary['manifest_verified']
    assert summary['worker1_revalidation_count']==1 and summary['worker1_revalidation_passed']
    comparison=json.loads((root/'worker1_revalidation_comparison.json').read_bytes())
    assert comparison['passed'] and all(comparison['checks'].values())
    assert summary['get_counts']==dict.fromkeys(smoke.WORKERS,1)
    assert [c for c in calls if c[0] in ('ownership','tunnel','get','cleanup')]==[
        ('ownership','ownership-readonly'),('tunnel','worker-0'),('get','worker-0'),
        ('ownership','ownership-readonly'),('tunnel','worker-1'),('get','worker-1'),('cleanup','worker-0'),('cleanup','worker-1')]
    seq=[json.loads(x) for x in (root/'execution_sequence.jsonl').read_text().splitlines()]
    assert [(x['event'],x['worker_id']) for x in seq]==[
        ('Ownership Preflight',None),('Tunnel','worker-0'),('GET','worker-0'),('PASS','worker-0'),
        ('Live Ownership Revalidation','worker-1'),('Ownership Comparison','worker-1'),
        ('Ownership PASS','worker-1'),('Tunnel','worker-1'),('GET','worker-1'),('PASS','worker-1'),('Cleanup',None)]
    for worker in smoke.WORKERS:
        trace=[json.loads(x) for x in (root/(worker+'_tcp_tls_trace.jsonl')).read_text().splitlines()]
        assert trace[0]['check_hostname'] is True and trace[0]['verify_mode']==ssl.CERT_REQUIRED
    assert summary['completion_requests']==summary['automatic_retries']==summary['worker_failovers']==0


@pytest.mark.parametrize('failure,tunnels,gets,status',[
    ('owner0',0,0,'BLOCKED'),('owner1',0,0,'BLOCKED'),('ssh',0,0,'BLOCKED'),
    ('key',0,0,'BLOCKED'),('ca',0,0,'BLOCKED'),('tunnel0',1,0,'BLOCKED'),
    ('get0',1,1,'BLOCKED'),('model0',1,1,'BLOCKED'),('401',1,1,'BLOCKED'),
    ('redirect',1,1,'BLOCKED'),('tls0',1,1,'BLOCKED'),('audit',1,1,'BLOCKED'),
    ('get1',2,2,'PARTIAL'),('model1',2,2,'PARTIAL'),('afterpass0',1,1,'PARTIAL'),
    ('manifest',2,2,'INCONCLUSIVE'),('cleanup',2,2,'INCONCLUSIVE')])
def test_formal_cli_fail_closed_counts(monkeypatch,tmp_path,failure,tunnels,gets,status):
    code,summary,calls,root,_=exercise(monkeypatch,tmp_path,failure)
    assert code==1 and not summary['passed'] and summary['status']==status
    assert len([c for c in calls if c[0]=='get'])==gets
    assert len([c for c in calls if c[0]=='tunnel'])==tunnels
    if tunnels<2:
        assert ('tunnel','worker-1') not in calls
        assert summary['worker1_revalidation_count']==0
    if status=='PARTIAL': assert summary['workers_passed']==['worker-0']
    if failure in ('key','ca'): assert ('ownership','ownership-readonly') not in calls
    if failure=='cleanup': assert not json.loads((root/'resource_release.json').read_text())['passed']


@pytest.mark.parametrize('field,value',[
    ('remote_container_id','foreign'),('remote_user','other'),('worker_pid',0),('alive',False),
    ('worker_process_identity',None),('worker_command_identity','foreign'),
    ('worker_command_fingerprint','invalid'),('cuda_visible_devices','2'),('physical_gpu_id',2),
    ('gpu_uuid','unknown'),('state_gpu_uuid','other'),('state_pid',123),('state_gpu_id',2),
    ('endpoint','127.0.0.1:8001'),('listener_ownership_verified',False),('model_id','other'),('model_path','other')])
def test_ownership_runtime_fields_are_independently_enforced(field,value):
    record=deployment.worker_ownership_preflight(System())
    record['workers'][0][field]=value
    with pytest.raises(ValueError): smoke.verify_ownership(record)


def test_readonly_remote_producer_has_no_deployment_script_side_effects(monkeypatch):
    seen=[]
    def run(command,**kwargs):
        seen.append((command,kwargs)); return subprocess.CompletedProcess(command,0,'{}','')
    monkeypatch.setattr(deployment.subprocess,'run',run)
    deployment.remote('ownership-readonly',ssh_command=['ssh','fixture'])
    command,kw=seen[0]
    assert command==['ssh','fixture','python3','-B','-']
    program=kw['input']
    assert 'mkdir' not in program and 'healthy()' not in program and 'curl' not in program
    assert "'--id=0,1'" in program and '--query-gpu=index,uuid' in program
    assert program.count("'--id=0,1'")==1
    # Execute exactly the shipped definitions with a Fake read-only system.
    definitions=program[:program.index('\nimport json\nresult=')]
    namespace={}; exec(definitions,namespace)
    assert namespace['worker_ownership_preflight'](System())['passed']


def test_direct_script_import_and_help_without_network(monkeypatch):
    import runpy
    monkeypatch.syspath_prepend(str(Path(smoke.__file__).parent))
    entry=runpy.run_path(str(smoke.__file__),run_name='offline_direct_entry')
    assert entry['deployment'].__file__==deployment.__file__
    with pytest.raises(SystemExit) as result: entry['main'](['--help'])
    assert result.value.code==0


@pytest.mark.parametrize('insecure',['hostname','certificate'])
def test_strict_context_refuses_weakened_tls(monkeypatch,tmp_path,insecure):
    ca=tmp_path/'ca'; ca.write_text('synthetic public CA')
    monkeypatch.setattr(smoke,'sha256_file',lambda path:CA_SHA256)
    ctx=ssl.create_default_context()
    ctx.check_hostname=False
    if insecure=='certificate': ctx.verify_mode=ssl.CERT_NONE
    monkeypatch.setattr(smoke.ssl,'create_default_context',lambda **kw:ctx)
    with pytest.raises(ValueError,match='Strict CA'): smoke.strict_context(ca)


def test_sequential_launch_retains_exact_owned_children_and_rejects_duplicate(monkeypatch,tmp_path):
    monkeypatch.setattr(smoke.socket,'socket',lambda *a,**kw:ClosedSocket())
    calls=[]; manager=ConnectionLifecycle('run',tmp_path/'connections.jsonl')
    def factory(**kw): return FakeSSH(calls,None,**kw)
    for i in (0,1):
        worker=f'worker-{i}'
        manager.launch_worker(worker,dict(local_port=8000+i,remote_port=8000+i),factory=factory)
        assert len(manager.adapters)==i+1
        if i==0: manager.start_monitor()
    with pytest.raises(ValueError):
        manager.launch_worker('worker-1',dict(local_port=8001,remote_port=8001),factory=factory)
    manager.close()
    assert all(a.cleaned for a in manager.adapters.values())
    assert len([c for c in calls if c[0]=='tunnel'])==2


def test_actual_readonly_inspector_discovers_current_pid_and_keeps_secrets_in_memory(monkeypatch,tmp_path):
    import os
    import pathlib
    project='/mnt/risk-aware-smolagents'
    system=System(); proc=tmp_path/'proc'; proc.mkdir()
    etc=tmp_path/'etc'; etc.mkdir(); (etc/'os-release').write_text('ID=ubuntu\nVERSION_ID="22.04"\n')
    state=tmp_path/'mnt/risk-aware-smolagents/logs/vllm/day2'; state.mkdir(parents=True)
    net=proc/'net'; net.mkdir(); lines=['header']
    for i,pid in enumerate((12341,23452)):
        base=proc/str(pid); (base/'fd').mkdir(parents=True)
        (base/'fd/7').symlink_to(f'socket:[{500+i}]')
        fields=['S']+['0']*18+[str(9000+i)]+['0']*3
        (base/'stat').write_text(f'{pid} (vllm) '+ ' '.join(fields))
        args=['/venv/bin/vllm','serve',project+'/remote_models/Qwen3-8B',
              '--served-model-name','Qwen/Qwen3-8B','--host','127.0.0.1','--port',str(8000+i)]
        (base/'cmdline').write_bytes(b'\0'.join(a.encode() for a in args)+b'\0')
        (base/'environ').write_bytes(f'CUDA_VISIBLE_DEVICES={i}\0VLLM_API_KEY=never-export-this-fixture\0'.encode())
        (state/f'worker-{i}.state').write_text(f'pid={pid}\nport={8000+i}\ngpu={i}\ngpu_uuid={system.gpu_rows()[i]}\n')
        lines.append(f'0: 0100007F:{8000+i:04X} 00000000:0000 0A 0 0 0 0 0 {500+i}')
    (net/'tcp').write_text('\n'.join(lines)+'\n')
    real_path=pathlib.Path
    def mapped(path):
        value=str(path)
        return real_path(tmp_path/value.lstrip('/')) if value.startswith(('/proc','/etc','/mnt')) else real_path(path)
    monkeypatch.setattr(pathlib,'Path',mapped)
    monkeypatch.setattr(os,'uname',lambda:SimpleNamespace(nodename='36f825bcc5da'))
    monkeypatch.setattr(os,'getuid',lambda:0)
    commands=[]
    def run(command,**kw):
        commands.append(command)
        assert command==['nvidia-smi','--id=0,1','--query-gpu=index,uuid','--format=csv,noheader,nounits']
        return subprocess.CompletedProcess(command,0,'\n'.join(f'{i}, {uuid}' for i,uuid in system.gpu_rows().items()).encode(),b'')
    monkeypatch.setattr(subprocess,'run',run)
    result=deployment.worker_ownership_preflight()
    assert result['passed'] and len(commands)==1
    assert [w['worker_pid'] for w in result['workers']]==[12341,23452]
    assert 'never-export-this-fixture' not in json.dumps(result)
    assert all(w['ownership_verified'] for w in result['workers'])
