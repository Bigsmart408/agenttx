#!/usr/bin/env python3
"""Verify retirement or drain, validate six arms, then launch the registered profile."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from experiments.scripts import run_swe_monitored_local as runner

DATA=Path('/mnt/data/agenttx-runtime')
PREVIOUS='sweagent-swe-local-monitored-20261018-full-r5'
CANARY='sweagent-swe-local-monitored-20261018-selective-canary-r1'
FULL='sweagent-swe-local-monitored-20261018-selective-full-r1'
STATE=DATA/'validation/swe-selective-launch-20261018.json'
PROOF=DATA/'validation/swe-selective-six-arm-canary-20261018.json'


def record(stage,**extra):
    runner.save(STATE,dict(updated_at=datetime.now(timezone.utc).isoformat(),stage=stage,
                          supervisor_pid=os.getpid(),previous_cohort=PREVIOUS,
                          canary_cohort=CANARY,full_cohort=FULL,
                          reset_proof=os.environ.get('AGENTTX_SWE_RESET_PROOF'),
                          context_by_policy=runner.context_by_policy(),**extra))


def run_cohort(name,*args):
    env=dict(os.environ,AGENTTX_SWE_MONITORED_RUN=name,AGENTTX_SWE_CANARY_PROOF=str(PROOF))
    log_path=DATA/'logs'/(name+'.log')
    with log_path.open('x') as log:
        process=subprocess.Popen([sys.executable,'-u',str(ROOT/'experiments/scripts/run_swe_monitored_local.py'),*args],
                                  cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
        record('validating_six_arms' if name==CANARY else 'running_full_suite',coordinator_pid=process.pid,log=str(log_path))
        returncode=process.wait()
    if returncode:raise RuntimeError(f'{name} exited {returncode}; inspect {log_path}')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--use-existing-canary',action='store_true',help='Launch the full suite using existing infrastructure-valid attempts, including task failures')
    args=parser.parse_args()
    assert os.geteuid()==0
    assert os.environ['AGENTTX_SWE_CONTEXT_PROFILE']=='selective-compact-v1'
    assert len(runner.POLICIES)==6
    assert not (DATA/'results'/FULL).exists(), 'never overwrite a cohort'
    assert args.use_existing_canary or not (DATA/'results'/CANARY).exists(), 'never overwrite a canary'
    reset_path=os.environ.get('AGENTTX_SWE_RESET_PROOF')
    if reset_path:
        reset=json.loads(Path(reset_path).read_text())
        assert reset['user_requested_reset'] and reset['cleanup_complete']
        assert PREVIOUS in reset['deleted_cohorts']
        assert not (DATA/'results'/PREVIOUS).exists()
        assert not reset['remaining_old_processes'] and not reset['remaining_eval_containers']
        record('user_requested_reset_verified')
    else:
        record('waiting_for_previous_workers_to_drain')
        while not (DATA/'results'/PREVIOUS/'execution.json').exists():time.sleep(5)
    if not args.use_existing_canary:run_cohort(CANARY)
    root=DATA/'results'/CANARY
    progress=json.loads((root/'progress.json').read_text())
    rows=[]
    for policy,context in runner.context_by_policy().items():
        path=root/'cells/psf__requests-1963'/policy/'result.json'
        row=json.loads(path.read_text())
        assert runner.infrastructure_issue(row,(path.parent/'worker.log').read_text(errors='replace'),context) is None
        rows.append(dict(policy=policy,result=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),actual_context=row['recovery_context_variant'],task_success=row['success']))
    assert all(r['state']=='completed' for r in progress['tasks'][:6])
    runner.save(PROOF,dict(cohort=CANARY,infrastructure_valid=6,task_successes=sum(r['task_success'] for r in rows),attempts=6,rows=rows,
                           gate='infrastructure_and_context_only',task_success_required=False,
                           context_profile='selective-compact-v1',context_by_policy=runner.context_by_policy(),
                           checked_at=datetime.now(timezone.utc).isoformat()))
    # This checks the actual result hashes, contexts and frozen execution core.
    runner.full_suite_gate(PROOF,ROOT)
    record('six_arm_infrastructure_validated',task_successes=sum(r['task_success'] for r in rows),task_success_required=False)
    run_cohort(FULL,'--full-suite')
    record('full_cohort_ended',execution=json.loads((DATA/'results'/FULL/'execution.json').read_text()))


if __name__=='__main__':
    try:main()
    except Exception as exc:
        record('attention_required',error=type(exc).__name__+': '+str(exc))
        raise
