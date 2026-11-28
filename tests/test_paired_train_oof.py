from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pytest

from risk_agent.agent.agent_state_adapter import canonical_hash
from risk_agent.collection.formal_features import NAMES
from risk_agent.prediction.paired_train_oof import (
    BIT, V2Preprocessor, paired_oof, folds_for, paired_bootstrap, evaluation)
from risk_agent.prediction.train_oof_inputs import check_config, join_sidecars

ROOT = Path(__file__).resolve().parents[1]


def config():return json.loads((ROOT/'configs/p2_d4_day4_paired_oof_v1.json').read_text())


def rows():
    result=[]
    for i in range(36):
        values={n:1 for n in NAMES};values.update(latest_observed_error_type='none' if i%3 else 'timeout',
            recent_model_latency=float(i%7),recent_tool_latency=float(i%5),elapsed_time_fraction=i/1000)
        values[BIT]=int(i%9==0)
        result.append({'task_id':f'fixture_{i}','template_id':f'group_{i//2}','checkpoint_id':f'cp_{i}',
            'split':'train','allowed_for_weight_fitting':True,'y_failure':i%3==0,'y_continue':int(i%3!=0),
            'features':values,'features_sha256':canonical_hash(values)})
        result[-1]['y_failure']=int(result[-1]['y_failure'])
    return result


def test_paired_folds_cover_each_row_once_and_share_base_preprocessing():
    data=rows();before=deepcopy(data)
    p,audit=paired_oof(data,config())
    assert audit['fit_invocations']==6 and audit['complete_predictions']
    assert data==before and all(np.isfinite(v).all() for v in p.values())
    held=[]
    for f in audit['folds']:
        assert not set(f['fit_templates']) & set(f['heldout_templates'])
        assert f['arms']['v1']['fit_task_ids']==f['arms']['v2']['fit_task_ids']==f['fit_task_ids']
        assert not set(f['fit_task_ids']) & set(f['heldout_task_ids'])
        held+=f['heldout_indices']
    assert sorted(held)==list(range(len(data)))


def test_new_constant_is_dropped_without_consulting_heldout_values():
    data=rows()
    for r in data:r['features'][BIT]=0;r['features_sha256']=canonical_hash(r['features'])
    pre=V2Preprocessor().fit(data[:24]);before=pre.audit()
    held=deepcopy(data[24:]);held[0]['features'][BIT]=1;held[0]['features_sha256']=canonical_hash(held[0]['features'])
    transformed=pre.transform(held)
    assert pre.dropped and transformed.shape[1]==len(pre.base.encoded_names)
    assert pre.audit()==before and BIT not in pre.audit()['encoded_features']


def test_new_bit_scaling_only_uses_training_fold():
    data=rows();pre=V2Preprocessor().fit(data[:24]);before=pre.audit()
    pre.transform(data[24:]);assert pre.audit()==before
    assert pre.mean==np.mean([r['features'][BIT] for r in data[:24]])
    assert pre.scale==np.std([r['features'][BIT] for r in data[:24]])


def test_constant_new_bit_gives_identical_v1_v2_predictions():
    data=rows()
    for r in data:r['features'][BIT]=0;r['features_sha256']=canonical_hash(r['features'])
    predictions,audit=paired_oof(data,config())
    assert np.array_equal(predictions['v1'],predictions['v2'])
    assert all(f['arms']['v2']['new_feature_dropped_constant'] for f in audit['folds'])


@pytest.mark.parametrize('bad',['UNKNOWN',None,True,0.0,2])
def test_unknown_or_noninteger_new_bit_rejected(bad):
    data=rows();data[0]['features'][BIT]=bad;data[0]['features_sha256']=canonical_hash(data[0]['features'])
    with pytest.raises(ValueError):V2Preprocessor().fit(data)


@pytest.mark.parametrize('split',['validation','test'])
def test_other_splits_cannot_fit_or_make_folds(split):
    data=rows();data[0]['split']=split
    with pytest.raises(ValueError):V2Preprocessor().fit(data)
    with pytest.raises(ValueError):folds_for(data,config()['folds'])


def test_one_class_training_is_reported_without_fallback_or_new_folds():
    data=rows()
    for r in data:r['y_failure']=0;r['y_continue']=1
    p,a=paired_oof(data,config())
    assert a['fit_invocations']==0 and not a['complete_predictions']
    assert all(f['status']=='NOT_ESTIMABLE_ONE_CLASS_TRAIN' for f in a['folds'])
    assert all(np.isnan(v).all() for v in p.values())


