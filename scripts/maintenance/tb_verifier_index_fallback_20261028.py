"""Evidence-only classification and one retry for failed verifier prerequisites."""
import hashlib
import json
from pathlib import Path

from experiments.scripts.tb_verifier_download_fallback import CONTRACT


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def failure_evidence(row):
    if row['state'] not in {'completed','infrastructure_error'}:
        return None
    path=Path(row['result_path'])
    result=json.loads(path.read_text())
    if result.get('success') or result.get('official_success') or result.get('verifier_download_contract')==CONTRACT:
        return None
    log=path.parent/'verifier.stderr.log'
    if not log.is_file():
        return None
    text=log.read_text(errors='replace')
    legacy_timeout=all(s in text for s in ('+ uv pip install pytest==8.4.1',
        'Failed to fetch: `https://mirrors.aliyun.com/pypi/simple/pytest/`',
        'operation timed out','Failed to spawn: `pytest`','No such file or directory'))
    wheel_timeout=all(s in text for s in ('+ uv pip install ',
        'Failed to download distribution due to network timeout.',
        'Failed to spawn: `pytest`','No such file or directory'))
    if not (legacy_timeout or wheel_timeout):
        return None
    stdout=path.parent/'verifier.stdout.log'
    if stdout.is_file() and any(s in stdout.read_text(errors='replace') for s in ('test session starts','short test summary info')):
        return None
    return dict(reason=('official_verifier_wheel_stream_network_timeout_before_tests' if wheel_timeout
                        else 'official_verifier_pytest_index_timeout_before_tests'),
                result_path=str(path),result_sha256=sha(path),source=str(log),source_sha256=sha(log),
                official_tests_executed=False,model_returncode=result.get('model_returncode'),
                independent_model_failure_preserved=result.get('model_returncode')!=0)


def register(cells,retries,cohort,key):
    latest={key(r):r for r in cells}
    for row in retries:
        if row['state'] in {'completed','infrastructure_error'}:latest[key(row)]=row
    added=[]
    for k,old in latest.items():
        history=[r for r in retries if key(r)==k]
        if any(r['state']=='running' or r.get('verifier_index_repair_cohort')==cohort for r in history):
            continue
        proof=failure_evidence(old)
        if proof is None:continue
        first=next(r for r in cells if key(r)==k)
        pending=next((r for r in history if r['state']=='pending'),None)
        if pending is None:
            pending=dict(task=k[0],harness=k[1],policy=k[2],state='pending',
                         expected_context=first['expected_context'],attempt=max([0]+[r.get('attempt',1) for r in history])+1,
                         original_state=first['state'],original_result_path=first['result_path'],
                         original_result_sha256=sha(first['result_path']))
            retries.append(pending)
        pending.update(verifier_index_repair_of=old['result_path'],verifier_index_repair_cohort=cohort,
                       verifier_index_failure_evidence=proof,previous_attempt_path=old['result_path'],
                       previous_attempt_sha256=sha(old['result_path']),
                       retry_reason='one_evidenced_verifier_prerequisite_index_fallback_repair')
        added.append(pending)
    return added


def annotate(rows):
    for row in rows:
        proof=failure_evidence(row)
        if proof:row['verifier_network_exclusion']=proof
