import json
import pytest

from experiments.scripts import supervise_swe_local as supervisor


def test_unclassified_infrastructure_error_cannot_resume_automatically(tmp_path):
    progress = dict(tasks=[dict(task='t', policy='causal', state='infrastructure_error',
                                infrastructure_issue='environment_error')])
    registration = dict(images=[dict(task='t', image_id='sha256:' + 'a' * 64)])
    assert supervisor.audit_terminal_errors(tmp_path, progress, registration) is None


def test_audited_model_command_error_preserves_failed_task_outcome(tmp_path, monkeypatch):
    folder = tmp_path / 'cells/t/causal'
    folder.mkdir(parents=True)
    result = dict(success=False, tests_rc=1, usage_source='sweagent_model_stats',
                  total_tokens=2000, mode='causal', recovery_manifest_authoritative=True)
    (folder / 'result.json').write_text(json.dumps(result))
    (folder / 'worker.log').write_text('Official evaluator infrastructure error')
    proof = dict(classification='agent_authored_invalid_image_id', correct_image_present=True,
                 episodes=[dict(step=2)])
    monkeypatch.setattr(supervisor, 'verifier_command_audit', lambda *args: proof)
    progress = dict(tasks=[dict(task='t', policy='causal', state='infrastructure_error',
                                infrastructure_issue='agent_verifier_infrastructure_error')])
    registration = dict(images=[dict(task='t', image_id='sha256:' + 'a' * 64)],
                        context_by_policy=dict(causal=None))
    audit = supervisor.audit_terminal_errors(tmp_path, progress, registration)
    assert len(audit['cells']) == 1
    assert audit['task_retried'] is False
    assert json.loads((folder / 'result.json').read_text())['success'] is False


def test_single_real_infrastructure_error_blocks_multi_cell_auto_continuation(tmp_path, monkeypatch):
    progress = dict(tasks=[dict(task='t', policy='causal', state='infrastructure_error',
                                infrastructure_issue='agent_verifier_infrastructure_error')])
    folder = tmp_path / 'cells/t/causal'
    folder.mkdir(parents=True)
    (folder / 'result.json').write_text('{}')
    (folder / 'worker.log').write_text('Official evaluator infrastructure error')
    monkeypatch.setattr(supervisor, 'verifier_command_audit', lambda *args: None)
    registration = dict(images=[dict(task='t', image_id='sha256:' + 'a' * 64)])
    assert supervisor.audit_terminal_errors(tmp_path, progress, registration) is None


def test_proven_timeout_can_resume_but_unclassified_exit_error_cannot(tmp_path,monkeypatch):
    folder=tmp_path/'cells/t/clean_recovery';folder.mkdir(parents=True)
    row=dict(success=False,tests_rc=1,usage_source='sweagent_model_stats',total_tokens=3200,
             mode='clean_recovery',harness_returncode=1,harness_stderr='SWEAgent exit_status=exit_error')
    (folder/'result.json').write_text(json.dumps(row));(folder/'worker.log').write_text('')
    progress=dict(tasks=[dict(task='t',policy='clean_recovery',state='infrastructure_error',
                             infrastructure_issue='harness_integration_error')])
    registration=dict(images=[dict(task='t',image_id='sha256:'+'a'*64)],context_by_policy=dict(clean_recovery=None))
    monkeypatch.setattr(supervisor,'verifier_command_audit',lambda *a:None)
    assert supervisor.audit_terminal_errors(tmp_path,progress,registration) is None
    proof=dict(correct_image_present=True,session_timeout=dict(classification='registered_session_timeout'))
    monkeypatch.setattr(supervisor,'verifier_command_audit',lambda *a:proof)
    audit=supervisor.audit_terminal_errors(tmp_path,progress,registration)
    assert audit['task_retried'] is False and len(audit['cells'])==1
    assert json.loads((folder/'result.json').read_text())['success'] is False


def truncated_attempt(tmp_path):
    folder=tmp_path/'cells/t/clean_recovery';(folder/'trajectories').mkdir(parents=True)
    (folder/'trajectories/main.traj').write_text(json.dumps(dict(
        info=dict(exit_status=None,model_stats=dict(api_calls=1)),trajectory=[dict(action='read source')])) )
    (folder/'worker.log').write_text('usage=Usage(completion_tokens=5,prompt_tokens=10,total_tokens=15)')
    cell=dict(task='t',policy='clean_recovery',state='infrastructure_error',
        infrastructure_issue='worker_terminated_without_result',returncode=124,
        started_at='2026-10-19T03:00:00+00:00',finished_at='2026-10-19T04:00:01+00:00')
    registration=dict(images=[dict(task='t',image_id='sha256:'+'a'*64)],cell_wall_timeout_s=3600)
    return folder,cell,registration


def test_registered_worker_timeout_preserves_missing_result_and_infra_status(tmp_path):
    folder,cell,registration=truncated_attempt(tmp_path)
    audit=supervisor.audit_terminal_errors(tmp_path,dict(tasks=[cell]),registration)
    record=audit['cells'][0]
    assert record['preserve_infrastructure_error'] is True
    assert record['proof']['logged_api_tokens']==15
    assert record['proof']['usage_complete'] is False
    assert cell['state']=='infrastructure_error' and not (folder/'result.json').exists()
    assert audit['task_retried'] is False


@pytest.mark.parametrize('change',['early','other_exit','mismatched_calls','result_exists','other_policy'])
def test_worker_timeout_evidence_is_narrow(tmp_path,change):
    folder,cell,registration=truncated_attempt(tmp_path)
    if change=='early':cell['finished_at']='2026-10-19T03:59:59+00:00'
    if change=='other_exit':cell['returncode']=137
    if change=='other_policy':cell['policy']='causal'
    if change=='result_exists':(folder/'result.json').write_text('{}')
    if change=='mismatched_calls':(folder/'worker.log').write_text('no completed response')
    assert supervisor.worker_wall_timeout_audit(folder,cell,registration) is None


def test_missing_result_usage_is_charged_but_excluded_from_comparisons(tmp_path,monkeypatch):
    from experiments.scripts import audit_swe_token_usage as usage
    root=tmp_path/'results/cohort';folder,cell,registration=truncated_attempt(root)
    (root/'progress.json').write_text(json.dumps(dict(tasks=[cell],counts=dict(infrastructure_error=1))))
    monkeypatch.setattr(usage,'DATA',tmp_path)
    report=usage.audit('cohort')
    assert report['coverage']==1 and report['cells_with_complete_logged_usage']==0
    assert report['incomplete_terminal_logged_api_tokens']==report['all_terminal_logged_api_tokens']==15
    assert report['paired_success_comparisons']=={}
    assert report['rows'][0]['in_flight_request_usage_unknown'] is True
