#!/usr/bin/env python3
"""Reconcile naturally finishing workers under an already recorded user hold.

Never launches a cohort or resumes a paused dispatcher, including on error.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from experiments.scripts.continue_tb_after_environment_repairs import process_state
from experiments.scripts.run_tb_dual_local import DATA, effects, infrastructure_issue, now, save


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit',type=Path,required=True)
    args=parser.parse_args()
    assert args.audit.resolve().is_relative_to(DATA/'validation')
    hold=json.loads((args.audit/'registration.json').read_text())
    assert not hold['full_dispatch_allowed'] and not hold['automatic_full_resume']
    root=DATA/'results'/hold['cohort'];pid=hold['coordinator_pid']
    command=Path(f'/proc/{pid}/cmdline').read_bytes()
    assert b'run_tb_dual_local.py' in command and hold['cohort'].encode() in command
    assert process_state(pid)=='T', 'Dispatcher must already be paused'
    progress=json.loads((args.audit/'original-progress.json').read_text())
    running=[r for r in progress['tasks'] if r['state']=='running']
    children=subprocess.check_output(['ps','--ppid',str(pid),'-o','pid=,args='],text=True)
    actual={int(line.split(None,1)[0]) for line in children.splitlines()
            if 'bench_tb_image_native.py host ' in line}
    assert actual<={r['worker_pid'] for r in running}, 'Unrecorded worker; keep held for inspection'
    limit=json.loads((root/'registration.json').read_text())['cell_wall_s']+300
    started=time.monotonic()
    while True:
        active=[r for r in running if process_state(r['worker_pid']) not in {None,'Z'}]
        save(args.audit/'drain-progress.json',dict(checked_at=now(),remaining=len(active),
            automatic_full_resume=False,cells=[{k:r[k] for k in ('task','harness','policy','worker_pid')} for r in active]))
        if not active:break
        if time.monotonic()-started>limit:
            raise RuntimeError('Worker envelope exceeded; dispatcher remains held, inspect live workers')
        time.sleep(10)
    hashes={}
    for row in running:
        path=root/'cells'/row['task']/row['harness']/row['policy']/row['policy']/'host-result.json'
        assert path.is_file(), 'Missing worker evidence; dispatcher remains held: '+str(path)
        raw=path.read_bytes();result=json.loads(raw)
        issue=infrastructure_issue(result,row['expected_context'])
        row.update(state='infrastructure_error' if issue else 'completed',issue=issue,
            result_path=str(path),finished_at=now(),worker_returncode=result.get('container_returncode'))
        hashes[str(path)]=hashlib.sha256(raw).hexdigest()
    assert process_state(pid)=='T'
    os.kill(pid,signal.SIGTERM);os.kill(pid,signal.SIGCONT)
    for _ in range(100):
        if process_state(pid) in {None,'Z'}:break
        time.sleep(.1)
    assert process_state(pid) in {None,'Z'}
    prior_stop=progress.get('stop_reason')
    progress.update(updated_at=now(),active=[],counts=dict(Counter(r['state'] for r in progress['tasks'])),
        stop_reason='user_repair_and_trend_validation_hold',previous_stop_reason=prior_stop)
    save(root/'progress.json',progress)
    config=json.loads((root/'registration.json').read_text())['comparison_configuration']
    save(root/'effects.json',dict(checked_at=now(),comparisons=effects(progress['tasks'],config)))
    execution=dict(finished_at=now(),stop_reason=progress['stop_reason'],previous_stop_reason=prior_stop,
        counts=progress['counts'],formal_eligible=False,all_expected_cells_completed=False,
        hold_audit=str(args.audit),raw_results_modified=False,source_results_sha256=hashes,
        automatic_full_resume=False)
    save(root/'execution.json',execution);save(args.audit/'execution.json',execution)


if __name__=='__main__':main()
