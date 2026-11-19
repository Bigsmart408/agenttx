"""Finite prerequisite repair only for observed stalled official installers."""
import copy
import datetime
import hashlib
import json
from pathlib import Path

D=Path('/mnt/data/agenttx-runtime')
EVIDENCE=D/'validation/tb-live-monitor-20261026/prerequisite-process-evidence'
PINNED={'pytest==8.4.1','psutil==7.0.0','transformers==4.52.4','torch==2.7.1','peft==0.15.0'}


def elapsed_seconds(value):
    days,_,clock=value.partition('-')
    if not clock:clock,days=days,'0'
    parts=[int(x) for x in clock.split(':')]
    return int(days)*86400+sum(x*60**i for i,x in enumerate(reversed(parts)))


def prerequisite_fault(row,evidence_dir=EVIDENCE):
    if row['task']!='hf-train-lora-adapter' or row['state'] not in ('completed','infrastructure_error'):
        return None
    path=Path(row['result_path']);result=json.loads(path.read_text())
    if result.get('prerequisite_cache') or result.get('status')!='verifier_timeout' or result.get('official_tests_rc')!=124:
        return None
    stdout=path.parent/'verifier.stdout.log'
    if not stdout.exists() or stdout.read_text().strip():return None
    for proof in sorted(Path(evidence_dir).glob(row['harness']+'-'+row['policy']+'-*.json'),reverse=True):
        data=json.loads(proof.read_text())
        if data.get('result_path')!=str(path) or data.get('model_returncode')!=124:continue
        if not row['started_at']<=data['at']<=row['finished_at']:continue
        for process in data['installers']:
            argv=process['args'].split()
            if not process['official_verifier_ancestors'] or elapsed_seconds(process['elapsed'])<900:continue
            if len(argv)!=8 or Path(argv[0]).name!='uv' or argv[1:3]!=['pip','install'] or set(argv[3:])!=PINNED:continue
            return dict(classification='observed_official_fixed_dependency_install_stall_then_verifier_timeout',
                        evidence_path=str(proof),evidence_sha256=hashlib.sha256(proof.read_bytes()).hexdigest(),
                        installer_elapsed_s=elapsed_seconds(process['elapsed']),source_result=str(path))
    return None


def register_prerequisite_repairs(cells,retries,cohort,sha,key):
    latest={key(r):r for r in cells}
    for r in retries:
        if r['state'] in ('completed','infrastructure_error'):latest[key(r)]=r
    added=[]
    for k,old in latest.items():
        history=[r for r in retries if key(r)==k]
        if any(r['state']=='running' or r.get('hf_prerequisite_repair_of') for r in history):continue
        proof=prerequisite_fault(old)
        if not proof:continue
        first=next(r for r in cells if key(r)==k)
        pending=next((r for r in history if r['state']=='pending'),None)
        if pending is None:
            pending=dict(task=k[0],harness=k[1],policy=k[2],expected_context=first['expected_context'],state='pending',
                attempt=max([0]+[r.get('attempt',1) for r in history])+1,
                original_state=first['state'],original_result_path=first['result_path'],original_result_sha256=sha(first['result_path']))
            retries.append(pending)
        pending.update(hf_prerequisite_repair_of=old['result_path'],hf_prerequisite_repair_cohort=cohort,
            hf_prerequisite_failure_evidence=proof,previous_attempt_path=old['result_path'],previous_attempt_sha256=sha(old['result_path']),
            retry_reason='one_audited_offline_fixed_prerequisite_environment_repair')
        for field in ('download_proxy_retry','download_retry_cohort','download_failure_evidence','download_retry_of'):
            if field in old:pending[field]=copy.deepcopy(old[field])
        added.append(pending)
    return added
