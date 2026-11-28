from pathlib import Path
import json,sys
import pytest
from risk_agent.collection.aggregate_target_policy import AggregateFaultInjector,POLICY

def CandidateInjector(config):
 return AggregateFaultInjector(config,domain='data_analysis')
from risk_agent.collection.formal_faults import FormalFaultConfig,FormalFaultInjector,NoProgressFailure
from risk_agent.faults import ToolOperation
from risk_agent.faults.injector import InjectedServiceUnavailableError
from risk_agent.tools.data_analysis_tools import DataAnalysisTools


def config(kind='F2_API_503',policy=True):
 params={'variant':'visible_no_progress_without_execution'}if kind=='F5_REPEATED_ACTION'else{}
 if policy:params['target_match_policy']=POLICY
 return FormalFaultConfig.from_case(dict(fault_id='offline_proposal',fault_type=kind,fault_seed=1,target_tool='aggregate_data',call_indices=[1],parameters=params))

def invoke(injector,name,calls):
 return injector.execute(ToolOperation(name,True),{'opaque_input':'unchanged'},lambda:calls.append(name)or 'ok')

@pytest.mark.parametrize('kind',['F2_API_503','F5_REPEATED_ACTION'])
@pytest.mark.parametrize('first',['aggregate_data','aggregate_records'])
def test_both_orders_one_fault_and_restore(kind,first):
 other='aggregate_records'if first=='aggregate_data'else'aggregate_data'
 i=CandidateInjector(config(kind));calls=[]
 assert invoke(i,'read_csv',calls)=='ok'
 error=NoProgressFailure if kind=='F5_REPEATED_ACTION'else InjectedServiceUnavailableError
 with pytest.raises(error):invoke(i,first,calls)
 assert calls==['read_csv']and i.events[-1].tool_name==first and i.events[-1].call_index==1
 state=json.loads(json.dumps(i.checkpoint_state()));restored=CandidateInjector(config(kind));restored.restore_checkpoint_state(state)
 assert invoke(restored,other,calls)=='ok'
 assert invoke(restored,first,calls)=='ok'
 assert sum(e.triggered for e in restored.events)==1
 assert set(restored.checkpoint_state())==set(state)
 assert state['fault_identity']['parameters']['target_match_policy']==POLICY

@pytest.mark.parametrize('kind',['F2_API_503','F5_REPEATED_ACTION'])
def test_restore_before_target_retains_pending_first_group_call(kind):
 i=CandidateInjector(config(kind));invoke(i,'read_csv',[])
 state=json.loads(json.dumps(i.checkpoint_state()));restored=CandidateInjector(config(kind));restored.restore_checkpoint_state(state)
 with pytest.raises(NoProgressFailure if kind=='F5_REPEATED_ACTION'else InjectedServiceUnavailableError):invoke(restored,'aggregate_records',[])
 assert sum(e.triggered for e in restored.events)==1

@pytest.mark.parametrize('kind',['F2_API_503','F5_REPEATED_ACTION'])
def test_without_opt_in_old_exact_matching_is_unchanged(kind):
 i=CandidateInjector(config(kind,False));calls=[]
 assert invoke(i,'aggregate_records',calls)=='ok'
 with pytest.raises(NoProgressFailure if kind=='F5_REPEATED_ACTION'else InjectedServiceUnavailableError):invoke(i,'aggregate_data',calls)
 assert calls==['aggregate_records']

@pytest.mark.parametrize('kind',['F2_API_503','F5_REPEATED_ACTION'])
def test_no_group_call_still_means_no_fault(kind):
 i=CandidateInjector(config(kind));calls=[]
 invoke(i,'read_csv',calls);invoke(i,'filter_records',calls)
 assert not any(e.triggered for e in i.events)

@pytest.mark.parametrize('new_to_old',[True,False])
def test_cross_policy_restore_rejected(new_to_old):
 source=CandidateInjector(config(policy=new_to_old));state=source.checkpoint_state()
 target=CandidateInjector(config(policy=not new_to_old))
 with pytest.raises(ValueError,match='identity'):target.restore_checkpoint_state(state)

@pytest.mark.parametrize('operation',['sum','mean','count'])
def test_full_csv_and_records_have_same_aggregation_when_same_rows(tmp_path,operation):
 csv=tmp_path/'rows.csv';csv.write_text('category,value\nx,2\nx,3\ny,7\n')
 class Env:
  def resolve_path(self,path):return tmp_path/path
 tools=DataAnalysisTools(Env());before=csv.read_bytes()
 assert tools.aggregate_data('rows.csv','category','value',operation)==tools.aggregate_records(tools.read_csv('rows.csv'),'category','value',operation)
 assert csv.read_bytes()==before

def test_filtered_records_are_not_interchangeable_inputs(tmp_path):
 csv=tmp_path/'rows.csv';csv.write_text('category,value\nx,2\nx,3\ny,7\n')
 class Env:
  def resolve_path(self,path):return tmp_path/path
 tools=DataAnalysisTools(Env());rows=tools.filter_records(tools.read_csv('rows.csv'),'value','gt','2')
 assert tools.aggregate_data('rows.csv','category','value','sum')!=tools.aggregate_records(rows,'category','value','sum')

def test_proposal_rejects_unsupported_fault_and_unknown_policy():
 case=dict(fault_id='offline_reject',fault_type='F3_INVALID_ARGUMENT',fault_seed=1,target_tool='aggregate_data',call_indices=[1],parameters={'target_match_policy':POLICY})
 with pytest.raises(ValueError):CandidateInjector(FormalFaultConfig.from_case(case))
 case['fault_type']='F2_API_503';case['parameters']['target_match_policy']='unknown'
 with pytest.raises(ValueError):CandidateInjector(FormalFaultConfig.from_case(case))

def test_naive_per_tool_matching_would_inject_twice():
 # Baseline wildcard matches each tool's local ordinal 1; not the proposal.
 case=dict(fault_id='offline_naive',fault_type='F2_API_503',fault_seed=1,target_tool='*',call_indices=[1],parameters={})
 i=FormalFaultInjector(FormalFaultConfig.from_case(case))
 for name in ['aggregate_data','aggregate_records']:
  with pytest.raises(InjectedServiceUnavailableError):invoke(i,name,[])
 assert sum(e.triggered for e in i.events)==2

@pytest.mark.parametrize('field,value',[('domain','api_workflow'),('target','read_csv'),('policy',None),('indices',[2])])
def test_invalid_scope_rejected(field,value):
 case=dict(fault_id='invalid_scope',fault_type='F2_API_503',fault_seed=1,target_tool='aggregate_data',call_indices=[1],parameters={'target_match_policy':POLICY})
 domain='data_analysis'
 if field=='domain':domain=value
 if field=='target':case['target_tool']=value
 if field=='policy':case['parameters']['target_match_policy']=value
 if field=='indices':case['call_indices']=value
 with pytest.raises(ValueError):AggregateFaultInjector(FormalFaultConfig.from_case(case),domain=domain)

@pytest.mark.parametrize('kind',['F2_API_503','F5_REPEATED_ACTION'])
def test_write_flag_rejected_without_execution_or_counting(kind):
 i=CandidateInjector(config(kind));calls=[]
 with pytest.raises(ValueError,match='write operation'):
  i.execute(ToolOperation('aggregate_records',False),{},lambda:calls.append(1))
 assert not calls and not i.events and not i.checkpoint_state()['call_counts']
