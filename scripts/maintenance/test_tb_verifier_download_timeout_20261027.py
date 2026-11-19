import copy
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).parent))
import run_tb_verifier_download_timeout_20261027 as repair


def row(tmp_path,**extra):
    path=tmp_path/'host-result.json';path.write_text(json.dumps(dict(success=False,official_tests_rc=2,model_returncode=0,**extra)))
    (tmp_path/'verifier.stderr.log').write_text('Failed to download distribution due to network timeout. UV_HTTP_TIMEOUT (current value: 40s). Failed to spawn: `pytest`')
    return dict(task='fixture',harness='codex',policy='causal',expected_context='compact',state='completed',result_path=str(path))


def test_only_explicit_uv_timeout_before_pytest_is_repairable(tmp_path):
    original=row(tmp_path);assert repair.timeout_fault(original)
    (tmp_path/'verifier.stderr.log').write_text('FAILED test_output: assertion failed')
    assert repair.timeout_fault(original) is None
    row(tmp_path,download_timeout_contract=repair.CONTRACT)
    assert repair.timeout_fault(original) is None


def test_one_finite_repair_preserves_fees_and_live_workers(tmp_path):
    original=row(tmp_path);raw=(tmp_path/'host-result.json').read_bytes()
    running=dict(original,task='running',state='running',worker_pid=123)
    cells=[original,running];before=copy.deepcopy(cells);retries=[]
    assert len(repair.register_timeout_repairs(cells,retries))==1
    assert cells==before and (tmp_path/'host-result.json').read_bytes()==raw
    assert repair.register_timeout_repairs(cells,retries)==[]
    retries[0].update(state='completed',result_path=original['result_path'])
    assert repair.register_timeout_repairs(cells,retries)==[]


def test_running_retry_is_left_intact_and_pending_is_reused(tmp_path):
    original=row(tmp_path);running=dict(original,state='running');retries=[running]
    assert repair.register_timeout_repairs([original],retries)==[]
    running['state']='pending'
    assert len(repair.register_timeout_repairs([original],retries))==1 and len(retries)==1
