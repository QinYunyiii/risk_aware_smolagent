from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest

from risk_agent.monitoring.connection_lifecycle import ConnectionIdentity, ConnectionLifecycle
from risk_agent.monitoring.trajectory import JsonlTraceWriter
from scripts import run_p2_d2_connection_smoke as smoke


class Adapter:
    def __init__(self, root, worker):
        self.identity = ConnectionIdentity('run',worker,worker,'fake WSL',8000+smoke.WORKERS.index(worker),
            'fake-target',100+smoke.WORKERS.index(worker),'start-'+worker,None,smoke.now())
        self.alive=True
        self.stderr_path=root/(worker+'_ssh.stderr')
        self.stderr_path.write_text('')
        self.process=SimpleNamespace(args=['fake-ssh'])
        self.cleaned=False
    def snapshot(self):
        return dict(process_state='PROCESS_ALIVE' if self.alive else 'PROCESS_EXITED',
            listener_state='LOCAL_PORT_LISTENING' if self.alive else 'LOCAL_PORT_CLOSED',
            exit_code=None if self.alive else 255,source='fake')
    def close(self, expected):
        assert expected==self.identity
        self.cleaned=True
        self.alive=False


def setup_manager(tmp_path):
    c=ConnectionLifecycle('run',tmp_path/'connection_audit.jsonl')
    for worker in smoke.WORKERS:
        c.add(Adapter(tmp_path,worker))
    return c


def fake_time():
    state=[0.0]
    def wait(seconds): state[0]+=seconds
    return lambda:state[0],wait


def test_fixed_90_second_plan_and_two_independent_workers(tmp_path):
    c=setup_manager(tmp_path)
    clock,wait=fake_time()
    calls=[]
    duration=smoke.observation_window(c,lambda w:calls.append((clock(),w)),clock=clock,wait=wait)
    assert duration==90
    assert calls==[(0,'worker-0'),(0,'worker-1'),(45,'worker-0'),(45,'worker-1')]
    assert len({a.identity.pid for a in c.adapters.values()})==2
    assert len({a.identity.tunnel_id for a in c.adapters.values()})==2
    c.close()
    assert all(a.cleaned for a in c.adapters.values())


def test_only_get_models_and_external_audit(tmp_path):
    c=setup_manager(tmp_path)
    seen=[]
    def handler(request):
        seen.append((request.method,request.url.path))
        return httpx2.Response(200,json={'data':[{'id':'Qwen/Qwen3-8B'}]})
    writer=JsonlTraceWriter(tmp_path/'worker_probe_audit.jsonl')
    with httpx2.Client(transport=httpx2.MockTransport(handler)) as client:
        result=smoke.get_models(c,'worker-0',client,'https://localhost:8000/v1/models','fixture-key',writer)
        assert result['state']=='API_REQUEST_SUCCESS'
        with pytest.raises(ValueError,match='Only the frozen GET'):
            smoke.get_models(c,'worker-0',client,'https://localhost:8000/v1/chat/completions','fixture-key',writer)
    assert seen==[('GET','/v1/models')]
    assert not (tmp_path/'checkpoints').exists()
    assert 'fixture-key' not in (tmp_path/'worker_probe_audit.jsonl').read_text()
    events=[json.loads(x) for x in c.path.read_text().splitlines()]
    starts=[r for r in events if r['event']=='provider_request_started']
    ends=[r for r in events if r['event']=='provider_request_finished']
    assert len(starts)==len(ends)==1
    assert starts[0]['provider_request_id']==ends[0]['provider_request_id']
    c.close()


@pytest.mark.parametrize('failure',['exit','port','identity','api','model'])
def test_failure_stops_without_retry_or_worker_failover(tmp_path,failure):
    c=setup_manager(tmp_path)
    clock,wait=fake_time()
    calls=[]
    def probe(worker):
        calls.append(worker)
        if worker=='worker-1':
            if failure=='exit':
                c.adapters[worker].alive=False
            elif failure=='port':
                c.adapters[worker].snapshot=lambda:dict(process_state='PROCESS_ALIVE',listener_state='LOCAL_PORT_CLOSED')
            elif failure=='identity':
                c.adapters[worker].identity=replace(c.adapters[worker].identity,process_start_identity='foreign')
            else:
                raise ConnectionError('synthetic '+failure)
    with pytest.raises((ConnectionError,RuntimeError)):
        smoke.observation_window(c,probe,clock=clock,wait=wait)
    assert calls==['worker-0','worker-1']
    assert clock()==0
    if failure=='identity':
        c.adapters['worker-1'].identity=c.identities['worker-1']
    c.close()


@pytest.mark.parametrize('status',[401,503])
def test_http_error_saved_once_with_cleanup(tmp_path,status):
    c=setup_manager(tmp_path)
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx2.Response(status,json={'error':'synthetic'})
    writer=JsonlTraceWriter(tmp_path/'probes.jsonl')
    with httpx2.Client(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(httpx2.HTTPStatusError):
            smoke.get_models(c,'worker-1',client,'https://localhost:8001/v1/models','fixture',writer)
    assert len(calls)==1
    assert json.loads((tmp_path/'probes.jsonl').read_text())['http_status']==status
    assert c.active=={}
    c.close()


def test_cli_denied_before_any_connection_or_run(tmp_path):
    with pytest.raises(RuntimeError,match='authorization'):
        smoke.main(['--run-id','denied','--output-root',str(tmp_path/'runs')])
    assert not (tmp_path/'runs').exists()


# The formal CLI now uses the two-GET protocol. Its full entrypoint regression
# (including partial launch cleanup) lives in test_connection_smoke_integration.py.
# The historical 90-second helper expectations above remain unchanged.
