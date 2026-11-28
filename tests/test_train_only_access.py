"""Real opens happen only in isolated children over synthetic temporary files."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
ACCESS = ROOT / "src/risk_agent/prediction/train_only_access.py"
CHILD = r'''
import importlib.util,json,os,pathlib,socket,subprocess,sys
module_path,case_path,operation=sys.argv[1:]
spec=importlib.util.spec_from_file_location('_isolated_guard',module_path)
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
case=json.loads(pathlib.Path(case_path).read_text())
base=pathlib.Path(case['base']);source=base/'allowed.json';out=base/'output'
p=m.ReadPolicy(output=out,support={str(base/'support.py'):None},outputs={str(out/'declared.json')})
p.enroll(source,case['sha']);p.seal();p.install()
blocked=False;result=None
try:
    if operation=='allowed':result=p.json(source)
    elif operation=='support':result=(base/'support.py').read_text()
    elif operation=='output':p.write_json('declared.json',{'ok':True});result='written'
    elif operation=='write_input':source.write_text('changed')
    elif operation=='rewrite_output':
        p.write_json('declared.json',{});p.write_json('declared.json',{})
    elif operation=='undeclared_output':(out/'unknown.json').write_text('{}')
    elif operation=='descriptor':open(0)
    elif operation=='traversal':(base/'other'/'..'/'allowed.json').read_bytes()
    elif operation=='socket':
        s=socket.socket();s.connect(('127.0.0.1',9))
    elif operation=='subprocess':subprocess.run([sys.executable,'-c','raise SystemExit(99)'])
    elif operation=='system':os.system('exit 99')
    elif operation=='late_enroll':p.enroll(base/'outside.json','a'*64)
    elif operation=='remove':source.unlink()
    else:(base/case['target']).read_bytes()
except (PermissionError,RuntimeError,ValueError):blocked=True
clean=True
try:p.assert_clean()
except RuntimeError:clean=False
print(json.dumps({'blocked':blocked,'clean':clean,'events':p.events,'result':result}))
'''


def child(tmp_path, operation, target="outside.json", tamper=False):
    out = tmp_path / "output"; out.mkdir()
    source = tmp_path / "allowed.json"; source.write_text('{"qualified":true}')
    (tmp_path / "support.py").write_text('"trusted support source"')
    case = {"base": str(tmp_path), "target": target, "sha": hashlib.sha256(source.read_bytes()).hexdigest()}
    config = tmp_path / "case.json"; config.write_text(json.dumps(case))
    forbidden = tmp_path / target
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_text('"forbidden synthetic content"')
    if tamper:source.write_text('{"qualified":false}')
    result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", CHILD, str(ACCESS), str(config), operation],
        capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize("operation", ["allowed", "support", "output"])
def test_declared_source_support_and_exclusive_output(tmp_path, operation):
    result = child(tmp_path, operation)
    assert not result["blocked"] and result["clean"] and result["events"] == []


@pytest.mark.parametrize("target", ["outside.json", "outside.py", "docs/validation_copy.json",
    "runs/formal_validation/record.json", "runs/test/record.json", "mixed_snapshot/validation.jsonl",
    "continue/outcome.json", "hidden/fault_adapter_state.json", "output/copied_data.json"])
def test_unregistered_contents_rejected_inside_and_outside_runs(tmp_path, target):
    result = child(tmp_path, "read", target)
    assert result["blocked"] and not result["clean"] and result["events"][-1]["reason"] == "unregistered read"


@pytest.mark.parametrize("operation", ["write_input", "rewrite_output", "undeclared_output", "descriptor",
    "traversal", "socket", "subprocess", "system", "remove"])
def test_mutation_escape_network_and_child_process_blocked(tmp_path, operation):
    result = child(tmp_path, operation)
    assert result["blocked"] and not result["clean"]


def test_caught_denial_cannot_publish_pass(tmp_path):
    result = child(tmp_path, "read")
    assert result["blocked"] and result["clean"] is False


def test_hash_mismatch_and_sealed_enrollment(tmp_path):
    bad = child(tmp_path, "allowed", tamper=True)
    assert bad["blocked"] and not bad["clean"]
    other = tmp_path / "other_case"; other.mkdir()
    assert child(other, "late_enroll")["blocked"]


def test_symlink_and_parent_escape_enrollment_rejected(tmp_path):
    from risk_agent.prediction.train_only_access import ReadPolicy
    real = tmp_path / "real.json"; real.write_text('{}')
    alias = tmp_path / "alias.json"; alias.symlink_to(real)
    policy = ReadPolicy(output=tmp_path, support={})
    with pytest.raises(ValueError, match="Symlink"):policy.enroll(alias, 'a' * 64)
    with pytest.raises(ValueError, match="traversal"):policy.enroll(tmp_path / 'other' / '..' / 'real.json', 'a' * 64)


def test_symlink_swapped_after_registration_rejected(tmp_path):
    code = CHILD.replace("p.enroll(source,case['sha']);p.seal();p.install()",
        "p.enroll(source,case['sha']);source.unlink();source.symlink_to(base/'outside.json');p.seal();p.install()")
    out = tmp_path / "output"; out.mkdir()
    source = tmp_path / 'allowed.json'; source.write_text('{}')
    (tmp_path / 'outside.json').write_text('[]')
    config = tmp_path / 'case.json';config.write_text(json.dumps({'base':str(tmp_path),'sha':hashlib.sha256(source.read_bytes()).hexdigest()}))
    result = subprocess.run([sys.executable,'-I','-S','-B','-c',code,str(ACCESS),str(config),'allowed'],capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)['blocked']


def test_day1_qualification_with_real_loader_and_synthetic_cohort(tmp_path):
    from test_train_only_signal_audit import fixture_train
    from risk_agent.prediction.train_only_access import ReadPolicy
    from risk_agent.prediction.train_day1_audit import run_day1
    from risk_agent.prediction.train_evidence import canonical, digest
    proof, spec, *_ = fixture_train(tmp_path)
    base = Path(spec['directory'])
    task = {'previous_record_sha256':None,'split':'train','source_attempt_index':1,'task_id':'task'}
    task['record_sha256'] = canonical(task)
    tasks = base/'records/tasks.jsonl'; tasks.write_text(json.dumps(task)+'\n')
    release_path = base/'train_eligibility_release.json';release = json.loads(release_path.read_text())
    release['repeats_are_not_additional_independent_examples'] = True;release_path.write_text(json.dumps(release))
    manifest = base/'artifact_manifest.json';data=json.loads(manifest.read_text())
    for entry in data['files']:
        path=base/entry['path'];entry.update(sha256=digest(path.read_bytes()),size_bytes=path.stat().st_size)
    data['files'].append({'path':'records/tasks.jsonl','size_bytes':tasks.stat().st_size,'sha256':digest(tasks.read_bytes())})
    manifest.write_text(json.dumps(data));spec['artifact_manifest_sha256']=digest(manifest.read_bytes())
    info=json.loads(proof.read_text());info['runs']=[spec];info['release_sha256']=digest(release_path.read_bytes());proof.write_text(json.dumps(info))
    profile={'train_proof':str(proof.relative_to(tmp_path)),'train_proof_sha256':digest(proof.read_bytes()),
        'expected':{'source_attempts':1,'qualified_primaries':1,'successes':1,'failures':0,'templates':1,'censored':0,'repeat_manifest_branches':0},
        'historical_repeat_label_disagreements':0,'repeat_label_evidence':'synthetic test only','integrity_scope':'authorized only'}
    policy=ReadPolicy(output=tmp_path/'output',support={})
    result=run_day1(policy,tmp_path,profile)
    assert result['gate']=='PASS' and result['baseline']['qualified_primaries']==1
    assert policy.sealed and result['formal_fit_count']==0


def test_wrong_qualification_profile_does_not_pass(tmp_path):
    from risk_agent.prediction.train_only_access import ReadPolicy
    from risk_agent.prediction.train_day1_audit import enroll_train
    proof=tmp_path/'runs/proof/train_proof.json';proof.parent.mkdir(parents=True);proof.write_text('{}')
    with pytest.raises(ValueError,match='SHA-256'):
        enroll_train(ReadPolicy(output=tmp_path,support={}),tmp_path,{'train_proof':str(proof),'train_proof_sha256':'0'*64})


@pytest.mark.parametrize('bad_hash',[False,True])
def test_pure_runtime_reuses_frozen_code_without_model_or_network_packages(bad_hash):
    code = '''import importlib.util,json,pathlib,sys,hashlib
root=pathlib.Path(sys.argv[1]);bad=sys.argv[2]=='True'
def module(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
access=module('_test_access',root/'src/risk_agent/prediction/train_only_access.py')
runtime=module('_test_runtime',root/'src/risk_agent/prediction/train_day1_runtime.py')
pins=json.loads((root/'configs/p2_d4_day1_access_v1.json').read_text())['frozen_reuse_sha256']
if bad:pins[next(iter(pins))]='0'*64
try:runtime.prepare_runtime(root,pins,access)
except ValueError:
 assert bad;print('EXPECTED_HASH_BLOCK')
else:
 assert not bad
 from risk_agent.agent.agent_state_adapter import canonical_hash
 value={'nested':[True,None,'中文']}
 assert canonical_hash(value)==hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
 assert not ({'numpy','sklearn','smolagents','openai','urllib3'} & set(sys.modules))
 print('PURE_RUNTIME_PASS')
'''
    p=subprocess.run([sys.executable,'-I','-S','-B','-c',code,str(ROOT),str(bad_hash)],capture_output=True,text=True,check=True)
    assert p.stdout.strip()==('EXPECTED_HASH_BLOCK' if bad_hash else 'PURE_RUNTIME_PASS')
