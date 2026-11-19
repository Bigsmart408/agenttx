import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).parent))
import run_tb_immediate_network_retry_20261026 as q


def test_all_eight_slots_prioritize_network_backlog():
    first=dict(task='ordinary',state='pending')
    retry=dict(task='network',state='pending',download_proxy_retry=True)
    for count in range(8):
        assert q.select([first],[retry],{i:dict(retry=True) for i in range(count)})==(retry,True)
    retry['state']='running'
    assert q.select([first],[retry],{})==(first,False)


def test_interrupted_retry_is_preserved_and_rescheduled_once(tmp_path):
    path=tmp_path/'host-result.json';path.write_text(json.dumps(dict(api_usage=dict(complete=False,total_tokens=55))))
    first=dict(task='a',harness='sweagent',policy='causal',state='completed',expected_context='compact',result_path=str(path))
    old=dict(first,state='infrastructure_error',attempt=2,interrupted_at_user_request=True)
    retries=[old];before=path.read_bytes()
    q.register_interrupted_retries([first],retries)
    assert len(retries)==2 and retries[1]['attempt']==3
    assert retries[1]['resumed_user_interruption_of']==str(path)
    q.register_interrupted_retries([first],retries)
    assert len(retries)==2 and path.read_bytes()==before
    assert q.prior.report([first],[old])['attempts_with_incomplete_usage']==2


def test_worker_sources_unchanged_and_download_registration_not_duplicated():
    assert q.REPAIR_FILES==()
    assert q.DOWNLOAD_REPAIR_COHORT=='tb-dual-native-full-20261026-r26-selective-download-retry'
    source=Path(q.__file__).read_text()
    assert "and not user_interruption" in source
    assert "if user_interruption:" in source