def test_template_bootstrap_is_paired_conditional_and_delta_exact():
    data=rows()[:-1];p={'v1':np.array([.1+.3*r['y_failure'] for r in data]),'v2':np.array([.05+.5*r['y_failure'] for r in data])}
    summary,draws=paired_bootstrap(data,p,resamples=20,seed=20261128)
    assert summary['paired_draws'] and not summary['models_refitted'] and len(draws)==20
    for d in draws:
        assert d['row_count']==sum(sum(r['template_id']==g for r in data) for g in d['templates'])
        for m,v in d['delta_v2_minus_v1'].items():
            if v is not None:assert v==d['v2'][m]-d['v1'][m]
    assert len({d['row_count'] for d in draws})>1
    for arm in summary['intervals'].values():
        for interval in arm.values():assert interval['valid_resamples']+interval['undefined_resamples']==20
    same,_=paired_bootstrap(data,p,resamples=20,seed=20261128);assert same==summary


def test_single_class_bootstrap_and_empty_precision_are_undefined():
    data=rows()
    for r in data:r['y_failure']=0;r['y_continue']=1
    summary,_=paired_bootstrap(data,{'v1':np.zeros(36),'v2':np.zeros(36)},resamples=5,seed=7)
    assert summary['intervals']['delta_v2_minus_v1']['auroc']['undefined_resamples']==5
    e=evaluation([0,1],[.1,.2]);assert e['precision'] is None and e['recall']==0
    assert e['confusion_matrix']=={'tn':1,'fp':0,'fn':1,'tp':0}
    assert len(e['calibration_bins'])==5


@pytest.mark.parametrize('change',['C','seed','threshold','bootstrap','validation','final_fit','arms'])
def test_fixed_protocol_cannot_drift(change):
    c=config();approval=json.loads((ROOT/'docs/phase2/approvals/p2_d4_day4_oof_approval_v1.json').read_text())
    if change=='C':c['logistic']['C']=1
    elif change=='seed':c['folds']['random_seed']=1
    elif change=='threshold':c['threshold']=.2
    elif change=='bootstrap':c['bootstrap']['resamples']=10
    elif change=='validation':c['validation_access']=True
    elif change=='final_fit':c['full_train_final_fit']=True
    else:c['arms'].append('lightgbm')
    with pytest.raises(ValueError):check_config(c,approval)


def test_sidecar_join_uses_qualified_labels_and_rejects_hash_or_scope_changes(tmp_path):
    from test_train_feature_backfill_v2 import sidecar_fixture
    from risk_agent.prediction.train_feature_backfill_v2 import build_sidecars,CANDIDATE_SHA256
    original,c=sidecar_fixture(tmp_path)
    sidecars,_=build_sidecars(original,contract=c,extractor_sha256='a'*64,execution_manifest_sha256='b'*64,
        approval_sha256='c'*64,targets={'qualified_train':1,'ones':0,'zeros':1,'unknowns':0,'original_v1_feature_changes':0})
    joined=join_sidecars(original,sidecars,CANDIDATE_SHA256)
    assert len(joined)==1 and joined[0]['y_failure']==original[0]['y_failure']
    changed=deepcopy(sidecars);changed[0]['features'][BIT]=1
    with pytest.raises(ValueError):join_sidecars(original,changed,CANDIDATE_SHA256)
    with pytest.raises(ValueError):join_sidecars(original,sidecars*2,CANDIDATE_SHA256)


def test_numerical_runtime_map_exception_does_not_grant_other_proc_or_symlink_reads(tmp_path):
    import subprocess,sys
    code='''import importlib.util,pathlib,sys,json
root=pathlib.Path(sys.argv[1]);out=pathlib.Path(sys.argv[2])
def direct(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
a=direct('_access',root/'src/risk_agent/prediction/train_only_access.py')
r=direct('_runtime',root/'src/risk_agent/prediction/train_oof_runtime.py')
alias=out/'maps_alias';alias.symlink_to('/proc/self/maps')
p=r.runtime_policy_class(a)(output=out,support={});p.seal();p.install()
with open('/proc/self/maps','rb') as f:assert f.readline()
try:open('/proc/cpuinfo','rb')
except PermissionError:pass
else:raise AssertionError('Unexpected proc permission')
try:alias.open('rb')
except PermissionError:pass
else:raise AssertionError('Unexpected symlink permission')
assert p.events and p.reads['runtime_system:/proc/self/maps']==1
print('PASS')
'''
    p=subprocess.run([sys.executable,'-I','-S','-B','-c',code,str(ROOT),str(tmp_path)],capture_output=True,text=True,check=True)
    assert p.stdout.strip()=='PASS'
