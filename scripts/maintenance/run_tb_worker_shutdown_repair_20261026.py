#!/usr/bin/env python3
"""Adopt live workers; retry proven download failures through domain-scoped private Mihomo."""
import collections
import copy
import datetime
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time

PROJECT = Path('/home/bfq/agenttx-workspace/agenttx')
sys.path[:0] = [str(PROJECT), str(PROJECT/'src'), '/home/bfq/agenttx-workspace/agenttx/scripts/maintenance']
import run_tb_original_service_resume_20261023 as prior
from experiments.scripts.run_tb_dual_local import (
    cell_command, infrastructure_issue, effects, experiment_configuration,
    CELL_TIMEOUT_S, halts_dispatch, model_budget_failure_evidence,
)
from experiments.scripts.experiment_provenance import source_manifest
from tb_provider_health_20261026 import classify_provider_failure, probe_inference, probe_accounting
from tb_network_deferrals_20261026 import TASK, annotate, analysis_rows
from tb_download_retry_20261026 import register_download_retries, requires_proxy, register_provider_retry

D = Path('/mnt/data/agenttx-runtime')
NAME = 'tb-dual-native-full-20261026-r28-worker-shutdown-repair'
ROOT = D/'results'/NAME
PREVIOUS = D/'results/tb-dual-native-full-20261026-r27-immediate-network-retry'
AUDIT = D/'validation/tb-worker-shutdown-repair-20261026'
TRANSPORT_AUDIT = D/'validation/tb-selective-download-proxy-20261026'
DOWNLOAD_REPAIR_COHORT='tb-dual-native-full-20261026-r26-selective-download-retry'
PROVIDER_AUDIT = D/'validation/tb-provider-resume-20261026'
OLD_UNIT = 'agenttx-tb-dual-full-20261026-r27-immediate-network-retry.service'
NETWORK_DEFERRED = {}
FROZEN = Path('/home/bfq/agenttx-workspace/agenttx-executions')/NAME/'project'
# These failed tasks need separate prerequisite repair, not outcome-based reruns.
DEFERRED_TASKS = set()
REPAIR_FILES = ('src/agenttx/semisolate.py',)
REPAIR_TASKS = set()
TERMINAL = {'completed', 'infrastructure_error'}
now, save, sha, key = prior.now, prior.save, prior.sha, prior.key


def fresh_connection_evidence(row):
    """Only explicit diagnostics can classify a newly run connection failure."""
    evidence = prior.connection_evidence(row)
    return evidence if evidence and not evidence.get('inferred') else None


def select(cells, retries, active):
    eligible = lambda rows: [r for r in rows if r['task'] not in DEFERRED_TASKS]
    first, repeat = eligible(cells), eligible(retries)
    repeat = sorted(repeat, key=lambda r: (not requires_proxy(r), not bool(r.get('provider_repair_of')), r['task']!='filter-js-from-html', r['task']!='lean4-proof'))
    # User requests immediate network retries: all available slots serve this
    # finite registered backlog first; resume balanced ordinary work afterward.
    urgent=next((r for r in repeat if r['state']=='pending' and requires_proxy(r)),None)
    if urgent:return urgent,True
    return prior.old.select(first, repeat, active)


def result_path(row, retry):
    folder=ROOT/('retry-cells' if retry else 'cells')/row['task']/row['harness']/row['policy']
    if retry:folder=folder/('attempt-%03d'%row.get('attempt',1))
    return folder/row['policy']/'host-result.json'



def add_provider_repairs(cells, retries):
    """Retry only explicit terminal provider failures, preserving every bill."""
    latest={key(r):r for r in cells}
    for row in retries:
        if row['state'] in TERMINAL:latest[key(row)]=row
    added=[]
    for k,previous in latest.items():
        if previous['state'] not in TERMINAL:continue
        evidence=classify_provider_failure(previous)
        if evidence is None:continue
        history=[r for r in retries if key(r)==k]
        if any(r.get('provider_repair_of')==previous['result_path'] for r in history):continue
        pending=next((r for r in history if r['state']=='pending'),None)
        if any(r['state']=='running' for r in history):continue
        if pending is None:
            first=next(r for r in cells if key(r)==k)
            pending=dict(task=k[0],harness=k[1],policy=k[2],expected_context=first['expected_context'],state='pending',
                attempt=max([0]+[r.get('attempt',1) for r in history])+1,original_state=first['state'],
                original_result_path=first['result_path'],original_result_sha256=sha(first['result_path']))
            retries.append(pending);added.append(pending)
        pending.update(provider_repair_of=previous['result_path'],provider_repair_cohort=NAME,
            provider_failure_evidence=evidence,retry_reason='user_authorized_resume_after_explicit_provider_http_5xx',
            previous_attempt_path=previous['result_path'],previous_attempt_sha256=sha(previous['result_path']))
    return added


