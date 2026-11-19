import copy
import io
import json
from pathlib import Path
import sys
import urllib.error

sys.path.insert(0,str(Path(__file__).parent))
import run_tb_provider_resume_20261026 as q
import tb_provider_health_20261026 as health


def failed(tmp_path, name='first', harness='codex', status=502, summary=False):
    folder=tmp_path/name;folder.mkdir()
    path=folder/'host-result.json'
    path.write_text(json.dumps(dict(success=False,model_returncode=1,container_returncode=0,
        api_usage=dict(complete=False,total_tokens=123))))
    row=dict(task='a',harness=harness,policy='causal',expected_context='compact',
        state='infrastructure_error',result_path=str(path))
    data=dict(returncode=1,events=[dict(type='turn.failed',error=dict(message=f'unexpected status {status} Bad Gateway, url: http://example/v1/responses'))])
    if harness=='sweagent':
        data=dict(returncode=1,metadata=dict(sweagent_exit_status='exit_error'))
        (folder/'failure-diagnostics.json').write_text(json.dumps(dict(model_error_lines=['litellm.BadGatewayError: 502 Upstream service temporarily unavailable'])))
    if summary:
        data['usage_and_terminal_events']=data.pop('events',[])
        (folder/'accounting-summary.json').write_text(json.dumps(dict(model_summaries={'model-result.json':data})))
    else:(folder/'model-result.json').write_text(json.dumps(data))
    return row


def test_direct_and_retained_gateway_evidence(tmp_path):
    for i,(harness,summary) in enumerate([('codex',False),('codex',True),('sweagent',False),('sweagent',True)]):
        row=failed(tmp_path,str(i),harness,summary=summary)
        evidence=health.classify_provider_failure(row)
        assert evidence['http_status']==502 and evidence['source_sha256']


def test_no_retry_on_verifier_text_or_deadline_or_normal_task_failure(tmp_path):
    row=failed(tmp_path,status=429)
    (Path(row['result_path']).parent/'verifier.log').write_text('502 Bad Gateway /v1/responses')
    assert health.classify_provider_failure(row) is None
    path=Path(row['result_path']);data=json.loads(path.read_text());data['model_returncode']=124;path.write_text(json.dumps(data))
    assert health.classify_provider_failure(row) is None
    row['state']='completed'
    assert not q.add_provider_repairs([row],[])


def test_pending_reused_and_originals_unchanged(tmp_path):
    first=failed(tmp_path);before=copy.deepcopy(first);original=Path(first['result_path']).read_bytes()
    pending=dict(first,state='pending',attempt=1);retries=[pending]
    assert not q.add_provider_repairs([first],retries)
    assert pending['provider_repair_of']==first['result_path']
    assert not q.add_provider_repairs([first],retries)
    assert len(retries)==1 and first==before and Path(first['result_path']).read_bytes()==original


def test_repeated_outages_get_unique_attempts_without_best_of(tmp_path):
    first=failed(tmp_path,harness='sweagent');retries=[]
    assert len(q.add_provider_repairs([first],retries))==1
    one=retries[0];one.update(failed(tmp_path,'second',harness='sweagent'));one['attempt']=1
    assert len(q.add_provider_repairs([first],retries))==1
    assert retries[1]['attempt']==2
    assert q.result_path(one,True)!=q.result_path(retries[1],True)
    assert not q.add_provider_repairs([first],retries)
    report=q.prior.report([first],retries[:1])
    assert report['known_cumulative_tokens']==246 and report['attempts_with_incomplete_usage']==2


def test_later_success_prevents_provider_retry(tmp_path):
    first=failed(tmp_path)
    later=dict(first,state='completed',attempt=1)
    assert not q.add_provider_repairs([first],[later])


def profile(tmp_path):
    p=tmp_path/'profile';p.mkdir()
    (p/'config.toml').write_text('model="gpt-5.6-luna"\nmodel_provider="test"\n[model_providers.test]\nbase_url="http://example/v1"\n')
    (p/'auth.json').write_text(json.dumps(dict(OPENAI_API_KEY='test')))
    return p


class Response(io.BytesIO):
    status=200


def test_probe_requires_both_routes_and_records_cost_separately(tmp_path,monkeypatch):
    called=[]
    def request(req,timeout):
        called.append(req.full_url)
        data=dict(usage=dict(total_tokens=7))
        if req.full_url.endswith('responses'):data.update(status='completed',output=[dict(type='message')])
        else:data.update(choices=[dict(finish_reason='stop',message=dict(content='OK'))])
        return Response(json.dumps(data).encode())
    monkeypatch.setattr(health.urllib.request,'urlopen',request)
    result=health.probe_inference(tmp_path,profile(tmp_path))
    assert result['healthy'] and len(called)==2
    assert health.probe_accounting(tmp_path)['known_tokens']==14


def test_probe_http_failure_stays_closed_and_does_not_send_next(tmp_path,monkeypatch):
    called=[]
    def request(req,timeout):
        called.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url,502,'Bad Gateway',{},io.BytesIO(b'Unavailable'))
    monkeypatch.setattr(health.urllib.request,'urlopen',request)
    assert not health.probe_inference(tmp_path,profile(tmp_path))['healthy']
    assert len(called)==1 and health.probe_accounting(tmp_path)['incomplete_probe_bills']==1


def test_legacy_probe_accounting_and_worker_free_gate(tmp_path):
    (tmp_path/'probe-initial.json').write_text(json.dumps(dict(usage=dict(total_tokens=9))))
    assert health.probe_accounting(tmp_path)['known_tokens']==9
    assert q.REPAIR_FILES==()
    source=Path(q.__file__).read_text()
    assert 'provider_gate and not active' in source
    assert 'len(active)<8' in source
