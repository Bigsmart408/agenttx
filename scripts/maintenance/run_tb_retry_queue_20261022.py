#!/usr/bin/env python3
"""Adopt existing workers and run one explicitly authorized retry per failed cell."""
import argparse
import collections
import copy
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

PROJECT=Path('/home/bfq/agenttx')
sys.path[:0]=[str(PROJECT),str(PROJECT/'src')]
from experiments.scripts.run_tb_dual_local import (
    cell_command, infrastructure_issue, effects, experiment_configuration,
    CELL_TIMEOUT_S, model_budget_failure_evidence, halts_dispatch, logged_usage)

DATA=Path('/mnt/data/agenttx-runtime')
NAME='tb-dual-native-full-20261022-r17-retry1'
ROOT=DATA/'results'/NAME
AUDIT=DATA/'validation/tb-failed-retry-20261022'
PREVIOUS=DATA/'results/tb-dual-native-full-20261021-r16'
OLD_UNIT='agenttx-tb-dual-full-20261021-r16.service'

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def save(p,d):
    p.parent.mkdir(parents=True,exist_ok=True)
    t=p.with_name(p.name+'.tmp');t.write_text(json.dumps(d,indent=2,ensure_ascii=False)+'\n');t.replace(p)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def state(pid):
    try:return Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split()[0]
    except FileNotFoundError:return None
def key(r):return (r['task'],r['harness'],r['policy'])
def failed(row):
    if row['state']=='infrastructure_error':return True
    return row['state']=='completed' and not json.loads(Path(row['result_path']).read_text()).get('success',False)
def add_retries(cells,retries):
    existing={key(r) for r in retries}
    for row in cells:
        if key(row) in existing or not failed(row):continue
        retries.append(dict(task=row['task'],harness=row['harness'],policy=row['policy'],
            expected_context=row['expected_context'],state='pending',attempt=1,
            original_state=row['state'],original_result_path=row['result_path'],
            original_result_sha256=sha(row['result_path']),retry_reason='user_authorized_failed_or_incomplete_usage_retry'))
        existing.add(key(row))
def select(cells,retries,active):
    counts=collections.Counter(item['retry'] for item in active.values())
    # When both queues are nonempty, keep about four slots for each.
    order=[(retries,True),(cells,False)] if counts[True]<4 else [(cells,False),(retries,True)]
    for rows,retry in order:
        for row in rows:
            if row['state']=='pending':return row,retry
    return None
def result_path(root,row,retry):
    return root/('retry-cells' if retry else 'cells')/row['task']/row['harness']/row['policy']/row['policy']/'host-result.json'
def cleanup_timeout(item):
    pid=item['pid']
    try:os.killpg(pid,signal.SIGTERM)
    except ProcessLookupError:return
    end=time.monotonic()+10
    while state(pid) not in (None,'Z') and time.monotonic()<end:time.sleep(.2)
    if state(pid) not in (None,'Z'):
        try:os.killpg(pid,signal.SIGKILL)
        except ProcessLookupError:pass
    cid=Path(item['path']).parent/'container.cid'
    if cid.exists():subprocess.run(['docker','rm','-f',cid.read_text().strip()],capture_output=True,timeout=30)
