#!/usr/bin/env python3
"""Resume with audited socket and background-process runtime repairs."""
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

D = Path('/mnt/data/agenttx-runtime')
NAME = 'tb-dual-native-full-20261026-r23-runtime-repair'
ROOT = D/'results'/NAME
PREVIOUS = D/'results/tb-dual-native-full-20261026-r22-socket-quarantine'
AUDIT = D/'validation/tb-socket-service-repair-20261026'
FROZEN = Path('/home/bfq/agenttx-workspace/agenttx-executions')/NAME/'project'
# These failed tasks need separate prerequisite repair, not outcome-based reruns.
DEFERRED_TASKS = {'hf-model-inference', 'hf-train-lora-adapter'}
REPAIR_FILES = ('src/agenttx/layers.py', 'src/agenttx/semisolate.py', 'src/agenttx/trace.py', 'src/agenttx/try_worker.py', 'experiments/scripts/bench_tb_image_native.py', 'experiments/scripts/tb_runtime_service_probe.py')
REPAIR_TASKS = {'install-windows-xp', 'install-windows-3.11', 'home-server-https', 'hdfs-deployment', 'filter-js-from-html'}
TERMINAL = {'completed', 'infrastructure_error'}
now, save, sha, key = prior.now, prior.save, prior.sha, prior.key


def fresh_connection_evidence(row):
    """Only explicit diagnostics can classify a newly run connection failure."""
    evidence = prior.connection_evidence(row)
    return evidence if evidence and not evidence.get('inferred') else None


def select(cells, retries, active):
    eligible = lambda rows: [r for r in rows if r['task'] not in DEFERRED_TASKS]
    first, repeat = eligible(cells), eligible(retries)
    repeat = sorted(repeat, key=lambda r: (r.get('runtime_repair_cohort')!=NAME, r['task']!='home-server-https', r['task']!='install-windows-xp'))
    # Keep both queues advancing; at most four ordinary retry slots when both
    # have work. Historical incident priority must not starve the first grid.
    return prior.old.select(first, repeat, active)


def result_path(row, retry):
    return ROOT/('retry-cells' if retry else 'cells')/row['task']/row['harness']/row['policy']/row['policy']/'host-result.json'



def add_runtime_repairs(cells, retries):
    """One audited runtime repair per affected cell, across every policy.

    Reuse unstarted ordinary retries. Preserve every prior outcome and charge;
    success is never used to choose which policies receive the runtime fix.
    """
    added=[]
    for first in cells:
        if first['task'] not in REPAIR_TASKS or first['state'] not in TERMINAL:
            continue
        history=[r for r in retries if key(r)==key(first)]
        if any(r.get('runtime_repair_cohort')==NAME for r in history):
            continue
        pending=next((r for r in history if r['state']=='pending'),None)
        if pending is not None:
            pending.update(runtime_repair_cohort=NAME,runtime_repair_reason='audited_background_service_and_socket_runtime_fix')
            continue
        previous=history[-1] if history else first
        assert previous['state'] in TERMINAL
        row=dict(task=first['task'],harness=first['harness'],policy=first['policy'],
            expected_context=first['expected_context'],state='pending',
            attempt=max([0]+[r.get('attempt',1) for r in history])+1,
            runtime_repair_cohort=NAME,retry_reason='audited_background_service_and_socket_runtime_fix',
            original_state=first['state'],original_result_path=first['result_path'],
            original_result_sha256=sha(first['result_path']),previous_attempt_path=previous['result_path'],
            previous_attempt_sha256=sha(previous['result_path']))
        retries.append(row);added.append(row)
    return added


