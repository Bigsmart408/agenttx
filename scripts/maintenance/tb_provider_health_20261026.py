"""Strict provider-outage evidence and auditable low-cost inference probes."""
import datetime
import hashlib
import json
from pathlib import Path
import re
import tomllib
import urllib.error
import urllib.request

PROFILE=Path('/home/bfq/.config/agenttx/experiment-20261017')
GATEWAY=re.compile(r'unexpected status (502|503|504)\b',re.I)


def classify_provider_failure(row):
    if row.get('state')!='infrastructure_error':return None
    path=Path(row['result_path']);result=json.loads(path.read_text())
    if result.get('model_returncode')!=1 or result.get('container_returncode')!=0:return None
    if result.get('api_usage',{}).get('complete'):return None
    summary=path.parent/'accounting-summary.json'
    summaries=json.loads(summary.read_text()).get('model_summaries',{}) if summary.exists() else {}
    for name in ('model-result.json','replay-result.json'):
        source=path.parent/name
        if source.exists():
            raw=source.read_bytes();data=json.loads(raw);events=data.get('events',[]);source_hash=hashlib.sha256(raw).hexdigest()
        else:
            data=summaries.get(name) or {};events=data.get('usage_and_terminal_events',[])
            source=summary;source_hash=hashlib.sha256(source.read_bytes()).hexdigest() if source.exists() else None
        if data.get('returncode')!=1:continue
        if row['harness']=='codex' and events:
            terminal=events[-1]
            message=(terminal.get('error') or {}).get('message','') if terminal.get('type')=='turn.failed' else ''
            match=GATEWAY.search(message)
            if match and '/v1/responses' in message:
                return dict(classification='explicit_terminal_provider_http_5xx',http_status=int(match.group(1)),
                    source=str(source),source_sha256=source_hash,failed_phase=name,source_result=str(path))
        elif row['harness']=='sweagent' and data.get('metadata',{}).get('sweagent_exit_status')=='exit_error':
            diagnostic=path.parent/'failure-diagnostics.json'
            if not diagnostic.exists():continue
            lines=json.loads(diagnostic.read_text()).get('model_error_lines',[])
            text='\n'.join(lines)
            if 'litellm' in text and 'BadGatewayError' in text and ('502' in text or 'Upstream service temporarily' in text):
                return dict(classification='explicit_native_model_gateway_failure',http_status=502,
                    source=str(diagnostic),source_sha256=hashlib.sha256(diagnostic.read_bytes()).hexdigest(),
                    failed_phase=name,source_result=str(path))
    return None


def probe_inference(audit, profile=PROFILE):
    """Only call when there are zero model workers, so total concurrency stays8."""
    audit=Path(audit);folder=audit/'inference-probes';folder.mkdir(parents=True,exist_ok=True)
    cfg=tomllib.loads((profile/'config.toml').read_text())
    base=cfg['model_providers'][cfg['model_provider']]['base_url'].rstrip('/')
    key=json.loads((profile/'auth.json').read_text())['OPENAI_API_KEY'];model=cfg['model']
    requests=[('responses',dict(model=model,input='Reply exactly OK.',reasoning=dict(effort='max'),max_output_tokens=256)),
        ('chat/completions',dict(model=model,messages=[dict(role='user',content='Reply exactly OK.')],reasoning_effort='max',max_completion_tokens=256))]
    rows=[]
    for route,payload in requests:
        at=datetime.datetime.now(datetime.timezone.utc).isoformat()
        req=urllib.request.Request(base+'/'+route,data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=35) as response:data=json.loads(response.read());status=response.status
            usage=data.get('usage') or {}
            complete=(data.get('status')=='completed' and bool(data.get('output'))) if route=='responses' else any(c.get('finish_reason')=='stop' and c.get('message',{}).get('content') for c in data.get('choices',[]))
            row=dict(at=at,route=route,http_status=status,model=model,response_status=data.get('status'),usage=usage,
                healthy=status==200 and complete and isinstance(usage.get('total_tokens'),int),billing_complete=isinstance(usage.get('total_tokens'),int))
        except urllib.error.HTTPError as exc:
            row=dict(at=at,route=route,http_status=exc.code,error_body=exc.read(1500).decode(errors='replace'),healthy=False,billing_complete=False)
        except Exception as exc:
            row=dict(at=at,route=route,error_type=type(exc).__name__,healthy=False,billing_complete=False)
        path=folder/('probe-'+at.replace(':','').replace('.','')+'-'+route.replace('/','-')+'.json')
        path.write_text(json.dumps(row,indent=2)+'\n');rows.append(row)
        if not row['healthy']:break
    return dict(checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),healthy=len(rows)==2 and all(r['healthy'] for r in rows),probes=rows,
        model_worker_concurrency_during_probe=0,provider_configuration_modified=False,forwarding_8080_modified=False)


def probe_accounting(audit):
    # Include the two initial manual recovery probes, each once by file path.
    audit=Path(audit)
    paths=list((audit/'inference-probes').glob('*.json'))+list(audit.glob('probe-*.json'))
    rows=[json.loads(p.read_text()) for p in paths]
    return dict(probes=len(rows),known_tokens=sum((r.get('usage') or {}).get('total_tokens',0) for r in rows),
        incomplete_probe_bills=sum(not isinstance((r.get('usage') or {}).get('total_tokens'),int) for r in rows),
        definition='Monitoring inference overhead, recorded separately; never attributed to a policy or inserted into matched task savings.')
