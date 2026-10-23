import json

from experiments.scripts import validate_tb_trend_local as validation
from experiments.scripts.run_tb_dual_local import CONTEXTS, experiment_configuration


def panel(tmp_path):
    tasks=('a','b','c');cells=[]
    for task in tasks:
        for h in ('codex','sweagent'):
            for policy in validation.POLICIES:
                path=tmp_path/task/h/policy/'host-result.json';path.parent.mkdir(parents=True)
                row=dict(completed=True,model_returncode=0,official_tests_rc=0,
                    documents_valid=True,derived_removed=True,conversation_matches_workspace=True,
                    agent_runtime='official_task_container',verifier_runtime='same_official_task_container',
                    context_variant=CONTEXTS[policy],api_usage=dict(complete=True,total_tokens=100 if policy=='causal' else 200),
                    **experiment_configuration())
                path.write_text(json.dumps(row))
                cells.append(dict(task=task,harness=h,policy=policy,state='completed',result_path=str(path)))
    return cells,tasks


def change(cell,**fields):
    from pathlib import Path
    path=Path(cell['result_path']);row=json.loads(path.read_text());row.update(fields);path.write_text(json.dumps(row))


def test_validation_reports_common_pairs_without_authorizing_full_experiment(tmp_path):
    cells,tasks=panel(tmp_path);report=validation.assess(cells,tasks)
    assert report['gate_passed'] and report['panel_complete']
    assert not report['full_dispatch_authorized'] and not report['automatic_full_resume']
    for r in report['comparisons'].values():
        assert r['common_success_tasks']==list(tasks)
        assert r['harnesses']['codex']['total_saving_pct']==50


def test_validation_keeps_cost_tail_and_fails_despite_positive_median(tmp_path):
    cells,tasks=panel(tmp_path)
    change(next(c for c in cells if c['harness']=='codex' and c['policy']=='causal'),
           api_usage=dict(complete=True,total_tokens=10000))
    report=validation.assess(cells,tasks)
    assert not report['gate_passed']
    codex=report['comparisons']['whole_branch_abort']['harnesses']['codex']
    assert codex['median_case_saving_pct']==50 and codex['total_saving_pct']<0


def test_validation_cannot_pass_with_missing_usage_failed_tasks_or_partial_grid(tmp_path):
    cells,tasks=panel(tmp_path)
    selected=next(c for c in cells if c['policy']=='causal')
    change(selected,api_usage=dict(complete=False,total_tokens=None))
    assert not validation.assess(cells,tasks)['gate_passed']
    change(selected,api_usage=dict(complete=True,total_tokens=1),official_tests_rc=1)
    report=validation.assess(cells,tasks)
    assert not report['gate_passed']
    assert len(report['comparisons']['whole_branch_abort']['common_success_tasks'])==2
    assert not validation.assess(cells[:-1],tasks)['gate_passed']


def test_validation_does_not_pool_different_budget_or_treat_unknown_as_zero(tmp_path):
    cells,tasks=panel(tmp_path)
    change(cells[0],model_session_wall_s=900)
    report=validation.assess(cells,tasks)
    assert not report['gate_passed']
    assert report['infrastructure_issues'][0]['issue']=='configuration_mismatch'
