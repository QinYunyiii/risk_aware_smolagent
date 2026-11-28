from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from risk_agent.prediction.train_feature_backfill_v2 import (
    CANDIDATE_SHA256, FRAMEWORK_SUFFIX, build_sidecars, check_approval, public_projection, public_value)
from risk_agent.prediction.train_public_signals_v2 import CandidateContract

ROOT = Path(__file__).resolve().parents[1]


def view():
    return {"split": "train", "task_text": "Read the current value.", "tool_registry": [
        {"name": "lookup", "description": "Read a value", "inputs": {"key": {"type": "string"}}, "output_type": "any"}],
        "calls": [{"id": "call-1", "name": "lookup", "arguments": {"key": "item"}}],
        "responses": [{"text": "Observation:\n42", "call_id": None}],
        "cutoff_step": 1, "cutoff_verified": True, "last_role": "tool-response"}


def error(v, message, suffix=True):
    c = v["calls"][-1]
    text = f"Call id: {c['id']}\nError:\nError executing tool '{c['name']}' with arguments {c['arguments']}: VisibleToolError: {message}"
    if suffix:text += "\n" + "\n".join(FRAMEWORK_SUFFIX)
    v["responses"][-1] = {"text": text + "\n", "call_id": None}
    return v


@pytest.mark.parametrize("text", ["42", "null", "false", "[]", '{"x":1}', '"completion status is unknown"'])
def test_valid_normal_json_has_zero_including_null_and_marker_strings(text):
    v=view();v['responses'][0]['text']='Observation:\n'+text
    assert public_value(v)['value']==0


@pytest.mark.parametrize("message,value", [('completion status is unknown',1),
    ('Tool request failed; Completion Status Is Unknown. Query before write.',1),
    ('Timeout: completion status is unknown.',1),('No progress: operation was not performed.',0),
    ('completion status is unknownish',0)])
def test_public_error_case_and_word_boundaries_reuse_parser(message,value):
    assert public_value(error(view(),message))['value']==value


def test_arguments_echo_not_used_as_error_body_and_inputs_unchanged():
    v=view();v['calls'][0]['arguments']['key']='completion status is unknown';error(v,'No progress: no change.')
    before=deepcopy(v)
    assert public_value(v)['value']==0 and v==before


@pytest.mark.parametrize("kind", ['json','nan','suffix','multiline','prefix','call_id','conflicting_ids',
    'duplicate_calls','duplicate_responses','last_role','count'])
def test_ambiguous_boundaries_are_unknown_without_zero_imputation(kind):
    v=error(view(),'completion status is unknown')
    if kind=='json':v['responses'][0]['text']='Observation:\n{broken'
    elif kind=='nan':v['responses'][0]['text']='Observation:\nNaN'
    elif kind=='suffix':v['responses'][0]['text']+='Framework: completion status is unknown\n'
    elif kind=='multiline':v['responses'][0]['text']=v['responses'][0]['text'].replace('VisibleToolError: ','VisibleToolError: multiline\n')
    elif kind=='prefix':v['responses'][0]['text']=v['responses'][0]['text'].replace("tool 'lookup'","tool 'other'")
    elif kind=='call_id':v['responses'][0]['text']=v['responses'][0]['text'].replace('Call id: call-1','Call id: wrong')
    elif kind=='conflicting_ids':v['responses'][0]['call_id']='wrong'
    elif kind=='duplicate_calls':v['calls']*=2;v['responses']*=2
    elif kind=='duplicate_responses':
        v['calls'].append({'id':'call-2','name':'lookup','arguments':{'key':'another'}});v['responses']*=2
    elif kind=='last_role':v['last_role']='assistant'
    else:v['responses']=[]
    assert public_value(v)['value']=='UNKNOWN'


def test_empty_error_suffix_is_supported():
    assert public_value(error(view(),'completion status is unknown',suffix=False))['value']==1


def test_multiple_calls_require_ids_and_received_order_selects_corresponding_call():
    v=view();v['calls'].append({'id':'call-2','name':'lookup','arguments':{'key':'other'}})
    v['responses']=[{'text':'Observation:\n1','call_id':'call-2'}, {'text':'Observation:\n2','call_id':'call-1'}]
    assert public_value(v)['value']==0
    v['responses'][1]['call_id']=None
    assert public_value(v)['value']=='UNKNOWN'


