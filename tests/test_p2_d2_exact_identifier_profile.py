"""Versioned T3-only prompt presentation; frozen catalog/model/restore semantics."""
import hashlib
import json
from pathlib import Path
import runpy

import pytest
import yaml

from risk_agent.collection.p2_d2_completion_budget import (
    CompletionBudgetConfigurationError, EXACT_IDENTIFIER_VERSION, EXACT_IDENTIFIER_TASK,
    EXACT_IDENTIFIER_PROMPT, load_exact_identifier_prompt_profile,
)
from risk_agent.collection.p2_d2_real_qwen_gate import P2D2RealQwenGateRunner, FIXED_TASK_IDS
from scripts import run_p2_d2_full_gate_1024 as cli
from scripts.run_p2_d2_real_qwen_gate import EXPECTED_WEIGHTS

helpers=runpy.run_path(str(Path(__file__).with_name('test_p2_d2_full_gate_1024.py')))
PROFILE=Path('configs/p2_d2_t3_exact_identifier_v2.yaml')


def test_registered_profile_is_only_approved_presentation():
    tasks=helpers['_tasks']()
    metadata=load_exact_identifier_prompt_profile(PROFILE,tasks)
    assert metadata['experiment_config_version']==EXACT_IDENTIFIER_VERSION
    assert metadata['task_id']==EXACT_IDENTIFIER_TASK and metadata['prompt']==EXACT_IDENTIFIER_PROMPT
    original=next(t for t in tasks if t.task_id==EXACT_IDENTIFIER_TASK)
    assert metadata['original_prompt_sha256']==hashlib.sha256(original.prompt.encode()).hexdigest()
    assert metadata['revised_prompt_sha256']!=metadata['original_prompt_sha256']
    assert metadata['baseline_full_gate_sha256']==hashlib.sha256(Path('configs/p2_d2_full_gate_1024.yaml').read_bytes()).hexdigest()


@pytest.mark.parametrize('key,value',[
    ('task_id','p2_data_region_units_sum_001'),('prompt','Use order 83001'),
    ('original_prompt_sha256','0'*64),('baseline_full_gate_sha256','0'*64),
    ('experiment_config_version','unregistered'),('temperature',1),
])
def test_unapproved_profile_changes_are_rejected(tmp_path,key,value):
    profile=yaml.safe_load(PROFILE.read_text());profile[key]=value
    p=tmp_path/'invalid.yaml';p.write_text(yaml.safe_dump(profile))
    with pytest.raises(CompletionBudgetConfigurationError):load_exact_identifier_prompt_profile(p,helpers['_tasks']())


def test_full_production_cli_executes_new_profile_with_five_checkpoints(monkeypatch,tmp_path):
    helpers['_settings_1024'](tmp_path,monkeypatch)
    requests=[]
    helpers['_install_transport'](monkeypatch,helpers['_responses_for_all_tasks'](tmp_path),requests)
    proof=dict(remote_operations_authorized=True,https_service_authorized=True,
        service_ownership_verified=True,gpu_mapping_verified=True,model_version_verified=True,
        weight_identity_verified=True,gpu_samples_utc=['2026-11-26T00:00:00+00:00','2026-11-26T00:01:01+00:00'],
        worker_gpu_mapping={'worker-0':0,'worker-1':1},model_weight_identity=EXPECTED_WEIGHTS,
        versions=dict(vllm='0.34.0',torch='2.14.0+cu132',transformers='5.22.2'))
    evidence=tmp_path/'evidence.json';evidence.write_text(json.dumps(proof))
    spec=tmp_path/'spec.json';spec.write_text('{}')
    monkeypatch.setattr(cli.ConnectionLifecycle,'launch',lambda self,specs:None)
    monkeypatch.setattr(cli,'_api_preflight',lambda *a,**kw:[])
    assert cli.main(['--mode',cli.REMOTE_MODE,'--authorize-remote','--run-id','exact_identifier_fake',
        '--output-root',str(tmp_path/'runs'),'--remote-evidence',str(evidence),'--connection-spec',str(spec),
        '--task-prompt-profile',str(PROFILE)])==0
    root=tmp_path/'runs/exact_identifier_fake'
    summary=json.loads((root/'gate_summary.json').read_bytes())
    manifest=json.loads((root/'run_manifest.json').read_bytes())
    assert summary['passed'] and summary['completed_task_count']==5 and len(requests)==15
    assert summary['experiment_config_version']==EXACT_IDENTIFIER_VERSION
    assert manifest['task_prompt_profile']['prompt']==EXACT_IDENTIFIER_PROMPT
    assert manifest['task_prompt_profile']['task_id']==EXACT_IDENTIFIER_TASK
    assert manifest['baseline_experiment_config_version']=='p2-d2-full-gate-1024-v1'
    tasks={t.task_id:t for t in helpers['_tasks']()}
    def strings(value):
        if isinstance(value,str):yield value
        elif isinstance(value,list):
            for item in value:yield from strings(item)
        elif isinstance(value,dict):
            for item in value.values():yield from strings(item)
    for i,taskid in enumerate(FIXED_TASK_IDS):
        body=json.loads(requests[i*3].content)
        texts=list(strings(body['messages']))
        expected=EXACT_IDENTIFIER_PROMPT if taskid==EXACT_IDENTIFIER_TASK else tasks[taskid].prompt
        assert any(expected in text for text in texts)  # smolagents prefixes user text with New task.
        if taskid==EXACT_IDENTIFIER_TASK:assert not any(tasks[taskid].prompt in text for text in texts)
        assert body['max_tokens']==1024 and body['temperature']==0 and body['seed']==20261118
    assert summary['six_hash_matches']==summary['exact_input_matches']==summary['real_restore_requests']==5
    assert summary['replay_failures']==summary['automatic_model_retries']==summary['worker_failovers']==0
    attempts=[json.loads(line) for line in (root/'task_attempts.jsonl').read_text().splitlines()]
    assert attempts[2]['api_write_audit']['idempotency_key_count']==1
    for row in json.loads((root/'artifact_manifest.json').read_bytes())['artifacts']:
        assert hashlib.sha256((root/row['path']).read_bytes()).hexdigest()==row['sha256']


def test_runner_refuses_unversioned_or_other_task_overrides(monkeypatch,tmp_path):
    settings=helpers['_settings_1024'](tmp_path,monkeypatch)
    common=dict(settings=settings,run_directory=tmp_path/'run',run_id='run',catalog_hash='a'*64,
        split_hash='b'*64,verifier_content_hash='c'*64)
    with pytest.raises(ValueError,match='versioned T3'):
        P2D2RealQwenGateRunner(**common,task_prompts={EXACT_IDENTIFIER_TASK:EXACT_IDENTIFIER_PROMPT})
    with pytest.raises(ValueError,match='versioned T3'):
        P2D2RealQwenGateRunner(**common,experiment_config_version=EXACT_IDENTIFIER_VERSION,
                              task_prompts={FIXED_TASK_IDS[0]:'changed'})