def register_interrupted_retries(cells,retries):
    latest={key(r):r for r in cells}
    for r in retries:
        if r['state'] in TERMINAL:latest[key(r)]=r
    for k,old in latest.items():
        if not old.get('interrupted_at_user_request'):continue
        history=[r for r in retries if key(r)==k]
        if any(r['state'] in ('pending','running') or r.get('resumed_user_interruption_of')==old['result_path'] for r in history):continue
        first=next(r for r in cells if key(r)==k)
        retries.append(dict(task=k[0],harness=k[1],policy=k[2],expected_context=first['expected_context'],
            state='pending',attempt=max([0]+[r.get('attempt',1) for r in history])+1,
            original_state=first['state'],original_result_path=first['result_path'],original_result_sha256=sha(first['result_path']),
            previous_attempt_path=old['result_path'],previous_attempt_sha256=sha(old['result_path']),
            resumed_user_interruption_of=old['result_path'],retry_reason='resume_user_interrupted_ordinary_work_after_network_backlog'))


def shutdown_fault(row):
    marker=AUDIT/'shutdown-enforcement.json'
    if not marker.exists():return None
    proof=json.loads(marker.read_text())
    if row.get('result_path')!=proof['affected_result_path']:return None
    return proof


def register_shutdown_repairs(cells,retries):
    for old in list(cells+retries):
        proof=shutdown_fault(old)
        if old['state'] not in TERMINAL or proof is None:continue
        history=[r for r in retries if key(r)==key(old)]
        if any(r.get('runtime_shutdown_repair_of')==old['result_path'] for r in history):continue
        if any(r['state']=='running' for r in history):continue
        first=next(r for r in cells if key(r)==key(old))
        pending=next((r for r in history if r['state']=='pending'),None)
        if pending is None:
            pending=dict(task=old['task'],harness=old['harness'],policy=old['policy'],
                expected_context=first['expected_context'],state='pending',
                attempt=max([0]+[r.get('attempt',1) for r in history])+1,
                original_state=first['state'],original_result_path=first['result_path'],
                original_result_sha256=sha(first['result_path']))
            retries.append(pending)
        pending.update(runtime_shutdown_repair_of=old['result_path'],runtime_shutdown_repair_cohort=NAME,
            runtime_shutdown_evidence=str(AUDIT/'shutdown-enforcement.json'),
            previous_attempt_path=old['result_path'],previous_attempt_sha256=sha(old['result_path']),
            retry_reason='repair_observed_unbounded_worker_shutdown_after_deadline')
        # The repaired attempt retains its already-authorized download transport.
        for field in ('download_proxy_retry','download_retry_cohort','download_failure_evidence','download_retry_of'):
            if field in old:pending[field]=copy.deepcopy(old[field])