@pytest.mark.parametrize('field',['label','future_outcome','fault_type','verifier','template_id','task_id'])
def test_private_future_and_identity_inputs_are_rejected(field):
    v=view();v[field]='not public projection'
    with pytest.raises(ValueError):public_value(v)


@pytest.mark.parametrize('split',['validation','test'])
def test_non_train_input_rejected(split):
    v=view();v['split']=split
    with pytest.raises(ValueError):public_value(v)
    with pytest.raises(ValueError):public_projection({'split':split})


def test_unverified_or_future_cutoff_rejected():
    v=view();v['cutoff_verified']=False
    with pytest.raises(ValueError):public_value(v)


@pytest.mark.parametrize('kind',['candidate','operation','model','collection','production','stop'])
def test_approval_cannot_expand_to_other_stages(kind):
    approval=json.loads((ROOT/'docs/phase2/approvals/p2_d4_day3_feature_v2_approval_v1.json').read_text())
    if kind=='candidate':approval['candidate_manifest_sha256']='0'*64
    elif kind=='operation':approval['allowed_operation']='day4'
    elif kind=='model':approval['model_comparison_authorized']=True
    elif kind=='collection':approval['new_collection_authorized']=True
    elif kind=='production':approval['production_activation_authorized']=True
    else:approval['stop_after_day3']=False
    with pytest.raises(ValueError):check_approval(approval,CANDIDATE_SHA256)


def test_valid_separate_approval_does_not_mutate_candidate():
    approval=json.loads((ROOT/'docs/phase2/approvals/p2_d4_day3_feature_v2_approval_v1.json').read_text())
    check_approval(approval,CANDIDATE_SHA256)
    config=json.loads((ROOT/'configs/formal_decision_features_v2_candidate.json').read_text())
    assert config['authorization']['historical_backfill'] is False


def sidecar_fixture(tmp_path):
    from test_train_only_signal_audit import fixture_train
    from risk_agent.prediction.train_evidence import load_train,digest
    proof,*_=fixture_train(tmp_path)
    rows,_=load_train(tmp_path,proof,digest(proof.read_bytes()))
    rows=[{**r,'split':'train'} for r in rows]
    names=tuple(rows[0]['features'])+('public_completion_unknown',)
    c=CandidateContract('formal-decision-features-v2',names,CANDIDATE_SHA256,'b'*64,'c'*64)
    return rows,c


def test_synthetic_sidecar_roundtrip_preserves_v1_hash_and_unknown_blocks_release(tmp_path):
    from risk_agent.prediction.train_evidence import canonical
    rows,c=sidecar_fixture(tmp_path);before=deepcopy(rows)
    kwargs={'contract':c,'extractor_sha256':'d'*64,'execution_manifest_sha256':'e'*64,
        'approval_sha256':'f'*64,'targets':{'qualified_train':1,'ones':0,'zeros':1,'unknowns':0,'original_v1_feature_changes':0}}
    sidecars,summary=build_sidecars(rows,**kwargs)
    assert summary['gate']=='PASS' and rows==before
    s=sidecars[0];assert s['original_v1_features_sha256']==canonical(rows[0]['features'])
    assert {n:s['features'][n] for n in rows[0]['features']}==rows[0]['features']
    assert s['sidecar_record_sha256']==canonical({k:v for k,v in s.items() if k!='sidecar_record_sha256'})
    assert json.loads(json.dumps(sidecars))==sidecars and 'y_failure' not in s
    rows[0]['payload']['canonical_next_model_input'][-1]['content']='Observation:\nbroken'
    bad,blocked=build_sidecars(rows,**kwargs)
    assert blocked['gate']=='BLOCKED' and not blocked['complete_v2_dataset_eligible']
    assert bad[0]['public_completion_unknown']=='UNKNOWN'


def test_duplicate_primary_cannot_inflate_backfilled_sample_size(tmp_path):
    rows,c=sidecar_fixture(tmp_path)
    with pytest.raises(ValueError,match='Unique'):
        build_sidecars(rows*2,contract=c,extractor_sha256='a'*64,execution_manifest_sha256='b'*64,
            approval_sha256='c'*64,targets={})
