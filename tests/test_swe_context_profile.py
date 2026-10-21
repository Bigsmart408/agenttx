import json
import shlex

import pytest

from experiments.scripts import run_swe_monitored_local as runner
from experiments.scripts.watch_swe_full_comparison import controls_comparison
from experiments.workloads import swe_bench_suite as swe


POLICIES=runner.BASE_POLICIES+('ideal_oracle',)
CONTEXTS={p:('compact' if p in ('causal','clean_recovery','ideal_oracle') else 'full') for p in POLICIES}


@pytest.mark.parametrize('policy',POLICIES)
def test_worker_sets_actual_context_before_benchmark(policy,tmp_path,monkeypatch):
    from experiments.scripts import bench_official_tasks as bench
    from experiments.workloads.recovery_inject import recovery_context_variant
    monkeypatch.setattr(runner,'POLICIES',POLICIES)
    monkeypatch.setattr(runner,'OUT',tmp_path)
    monkeypatch.setenv('AGENTTX_SWE_CONTEXT_PROFILE','selective-compact-v1')
    monkeypatch.setenv('AGENTTX_RECOVERY_CONTEXT','full')
    monkeypatch.setenv('AGENTTX_SWE_AGENT_ROOT',str(tmp_path))
    observed=[]
    def run_once(**kwargs):
        observed.append(recovery_context_variant(kwargs['mode']))
        return dict(recovery_context_variant=observed[-1])
    monkeypatch.setattr(bench,'run_once',run_once)
    monkeypatch.setattr(swe,'load_tasks',lambda _: {})
    monkeypatch.setattr(runner,'source_manifest',lambda _:dict(sha256='test'))
    monkeypatch.setattr(runner,'private_values',lambda:[])
    monkeypatch.setattr(runner,'purge_private_codex_auth',lambda *_:None)
    monkeypatch.setattr(runner.os,'chown',lambda *_:None)
    runner.cell('task',policy)
    result=json.loads((tmp_path/'cells/task'/policy/'result.json').read_text())
    assert observed==[CONTEXTS[policy]]
    assert result['recovery_context_variant']==result['expected_recovery_context_variant']==CONTEXTS[policy]


def test_six_arm_canary_does_not_omit_ideal():
    cells=[dict(policy=p,state='pending') for p in POLICIES]
    assert runner.launch_window(False,0,cells,6)==(1,1)
    cells[0]['state']='completed'
    assert runner.launch_window(False,1,cells,6)==(5,6)


def test_full_gate_accepts_task_failure_but_rejects_wrong_context(tmp_path,monkeypatch):
    import hashlib
    monkeypatch.setattr(runner,'POLICIES',POLICIES)
    monkeypatch.setattr(runner,'DATA',tmp_path)
    monkeypatch.setenv('AGENTTX_SWE_CONTEXT_PROFILE','selective-compact-v1')
    monkeypatch.setattr(runner,'source_manifest',lambda _:dict(files=[]))
    cohort=tmp_path/'results/canary';cohort.mkdir(parents=True)
    (cohort/'source-manifest.json').write_text(json.dumps(dict(files=[])))
    rows=[]
    for policy,context in CONTEXTS.items():
        path=cohort/(policy+'.json')
        path.write_text(json.dumps(dict(mode=policy,success=policy!='whole_branch_abort',
                                        tests_rc=1 if policy=='whole_branch_abort' else 0,committed=True,
                                        recovery_context_variant=context,usage_source='sweagent_model_stats',
                                        total_tokens=100,recovery_manifest_authoritative=True)))
        rows.append(dict(policy=policy,result=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    proof=tmp_path/'proof.json'
    proof.write_text(json.dumps(dict(cohort='canary',infrastructure_valid=6,task_successes=5,attempts=6,rows=rows)))
    gate=runner.full_suite_gate(proof,tmp_path)
    assert gate['execution_core_unchanged'] and gate['task_success_required'] is False
    # Even an internally consistent hash cannot conceal the wrong treatment.
    path=cohort/'causal.json';row=json.loads(path.read_text());row['recovery_context_variant']='full'
    path.write_text(json.dumps(row))
    rows[4]['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    proof.write_text(json.dumps(dict(cohort='canary',passed=6,attempts=6,rows=rows)))
    with pytest.raises(AssertionError):runner.full_suite_gate(proof,tmp_path)
    row['recovery_context_variant']='compact';row['environment_error']=True
    path.write_text(json.dumps(row));rows[4]['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    proof.write_text(json.dumps(dict(cohort='canary',infrastructure_valid=6,attempts=6,rows=rows)))
    with pytest.raises(AssertionError):runner.full_suite_gate(proof,tmp_path)


def test_intended_asymmetric_comparison_is_included_but_context_mismatch_excluded():
    progress=dict(tasks=[dict(task=t,policy=p,state='completed') for t in ('correct','wrong') for p in POLICIES])
    results={(t,p):dict(success=True,total_tokens=100 if p=='causal' else 200,
                           recovery_context_variant=CONTEXTS[p]) for t in ('correct','wrong') for p in POLICIES}
    results[('wrong','temporal_checkpoint')]['recovery_context_variant']='compact'
    report=controls_comparison(progress,results,dict(context_by_policy=CONTEXTS,context_profile='selective-compact-v1'))
    assert report['temporal_checkpoint']['paired_finished']==1
    assert report['temporal_checkpoint']['token_reduction_pct']==50
    assert report['ideal_oracle']['paired_finished']==2
    # Without a registered asymmetric profile, preserve the legacy exclusion.
    assert controls_comparison(progress,results)['crash_no_rollback']['paired_finished']==0


def test_sympy_files_precede_all_failure_keywords():
    task=swe.SWETask(instance_id='sympy__sympy-test',scale='short',doc_lines=0,
                     doc_specs=[],pythonpath='.',max_turns=None,faulty_relpath='sympy/core.py')
    instance=dict(repo='sympy/sympy',FAIL_TO_PASS=json.dumps(['test_first','test_second']),
                  test_patch='+++ b/sympy/core/tests/test_core.py\n+++ b/sympy/core/tests/test_other.py\n')
    argv=shlex.split(swe.test_command(task,instance,'/usr/bin/python3'))
    split=argv.index('-k')
    assert argv[split-2:split]==['sympy/core/tests/test_core.py','sympy/core/tests/test_other.py']
    assert argv[split+1:]==['test_first','test_second']
