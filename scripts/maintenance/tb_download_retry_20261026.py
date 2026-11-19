"""Retry demonstrated remote-download failures without dropping rows or bills."""
import json
import re
from pathlib import Path

from tb_network_deferrals_20261026 import verifier_network_evidence
from tb_provider_health_20261026 import classify_provider_failure

REMOTE = re.compile(r'(?:github\.com|githubusercontent\.com|huggingface\.co|hf\.co)', re.I)
ERROR = re.compile(r'Connection refused|timed out|Network is unreachable|Temporary failure in name resolution|Failed to establish a new connection',re.I)


def mnist_dataset_download_evidence(row):
    """Recognize the official HF filesystem traceback when no HTTPS URL prints."""
    if row.get('task') != 'mnist-learning-fix':
        return None
    path = Path(row['result_path'])
    log = path.parent / 'verifier.stdout.log'
    if not log.exists():
        return None
    import hashlib
    raw = log.read_bytes()
    # Keep evidence within one pytest failure, so another test's connection
    # error cannot turn a functional assertion into a download failure.
    for block in re.split(r'(?m)^_{5,}.*_{5,}\s*$', raw.decode(errors='replace')):
        if not re.search(r'(?m)^/tests/model\.py:\d+: in load\s*$', block):
            continue
        if not re.search(r'pd\.read_parquet\([\"\']hf://datasets/ylecun/mnist/[\"\']', block):
            continue
        if 'huggingface_hub/hf_file_system.py:' not in block:
            continue
        error = re.search(
            r'(?m)^E\s+http(?:x|core)2?\.(?:ConnectError|ConnectTimeout):[^\n]*', block)
        if not error or not ERROR.search(error.group(0)):
            continue
        return dict(classification='official_hf_filesystem_dataset_network_failure',
                    domain='huggingface.co', dataset_uri='hf://datasets/ylecun/mnist/',
                    source=str(log), source_sha256=hashlib.sha256(raw).hexdigest(),
                    error=error.group(0).strip(), result_path=str(path),
                    result_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    return None


def lean_toolchain_download_evidence(row):
    """Recognize Elan's actual failed download, never an agent's explanation.

    SWE's Rich output wraps URLs, and model-stage tool output is absent from
    verifier diagnostics. Restrict this repair to the registered Lean archive
    and to a real observation containing Elan/libcurl's low-speed timeout.
    """
    if (row.get('task') != 'lean4-proof' or row.get('harness') != 'sweagent'
            or row.get('state') not in ('completed', 'infrastructure_error')):
        return None
    path = Path(row['result_path'])
    if not path.exists() or json.loads(path.read_text()).get('success'):
        return None
    import hashlib
    for name in ('container.recovery.stdout.log', 'container.stdout.log'):
        log = path.parent / name
        if not log.exists():
            continue
        raw = log.read_bytes()
        text = raw.decode(errors='replace')
        observations = re.findall(r'<observation>(.*?)</observation>', text, re.S)
        for observation in observations:
            compact = re.sub(r'\s+', '', observation)
            if not all(part in compact for part in (
                'error:couldnotdownloadfilefrom',
                'https://releases.lean-lang.org/lean4/v4.21.0/lean-4.21.0-linux.tar.zst',
                'info:causedby:[28]Timeoutwasreached(Operationtooslow.',
            )):
                continue
            # Confirm the release endpoint's GitHub redirect in actual tool
            # output; the existing proxy allowlist itself stays unchanged.
            redirect = any(
                'location:https://github.com/leanprover/lean4/releases/download/v4.21.0/lean-4.21.0-linux.tar.zst'
                in re.sub(r'\s+', '', item)
                for item in observations
            )
            if not redirect:
                continue
            return dict(classification='elan_tool_output_download_low_speed_timeout',
                domain='github.com', source=str(log),
                source_sha256=hashlib.sha256(raw).hexdigest(),
                error='libcurl 28: low-speed download timeout',
                dependency='leanprover/lean4:v4.21.0',
                actual_tool_observation=True, model_prose_is_evidence=False,
                result_path=str(path),
                result_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    return None


def download_evidence(row):
    verifier=verifier_network_evidence(row)
    if verifier:
        return dict(verifier,classification='official_verifier_remote_download_failed')
    if row.get('state') not in ('completed','infrastructure_error'):
        return None
    path=Path(row['result_path']);result=json.loads(path.read_text())
    if result.get('success'):
        return None
    import hashlib
    for name in ('verifier.stdout.log','verifier.stderr.log','failure-diagnostics.json'):
        log=path.parent/name
        if not log.exists():continue
        raw=log.read_bytes();text=raw.decode(errors='replace')
        for match in REMOTE.finditer(text):
            excerpt=text[max(0,match.start()-250):match.end()+850]
            error=ERROR.search(excerpt)
            if error:
                return dict(classification='explicit_remote_download_network_failure',domain=match.group(0),
                    source=str(log),source_sha256=hashlib.sha256(raw).hexdigest(),error=error.group(0),
                    result_path=str(path),result_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    return mnist_dataset_download_evidence(row) or lean_toolchain_download_evidence(row)


def register_download_retries(cells,retries,cohort,sha,key):
    latest={key(r):r for r in cells}
    for row in retries:
        if row['state'] in ('completed','infrastructure_error'):latest[key(row)]=row
    changed=[]
    for k,previous in latest.items():
        evidence=download_evidence(previous)
        if evidence is None:continue
        history=[r for r in retries if key(r)==k]
        if any(r.get('download_retry_cohort')==cohort for r in history):continue
        if any(r['state']=='running' for r in history):continue
        pending=next((r for r in history if r['state']=='pending'),None)
        if pending is None:
            first=next(r for r in cells if key(r)==k)
            pending=dict(task=k[0],harness=k[1],policy=k[2],expected_context=first['expected_context'],state='pending',
                attempt=max([0]+[r.get('attempt',1) for r in history])+1,original_state=first['state'],
                original_result_path=first['result_path'],original_result_sha256=sha(first['result_path']))
            retries.append(pending)
        pending.update(download_proxy_retry=True,download_retry_cohort=cohort,download_failure_evidence=evidence,
            download_retry_of=previous['result_path'],previous_attempt_path=previous['result_path'],
            previous_attempt_sha256=sha(previous['result_path']),retry_reason='user_authorized_selective_proxy_retry_after_remote_download_failure')
        changed.append(pending)
    return changed


def requires_proxy(row):
    # A provider502 retry is separate: never route the model endpoint remotely.
    return bool(row.get('download_proxy_retry'))


def register_provider_retry(cells,retries,add_provider_repairs):
    before={id(r) for r in retries}
    added=add_provider_repairs(cells,retries)
    # Preserve transport treatment for a provider interruption of a download retry.
    for row in retries:
        if id(row) in before or not row.get('provider_repair_of'):continue
        previous=next((r for r in retries if r.get('result_path')==row['provider_repair_of']),None)
        if previous and previous.get('download_proxy_retry'):
            row['download_proxy_retry']=True
    return added
