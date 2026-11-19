import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import run_tb_default_domain_proxy_20261027 as q


def scenario(tmp_path):
    original = tmp_path/'original.json'
    original.write_text('{"success":false}')
    result = tmp_path/'host-result.json'
    result.write_text('{"success":false,"api_usage":{"complete":false,"total_tokens":78925}}')
    evidence = tmp_path/'evidence.json'
    evidence.write_text('{"actual_tool_observation":true}')
    first = dict(task='mteb-eval', harness='sweagent', policy='causal', state='completed',
                 expected_context='compact', result_path=str(original))
    running = dict(first, state='running', worker_pid=123, attempt=1, result_path=str(result))
    other = dict(task='other', harness='codex', policy='clean_recovery', state='running', worker_pid=456)
    proof = dict(user_authorized_immediate_proxy=True, worker_stopped=True,
        network_failure_evidence=dict(actual_tool_observation=True,path=str(evidence),sha256=q.base.sha(evidence)),
        result_path=str(result), terminal_result_sha256=q.base.sha(result), worker_pid=123,
        finished_at='2026-10-27T09:30:00+00:00', worker_returncode=0)
    return [first,other], [running], proof


def test_exact_attempt_cost_and_other_workers_preserved(tmp_path):
    cells,retries,proof=scenario(tmp_path)
    raw=Path(proof['result_path']).read_bytes();other=copy.deepcopy(cells[1])
    q.apply_interruption(cells,retries,proof)
    assert cells[1]==other
    assert Path(proof['result_path']).read_bytes()==raw
    assert retries[0]['interrupted_at_user_request']
    assert retries[1]['download_proxy_retry'] and retries[1]['attempt']==2
    assert retries[1]['expected_context']=='compact'
    assert json.loads(raw)['api_usage']['total_tokens']==78925


@pytest.mark.parametrize('bad',['no_tool_evidence','wrong_worker','changed_result','duplicate','still_running'])
def test_reject_invalid_or_duplicate_replacement(tmp_path,bad):
    cells,retries,proof=scenario(tmp_path)
    if bad=='no_tool_evidence':proof['network_failure_evidence']['actual_tool_observation']=False
    if bad=='wrong_worker':proof['worker_pid']=999
    if bad=='changed_result':proof['terminal_result_sha256']='invalid'
    if bad=='duplicate':retries.append(dict(retries[0],state='pending',resumed_user_interruption_of=proof['result_path']))
    if bad=='still_running':proof['worker_stopped']=False
    before=copy.deepcopy((cells,retries))
    with pytest.raises(AssertionError):q.apply_interruption(cells,retries,proof)
    assert (cells,retries)==before


def test_ordinary_proxy_does_not_promote_all_retries_to_urgent():
    cells=[dict(state='pending')]
    retries=[dict(state='pending')]
    active={i:dict(retry=True) for i in range(4)}
    assert q.select(cells,retries,active)==(cells[0],False)
    retries[0]['download_proxy_retry']=True
    assert q.select(cells,retries,active)==(retries[0],True)
