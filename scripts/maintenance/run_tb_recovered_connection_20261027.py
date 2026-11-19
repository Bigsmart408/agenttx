#!/usr/bin/env python3
"""Adopt unchanged workers; distinguish recovered Codex notices from terminal failures."""
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
from tb_registered_deadline_20261026 import registered_deadline_evidence
from tb_shutdown_evidence_20261026 import shutdown_evidence
from tb_cache_comparisons_20261026 import separate_comparisons
from tb_hf_prerequisite_evidence_20261026 import register_prerequisite_repairs
from tb_recovered_connection_20261027 import recovered_connection_evidence
from tb_download_retry_20261026 import register_download_retries, requires_proxy, register_provider_retry

D = Path('/mnt/data/agenttx-runtime')
NAME = 'tb-dual-native-full-20261027-r37-recovered-connection'
ROOT = D/'results'/NAME
PREVIOUS = D/'results/tb-dual-native-full-20261027-r36-hf-uri-network'
AUDIT = D/'validation/tb-recovered-connection-20261027/activation'
SHUTDOWN_AUDIT = D/'validation/tb-worker-shutdown-repair-20261026'
TRANSPORT_AUDIT = D/'validation/tb-selective-download-proxy-20261026'
DOWNLOAD_REPAIR_COHORT='tb-dual-native-full-20261026-r26-selective-download-retry'
PROVIDER_AUDIT = D/'validation/tb-provider-resume-20261026'
OLD_UNIT = 'agenttx-tb-dual-full-20261027-r36-hf-uri-network.service'
NETWORK_DEFERRED = {}
FROZEN = Path('/home/bfq/agenttx-workspace/agenttx-executions')/NAME/'project'
# These failed tasks need separate prerequisite repair, not outcome-based reruns.
DEFERRED_TASKS = set()
REPAIR_FILES = ()
REPAIR_SOURCE_OVERRIDES = {}
CACHE_PROOF=D/'validation/tb-hf-cached-prerequisite-20261026/cache-integrity.json'
REPAIR_TASKS = set()
REVIEW_INHERITED_HALT = None
REVIEW_DEAD_COORDINATOR = None
FRESH_NETWORK_PROOF = None
TERMINAL = {'completed', 'infrastructure_error'}
now, save, sha, key = prior.now, prior.save, prior.sha, prior.key


def spawned_worker_pids(parent):
    result=subprocess.run(['ps','--ppid',str(parent),'-o','pid=,args='],text=True,capture_output=True)
    # An adopting dispatcher may own no children; ps then returns 1.
    assert result.returncode in (0,1) and not result.stderr,result.stderr
    return {int(line.split(None,1)[0]) for line in result.stdout.splitlines()
            if 'bench_tb_image_native.py host ' in line}


def apply_stall_interruption(cells, retries, proof):
    """One audited transport retry after a measured 30-minute download stall."""
    assert proof['dependency']=='leanprover/lean4:v4.21.0'
    assert proof['command']==['elan','toolchain','install','leanprover/lean4:v4.21.0']
    assert proof['enforcement_returncode']==0 and proof['worker_stopped']
    first,last=proof['samples']
    assert first['pid']==last['pid'] and first['io']==last['io']
    assert first['bytes']==last['bytes'] and first['mtime']==last['mtime']
    assert first['wchan']==last['wchan']=='do_poll.constprop.0'
    begin=datetime.datetime.fromisoformat(first['at']).timestamp()
    end=datetime.datetime.fromisoformat(last['at']).timestamp()
    assert end-begin>=60 and end-last['mtime']>=1800
    for source in proof['source_snapshots']:
        assert sha(source['path'])==source['sha256']
    old=next(r for r in cells+retries if r.get('result_path')==proof['result_path'])
    assert key(old)==('lean4-proof','sweagent','crash_no_rollback')
    assert old['state']=='running' and old['worker_pid']==proof['worker_pid']
    history=[r for r in retries if key(r)==key(old)]
    assert not any(requires_proxy(r) or r['state']=='pending' for r in history)
    assert sha(old['result_path'])==proof['terminal_result_sha256']
    result=json.loads(Path(old['result_path']).read_text())
    assert not result.get('completed') and not result.get('api_usage',{}).get('complete')
    old.update(state='infrastructure_error',issue='user_authorized_download_stall_interruption',
        finished_at=proof['finished_at'],worker_returncode=proof['worker_returncode'],
        interrupted_at_user_request=True,user_scheduling_interruption=proof,
        download_stall_interrupted=True)
    original=next(r for r in cells if key(r)==key(old))
    retries.append(dict(task=old['task'],harness=old['harness'],policy=old['policy'],
        expected_context=old['expected_context'],state='pending',
        attempt=max([r.get('attempt',1) for r in history])+1,
        original_state=original['state'],original_result_path=original['result_path'],
        original_result_sha256=sha(original['result_path']),previous_attempt_path=old['result_path'],
        previous_attempt_sha256=sha(old['result_path']),download_proxy_retry=True,
        download_retry_cohort=DOWNLOAD_REPAIR_COHORT,download_retry_of=old['result_path'],
        download_failure_evidence=proof,
        resumed_user_interruption_of=old['result_path'],
        retry_reason='one_selective_proxy_retry_after_measured_30min_lean_download_stall'))