def main():
    assert '--start' in sys.argv
    assert socket.gethostname() == 'r760-ssd'
    assert os.environ.get('DOCKER_HOST') == 'unix:///run/agenttx-docker/docker.sock'
    AUDIT.mkdir(parents=True, exist_ok=True)
    lock = (D/'validation/tb-dual-native.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert not ROOT.exists(), 'Use a new audited continuation; never overwrite a cohort'
    pointer = json.loads((D/'validation/active-tb-dual-native.json').read_text())
    assert Path(pointer['root']) == PREVIOUS
    assert (AUDIT/'regression-r6/result.json').exists()
    assert json.loads((AUDIT/'regression-r6/result.json').read_text())['returncode']==0
    for task in ('install-windows-xp','home-server-https'):
        proof=json.loads((AUDIT/('final-native-'+task+'-codex')/'clean_recovery/host-result.json').read_text())
        assert proof.get('status')=='preflight_passed' and proof['service_lifecycle_preflight']['trusted_verifier_connected']
    previous = json.loads((PREVIOUS/'progress.json').read_text())
    assert (PREVIOUS/'execution.json').exists() and not previous['active']
    assert not any(r['state'] == 'running' for r in previous['tasks']+previous['retry_tasks'])
    # Do not trust stale PIDs across a reboot; the global lock and immutable
    # terminal queue determine eligibility, not a recycled process number.
    stray = subprocess.check_output(['ps', '-eo', 'pid=,args='], text=True)
    assert not any('bench_tb_image_native.py host ' in line for line in stray.splitlines()), 'Unregistered worker exists'
    reg = json.loads((PREVIOUS/'registration.json').read_text())
    assert reg['workers_total'] == 8 and reg['min_free_disk_gib'] == 0
    cells, retries = copy.deepcopy(previous['tasks']), copy.deepcopy(previous['retry_tasks'])
    protected = {r['result_path']: sha(r['result_path']) for r in cells+retries if r['state'] in TERMINAL}
    health = prior.health()
    assert health['healthy'], 'Configured model endpoint is unavailable'
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
        coordinator_script_sha256=sha(__file__), user_instruction='继续跑实验吧，恢复',
        original_frozen_worker_sources_unchanged=False, source_changes=changes,
        runtime_repair='socket-metadata-snapshots-and-session-strace/v1',
        superseded_runtime_results_retained=True,
        historical_incident_inference_used_for_new_attempts=False,
        failed_hf_tasks_deferred=sorted(DEFERRED_TASKS),
        deferred_environment_tasks=sorted(DEFERRED_TASKS),
        containment_only=False, unsupported_socket_support_fixed=True, process_checkpointing_supported=False,
        deferred_reason='HF prerequisites remain unresolved; XP socket metadata and background service lifetime repairs passed zero-model original-image probes.',
        cumulative_all_attempt_costs=True, prior_results_immutable=True,
        disk_guard_enabled=False, min_free_disk_gib=0, workers_total=8,
        host_8080_forwarding_modified=False, api_configuration_changed=False,
        scheduling='Balanced pending first attempts and existing retries; maximum 8 combined')
    added=add_runtime_repairs(cells,retries)
    registration['runtime_repair_attempts_added']=len(added)
    registration['runtime_repair_tasks']=sorted(REPAIR_TASKS)
    save(AUDIT/'runtime-repair-attempts.json', dict(at=now(),added=added))
    save(ROOT/'registration.json', registration); save(ROOT/'source-manifest.json', manifest)
    inventory = json.loads(Path(reg['inventory']).read_text())
    evidence = {r['task']: r['evidence'] for r in inventory['official_baseline_tasks']+inventory.get('audited_task_baseline_tasks', [])}
    active = {}; stop = None; last_review = 0; last_health = time.monotonic()

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
                         pid=i['pid'], retry=i['retry'], adopted=False) for i in active.values()],
            stop_reason=stop, api_health=health, disk_free_gib=shutil.disk_usage(D).free/2**30,
            network_isolation=reg['network_isolation'], formal_eligible=False,
            deferred_tasks=sorted(DEFERRED_TASKS),
            deferred_pending=sum(r['state']=='pending' and r['task'] in DEFERRED_TASKS for r in cells+retries)))

    def reports():
        save(ROOT/'effects.json', dict(checked_at=now(), comparisons=effects(cells, experiment_configuration()),
            token_definition='Original first attempts only; cumulative all-attempt costs in retry-report.json'))
        latest = {key(r): r for r in retries if r['state'] in TERMINAL}
        save(ROOT/'retry-effects.json', dict(checked_at=now(), comparisons=effects(list(latest.values()), experiment_configuration())))
        report = prior.report(cells, retries)
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
            proc = item['proc']; timed_out = proc.poll() is None and time.time()-item['started'] >= CELL_TIMEOUT_S
            if proc.poll() is None and not timed_out:
                continue
            if timed_out: prior.old.cleanup_timeout(item)
            proc.wait(timeout=20); item['log'].close()
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
                finished_at=now(), worker_returncode=proc.returncode)
            budget = model_budget_failure_evidence(result, path) if issue=='incomplete_api_usage' else None
            if budget: row['model_failure_evidence'] = budget
            connection = fresh_connection_evidence(row)
            if halts_dispatch(issue, result, budget) or connection:
                stop = 'new_environment_exception_requires_review'
                save(AUDIT/'dispatch-halt.json', dict(at=now(), row=row, connection_evidence=connection))
            del active[pid]
        prior.old.add_retries(cells, retries)
        if time.monotonic()-last_health >= 30:
            health = prior.health(); last_health=time.monotonic(); save(AUDIT/'api-health.json', health)
        while not stop and health.get('healthy') and len(active)<8:
            choice = select(cells, retries, active)
            if not choice: break
            row, retry = choice; path=result_path(row, retry); folder=path.parent.parent
            folder.parent.mkdir(parents=True, exist_ok=True)
            assert not folder.exists(), 'Never reuse an attempt directory'
            logfile=ROOT/('retry-worker-logs' if retry else 'worker-logs')/row['task']/row['harness']/(row['policy']+'.log')
            logfile.parent.mkdir(parents=True, exist_ok=True); log=logfile.open('w')
            env=dict(os.environ, PYTHONPATH=f'{FROZEN}/src:{FROZEN}', AGENTTX_SKIP_ORPHAN_REAP='1', AGENTTX_NO_PROXY='1', NO_PROXY='*')
            for k in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'): env.pop(k,None)
            proc=subprocess.Popen(cell_command(FROZEN, folder, row, evidence.get(row['task'])), cwd=FROZEN,
                env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            row.update(state='running', started_at=now(), worker_pid=proc.pid, result_path=str(path), execution_cohort=NAME)
            active[proc.pid]=dict(row=row, retry=retry, pid=proc.pid, path=str(path), proc=proc, log=log, started=time.time())
            with (AUDIT/'dispatches.jsonl').open('a') as out:
                out.write(json.dumps(dict(at=now(), task=row['task'], harness=row['harness'], policy=row['policy'], retry=retry,
                    attempt=row.get('attempt',0), pid=proc.pid, total_active=len(active)))+'\n')
        progress()
        if time.monotonic()-last_review>=60:
            reports(); last_review=time.monotonic()
        if not active and (stop or select(cells,retries,active) is None): break
        time.sleep(5)
    assert all(sha(p)==h for p,h in protected.items())
    progress(); reports()
    save(AUDIT/'protected-results-verification.json', dict(at=now(), count=len(protected), all_hashes_unchanged=True))
    save(ROOT/'execution.json', dict(finished_at=now(), stop_reason=stop,
        first_attempt_counts=dict(collections.Counter(r['state'] for r in cells)),
        retry_counts=dict(collections.Counter(r['state'] for r in retries))))


if __name__ == '__main__':
    main()
