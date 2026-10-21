#!/usr/bin/env python3
"""Durable TB queues for both image-native harnesses, eight workers in total."""
from __future__ import annotations

import argparse
import copy
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from experiments.scripts.bench_tb_image_native import (
    POLICIES, CONTEXTS, PROTOCOL, COMPLETION_CONTRACT, SUCCESS_POLICY,
    MODEL_TIMEOUT_S, VERIFIER_TIMEOUT_S, logged_usage, result_success)
from experiments.scripts.experiment_provenance import source_manifest
from experiments.workloads import terminal_bench_suite as tb

DATA=Path('/mnt/data/agenttx-runtime')
PYTHON=Path('/home/bfq/.venvs/agenttx/bin/python')
PROFILE=Path('/home/bfq/.config/agenttx/experiment-20261017')
SWE=Path('/home/bfq/agenttx-deps/SWE-agent-3ea751c087f32b16e039a2233dd6eefecef325d5')
CODEX=Path('/home/bfq/.local/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex')
TOOLS=DATA/'validation/tb-validation-tools-20261018'
CELL_TIMEOUT_S=2*MODEL_TIMEOUT_S+VERIFIER_TIMEOUT_S+600


def experiment_configuration():
    return dict(protocol=PROTOCOL,completion_contract=COMPLETION_CONTRACT,
                model_session_wall_s=MODEL_TIMEOUT_S,verifier_wall_s=VERIFIER_TIMEOUT_S)


def configuration_id(configuration):
    normalized=dict(configuration)
    for field in ('model_session_wall_s','verifier_wall_s'):
        normalized[field]=float(normalized[field])
    return hashlib.sha256(json.dumps(normalized,sort_keys=True).encode()).hexdigest()


def result_configuration(result,path):
    # Old result files do not all store limits; use their own cell launch
    # registration, never the current continuation's longer limits.
    launch=path.parent.parent/'registration.json'
    registration=json.loads(launch.read_text()) if launch.is_file() else {}
    return dict(protocol=result.get('protocol',registration.get('protocol','unknown')),
        completion_contract=result.get('completion_contract',registration.get('completion_contract','legacy-implicit-cleanup')),
        model_session_wall_s=result.get('model_session_wall_s',registration.get('wall_timeout',900)),
        verifier_wall_s=result.get('verifier_wall_s',registration.get('verifier_timeout',900)))


def resume_protocol_changes(prior, allow_update):
    for field,expected in dict(model='gpt-5.6-luna',reasoning_effort='max',fault_seed='20261019',contexts=CONTEXTS).items():
        assert prior[field]==expected, 'resume protocol configuration differs: '+field
    expected=dict(**experiment_configuration(),cell_wall_s=CELL_TIMEOUT_S,success_policy=SUCCESS_POLICY)
    defaults=dict(protocol='agenttx.tb-image-native-recovery/v4',completion_contract='legacy-implicit-cleanup',
                  success_policy='require-normal-model-exit/v1')
    changes={field:dict(previous=prior.get(field,defaults.get(field)),current=value)
             for field,value in expected.items() if prior.get(field,defaults.get(field))!=value}
    assert not changes or allow_update, 'explicit --allow-protocol-update required for changed limits or completion contract'
    return changes


def now(): return datetime.now(timezone.utc).isoformat()


def save(path,value):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,indent=2)+'\n');temp.replace(path)


def freeze(name):
    destination=Path('/home/bfq/agenttx-executions')/name/'project'
    destination.mkdir(parents=True,exist_ok=False)
    manifest=source_manifest(ROOT)
    for entry in manifest['files']:
        src=ROOT/entry['path'];dst=destination/entry['path']
        dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
    assert source_manifest(destination)['sha256']==manifest['sha256']
    return destination,manifest


def cell_command(frozen,folder,row,evidence):
    command=[str(PYTHON),'-u',str(frozen/'experiments/scripts/bench_tb_image_native.py'),'host',
        '--task',row['task'],'--harness',row['harness'],'--policies',row['policy'],
        '--output-dir',str(folder),'--timeout',str(MODEL_TIMEOUT_S),'--verifier-timeout',str(VERIFIER_TIMEOUT_S),
        '--fault-seed','20261019','--cache-dir',str(DATA/'cache'),
        '--profile-dir',str(PROFILE),'--python',str(PYTHON),
        '--swe-agent-root',str(SWE),'--codex-bin',str(CODEX),
        '--uv',str(TOOLS/'bin/uv'),'--tiktoken-cache',str(DATA/'tiktoken-cache'),
        '--task-user-adapter','--static-strace',str(TOOLS/'static-strace/strace-6.13+ds/src/strace')]
    if evidence:command+=['--verifier-evidence',str(evidence)]
    return command


