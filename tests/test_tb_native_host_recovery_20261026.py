import json

from scripts.maintenance.recover_tb_native_host_20261026 import finalize


def test_native_result_and_cost_are_recovered_without_new_attempt(tmp_path):
    row=dict(task='hf',policy='causal',harness='codex',started_at='2026-10-26T14:00:00+00:00')
    native=dict(completed=True,success=True,status='passed',prerequisite_install=dict(mode='fixed'),total_tokens=12)
    (tmp_path/'result.json').write_text(json.dumps(native));(tmp_path/'prerequisite-cache.json').write_text(json.dumps(dict(cache_sha256='hash')))
    assert finalize(row,tmp_path,0,'docker-wait',lambda *args:dict(complete=True,total_tokens=12),{})
    path=tmp_path/'host-result.json';result=json.loads(path.read_text());original=path.read_bytes()
    assert result['success'] and result['api_usage']['total_tokens']==12
    assert result['prerequisite_cache']['preparation']=='fixed'
    assert result['orchestrator_recovery']['model_restarted'] is False
    assert not finalize(row,tmp_path,1,'different',lambda *args:None,{})
    assert path.read_bytes()==original


def test_unobserved_exit_never_implies_task_success(tmp_path):
    row=dict(policy='causal',harness='codex',started_at='2026-10-26T14:00:00+00:00')
    (tmp_path/'result.json').write_text(json.dumps(dict(completed=True,success=True,status='passed')))
    finalize(row,tmp_path,None,'unknown-exit-code',lambda *args:dict(complete=False),{})
    result=json.loads((tmp_path/'host-result.json').read_text())
    assert not result['success'] and not result['completed']
    assert result['native_success']