def main():
    assert '--start' in sys.argv
    assert json.loads((AUDIT/'regression-r2-result.json').read_text())['returncode']==0
    assert socket.gethostname() == 'r760-ssd'
    assert os.environ.get('DOCKER_HOST') == 'unix:///run/agenttx-docker/docker.sock'
    AUDIT.mkdir(parents=True, exist_ok=True)
    own_lock = (AUDIT/'coordinator.lock').open('a')
    fcntl.flock(own_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert not ROOT.exists(), 'Use a new audited continuation; never overwrite a cohort'
    pointer = json.loads((D/'validation/active-tb-dual-native.json').read_text())
    assert Path(pointer['root']) == PREVIOUS
    assert (D/'validation/tb-socket-service-repair-20261026/regression-r8-streaming/result.json').exists()
    assert json.loads((D/'validation/tb-socket-service-repair-20261026/regression-r8-streaming/result.json').read_text())['returncode']==0
    old_pid=pointer['coordinator_pid']
    assert prior.state(old_pid)=='T', 'Only the old dispatcher must be paused; workers keep running'
    assert int(subprocess.check_output(['systemctl','--user','show',OLD_UNIT,'-p','MainPID','--value'],text=True))==old_pid
    previous = json.loads((PREVIOUS/'progress.json').read_text())
    running=[r for r in previous['tasks']+previous['retry_tasks'] if r['state']=='running']
    registered={r['worker_pid'] for r in running}
    spawned=subprocess.check_output(['ps','--ppid',str(old_pid),'-o','pid=,args='],text=True)
    assert {int(x.split(None,1)[0]) for x in spawned.splitlines() if 'bench_tb_image_native.py host ' in x}<=registered, 'Dispatcher paused between spawn/progress; resume and retry handoff'
    assert len(running)<=8
    reg = json.loads((PREVIOUS/'registration.json').read_text())
    assert reg['workers_total'] == 8 and reg['min_free_disk_gib'] == 0
    cells, retries = copy.deepcopy(previous['tasks']), copy.deepcopy(previous['retry_tasks'])
    protected = {r['result_path']: sha(r['result_path']) for r in cells+retries if r['state'] in TERMINAL}
    health = previous.get('api_health') or prior.health()
    assert json.loads((TRANSPORT_AUDIT/'private-network-probe-result.json').read_text())['returncode']==0
    assert json.loads((TRANSPORT_AUDIT/'adapter-network-preflights.json').read_text())['passed']==2
    for row in cells+retries:row.pop('dispatch_deferral',None)
    annotate(cells+retries,{})
    # Metadata health alone never opens dispatch; real inference is checked below.
    proof = json.loads((D/'validation/tb-api-connection-repair-20261023/isolation-verification.json').read_text())
    assert proof['at'][:10] == now()[:10] and proof['host_nat_unchanged'] and proof['private_authenticated_status'] == 200
    for harness in ('codex', 'sweagent'):
        hp = json.loads((D/'validation/tb-api-connection-repair-20261023'/('hosts-baseline-'+harness+'-verification.json')).read_text())
        assert hp['at'][:10] == now()[:10] and hp['no_model_calls'] and hp['network_baseline']['localhost_resolution']
    manifest = json.loads((PREVIOUS/'source-manifest.json').read_text())
    FROZEN.mkdir(parents=True, exist_ok=False)
    for entry in manifest['files']:
        src = Path(reg['frozen_source'])/entry['path']
        assert sha(src) == entry['sha256'], entry['path']
        dst = FROZEN/entry['path']; dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    assert source_manifest(FROZEN)['sha256'] == manifest['sha256']
    changes=[]
    for relative in REPAIR_FILES:
        dst=FROZEN/relative; src=PROJECT/relative
        changes.append(dict(path=relative, previous_sha256=sha(dst) if dst.exists() else None, repaired_sha256=sha(src)))
        dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
    manifest=source_manifest(FROZEN)
    ROOT.mkdir()
    save(AUDIT/'protected-results.json', protected)
    save(AUDIT/'previous-progress.json', previous)
    registration = dict(reg, cohort=NAME, registered_at=now(), previous_cohort=PREVIOUS.name,
        frozen_source=str(FROZEN), source_sha256=manifest['sha256'], coordinator_script=__file__,
        coordinator_script_sha256=sha(__file__), user_instruction='持续监控；半小时无进展调查并修复已证环境阻塞；保留费用，下载限定分流，8080不动',
        original_frozen_worker_sources_unchanged=False, source_changes=changes,
        runtime_repair='bounded-worker-shutdown-after-interrupted-response/v1',
        superseded_runtime_results_retained=True,
        historical_incident_inference_used_for_new_attempts=False,
        failed_hf_tasks_deferred=sorted(DEFERRED_TASKS-{TASK}),
        deferred_environment_tasks=sorted(DEFERRED_TASKS),
        containment_only=False, unsupported_socket_support_fixed=True, process_checkpointing_supported=False,
        deferred_reason=None,
        cumulative_all_attempt_costs=True, prior_results_immutable=True,
        disk_guard_enabled=False, min_free_disk_gib=0, workers_total=8,
        host_8080_forwarding_modified=False, api_configuration_changed=False,
        scheduling='Balanced pending first attempts and existing retries; maximum 8 combined')
    registration['retry_limit_per_cell']='One ordinary failure retry plus separately audited environment repairs; each explicit provider outage attempt retained and charged, never outcome-based best-of.'
    register_shutdown_repairs(cells,retries)
    added=add_provider_repairs(cells,retries)
    registration['provider_repair_attempts_added']=len(added)
    download_repairs=register_download_retries(cells,retries,DOWNLOAD_REPAIR_COHORT,sha,key)
    registration['download_retries_registered']=len(download_repairs)
    registration['download_retry_transport']='private-netns Mihomo; explicit domains only; default DIRECT; 8080 and API DIRECT'
    registration['network_skip_revoked']=True
    save(AUDIT/'initial-download-retries.json',dict(at=now(),attempts=download_repairs))
    registration['adopted_running_workers']=len(running)
    registration['existing_network_task_workers_finish_without_restart']=False
    registration['old_attempts_interrupted_at_user_request']=False
    registration['runtime_shutdown_repair_evidence']=str(AUDIT/'affected-worker.json')
    registration['network_backlog_uses_all_8_slots_before_ordinary_work']=True
    registration['provider_auto_resume']=True
    registration['provider_probe_backoff_seconds']=300
    registration['inference_probes_only_when_no_model_workers']=True
    save(AUDIT/'provider-repair-attempts.json', dict(at=now(),added=added))
    save(ROOT/'registration.json', registration); save(ROOT/'source-manifest.json', manifest)
    inventory = json.loads(Path(reg['inventory']).read_text())
    evidence = {r['task']: r['evidence'] for r in inventory['official_baseline_tasks']+inventory.get('audited_task_baseline_tasks', [])}
    active = {}; stop = None; last_review = 0; last_health = time.monotonic()
    provider_gate=previous.get('provider_gate',False); last_probe=time.monotonic(); provider_failure=previous.get('provider_failure')
    assert not provider_gate, 'Review inherited provider outage before handoff'
    old_finalized=False; global_lock=None
    ancestor_pids=[old_pid]
    for ancestor_root in ('tb-dual-native-full-20261026-r26-selective-download-retry','tb-dual-native-full-20261026-r25-provider-resume'):
        older=json.loads((D/'results'/ancestor_root/'progress.json').read_text())['coordinator_pid']
        if prior.state(older)=='T':ancestor_pids.append(older)
    for retry,rows in [(False,cells),(True,retries)]:
        for row in rows:
            if row['state']=='running':
                active[row['worker_pid']]=dict(row=row,retry=retry,pid=row['worker_pid'],path=row['result_path'],proc=None,log=None,adopted=True,started=datetime.datetime.fromisoformat(row['started_at']).timestamp())

    def terminate(*_):
        nonlocal stop
        stop = 'user_or_service_stop_drain'
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)

    def progress():
        save(ROOT/'progress.json', dict(updated_at=now(), coordinator_pid=os.getpid(),
            counts=dict(collections.Counter(r['state'] for r in cells)),
            retry_counts=dict(collections.Counter(r['state'] for r in retries)),
            tasks=cells, retry_tasks=retries,
            active=[dict(task=i['row']['task'], harness=i['row']['harness'], policy=i['row']['policy'],
                         pid=i['pid'], retry=i['retry'], adopted=i['adopted']) for i in active.values()],
            stop_reason=stop, api_health=health, provider_gate=provider_gate, provider_failure=provider_failure, disk_free_gib=shutil.disk_usage(D).free/2**30,
            network_isolation=reg['network_isolation'], formal_eligible=False,
            deferred_tasks=sorted(DEFERRED_TASKS),
            deferred_pending=sum(r['state']=='pending' and r['task'] in DEFERRED_TASKS for r in cells+retries),
            download_retry=dict(marked=sum(requires_proxy(r) for r in cells+retries),running=sum(r['state']=='running' and requires_proxy(r) for r in cells+retries),network_skip=False)))

    def reports():
        analyzed_cells,analyzed_retries=analysis_rows(cells),analysis_rows(retries)
        save(ROOT/'effects.json', dict(checked_at=now(), comparisons=effects(analyzed_cells, experiment_configuration()),
            token_definition='Original first attempts only; cumulative all-attempt costs in retry-report.json'))
        latest = {key(r): r for r in analyzed_retries if r['state'] in TERMINAL}
        save(ROOT/'retry-effects.json', dict(checked_at=now(), comparisons=effects(list(latest.values()), experiment_configuration())))
        report = prior.report(analyzed_cells, analyzed_retries)
        report['download_retry_counts']=dict(collections.Counter(r['state'] for r in cells+retries if requires_proxy(r)))
        report['raw_registered_attempt_counts']=dict(collections.Counter(r['state'] for r in cells+retries))
        report['verifier_network_exclusions']=[r['verifier_network_exclusion'] for r in cells+retries if r.get('verifier_network_exclusion')]
        report['raw_first_counts']=dict(collections.Counter(r['state'] for r in cells))
        report['raw_retry_counts']=dict(collections.Counter(r['state'] for r in retries))
        report['provider_health_probe_accounting']=probe_accounting(PROVIDER_AUDIT)
        report['current_execution_cohort'] = NAME
        report['fresh_cohort_counts'] = dict(collections.Counter(r['state'] for r in cells+retries if r.get('execution_cohort')==NAME))
        report['fresh_cohort_successful'] = sum(r['state']=='completed' and json.loads(Path(r['result_path']).read_text()).get('success',False)
            for r in cells+retries if r.get('execution_cohort')==NAME)
        save(ROOT/'retry-report.json', report)

    progress(); reports()
    save(D/'validation/active-tb-dual-native.json', dict(cohort=NAME, root=str(ROOT), canary=False,
        coordinator_pid=os.getpid(), activated_at=now(), retry_authorized=True))
    while True:
        for pid, item in list(active.items()):
            proc = item['proc']
            alive=proc.poll() is None if proc else prior.state(pid) not in (None,'Z')
            timed_out = alive and time.time()-item['started'] >= CELL_TIMEOUT_S
            if alive and not timed_out:
                continue
            if timed_out: prior.old.cleanup_timeout(item)
            if proc:proc.wait(timeout=20)
            if item['log']:item['log'].close()
            row = item['row']; path = Path(item['path'])
            if path.exists():
                result = json.loads(path.read_text())
            else:
                error = path.parent.parent/'host-error.txt'
                result = dict(completed=False, success=False, status='missing_worker_result',
                    host_error=error.read_text()[-4000:] if error.exists() else 'Worker result missing; usage unknown',
                    api_usage=dict(complete=False), task=row['task'], harness=row['harness'], policy=row['policy'])
                save(path, result)
            issue = 'worker_wall_timeout' if timed_out else infrastructure_issue(result, row['expected_context'])
            row.update(state='infrastructure_error' if issue else 'completed', issue=issue,
                finished_at=now(), worker_returncode=proc.returncode if proc else result.get('container_returncode'))
            annotate([row],NETWORK_DEFERRED)
            interrupted=path.parent/'immediate-network-retry-interruption.json'
            user_interruption=interrupted.exists()
            if user_interruption:
                row['user_scheduling_interruption']=json.loads(interrupted.read_text())
                row['interrupted_at_user_request']=True
            budget = model_budget_failure_evidence(result, path) if issue=='incomplete_api_usage' else None
            if budget: row['model_failure_evidence'] = budget
            connection = fresh_connection_evidence(row)
            shutdown_evidence=shutdown_fault(row)
            if shutdown_evidence:row['runtime_shutdown_fault']=shutdown_evidence
            gateway=classify_provider_failure(row) if issue else None
            if gateway and not user_interruption:
                row['provider_failure_evidence']=gateway
                provider_gate=True;provider_failure=gateway;last_probe=time.monotonic()
                save(AUDIT/'provider-outage.json',dict(at=now(),row=row,evidence=gateway))
            elif (halts_dispatch(issue, result, budget) or connection) and not row.get('verifier_network_exclusion') and not user_interruption and not shutdown_evidence:
                stop = 'new_environment_exception_requires_review'
                save(AUDIT/'dispatch-halt.json', dict(at=now(), row=row, connection_evidence=connection))
            del active[pid]
        if not old_finalized and not any(i['adopted'] for i in active.values()):
            for ancestor in ancestor_pids:
                assert prior.state(ancestor)=='T'
                os.kill(ancestor,signal.SIGTERM);os.kill(ancestor,signal.SIGCONT)
                for _ in range(50):
                    if prior.state(ancestor) in (None,'Z'):break
                    time.sleep(.1)
                assert prior.state(ancestor) in (None,'Z')
            global_lock=(D/'validation/tb-dual-native.lock').open('a')
            fcntl.flock(global_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            save(AUDIT/'handoff-verification.json',dict(at=now(),old_dispatcher_retired=True,workers_interrupted=False))
            old_finalized=True
        prior.old.add_retries(cells, retries)
        register_interrupted_retries(cells,retries)
        register_shutdown_repairs(cells,retries)
        annotate(cells+retries,NETWORK_DEFERRED)
        download_repairs=register_download_retries(cells,retries,DOWNLOAD_REPAIR_COHORT,sha,key)
        if download_repairs:
            with (AUDIT/'download-retry-registrations.jsonl').open('a') as out:out.write(json.dumps(dict(at=now(),attempts=download_repairs))+'\n')
        new_repairs=register_provider_retry(cells,retries,add_provider_repairs)
        if new_repairs:
            with (AUDIT/'auto-provider-repairs.jsonl').open('a') as out:
                out.write(json.dumps(dict(at=now(),added=new_repairs))+'\n')
        if time.monotonic()-last_health >= 30:
            health = prior.health(); last_health=time.monotonic(); save(AUDIT/'api-health.json', health)
        if not stop and provider_gate and not active and time.monotonic()-last_probe>=300:
            proof=probe_inference(PROVIDER_AUDIT);last_probe=time.monotonic()
            save(AUDIT/'inference-health.json',proof)
            if proof['healthy']:provider_gate=False;provider_failure=None
        while not stop and not provider_gate and health.get('healthy') and len(active)<8:
            choice = select(cells, retries, active)
            if not choice: break
            row, retry = choice; path=result_path(row, retry); folder=path.parent.parent
            folder.parent.mkdir(parents=True, exist_ok=True)
            assert not folder.exists(), 'Never reuse an attempt directory'
            logfile=ROOT/('retry-worker-logs' if retry else 'worker-logs')/row['task']/row['harness']/(row['policy']+('-attempt-%03d'%row.get('attempt',1) if retry else '')+'.log')
            logfile.parent.mkdir(parents=True, exist_ok=True); log=logfile.open('w')
            env=dict(os.environ, PYTHONPATH=f'{FROZEN}/src:{FROZEN}', AGENTTX_SKIP_ORPHAN_REAP='1', AGENTTX_NO_PROXY='1', NO_PROXY='*')
            for k in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'): env.pop(k,None)
            env.pop('AGENTTX_DOWNLOAD_PROXY_RETRY',None)
            if requires_proxy(row):env['AGENTTX_DOWNLOAD_PROXY_RETRY']='1'
            proc=subprocess.Popen(cell_command(FROZEN, folder, row, evidence.get(row['task'])), cwd=FROZEN,
                env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            row.update(state='running', started_at=now(), worker_pid=proc.pid, result_path=str(path), execution_cohort=NAME)
            active[proc.pid]=dict(row=row, retry=retry, pid=proc.pid, path=str(path), proc=proc, log=log, started=time.time(),adopted=False)
            with (AUDIT/'dispatches.jsonl').open('a') as out:
                out.write(json.dumps(dict(at=now(), task=row['task'], harness=row['harness'], policy=row['policy'], retry=retry,
                    attempt=row.get('attempt',0), pid=proc.pid, total_active=len(active),download_proxy_retry=requires_proxy(row)))+'\n')
        progress()
        if time.monotonic()-last_review>=60:
            reports(); last_review=time.monotonic()
        if not active and (stop or (not provider_gate and select(cells,retries,active) is None)): break
        time.sleep(5)
    assert all(sha(p)==h for p,h in protected.items())
    progress(); reports()
    save(AUDIT/'protected-results-verification.json', dict(at=now(), count=len(protected), all_hashes_unchanged=True))
    save(ROOT/'execution.json', dict(finished_at=now(), stop_reason=stop,
        first_attempt_counts=dict(collections.Counter(r['state'] for r in cells)),
        retry_counts=dict(collections.Counter(r['state'] for r in retries))))


if __name__ == '__main__':
    main()
