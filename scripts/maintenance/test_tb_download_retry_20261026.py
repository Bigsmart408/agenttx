import copy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).parent))
import tb_download_retry_20261026 as d
import run_tb_selective_download_retry_20261026 as q


def attempt(tmp_path,name='first',text=None,success=False):
    folder=tmp_path/name;folder.mkdir()
    p=folder/'host-result.json';p.write_text(json.dumps(dict(success=success,api_usage=dict(complete=True,total_tokens=123))))
    (folder/'verifier.stdout.log').write_text(text or 'HTTPSConnectionPool(host=\'huggingface.co\', port=443) Failed to establish a new connection: [Errno 111] Connection refused')
    return dict(task='hf-train-lora-adapter',harness='sweagent',policy='causal',state='completed',result_path=str(p),expected_context='compact')


def test_explicit_external_failure_only(tmp_path):
    assert d.download_evidence(attempt(tmp_path))['domain']=='huggingface.co'
    assert d.download_evidence(attempt(tmp_path,'local',text="HTTPConnectionPool(host='localhost', port=5000): Connection refused")) is None
    assert d.download_evidence(attempt(tmp_path,'code',text='https://huggingface.co/gpt2\nAssertionError: incorrect tensor')) is None
    assert d.download_evidence(attempt(tmp_path,'success',success=True)) is None


def test_one_independent_transport_retry_retains_results_costs_and_latest_outcome(tmp_path):
    first=attempt(tmp_path);before=copy.deepcopy(first);raw=Path(first['result_path']).read_bytes();retries=[]
    new=d.register_download_retries([first],retries,q.NAME,q.sha,q.key)
    assert len(new)==1 and new[0]['attempt']==1 and d.requires_proxy(new[0])
    assert not d.register_download_retries([first],retries,q.NAME,q.sha,q.key)
    assert first==before and raw==Path(first['result_path']).read_bytes()
    new[0].update(attempt(tmp_path,'retry',success=True))
    report=q.prior.report([first],retries)
    assert report['known_cumulative_tokens']==246


def test_reuse_pending_and_never_duplicate_active(tmp_path):
    first=attempt(tmp_path);pending=dict(first,state='pending',attempt=2);retries=[pending]
    assert d.register_download_retries([first],retries,q.NAME,q.sha,q.key)==[pending]
    assert len(retries)==1 and pending['attempt']==2
    active=dict(first,state='running')
    assert not d.register_download_retries([first],[active],q.NAME,q.sha,q.key)


def test_ordinary_attempts_direct_network_skip_revoked():
    assert not q.DEFERRED_TASKS
    assert not d.requires_proxy(dict(task='hf-model-inference',state='pending'))
    first=dict(task='hf-model-inference',state='pending')
    assert q.select([first],[],{})==(first,False)
    assert set(q.REPAIR_FILES)=={'experiments/scripts/bench_tb_image_native.py','experiments/scripts/tb_task_services.py','experiments/scripts/tb_download_proxy.py'}


MNIST_HF_FAILURE = '''__________________________ test_shapes __________________________
/tests/model.py:57: in load
    train_data = pd.read_parquet("hf://datasets/ylecun/mnist/" + splits["train"])
.tbench-testing/lib/python3.13/site-packages/huggingface_hub/hf_file_system.py:1224: in info
E           httpx2.ConnectError: [Errno 101] Network is unreachable
'''


def test_official_hf_uri_traceback_enables_only_one_evidenced_retry(tmp_path):
    first=attempt(tmp_path,text=MNIST_HF_FAILURE)
    first['task']='mnist-learning-fix'
    before=copy.deepcopy(first);raw=Path(first['result_path']).read_bytes()
    evidence=d.download_evidence(first)
    assert evidence['domain']=='huggingface.co'
    assert evidence['dataset_uri']=='hf://datasets/ylecun/mnist/'
    assert evidence['source_sha256']==hashlib.sha256(Path(evidence['source']).read_bytes()).hexdigest()
    retries=[]
    assert len(d.register_download_retries([first],retries,q.NAME,q.sha,q.key))==1
    assert d.requires_proxy(retries[0])
    assert not d.register_download_retries([first],retries,q.NAME,q.sha,q.key)
    assert first==before and raw==Path(first['result_path']).read_bytes()


def test_hf_uri_requires_official_traceback_and_matching_failure_block(tmp_path):
    texts=[
        MNIST_HF_FAILURE.replace('E           httpx2.ConnectError: [Errno 101] Network is unreachable', 'E           AssertionError: incorrect shape'),
        MNIST_HF_FAILURE.replace('/tests/model.py:57: in load', '/app/model.py:57: in load'),
        MNIST_HF_FAILURE.replace('huggingface_hub/hf_file_system.py:', 'local_service.py:'),
        MNIST_HF_FAILURE.replace('E           httpx2.ConnectError: [Errno 101] Network is unreachable', 'E           AssertionError: incorrect shape\n__________________________ test_local_service __________________________\nE           httpx2.ConnectError: [Errno 101] Network is unreachable'),
    ]
    for i,text in enumerate(texts):
        row=attempt(tmp_path,str(i),text=text);row['task']='mnist-learning-fix'
        assert d.download_evidence(row) is None
    unrelated=attempt(tmp_path,'other',text=MNIST_HF_FAILURE)
    assert d.download_evidence(unrelated) is None
    success=attempt(tmp_path,'passed',text=MNIST_HF_FAILURE,success=True)
    success['task']='mnist-learning-fix'
    assert d.download_evidence(success) is None