def fresh_connection_evidence(row):
    """Only explicit diagnostics can classify a newly run connection failure."""
    if registered_deadline_evidence(row):return None
    evidence = prior.connection_evidence(row)
    if evidence and not evidence.get('inferred') and recovered_connection_evidence(row):
        return None
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
    return shutdown_evidence(row)


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
            runtime_shutdown_evidence=proof['evidence_path'],runtime_shutdown_evidence_sha256=proof['evidence_sha256'],
            previous_attempt_path=old['result_path'],previous_attempt_sha256=sha(old['result_path']),
            retry_reason='repair_observed_unbounded_worker_shutdown_after_deadline')
        # The repaired attempt retains its already-authorized download transport.
        for field in ('download_proxy_retry','download_retry_cohort','download_failure_evidence','download_retry_of'):
            if field in old:pending[field]=copy.deepcopy(old[field])


def main():
    assert '--start' in sys.argv
    preinstall_audit=D/'validation/tb-hf-preinstalled-prerequisite-20261026'
    for harness in ('codex','sweagent'):
        proof=json.loads((preinstall_audit/(harness+'-native-result.json')).read_text())
        result=json.loads((preinstall_audit/('native-'+harness)/'clean_recovery/host-result.json').read_text())
        assert proof['returncode']==0 and proof['no_model_calls'] and result['status']=='preflight_passed'
        assert result['prerequisite_install']['offline'] and result['prerequisite_install']['before_task_transaction']
    assert json.loads((preinstall_audit/'focused-regression-result.json').read_text())['returncode']==0
    cached_probe=json.loads((CACHE_PROOF.parent/'native-transaction/native-prerequisite-result.json').read_text())
    assert cached_probe['returncode']==0 and cached_probe['network']=='none' and cached_probe['no_model_calls']
    assert json.loads((CACHE_PROOF.parent/'focused-regression-result.json').read_text())['returncode']==0
    assert json.loads((D/'validation/tb-ledger-timeout-repair-20261026/regression-result.json').read_text())['returncode']==0
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
    old_dead = prior.state(old_pid) in (None, 'Z')
    if old_dead:
        assert REVIEW_DEAD_COORDINATOR is not None, 'Dead coordinator requires exact reviewed service evidence'
        reviewed_dead = REVIEW_DEAD_COORDINATOR(pointer)
        assert reviewed_dead and reviewed_dead['coordinator_dead'] is True
        save(AUDIT/'dead-coordinator-review.json', reviewed_dead)
    else:
        assert prior.state(old_pid)=='T', 'Pause only the old coordinator; existing workers continue'
        assert int(subprocess.check_output(['systemctl','--user','show',OLD_UNIT,'-p','MainPID','--value'],text=True))==old_pid
    if not old_dead or not reviewed_dead.get('no_live_workers_and_unit_collected'):
        assert subprocess.check_output(['systemctl','--user','show',OLD_UNIT,'-p','KillMode','--value'],text=True).strip()=='process'
    repair_proof=json.loads((D/'validation/tb-live-monitor-20261026/mnist-hf-uri-detector-validation-20261027-1305.json').read_text())
    assert repair_proof['returncode']==0 and repair_proof['evidence']['classification']=='official_hf_filesystem_dataset_network_failure'
    assert repair_proof['classifier_sha256']==sha(PROJECT/'scripts/maintenance/tb_download_retry_20261026.py')
    assert repair_proof['no_model_calls']
    previous = json.loads((PREVIOUS/'progress.json').read_text())
    if previous.get('stop_reason'):
        assert previous['stop_reason']=='new_environment_exception_requires_review'
        if REVIEW_INHERITED_HALT is not None:
            reviewed=REVIEW_INHERITED_HALT(previous)
            assert reviewed and reviewed['reviewed_environment_repair'] is True
        else:
            halt=json.loads((D/'validation/tb-mnist-hf-uri-network-20261027/activation/dispatch-halt.json').read_text())
            reviewed=recovered_connection_evidence(halt['row'])
            expected=json.loads((AUDIT/'reviewed-halt.json').read_text())
            assert reviewed and reviewed['result_sha256']==expected['result_sha256'], 'Inherited halt lacks exact reviewed recovered-turn evidence'
            assert expected['classifier_sha256']==sha(PROJECT/'scripts/maintenance/tb_recovered_connection_20261027.py')
            assert json.loads((AUDIT/'focused-validation.json').read_text())['returncode']==0
        save(AUDIT/'inherited-halt-review.json',reviewed)
    running=[r for r in previous['tasks']+previous['retry_tasks'] if r['state']=='running']
    registered={r['worker_pid'] for r in running}
    assert spawned_worker_pids(old_pid)<=registered, 'Dispatcher paused between spawn/progress; resume and retry handoff'
    assert len(running)<=8
    reg = json.loads((PREVIOUS/'registration.json').read_text())
    assert reg['workers_total'] == 8 and reg['min_free_disk_gib'] == 0
    cells, retries = copy.deepcopy(previous['tasks']), copy.deepcopy(previous['retry_tasks'])
    stall_proof=reg.get('single_download_stall_repair')
    # No workers are interrupted: only pending evidenced network retries change.
    # Preserve the other seven workers and the interrupted attempt's original path.
    protected = {r['result_path']: sha(r['result_path']) for r in cells+retries if r['state'] in TERMINAL}
    health = prior.health()
    assert json.loads((TRANSPORT_AUDIT/'private-network-probe-result.json').read_text())['returncode']==0
    assert json.loads((TRANSPORT_AUDIT/'adapter-network-preflights.json').read_text())['passed']==2
    for row in cells+retries:row.pop('dispatch_deferral',None)
    annotate(cells+retries,{})
    # Metadata health alone never opens dispatch; real inference is checked below.
    proof = FRESH_NETWORK_PROOF() if FRESH_NETWORK_PROOF else json.loads((D/'validation/tb-mnist-hf-uri-network-20261027/activation/fresh-readonly-isolation-verification.json').read_text())
    assert proof['at'][:10] == now()[:10] and proof['host_nat_unchanged'] and proof['private_authenticated_status'] == 200
    for harness in ('codex', 'sweagent'):
        hp = next(item for item in proof['checks'] if item['harness']==harness)
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
        dst=FROZEN/relative; src=Path(REPAIR_SOURCE_OVERRIDES.get(relative,PROJECT/relative))
        changes.append(dict(path=relative, previous_sha256=sha(dst) if dst.exists() else None,
                            repaired_sha256=sha(src),repair_source=str(src)))
        dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
    manifest=source_manifest(FROZEN)
    ROOT.mkdir()
    save(AUDIT/'protected-results.json', protected)
    save(AUDIT/'previous-progress.json', previous)
    registration = dict(reg, cohort=NAME, registered_at=now(), previous_cohort=PREVIOUS.name,
        frozen_source=str(FROZEN), source_sha256=manifest['sha256'], coordinator_script=__file__,
        coordinator_script_sha256=sha(__file__), user_instruction='持续监控；半小时无进展调查并修复已证环境阻塞；保留费用，下载限定分流，8080不动',
        original_frozen_worker_sources_unchanged=True, source_changes=changes,
        runtime_repair='recovered-codex-reconnect-classification; native-r36-unchanged/v1', native_task_containers_restarted=False, new_inference_for_reattachment=0, coordinator_kill_mode='process', prerequisite_cache_proof=str(CACHE_PROOF), prerequisite_cache_sha256=json.loads(CACHE_PROOF.read_text())['sha256'],
        cache_applies_to_future_hf_attempts_only=True, cached_and_uncached_results_separate=True,
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
    registration['recovered_connection_classifier_sha256']=sha(PROJECT/'scripts/maintenance/tb_recovered_connection_20261027.py')
    registration['reviewed_recovered_connection_halt']=str(AUDIT/'reviewed-halt.json')
    registration['no_outcome_retry_for_reviewed_assertion_failure']=True
    registration['inherited_single_download_stall_repair']=stall_proof
    registration['other_workers_restarted']=False
    registration['download_evidence_classifier_sha256']=sha(PROJECT/'scripts/maintenance/tb_download_retry_20261026.py')
    registration['download_evidence_repair_proof']=str(D/'validation/tb-live-monitor-20261026/mnist-hf-uri-detector-validation-20261027-1305.json')
    registration['retry_limit_per_cell']='One ordinary failure retry plus separately audited environment repairs; each explicit provider outage attempt retained and charged, never outcome-based best-of.'
    register_shutdown_repairs(cells,retries)
    initial_prerequisite_repairs=register_prerequisite_repairs(cells,retries,NAME,sha,key)
    save(AUDIT/'initial-prerequisite-repairs.json',dict(at=now(),attempts=initial_prerequisite_repairs))
    registration['initial_prerequisite_repairs']=len(initial_prerequisite_repairs)
    registration['fixed_prerequisite_versions']={'torch':'2.7.1+cu126','transformers':'4.52.4','peft':'0.15.0','pytest':'8.4.1','psutil':'7.0.0'}
    registration['preinstall_before_model']=True
    registration['prerequisite_repair_classifier_sha256']=sha(PROJECT/'scripts/maintenance/tb_hf_prerequisite_evidence_20261026.py')
    added=add_provider_repairs(cells,retries)
    registration['provider_repair_attempts_added']=len(added)
    download_repairs=register_download_retries(cells,retries,DOWNLOAD_REPAIR_COHORT,sha,key)
    registration['download_retries_registered']=len(download_repairs)
    registration['download_retry_transport']='private-netns Mihomo; explicit domains only; default DIRECT; 8080 and API DIRECT'
    registration['network_skip_revoked']=True
    save(AUDIT/'initial-download-retries.json',dict(at=now(),attempts=download_repairs))
    registration['adopted_running_workers']=len(running)
    registration['existing_network_task_workers_finish_without_restart']=True
    registration['old_attempts_interrupted_at_user_request']=False
    registration['runtime_shutdown_repair_evidence']=str(SHUTDOWN_AUDIT/'affected-worker.json')
    registration['network_backlog_uses_all_8_slots_before_ordinary_work']=True
    registration['shutdown_evidence_classifier_sha256']=sha(PROJECT/'scripts/maintenance/tb_shutdown_evidence_20261026.py')
    registration['registered_deadline_classifier_sha256']=sha(PROJECT/'scripts/maintenance/tb_registered_deadline_20261026.py')
    registration['dependency_graph_semantics_unchanged']=True
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
    # All validation and immutable snapshots are ready before retiring the
    # paused coordinator. KillMode=process protects its live child workers.
    assert prior.state(old_pid) in (None, 'Z') if old_dead else prior.state(old_pid)=='T'
    save(AUDIT/'handoff-ready.json',dict(at=now(),old_pid=old_pid,
        worker_pids=[r['worker_pid'] for r in cells+retries if r['state']=='running'],
        native_task_containers_restarted=False,new_inference_for_handoff=0))
    if not old_dead:
        os.kill(old_pid,signal.SIGKILL)
    until=time.monotonic()+15
    while prior.state(old_pid) not in (None,'Z') and time.monotonic()<until:time.sleep(0.05)
    assert prior.state(old_pid) in (None,'Z')
    global_lock=(D/'validation/tb-dual-native.lock').open('a')
    fcntl.flock(global_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
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
        report = separate_comparisons(prior.report(analyzed_cells, analyzed_retries),analyzed_cells,analyzed_retries)
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
            deadline_evidence=registered_deadline_evidence(row) if issue else None
            if deadline_evidence:row['registered_deadline_evidence']=deadline_evidence
            shutdown_evidence=shutdown_fault(row)
            if shutdown_evidence:row['runtime_shutdown_fault']=shutdown_evidence
            gateway=classify_provider_failure(row) if issue else None
            if gateway and not user_interruption:
                row['provider_failure_evidence']=gateway
                provider_gate=True;provider_failure=gateway;last_probe=time.monotonic()
                save(AUDIT/'provider-outage.json',dict(at=now(),row=row,evidence=gateway))
            elif (halts_dispatch(issue, result, budget) or connection) and not row.get('verifier_network_exclusion') and not user_interruption and not shutdown_evidence and not deadline_evidence:
                stop = 'new_environment_exception_requires_review'
                save(AUDIT/'dispatch-halt.json', dict(at=now(), row=row, connection_evidence=connection))
            del active[pid]
        prior.old.add_retries(cells, retries)
        register_interrupted_retries(cells,retries)
        register_shutdown_repairs(cells,retries)
        prerequisite_repairs=register_prerequisite_repairs(cells,retries,NAME,sha,key)
        if prerequisite_repairs:
            with (AUDIT/'prerequisite-repair-registrations.jsonl').open('a') as out:out.write(json.dumps(dict(at=now(),attempts=prerequisite_repairs))+'\n')
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
            env.pop('AGENTTX_TB_HF_PREINSTALL',None)
            if row['task']=='hf-train-lora-adapter':env['AGENTTX_TB_HF_PREINSTALL']='1'
            env.pop('AGENTTX_TB_HF_PREREQUISITE_CACHE_PROOF',None)
            if row['task']=='hf-train-lora-adapter':env['AGENTTX_TB_HF_PREREQUISITE_CACHE_PROOF']=str(CACHE_PROOF)
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