def retry_report(cells,retries):
    original={key(r):r for r in cells};latest={};known_first=known_retry=0;unknown=0;success_after_retry=0
    for row in cells:
        if row['state'] not in ('completed','infrastructure_error'):continue
        r=json.loads(Path(row['result_path']).read_text())
        u=r.get('api_usage',{})
        if row['harness']=='codex' and not u.get('complete'):
            u=dict(logged_usage(Path(row['result_path']).parent,'codex',r),complete=False)
        known_first+=u.get('total_tokens') or 0
        unknown+=not bool(u.get('complete'))
        latest[key(row)]=dict(row=row,result=r,tokens=u.get('total_tokens'),complete=bool(u.get('complete')) and row['state']=='completed')
    for row in retries:
        if row['state'] not in ('completed','infrastructure_error'):continue
        r=json.loads(Path(row['result_path']).read_text());u=r.get('api_usage',{})
        if row['harness']=='codex' and not u.get('complete'):
            u=dict(logged_usage(Path(row['result_path']).parent,'codex',r),complete=False)
        known_retry+=u.get('total_tokens') or 0;unknown+=not bool(u.get('complete'))
        prior=latest[key(row)];complete=prior['complete'] and bool(u.get('complete')) and row['state']=='completed'
        latest[key(row)]=dict(row=row,result=r,tokens=(prior['tokens'] or 0)+(u.get('total_tokens') or 0),complete=complete)
        success_after_retry+=bool(r.get('success')) and row['state']=='completed'
    paired={}
    for harness in ('codex','sweagent'):
        by={}
        for (task,h,p),v in latest.items():
            if h==harness and v['complete'] and v['result'].get('success'):by.setdefault(p,{})[task]=v['tokens']
        ca=by.get('causal',{});paired[harness]={}
        for policy,rs in by.items():
            if policy=='causal':continue
            ts=sorted(ca.keys()&rs.keys());a=sum(ca[t] for t in ts);b=sum(rs[t] for t in ts)
            paired[harness][policy]=dict(both_success_pairs=len(ts),causal_cumulative_tokens=a,
                control_cumulative_tokens=b,saving_pct=100*(1-a/b) if b else None)
    return dict(checked_at=now(),retry_counts=dict(collections.Counter(r['state'] for r in retries)),
        retries_successful=success_after_retry,known_first_attempt_tokens=known_first,
        known_retry_tokens=known_retry,known_cumulative_tokens=known_first+known_retry,
        attempts_with_incomplete_usage=unknown,
        cumulative_paired_success=paired,
        accounting_definition='Latest attempted outcome, one retry maximum; cumulative main+replay spend includes the original attempt and retry. Any incomplete attempt excludes that cell from complete-cost comparisons. Known totals are lower bounds when incomplete attempts exist. First-attempt effects are reported separately; never select the better attempt.')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--start',action='store_true');args=parser.parse_args()
    assert args.start
    AUDIT.mkdir(parents=True,exist_ok=True)
    lock=(AUDIT/'coordinator.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    assert not ROOT.exists(),'Use an explicit audited continuation after interruption; do not overwrite existing retry results'
    pointer=json.loads((DATA/'validation/active-tb-dual-native.json').read_text());assert Path(pointer['root'])==PREVIOUS
    pid=pointer['coordinator_pid'];assert state(pid) not in (None,'Z','T')
    assert int(subprocess.check_output(['systemctl','--user','show','--property=MainPID','--value',OLD_UNIT],text=True))==pid
    reg=json.loads((PREVIOUS/'registration.json').read_text());frozen=Path(reg['frozen_source'])
    manifest=json.loads((PREVIOUS/'source-manifest.json').read_text())
    for entry in manifest['files']:assert sha(frozen/entry['path'])==entry['sha256'],entry['path']
    inventory=json.loads(Path(reg['inventory']).read_text())
    evidence={r['task']:r['evidence'] for r in inventory['official_baseline_tasks']+inventory.get('audited_task_baseline_tasks',[])}
    assert reg['workers_total']==8 and reg['min_free_disk_gib']==0
    os.kill(pid,signal.SIGSTOP)
    switched=False
    try:
        time.sleep(.2);assert state(pid)=='T'
        before=(PREVIOUS/'progress.json').read_bytes();save(AUDIT/'previous-progress.json',json.loads(before))
        old_progress=json.loads(before);cells=copy.deepcopy(old_progress['tasks']);retries=[]
        active={};adopted={}
        actual=subprocess.run(['ps','--ppid',str(pid),'-o','pid=,args='],capture_output=True,text=True).stdout
        running=[r for r in cells if r['state']=='running'];registered={r['worker_pid'] for r in running}
        spawned={int(s.split(None,1)[0]) for s in actual.splitlines() if 'bench_tb_image_native.py host ' in s}
        assert spawned<=registered,'Dispatcher between spawn/progress; resume it and retry handoff'
        assert len(running)<=8
        ROOT.mkdir()
        protected={r['result_path']:sha(r['result_path']) for r in cells if r['state'] in ('completed','infrastructure_error')}
        save(AUDIT/'protected-first-results.json',protected)
        save(ROOT/'source-manifest.json',manifest)
        registration=dict(reg,registered_at=now(),cohort=NAME,outcome_based_retry=True,
            coordinator_script='/home/bfq/agenttx-maintenance/run_tb_retry_queue_20261022.py',
            coordinator_script_sha256=sha(__file__),previous_cohort=PREVIOUS.name,
            user_instruction='看看进展，任务失败的需要重启',retry_limit_per_cell=1,
            retries_are_additional_attempts=True,original_result_bytes_unchanged=True,
            first_attempt_and_retry_effects_separate=True,total_model_workers_including_adopted=8,
            original_frozen_worker_sources_unchanged=True,
            continuation=dict(previous_cohort=PREVIOUS.name,adopted_running_cells=len(running),
                prior_outcomes_unchanged=True,unstarted_primary_cells_continued=True))
        save(ROOT/'registration.json',registration);save(AUDIT/'authorization.json',registration)
        for row in running:
            path=result_path(PREVIOUS,row,False);row['result_path']=str(path)
            item=dict(row=row,retry=False,pid=row['worker_pid'],path=str(path),proc=None,log=None,
                started=datetime.datetime.fromisoformat(row['started_at']).timestamp(),adopted=True)
            active[row['worker_pid']]=item;adopted[row['worker_pid']]=item
        add_retries(cells,retries)
        def progress():
            save(ROOT/'progress.json',dict(updated_at=now(),coordinator_pid=os.getpid(),
                counts=dict(collections.Counter(r['state'] for r in cells)),
                retry_counts=dict(collections.Counter(r['state'] for r in retries)),retry_tasks=retries,
                active=[dict(task=i['row']['task'],harness=i['row']['harness'],policy=i['row']['policy'],pid=i['pid'],retry=i['retry']) for i in active.values()],
                stop_reason=stop,disk_free_gib=__import__('shutil').disk_usage(DATA).free/2**30,
                formal_eligible=False,tasks=cells))
        stop=None;progress()
        save(DATA/'validation/active-tb-dual-native.json',dict(cohort=NAME,root=str(ROOT),canary=False,coordinator_pid=os.getpid(),activated_at=now(),retry_authorized=True))
        switched=True;old_finalized=False;last_review=0
        while True:
            for worker,item in list(active.items()):
                proc=item['proc'];alive=proc.poll() is None if proc else state(worker) not in (None,'Z')
                timed_out=alive and time.time()-item['started']>=CELL_TIMEOUT_S
                if alive and not timed_out:continue
                if timed_out:cleanup_timeout(item)
                if proc:proc.wait(timeout=20)
                if item['log']:item['log'].close()
                row=item['row'];path=Path(item['path'])
                if path.exists():result=json.loads(path.read_text())
                else:
                    result=dict(completed=False,success=False,status='missing_worker_result',host_error='Worker exited without a host result; usage unknown',api_usage=dict(complete=False),task=row['task'],harness=row['harness'],policy=row['policy'])
                    save(path,result)
                issue='worker_wall_timeout' if timed_out else infrastructure_issue(result,row['expected_context'])
                row.update(state='infrastructure_error' if issue else 'completed',issue=issue,
                    result_path=str(path),finished_at=now(),worker_returncode=proc.returncode if proc else result.get('container_returncode'))
                budget=model_budget_failure_evidence(result,path) if issue=='incomplete_api_usage' else None
                if budget:row['model_failure_evidence']=budget
                if halts_dispatch(issue,result,budget):
                    # Preserve the affected exception; register it for its single retry.
                    with (AUDIT/'infrastructure-exceptions.jsonl').open('a') as f:f.write(json.dumps(dict(row))+'\n')
                if item['adopted']:
                    old=next(r for r in old_progress['tasks'] if key(r)==key(row));old.update(copy.deepcopy(row))
                del active[worker]
            if not old_finalized and not any(i['adopted'] for i in active.values()):
                os.kill(pid,signal.SIGTERM);os.kill(pid,signal.SIGCONT)
                for _ in range(50):
                    if state(pid) in (None,'Z'):break
                    time.sleep(.1)
                assert state(pid) in (None,'Z')
                # The paused old dispatcher held the global lock until exit.
                # Keep holding it in the replacement before dispatch continues.
                global_lock=(DATA/'validation/tb-dual-native.lock').open('a')
                fcntl.flock(global_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                old_progress.update(updated_at=now(),active=[],stop_reason='user_authorized_retry_handoff',counts=dict(collections.Counter(r['state'] for r in old_progress['tasks'])))
                save(PREVIOUS/'progress.json',old_progress)
                save(PREVIOUS/'execution.json',dict(finished_at=now(),stop_reason='user_authorized_retry_handoff',counts=old_progress['counts'],continuation=NAME))
                assert all(sha(p)==h for p,h in protected.items()),'Original terminal results changed'
                save(AUDIT/'handoff-verification.json',dict(finished_at=now(),original_results_verified=len(protected),workers_interrupted=False))
                old_finalized=True
            add_retries(cells,retries)
            while not stop and len(active)<8:
                selected=select(cells,retries,active)
                if not selected:break
                row,retry=selected;path=result_path(ROOT,row,retry);folder=path.parent.parent
                folder.parent.mkdir(parents=True,exist_ok=True)
                logpath=ROOT/('retry-worker-logs' if retry else 'worker-logs')/row['task']/row['harness']/(row['policy']+'.log');logpath.parent.mkdir(parents=True,exist_ok=True)
                log=logpath.open('w');env=dict(os.environ,PYTHONPATH=f'{frozen}/src:{frozen}',AGENTTX_SKIP_ORPHAN_REAP='1',AGENTTX_NO_PROXY='1',NO_PROXY='*')
                for k in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):env.pop(k,None)
                proc=subprocess.Popen(cell_command(frozen,folder,row,evidence.get(row['task'])),cwd=frozen,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                row.update(state='running',started_at=now(),worker_pid=proc.pid,result_path=str(path))
                active[proc.pid]=dict(row=row,retry=retry,pid=proc.pid,path=str(path),proc=proc,log=log,started=time.time(),adopted=False)
                with (AUDIT/'dispatches.jsonl').open('a') as f:f.write(json.dumps(dict(at=now(),task=row['task'],harness=row['harness'],policy=row['policy'],retry=retry,pid=proc.pid,total_active=len(active)))+'\n')
            progress()
            if time.monotonic()-last_review>=60:
                save(ROOT/'effects.json',dict(checked_at=now(),comparisons=effects(cells,experiment_configuration()),token_definition='First-attempt main+replay only. Authorized retry outcomes and cumulative spend are separate in retry-report.json.'))
                save(ROOT/'retry-effects.json',dict(checked_at=now(),comparisons=effects(retries,experiment_configuration())))
                save(ROOT/'retry-report.json',retry_report(cells,retries));last_review=time.monotonic()
            if not active and not any(r['state']=='pending' for r in cells+retries):break
            time.sleep(5)
        save(ROOT/'execution.json',dict(finished_at=now(),first_attempt_counts=dict(collections.Counter(r['state'] for r in cells)),retry_counts=dict(collections.Counter(r['state'] for r in retries))))
    except BaseException:
        if not switched and state(pid)=='T':os.kill(pid,signal.SIGCONT)
        # After handoff, do not accidentally resume a second dispatcher.
        raise

if __name__=='__main__':main()
