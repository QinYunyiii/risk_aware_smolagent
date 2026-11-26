"""Live revalidation exercised through the production CLI; all IO is Fake."""
import json
from pathlib import Path
import runpy

import pytest

# Reuse the existing complete CLI harness, never replace its ownership comparator.
exercise=runpy.run_path(str(Path(__file__).with_name('test_connection_smoke_integration.py')))['exercise']


def test_worker1_success_requires_second_remote_preflight_in_order(monkeypatch,tmp_path):
    code,summary,calls,root,_=exercise(monkeypatch,tmp_path)
    assert code==0 and summary['status']=='PASS'
    assert summary['worker1_revalidation_count']==1 and summary['worker1_revalidation_passed']
    assert [c for c in calls if c[0] in ('ownership','get','tunnel','cleanup')]==[
        ('ownership','ownership-readonly'),('tunnel','worker-0'),('get','worker-0'),
        ('ownership','ownership-readonly'),('tunnel','worker-1'),('get','worker-1'),
        ('cleanup','worker-0'),('cleanup','worker-1')]
    comparison=json.loads((root/'worker1_revalidation_comparison.json').read_bytes())
    assert comparison['initial']==comparison['current'] and comparison['passed']
    sequence=[json.loads(line) for line in (root/'execution_sequence.jsonl').read_text().splitlines()]
    assert [(e['event'],e['worker_id']) for e in sequence]==[
        ('Ownership Preflight',None),('Tunnel','worker-0'),('GET','worker-0'),('PASS','worker-0'),
        ('Live Ownership Revalidation','worker-1'),('Ownership Comparison','worker-1'),
        ('Ownership PASS','worker-1'),('Tunnel','worker-1'),('GET','worker-1'),('PASS','worker-1'),('Cleanup',None)]
    assert all(e['timestamp'] for e in sequence)
    assert (root/'worker1_revalidation_ownership_ssh_command.json').exists()
    assert (root/'worker1_revalidation_ownership_ssh_result.json').exists()
    assert (root/'worker1_revalidation_ownership.json').exists()
    assert summary['get_counts']=={'worker-0':1,'worker-1':1}
    assert summary['automatic_retries']==summary['worker_failovers']==summary['completion_requests']==0


@pytest.mark.parametrize('failure',['get0','model0','401','tls0','tunnel0','afterpass0'])
def test_worker0_failure_never_revalidates_or_starts_worker1(monkeypatch,tmp_path,failure):
    code,summary,calls,root,_=exercise(monkeypatch,tmp_path,failure)
    assert code==1 and not summary['passed']
    assert len([c for c in calls if c[0]=='ownership'])==1
    assert summary['worker1_revalidation_count']==0
    assert ('tunnel','worker-1') not in calls and ('get','worker-1') not in calls
    assert not (root/'worker1_revalidation_ownership.json').exists()


@pytest.mark.parametrize('changes',[
    {'worker_pid':1901,'state_pid':1901},
    {'worker_process_identity':'different-start-ticks'},
    {'physical_gpu_id':0},
    {'gpu_uuid':'GPU-aaaaaaaa-1111-2222-3333-444444444444',
     'state_gpu_uuid':'GPU-aaaaaaaa-1111-2222-3333-444444444444'},
    {'endpoint':'127.0.0.1:8002'},
    {'listener_ownership_verified':False},
    {'worker_command_fingerprint':'a'*64},
    {'worker_command_identity':'foreign-command'},
    {'remote_container_id':'foreign-container'},
    {'cuda_visible_devices':'0'},
    {'model_id':'wrong-model'},
    {'alive':False},
    {'ownership_verified':False},
])
def test_worker1_live_changes_block_tunnel_and_get(monkeypatch,tmp_path,changes):
    def mutate(record): record['workers'][1].update(changes)
    code,summary,calls,root,_=exercise(monkeypatch,tmp_path,revalidation_mutation=mutate)
    assert code==1 and summary['status']=='PARTIAL' and not summary['passed']
    assert summary['worker1_revalidation_count']==1 and not summary['worker1_revalidation_passed']
    assert len([c for c in calls if c[0]=='ownership'])==2
    assert summary['workers_passed']==['worker-0']
    assert [c for c in calls if c[0]=='get']==[('get','worker-0')]
    assert [c for c in calls if c[0]=='tunnel']==[('tunnel','worker-0')]
    comparison=json.loads((root/'worker1_revalidation_comparison.json').read_bytes())
    assert not comparison['passed'] and any(not value for value in comparison['checks'].values())
    for field in changes: assert not comparison['checks'][field]
    if set(changes) in ({'worker_pid','state_pid'},{'worker_process_identity'},
                       {'worker_command_fingerprint'},{'gpu_uuid','state_gpu_uuid'}):
        # New snapshot is independently valid, but differs from the original owner.
        assert comparison['current_snapshot_valid']


@pytest.mark.parametrize('field',[
    'remote_container_id','remote_user','ubuntu_version','worker_id','worker_pid','alive',
    'worker_process_identity','worker_command_identity','worker_command_fingerprint',
    'cuda_visible_devices','physical_gpu_id','gpu_uuid','state_gpu_uuid','state_pid','state_gpu_id',
    'endpoint','listener_ownership_verified','model_id','model_path','ownership_verified'])
def test_worker1_any_missing_ownership_field_fails_closed(monkeypatch,tmp_path,field):
    def mutate(record): record['workers'][1].pop(field)
    code,summary,calls,root,_=exercise(monkeypatch,tmp_path,revalidation_mutation=mutate)
    assert code==1 and summary['status']=='PARTIAL'
    assert len([c for c in calls if c[0]=='ownership'])==2
    assert ('tunnel','worker-1') not in calls and ('get','worker-1') not in calls
    comparison=json.loads((root/'worker1_revalidation_comparison.json').read_bytes())
    assert not comparison['checks'][field] and not comparison['passed']


@pytest.mark.parametrize('failure',['revalidation_ssh','revalidation_timeout','revalidation_audit'])
def test_worker1_unverifiable_or_unpersisted_evidence_never_starts_tunnel(monkeypatch,tmp_path,failure):
    code,summary,calls,root,_=exercise(monkeypatch,tmp_path,failure)
    assert code==1 and summary['status']=='PARTIAL'
    assert len([c for c in calls if c[0]=='ownership'])==2
    assert summary['worker1_revalidation_count']==1 and not summary['worker1_revalidation_passed']
    assert ('tunnel','worker-1') not in calls and ('get','worker-1') not in calls


@pytest.mark.parametrize('failure',['get1','model1'])
def test_worker1_revalidation_pass_does_not_hide_its_http_failure(monkeypatch,tmp_path,failure):
    code,summary,calls,root,_=exercise(monkeypatch,tmp_path,failure)
    assert code==1 and summary['status']=='PARTIAL'
    assert summary['worker1_revalidation_passed'] and summary['worker1_revalidation_count']==1
    assert len([c for c in calls if c[0]=='ownership'])==2
    for worker in ('worker-0','worker-1'):
        assert calls.count(('tunnel',worker))==calls.count(('get',worker))==1