def infrastructure_issue(row,expected):
    if not row.get('completed'):return row.get('error') or row.get('status') or 'missing_completed_result'
    if row.get('context_variant')!=expected:return 'recovery_context_mismatch'
    if row.get('agent_runtime')!='official_task_container' or row.get('verifier_runtime')!='same_official_task_container':
        return 'agent_verifier_runtime_mismatch'
    if not row.get('conversation_matches_workspace'):return 'conversation_workspace_mismatch'
    if not row.get('api_usage',{}).get('complete'):return 'incomplete_api_usage'
    return None


def halts_dispatch(issue, result):
    # A recognized model deadline is a retained terminal exception. Missing
    # accounting still excludes this observation from complete-cost comparisons.
    return bool(issue) and not (
        issue == 'incomplete_api_usage' and result.get('model_returncode') == 124
        and result.get('completed') and result.get('status') in {'task_or_model_failed','passed_after_model_timeout'})


def summarize_outcomes(by):
    groups={p:dict(completed=len(rs),success=sum(result_success(r) for r in rs.values()),
                   official_success=sum(bool(r.get('official_success')) for r in rs.values()),
                   logged_api_tokens=sum(r['api_usage']['total_tokens'] for r in rs.values())) for p,rs in by.items()}
    pairs={}
    for p,rs in by.items():
        if p=='causal':continue
        causal=by.get('causal',{})
        tasks=sorted(t for t in causal.keys()&rs.keys() if result_success(causal[t]) and result_success(rs[t]))
        a=sum(causal[t]['api_usage']['total_tokens'] for t in tasks)
        b=sum(rs[t]['api_usage']['total_tokens'] for t in tasks)
        pairs[p]=dict(both_success_pairs=len(tasks),tasks=tasks,causal_tokens=a,control_tokens=b,saving_pct=100*(1-a/b) if b else None)
    return dict(by_policy=groups,paired_success=pairs)


def effects(cells, current_configuration=None):
    reports={}
    for harness in ('sweagent','codex'):
        complete=[r for r in cells if r['harness']==harness and r['state']=='completed']
        by={};variants={}
        for r in complete:
            path=Path(r['result_path']);result=json.loads(path.read_text())
            by.setdefault(r['policy'],{})[r['task']]=result
            config=result_configuration(result,path);key=configuration_id(config)
            group=variants.setdefault(key,dict(configuration=config,by={}))
            group['by'].setdefault(r['policy'],{})[r['task']]=result
        grouped={key:dict(configuration=value['configuration'],**summarize_outcomes(value['by']))
                 for key,value in variants.items()}
        selected=configuration_id(current_configuration) if current_configuration else next(iter(grouped)) if len(grouped)==1 else None
        exceptions=[]
        for r in cells:
            if r['harness']!=harness or r['state']!='infrastructure_error':continue
            path=Path(r['result_path'])
            result=json.loads(path.read_text()) if path.exists() else {}
            usage=result.get('api_usage',{})
            if harness=='codex' and not usage.get('complete') and path.exists():
                usage=logged_usage(path.parent,harness,result)
            exceptions.append(dict(task=r['task'],policy=r['policy'],issue=r.get('issue'),
                result_path=str(path),api_usage=usage,verified_task_success=result_success(result),
                excluded_from_complete_cost_comparisons=True))
        reports[harness]=dict(by_policy=summarize_outcomes(by)['by_policy'],
                             by_policy_scope='Descriptive coverage across retained configurations; not a pooled causal comparison.',
                             paired_success=grouped.get(selected,{}).get('paired_success',{}),
                             comparison_configuration_id=selected,configuration_groups=grouped,
                             success_policy=SUCCESS_POLICY,
                             retained_infrastructure_exceptions=exceptions)
    return reports


