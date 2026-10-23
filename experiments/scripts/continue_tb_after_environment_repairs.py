#!/usr/bin/env python3
"""Drain a frozen coordinator, retain every terminal result, then admit repaired tasks."""
import argparse
import atexit
from collections import Counter
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import signal
import shlex
import subprocess
import time
from experiments.scripts.run_tb_dual_local import (
    DATA,PYTHON,ROOT,save,now,infrastructure_issue,effects,resume_protocol_changes,full_dispatch_blocked)
from experiments.scripts.validate_tb_ready_local import digest


def process_state(pid):
    try:return Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split()[0]
    except FileNotFoundError:return None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from-cohort',required=True);parser.add_argument('--to-cohort',required=True)
    parser.add_argument('--inventory-output',type=Path,required=True)
    parser.add_argument('--audit',type=Path,required=True)
    parser.add_argument('--allow-protocol-update',action='store_true')
    args=parser.parse_args()
    if full_dispatch_blocked():
        raise RuntimeError('Full continuation is held pending repair/trend validation')
    old=DATA/'results'/args.from_cohort
    assert old.is_dir() and args.to_cohort.startswith('tb-dual-native-')
    assert Path(args.from_cohort).name==args.from_cohort and Path(args.to_cohort).name==args.to_cohort
    assert args.inventory_output.resolve().is_relative_to(DATA/'validation')
    assert args.audit.resolve().is_relative_to(DATA/'validation')
    args.audit.mkdir(exist_ok=False)
    prior_registration=json.loads((old/'registration.json').read_text())
    changes=resume_protocol_changes(prior_registration,args.allow_protocol_update)
    pointer=json.loads((DATA/'validation/active-tb-dual-native.json').read_text())
    assert pointer['cohort']==args.from_cohort
    pid=pointer['coordinator_pid']
    unit='agenttx-'+args.from_cohort.replace('tb-dual-native-','tb-dual-')+'.service'
    mainpid=int(subprocess.check_output(['systemctl','--user','show','--property=MainPID','--value',unit],text=True).strip())
    assert mainpid==pid and process_state(pid) not in {None,'Z','T'}
    cmd=Path('/proc/'+str(pid)+'/cmdline').read_bytes()
    assert b'run_tb_dual_local.py' in cmd and args.from_cohort.encode() in cmd
    # Pause only the dispatcher. Its independent workers keep running normally.
    os.kill(pid,signal.SIGSTOP)
    def resume_on_error():
        if process_state(pid)=='T':os.kill(pid,signal.SIGCONT)
    atexit.register(resume_on_error)
    time.sleep(.2);assert process_state(pid)=='T'
    original=(old/'progress.json').read_bytes();(args.audit/'original-progress.json').write_bytes(original)
    progress=json.loads(original)
    drain_timeout=prior_registration['cell_wall_s']+300
    running=[r for r in progress['tasks'] if r['state']=='running']
    assert len(running)<=8
    recorded={r['worker_pid'] for r in running}
    children=subprocess.check_output(['ps','--ppid',str(pid),'-o','pid=,args='],text=True)
    actual={int(line.split(None,1)[0]) for line in children.splitlines() if 'bench_tb_image_native.py host ' in line}
    assert actual<=recorded,'Dispatcher stopped between spawning a worker and saving progress; resumed safely'
    save(args.audit/'registration.json',dict(started_at=now(),from_cohort=args.from_cohort,
        to_cohort=args.to_cohort,coordinator_pid=pid,old_unit=unit,active_cells=len(running),
        original_progress_sha256=digest(args.audit/'original-progress.json'),
        allow_protocol_update=args.allow_protocol_update,drain_timeout_s=drain_timeout,
        protocol_changes=changes,
        workers_interrupted=False,raw_results_modified=False,outcome_based_retry=False))
    started=time.monotonic()
    while True:
        active=[r for r in running if process_state(r['worker_pid']) not in {None,'Z'}]
        save(args.audit/'drain-progress.json',dict(checked_at=now(),remaining=len(active),
            cells=[{k:r[k] for k in ['task','harness','policy','worker_pid']} for r in active]))
        if not active:break
        if time.monotonic()-started>drain_timeout:
            os.kill(pid,signal.SIGCONT)
            raise RuntimeError('Drain exceeded registered worker envelope; original coordinator resumed')
        time.sleep(10)
    hashes={};reconciled=[]
    for row in running:
        path=old/'cells'/row['task']/row['harness']/row['policy']/row['policy']/'host-result.json'
        assert path.is_file(),'Missing result; do not invent task outcomes: '+str(path)
        result=json.loads(path.read_text());issue=infrastructure_issue(result,row['expected_context'])
        row.update(state='infrastructure_error' if issue else 'completed',issue=issue,
            result_path=str(path),finished_at=now(),worker_returncode=result.get('container_returncode'))
        hashes[str(path)]=digest(path);reconciled.append({k:row[k] for k in ['task','harness','policy','state','issue']})
    # SIGTERM is pending while stopped; SIGCONT delivers it before any dispatch.
    os.kill(pid,signal.SIGTERM);os.kill(pid,signal.SIGCONT)
    for _ in range(50):
        if process_state(pid) in {None,'Z'}:break
        time.sleep(.1)
    assert process_state(pid) in {None,'Z'}
    progress.update(updated_at=now(),counts=dict(Counter(r['state'] for r in progress['tasks'])),
        active=[],stop_reason='controlled_environment_update')
    save(old/'progress.json',progress)
    save(old/'effects.json',dict(checked_at=now(),comparisons=effects(progress['tasks'])))
    save(old/'execution.json',dict(finished_at=now(),stop_reason='controlled_environment_update',
        counts=progress['counts'],formal_eligible=False,all_expected_cells_completed=False,
        continuation_audit=str(args.audit)))
    subprocess.run([str(PYTHON),str(ROOT/'experiments/scripts/inventory_tb_environment_repairs.py'),
        '--output',str(args.inventory_output)],cwd=ROOT,check=True,timeout=90)
    service='agenttx-'+args.to_cohort.replace('tb-dual-native-','tb-dual-')
    command=['systemd-run','--user','--unit='+service,'--property=WorkingDirectory='+str(ROOT),
        '/bin/bash','-c',
        'source scripts/agenttx-local-env.sh; export PYTHONPATH=/home/bfq/agenttx/src:/home/bfq/agenttx; exec '+str(PYTHON)+
        ' -u experiments/scripts/run_tb_dual_local.py --name '+shlex.quote(args.to_cohort)+' --resume-from '+shlex.quote(str(old))+
        ' --inventory '+shlex.quote(str(args.inventory_output))+
        (' --allow-protocol-update' if args.allow_protocol_update else '')]
    subprocess.run(command,cwd=ROOT,check=True,timeout=30)
    save(args.audit/'execution.json',dict(finished_at=now(),reconciled=reconciled,source_results_sha256=hashes,
        new_service=service,inventory=str(args.inventory_output),inventory_sha256=digest(args.inventory_output),
        workers_interrupted=False,raw_results_modified=False,outcome_based_retry=False))


if __name__=='__main__':main()
