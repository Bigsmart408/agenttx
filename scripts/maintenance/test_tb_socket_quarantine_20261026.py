import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('socket_quarantine_queue', Path(__file__).with_name('run_tb_socket_quarantine_20261026.py'))
q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(q)


def test_new_timeout_after_old_incident_does_not_classify_as_connection_error(tmp_path):
    path=tmp_path/'host-result.json'
    result=dict(completed=True,status='task_or_model_failed',model_returncode=124,api_usage=dict(complete=False))
    path.write_text(json.dumps(result))
    row=dict(task='example',state='infrastructure_error',finished_at='2026-10-25T11:00:00+00:00',result_path=str(path))
    assert q.prior.connection_evidence(row)['inferred']
    assert q.fresh_connection_evidence(row) is None
    assert not q.halts_dispatch('incomplete_api_usage',result)


def test_explicit_new_connection_failure_still_blocks_dispatch(tmp_path):
    path=tmp_path/'host-result.json'
    result=dict(completed=True,status='task_or_model_failed',model_returncode=1,api_usage=dict(complete=False))
    path.write_text(json.dumps(result))
    (tmp_path/'model-result.json').write_text(json.dumps(dict(stderr='APIConnectionError: Connection refused')))
    row=dict(task='example',state='infrastructure_error',finished_at='2026-10-25T11:00:00+00:00',result_path=str(path))
    assert q.fresh_connection_evidence(row)['classification']=='explicit_model_connection_failure'
    assert q.halts_dispatch('incomplete_api_usage',result)


def test_retry_backlog_cannot_starve_unstarted_first_attempts():
    first=[dict(task='new',state='pending')]
    retries=[dict(task='old',state='pending',network_repair_cohort='r20')]
    active={i:dict(retry=True) for i in range(4)}
    assert q.select(first,retries,active)==(first[0],False)
    assert q.select(first,retries,{})==(retries[0],True)


def test_deferred_environment_tasks_are_preserved_and_not_dispatched():
    first=[dict(task='hf-model-inference',state='pending'),dict(task='safe',state='pending')]
    retries=[dict(task='hf-train-lora-adapter',state='pending')]
    assert q.select(first,retries,{})==(first[1],False)
    assert all(row['state']=='pending' for row in first+retries)
    assert q.select(first[:1],retries,{}) is None


def test_resume_uses_new_paths_without_changing_prior_attempt():
    row=dict(task='example',harness='codex',policy='causal',result_path='/old/host-result.json')
    assert q.result_path(row,True)==q.ROOT/'retry-cells/example/codex/causal/causal/host-result.json'
    assert q.result_path(row,False)==q.ROOT/'cells/example/codex/causal/causal/host-result.json'
    assert row['result_path']=='/old/host-result.json'


def test_xp_all_policies_quarantined_without_erasing_pending_cells():
    rows=[dict(task="install-windows-xp", policy=p, state="pending") for p in ("causal","clean_recovery","crash_no_rollback","ideal_oracle")]
    assert q.select(rows, rows.copy(), {}) is None
    assert len(rows)==4 and all(r["state"]=="pending" for r in rows)
