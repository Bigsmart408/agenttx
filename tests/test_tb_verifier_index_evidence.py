"""Register a finite transport repair only for explicit pre-test failures."""
import json

from scripts.maintenance import tb_verifier_index_fallback_20261028 as repair


def fixture(tmp_path):
    folder=tmp_path/'cell';folder.mkdir()
    result=folder/'host-result.json'
    result.write_text(json.dumps(dict(success=False,official_success=False,model_returncode=124)))
    log=folder/'verifier.stderr.log'
    log.write_text('+ uv pip install pytest==8.4.1\nerror: Failed to fetch: `https://mirrors.aliyun.com/pypi/simple/pytest/`\nCaused by: operation timed out\nerror: Failed to spawn: `pytest`\nNo such file or directory\n')
    return dict(task='play-zork',harness='sweagent',policy='clean_recovery',state='completed',
                expected_context='compact',result_path=str(result))


def test_model_timeout_is_preserved_and_repair_is_idempotent(tmp_path):
    row=fixture(tmp_path);original=(tmp_path/'cell/host-result.json').read_bytes()
    proof=repair.failure_evidence(row)
    assert proof and not proof['official_tests_executed'] and proof['independent_model_failure_preserved']
    key=lambda r:(r['task'],r['harness'],r['policy'])
    retries=[]
    assert len(repair.register([row],retries,'cohort',key))==1
    assert len(repair.register([row],retries,'cohort',key))==0
    assert (tmp_path/'cell/host-result.json').read_bytes()==original


def test_success_test_execution_and_repaired_failure_cannot_trigger_retry(tmp_path):
    row=fixture(tmp_path);path=tmp_path/'cell/host-result.json'
    original=json.loads(path.read_text())
    for update in [dict(success=True),dict(official_success=True),dict(verifier_download_contract=repair.CONTRACT)]:
        path.write_text(json.dumps(dict(original,**update)))
        assert repair.failure_evidence(row) is None
    path.write_text(json.dumps(original))
    (tmp_path/'cell/verifier.stdout.log').write_text('test session starts\nAssertionError')
    assert repair.failure_evidence(row) is None


def test_running_retry_is_never_replaced_or_interrupted(tmp_path):
    row=fixture(tmp_path);retry=dict(row,state='running',attempt=1)
    key=lambda r:(r['task'],r['harness'],r['policy'])
    assert repair.register([row],[retry],'cohort',key)==[]


def test_v1_wheel_timeout_before_tests_qualifies_for_v2_repair(tmp_path):
    row=fixture(tmp_path);folder=tmp_path/'cell'
    result=json.loads((folder/'host-result.json').read_text())
    result.update(model_returncode=0,verifier_download_contract='verifier-uv-network-error-domestic-index-fallback/v1')
    (folder/'host-result.json').write_text(json.dumps(result))
    (folder/'verifier.stderr.log').write_text(
        '+ uv pip install pytest requests psutil easyocr==1.7.2\n'
        'Failed to download distribution due to network timeout.\n'
        'error: Failed to spawn: `pytest`\nNo such file or directory\n')
    original=(folder/'host-result.json').read_bytes()
    proof=repair.failure_evidence(row)
    assert proof['reason']=='official_verifier_wheel_stream_network_timeout_before_tests'
    assert not proof['official_tests_executed'] and not proof['independent_model_failure_preserved']
    key=lambda r:(r['task'],r['harness'],r['policy'])
    retries=[]
    assert len(repair.register([row],retries,'v2',key))==1
    assert repair.register([row],retries,'v2',key)==[]
    retries[0]['state']='completed'
    assert repair.register([row],retries,'v2',key)==[]
    assert (folder/'host-result.json').read_bytes()==original
    (folder/'verifier.stdout.log').write_text('test session starts\nAssertionError')
    assert repair.failure_evidence(row) is None
