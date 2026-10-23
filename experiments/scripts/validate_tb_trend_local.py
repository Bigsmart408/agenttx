#!/usr/bin/env python3
"""Run one fixed diagnostic panel; never launch or authorize a full experiment."""
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import statistics
import subprocess
import time

from experiments.scripts.run_tb_dual_local import (
    DATA, ROOT, CELL_TIMEOUT_S, POLICIES, CONTEXTS, cell_command,
    experiment_configuration, freeze, infrastructure_issue, result_configuration, result_success, save,
)

TASKS=('hello-world','csv-to-parquet','build-stp','build-tcc-qemu','catch-me-if-you-can')
CONTROLS=('crash_no_rollback','temporal_checkpoint','whole_branch_abort')
BASE=DATA/'validation/tb-trend-validation-20261021-r1'
HOLD=DATA/'validation/tb-user-validation-hold-20261021-r1'


def now():return datetime.now(timezone.utc).isoformat()


def assess(cells, tasks=TASKS):
    """Use four-way successful common cases; keep every failure in coverage."""
    by={};issues=[];pending=[];sources=[]
    for cell in cells:
        key=(cell['task'],cell['harness'],cell['policy'])
        if cell['state'] not in {'completed','infrastructure_error'}:
            pending.append(key);continue
        path=Path(cell['result_path'])
        if not path.is_file():issues.append(dict(cell=key,issue='missing_result'));continue
        row=json.loads(path.read_text());sources.append(dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        issue=infrastructure_issue(row,CONTEXTS[cell['policy']])
        if issue or cell['state']!='completed':issues.append(dict(cell=key,issue=issue or cell.get('issue')));continue
        if result_configuration(row,path)!=experiment_configuration():
            issues.append(dict(cell=key,issue='configuration_mismatch'));continue
        by[key]=row
    comparisons={}
    for control in CONTROLS:
        common=[t for t in tasks if all((t,h,p) in by and result_success(by[t,h,p])
                 for h in ('codex','sweagent') for p in ('causal',control))]
        report={}
        for h in ('codex','sweagent'):
            a=[by[t,h,'causal']['api_usage']['total_tokens'] for t in common]
            b=[by[t,h,control]['api_usage']['total_tokens'] for t in common]
            saving=100*(1-sum(a)/sum(b)) if sum(b)>0 else None
            median=statistics.median(100*(1-x/y) for x,y in zip(a,b)) if b and all(y>0 for y in b) else None
            successes=lambda p:sum(result_success(by[t,h,p]) for t in tasks if (t,h,p) in by)
            ca=successes('causal');co=successes(control)
            report[h]=dict(causal_success=ca,control_success=co,causal_tokens=sum(a),control_tokens=sum(b),
                causal_mean_tokens=sum(a)/len(a) if a else None,control_mean_tokens=sum(b)/len(b) if b else None,
                total_saving_pct=saving,median_case_saving_pct=median,
                passes=bool(len(common)>=3 and saving is not None and saving>0
                            and median is not None and median>=0 and ca>=co))
        comparisons[control]=dict(common_success_tasks=common,minimum_common_pairs=3,harnesses=report,
            passes=all(v['passes'] for v in report.values()))
    expected={(t,h,p) for t in tasks for h in ('codex','sweagent') for p in POLICIES}
    actual={(c['task'],c['harness'],c['policy']) for c in cells}
    complete=not pending and actual==expected and len(cells)==len(expected)
    return dict(checked_at=now(),panel_complete=complete,pending=len(pending),infrastructure_issues=issues,
        comparisons=comparisons,gate_passed=bool(complete and not issues and all(v['passes'] for v in comparisons.values())),
        full_dispatch_authorized=False,automatic_full_resume=False,source_results=sources,
        token_definition='Recorded complete API main + replay; no zero imputation; four-way common successful tasks',
        interpretation='Diagnostic panel only; a pass does not guarantee a full-suite ranking.')


def main():
    assert os.environ.get('DOCKER_HOST')=='unix:///run/agenttx-docker/docker.sock'
    BASE.mkdir(exist_ok=False)
    inventory_path=DATA/'validation/tb-usability-inventory-20261021-r14.json'
    inventory=json.loads(inventory_path.read_text())
    evidence={r['task']:r['evidence'] for r in inventory['official_baseline_tasks']+inventory.get('audited_task_baseline_tasks',[])}
    ready=set(evidence)|set(inventory['official_positive_negative'])
    assert set(TASKS)<=ready
    frozen,manifest=freeze('tb-dual-native-trend-validation-20261021-r1')
    registration=dict(registered_at=now(),purpose='repair_and_trend_validation_only',tasks=TASKS,policies=POLICIES,
        harnesses=['codex','sweagent'],expected_cells=len(TASKS)*len(POLICIES)*2,workers_total=8,
        contexts=CONTEXTS,configuration=experiment_configuration(),source_sha256=manifest['sha256'],frozen_source=str(frozen),
        inventory=str(inventory_path),inventory_sha256=hashlib.sha256(inventory_path.read_bytes()).hexdigest(),
        repeats=1,outcome_based_retry=False,automatic_task_replacement=False,
        automatic_full_resume=False,full_dispatch_authorized=False,gold_solutions_used=False,
        gate=dict(primary_controls=CONTROLS,min_four_way_success_pairs=3,
            each_harness_total_saving_pct='>0',each_harness_median_case_saving_pct='>=0',
            each_harness_causal_success_count='>= respective control on full fixed panel',
            all_cells_terminal=True,infrastructure_exceptions_allowed=0,
            clean_and_ideal='reported reference arms, no stochastic exact-rank requirement'))
    save(BASE/'registration.json',registration);save(BASE/'source-manifest.json',manifest)
    cells=[dict(task=t,harness=h,policy=p,state='pending',expected_context=CONTEXTS[p])
           for t in TASKS for p in POLICIES for h in ('sweagent','codex')]
    def progress(phase,stop=None):
        save(BASE/'progress.json',dict(updated_at=now(),phase=phase,counts=dict(Counter(r['state'] for r in cells)),
            stop_reason=stop,full_dispatch_authorized=False,tasks=cells))
    # The old two workers retain their natural deadlines. Do not overlap with
    # them or bypass the queue lock while their dispatcher is being reconciled.
    while not (HOLD/'execution.json').exists():
        progress('waiting_for_full_queue_drain');time.sleep(10)
    lock=(DATA/'validation/tb-dual-native.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    active={};stop=None
    while True:
        for pid,(proc,row,log,started) in list(active.items()):
            expired=time.monotonic()-started>=CELL_TIMEOUT_S
            if proc.poll() is None and not expired:continue
            if proc.poll() is None:
                os.killpg(pid,signal.SIGTERM)
                try:proc.wait(timeout=10)
                except subprocess.TimeoutExpired:os.killpg(pid,signal.SIGKILL);proc.wait()
                cid=BASE/'cells'/row['task']/row['harness']/row['policy']/row['policy']/'container.cid'
                if cid.exists():subprocess.run(['docker','rm','-f',cid.read_text().strip()],capture_output=True,timeout=30)
            log.close()
            path=BASE/'cells'/row['task']/row['harness']/row['policy']/row['policy']/'host-result.json'
            result=json.loads(path.read_text()) if path.is_file() else {}
            issue='worker_wall_timeout' if expired else infrastructure_issue(result,row['expected_context'])
            row.update(state='infrastructure_error' if issue else 'completed',issue=issue,result_path=str(path),
                finished_at=now(),worker_returncode=proc.returncode)
            if issue:stop=f"{row['task']}/{row['harness']}/{row['policy']}: {issue}"
            del active[pid]
        if shutil.disk_usage(DATA).free<350*2**30:stop=stop or 'data_disk_below_350_gib'
        for row in cells:
            if stop or len(active)>=8:break
            if row['state']!='pending':continue
            folder=BASE/'cells'/row['task']/row['harness']/row['policy'];folder.parent.mkdir(parents=True,exist_ok=True)
            logs=BASE/'worker-logs'/row['task']/row['harness'];logs.mkdir(parents=True,exist_ok=True)
            log=(logs/(row['policy']+'.log')).open('w')
            env=dict(os.environ,PYTHONPATH=f'{frozen}/src:{frozen}',AGENTTX_SKIP_ORPHAN_REAP='1',AGENTTX_NO_PROXY='1',NO_PROXY='*')
            for k in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):env.pop(k,None)
            proc=subprocess.Popen(cell_command(frozen,folder,row,evidence.get(row['task'])),cwd=frozen,
                env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            row.update(state='running',worker_pid=proc.pid,started_at=now());active[proc.pid]=(proc,row,log,time.monotonic())
        progress('validating_fixed_panel',stop)
        save(BASE/'trend-gate.json',assess(cells))
        if not active and (stop or all(r['state']!='pending' for r in cells)):break
        time.sleep(5)
    gate=assess(cells);save(BASE/'trend-gate.json',gate)
    progress('validation_complete' if not stop else 'validation_blocked',stop)
    save(BASE/'execution.json',dict(finished_at=now(),stop_reason=stop,gate_passed=gate['gate_passed'],
        full_dispatch_authorized=False,automatic_full_resume=False))


if __name__=='__main__':main()
