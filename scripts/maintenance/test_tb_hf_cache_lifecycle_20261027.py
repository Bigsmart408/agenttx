import copy
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).parent))
import run_tb_hf_cache_lifecycle_20261027 as q


def test_finite_repair_preserves_results_and_live_workers(tmp_path,monkeypatch):
    path=tmp_path/'result.json';path.write_text('{"success":false,"tokens":42}')
    original=dict(task='oom',harness='codex',policy='causal',expected_context='compact',
                  state='completed',result_path=str(path))
    other=dict(task='other',harness='sweagent',policy='clean_recovery',state='running',worker_pid=123)
    cells=[original,other];before=copy.deepcopy(cells);retries=[];raw=path.read_bytes()
    monkeypatch.setattr(q,'cache_fault',lambda r: {'exact':True} if r['task']=='oom' else None)
    assert len(q.register_cache_repairs(cells,retries))==1
    assert cells==before and path.read_bytes()==raw and retries[0]['attempt']==1
    assert q.register_cache_repairs(cells,retries)==[]
    retries[0].update(state='completed',result_path=str(path))
    assert q.register_cache_repairs(cells,retries)==[]


def test_pending_retry_is_reused_not_duplicated(tmp_path,monkeypatch):
    path=tmp_path/'result.json';path.write_text('{}')
    old=dict(task='oom',harness='codex',policy='causal',expected_context='compact',
             state='completed',result_path=str(path))
    pending=dict(old,state='pending',attempt=1)
    retries=[pending]
    monkeypatch.setattr(q,'cache_fault',lambda r: {'exact':True})
    q.register_cache_repairs([old],retries)
    assert len(retries)==1 and pending['hf_cache_lifecycle_repair_of']==str(path)
