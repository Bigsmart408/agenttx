import copy
import importlib.util
import json
from pathlib import Path

spec=importlib.util.spec_from_file_location('runtime_repair_queue',Path(__file__).with_name('run_tb_file_memory_repair_20261026.py'))
q=importlib.util.module_from_spec(spec)
spec.loader.exec_module(q)


def cell(tmp_path,policy='causal',state='completed',task='filter-js-from-html'):
    path=tmp_path/(task+policy+'.json')
    path.write_text(json.dumps(dict(success=state=='completed',api_usage=dict(complete=True,total_tokens=123))))
    return dict(task=task,harness='codex',policy=policy,state=state,expected_context='compact',result_path=str(path))


def test_repair_includes_success_and_failure_all_policies_once(tmp_path):
    policies=['clean_recovery','crash_no_rollback','temporal_checkpoint','whole_branch_abort','causal','ideal_oracle']
    cells=[cell(tmp_path,p,'completed' if i%2 else 'infrastructure_error') for i,p in enumerate(policies)]
    before=copy.deepcopy(cells)
    original={r['result_path']:q.sha(r['result_path']) for r in cells}
    retries=[]
    assert len(q.add_runtime_repairs(cells,retries))==6
    assert cells==before
    assert all(r['runtime_repair_cohort']==q.NAME and r['attempt']==1 for r in retries)
    assert not q.add_runtime_repairs(cells,retries)
    assert len(retries)==6
    assert all(q.sha(p)==h for p,h in original.items())


def test_reuse_pending_and_preserve_previous_attempt_cost(tmp_path):
    first=cell(tmp_path)
    old=dict(first,attempt=1)
    pending=dict(first,attempt=2,state='pending')
    retries=[old,pending]
    before=copy.deepcopy(old)
    assert not q.add_runtime_repairs([first],retries)
    assert len(retries)==2 and old==before
    assert pending['runtime_repair_cohort']==q.NAME and pending['attempt']==2


def test_repair_attempt_increments_after_interrupted_retry(tmp_path):
    first=cell(tmp_path,task='filter-js-from-html')
    old=dict(first,state='infrastructure_error',attempt=3)
    retries=[old]
    added=q.add_runtime_repairs([first],retries)
    assert added[0]['attempt']==4
    assert added[0]['previous_attempt_sha256']==q.sha(old['result_path'])
    assert old['state']=='infrastructure_error'


def test_xp_reenabled_hf_deferred_and_retry_queue_balanced():
    first=[dict(task='install-windows-xp',state='pending')]
    retry=[dict(task='filter-js-from-html',state='pending',runtime_repair_cohort=q.NAME)]
    assert q.select(first,retry,{})==(retry[0],True)
    assert q.select(first,retry,{i:dict(retry=True) for i in range(4)})==(first[0],False)
    assert q.select([dict(task='hf-model-inference',state='pending')],[],{}) is None


def test_pending_first_cells_not_repeated(tmp_path):
    first=cell(tmp_path,state='pending')
    assert not q.add_runtime_repairs([first],[])


def test_repaired_source_scope_and_current_evidence():
    assert set(q.REPAIR_FILES)=={'src/agenttx/layers.py','src/agenttx/semisolate.py'}
    assert 'install-windows-xp' not in q.DEFERRED_TASKS


def test_cumulative_cost_keeps_failed_attempt_and_latest_outcome(tmp_path):
    def attempt(policy,name,tokens,success,complete=True):
        path=tmp_path/name
        path.write_text(json.dumps(dict(success=success,api_usage=dict(complete=complete,total_tokens=tokens))))
        return dict(task='same-task',harness='sweagent',policy=policy,state='completed',result_path=str(path))
    causal=attempt('causal','causal.json',100,True)
    control=attempt('clean_recovery','clean.json',200,True)
    failed=attempt('causal','failed.json',300,False)
    repaired=attempt('causal','repaired.json',50,True)
    report=q.prior.report([causal,control],[failed,repaired])
    pair=report['cumulative_paired_success']['sweagent']['clean_recovery']
    assert report['known_cumulative_tokens']==650
    assert pair['causal_cumulative_tokens']==450 and pair['control_cumulative_tokens']==200
    # A later failure replaces success; no best-attempt outcome selection.
    report=q.prior.report([causal,control],[repaired,failed])
    assert report['cumulative_paired_success']['sweagent']['clean_recovery']['both_success_pairs']==0
    incomplete=attempt('causal','unknown.json',25,True,False)
    report=q.prior.report([causal,control],[incomplete,repaired])
    assert report['attempts_with_incomplete_usage']==1
    assert report['cumulative_paired_success']['sweagent']['clean_recovery']['both_success_pairs']==0