def inherit_terminal_cells(cells, previous):
    """Carry immutable observations forward; dispatch only unstarted work."""
    key=lambda row:(row['task'],row['harness'],row['policy'])
    old={key(row):row for row in previous}
    assert len(old)==len(previous) and set(old)=={key(row) for row in cells}, 'resume task grid differs'
    assert all(row['state']!='running' for row in previous), 'previous workers must be reconciled'
    result=[]
    for row in cells:
        prior=old[key(row)]
        if prior['state'] in {'completed','infrastructure_error'}:
            assert prior['expected_context']==row['expected_context']
            assert Path(prior['result_path']).is_file(), 'missing immutable result'
            result.append(copy.deepcopy(prior))
        else:
            result.append(row)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name',required=True)
    parser.add_argument('--canary',action='store_true')
    parser.add_argument('--resume-from',type=Path)
    parser.add_argument('--allow-protocol-update',action='store_true',
                        help='Register a user-authorized protocol change; never pool incompatible comparisons.')
    parser.add_argument('--inventory',type=Path,default=DATA/'validation/tb-usability-inventory-20261019.json')
    args=parser.parse_args()
    assert Path(args.name).name==args.name and args.name.startswith('tb-dual-native-')
    assert socket.gethostname()=='r760-ssd'
    assert os.environ['DOCKER_HOST']=='unix:///run/agenttx-docker/docker.sock'
    lock=(DATA/'validation/tb-dual-native.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    root=DATA/('validation' if args.canary else 'results')/args.name
    root.mkdir(exist_ok=False)
    frozen,manifest=freeze(args.name)
    inventory_path=args.inventory.resolve()
    assert inventory_path.is_relative_to(DATA/'validation')
    inventory=json.loads(inventory_path.read_text())
    evidence={r['task']:r['evidence'] for r in (
        inventory['official_baseline_tasks']+inventory.get('audited_task_baseline_tasks',[]))}
    ready=set(evidence)|set(inventory['official_positive_negative'])
    tasks=tb.load_tasks(DATA/'cache')
    names=['hello-world'] if args.canary else ['hello-world','csv-to-parquet']+sorted(set(tasks)-{'hello-world','csv-to-parquet'})
    cells=[dict(task=t,harness=h,policy=p,state='pending' if t in ready else 'blocked_environment',
                expected_context=CONTEXTS[p]) for t in names for p in POLICIES for h in ('sweagent','codex')]
    continuation=None
    if args.resume_from:
        previous=args.resume_from.resolve()
        assert previous.is_relative_to(DATA/'results') and (previous/'execution.json').is_file()
        prior_registration=json.loads((previous/'registration.json').read_text())
        assert not args.canary and not prior_registration['canary']
        changes=resume_protocol_changes(prior_registration,args.allow_protocol_update)
        old_progress=json.loads((previous/'progress.json').read_text())
        cells=inherit_terminal_cells(cells,old_progress['tasks'])
        continuation=dict(previous_cohort=previous.name,
            inherited_terminal_cells=sum(r['state'] in {'completed','infrastructure_error'} for r in cells),
            previous_source_sha256=prior_registration['source_sha256'],
            source_results_modified=False,outcome_based_retry=False,
            protocol_changes=changes,
            change='Preserve terminal observations and resume pending work. Any authorized limit/contract changes are explicit and cost comparisons remain grouped by their actual configuration.')
    registration=dict(registered_at=now(),host=socket.gethostname(),cohort=args.name,
        expected_cells=len(cells),tasks=names,harnesses=['sweagent','codex'],policies=POLICIES,
        contexts=CONTEXTS,workers_total=8,model='gpt-5.6-luna',reasoning_effort='max',
        model_session_wall_s=MODEL_TIMEOUT_S,verifier_wall_s=VERIFIER_TIMEOUT_S,cell_wall_s=CELL_TIMEOUT_S,
        protocol=PROTOCOL,completion_contract=COMPLETION_CONTRACT,success_policy=SUCCESS_POLICY,
        comparison_configuration=experiment_configuration(),max_steps=None,max_model_calls=None,
        fault_seed='20261019',randomized_fault_schedule=True,repeat=0,
        source_sha256=manifest['sha256'],frozen_source=str(frozen),
        inventory=str(inventory_path),inventory_sha256=hashlib.sha256(inventory_path.read_bytes()).hexdigest(),
        ready_tasks=len(set(names)&ready),blocked_tasks=sorted(set(names)-ready),
        original_task_instructions=True,gold_solutions_used=False,canary=args.canary,
        canary_task_success_required=False,formal_eligible=False,
        formal_limitations=['Hard token budget and prompt-accounting gates remain incomplete.'],
        old_results_inherited=bool(continuation),outcome_based_retry=False,continuation=continuation,
        verifier_filesystem='same_agent_transaction_view',publication_performed=False)
    registration['incomplete_timeout_usage_policy']='Retain infrastructure exception, record known usage lower bound, exclude from complete-cost comparisons, continue pending cells without retry.'
    save(root/'registration.json',registration);save(root/'source-manifest.json',manifest)
    save(DATA/'validation/active-tb-dual-native.json',dict(cohort=args.name,root=str(root),canary=args.canary,
        coordinator_pid=os.getpid(),activated_at=now()))
    active={};stop=None;last_review=0
    def progress():
        save(root/'progress.json',dict(updated_at=now(),coordinator_pid=os.getpid(),counts=dict(Counter(r['state'] for r in cells)),
            active=[dict(task=r['task'],harness=r['harness'],policy=r['policy'],pid=proc.pid) for proc,r,_,_ in active.values()],
            stop_reason=stop,disk_free_gib=round(shutil.disk_usage(DATA).free/2**30,1),formal_eligible=False,tasks=cells))
    while True:
        for key,(proc,row,log,started) in list(active.items()):
            if proc.poll() is None and time.monotonic()-started<CELL_TIMEOUT_S:continue
            timed_out=proc.poll() is None
            if timed_out:
                os.killpg(proc.pid,signal.SIGTERM)
                try:proc.wait(timeout=10)
                except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
                # Containers belong only to this cell.
                cid=root/'cells'/row['task']/row['harness']/row['policy']/row['policy']/'container.cid'
                if cid.exists():subprocess.run(['docker','rm','-f',cid.read_text().strip()],capture_output=True,timeout=30)
            log.close()
            path=root/'cells'/row['task']/row['harness']/row['policy']/row['policy']/'host-result.json'
            result=json.loads(path.read_text()) if path.exists() else {}
            issue='worker_wall_timeout' if timed_out else infrastructure_issue(result,row['expected_context'])
            row.update(state='infrastructure_error' if issue else 'completed',issue=issue,
                result_path=str(path),finished_at=now(),worker_returncode=proc.returncode)
            if halts_dispatch(issue,result):stop=f"{row['task']}/{row['harness']}/{row['policy']}: {issue}"
            del active[key]
        if shutil.disk_usage(DATA).free<350*2**30:stop=stop or 'data_disk_below_350_gib'
        for row in cells:
            if stop or len(active)>=8:break
            if row['state']!='pending':continue
            folder=root/'cells'/row['task']/row['harness']/row['policy']
            folder.parent.mkdir(parents=True,exist_ok=True)
            log_dir=root/'worker-logs'/row['task']/row['harness'];log_dir.mkdir(parents=True,exist_ok=True)
            log=(log_dir/(row['policy']+'.log')).open('w')
            env=dict(os.environ,PYTHONPATH=f'{frozen}/src:{frozen}',AGENTTX_SKIP_ORPHAN_REAP='1',AGENTTX_NO_PROXY='1',NO_PROXY='*')
            for k in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):env.pop(k,None)
            proc=subprocess.Popen(cell_command(frozen,folder,row,evidence.get(row['task'])),cwd=frozen,
                env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            row.update(state='running',started_at=now(),worker_pid=proc.pid)
            active[proc.pid]=(proc,row,log,time.monotonic())
        progress()
        if time.monotonic()-last_review>=60:
            save(root/'effects.json',dict(checked_at=now(),comparisons=effects(cells,experiment_configuration()),
                token_definition='Logged main + replay API usage; paired-success cost only; all outcomes retained.'))
            last_review=time.monotonic()
        if not active and (stop or not any(r['state']=='pending' for r in cells)):break
        time.sleep(5)
    save(root/'effects.json',dict(checked_at=now(),comparisons=effects(cells,experiment_configuration())))
    save(root/'execution.json',dict(finished_at=now(),stop_reason=stop,counts=dict(Counter(r['state'] for r in cells)),
        formal_eligible=False,all_expected_cells_completed=all(r['state']=='completed' for r in cells)))
    return 1 if stop else 0


if __name__=='__main__':raise SystemExit(main())
