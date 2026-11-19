#!/usr/bin/env python3
"""Fill the missing SWE/Codex arm using the registered SWE recovery protocol."""
from __future__ import annotations

import argparse
from collections import Counter
import fcntl
import hashlib
from itertools import zip_longest
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'src'), str(ROOT)]
from experiments.scripts import run_swe_monitored_local as shared
from experiments.scripts.experiment_provenance import source_manifest
from experiments.scripts.experiment_credentials import private_values, redact_credentials, purge_private_codex_auth

POLICIES = ('clean_recovery', 'crash_no_rollback', 'temporal_checkpoint',
            'whole_branch_abort', 'causal', 'ideal_oracle')
CONTEXTS = {p: 'compact' if p in ('causal', 'ideal_oracle', 'clean_recovery') else 'full' for p in POLICIES}
DATA = shared.DATA
REFERENCE = DATA/'results/sweagent-swe-local-monitored-20261018-selective-auto-20261019T064657Z/registration.json'


def save(path, value):
    shared.save(path, value)


def worker_command(*arguments):
    command = shared.worker_command(*arguments)
    # The container mounts /home/bfq below /home. Rebind it through try so
    # workspace venv symlinks can still resolve their host Python interpreter.
    index = command.index('TRY_BIND_READONLY')+1
    command[index] += ':/home/bfq'
    return command


def worker(cohort, task, policy):
    from experiments.scripts import bench_official_tasks as bench
    from experiments.workloads import swe_bench_suite as swe
    folder = cohort/'cells'/task/policy
    os.environ['AGENTTX_RECOVERY_CONTEXT'] = CONTEXTS[policy]
    row = bench.run_once(suite='swe', task_name=task, mode=policy, repeat=0,
        cache_root=shared.CACHE, model='gpt-5.6-luna', provider=None, max_turns=None,
        trace_backend='strace', python=sys.executable, oracle=False, harness_backend='codex',
        harness_root=None, harness_timeout_s=900, harness_output_dir=folder,
        reasoning_effort='max', swe_tasks=swe.load_tasks(shared.CACHE))
    row.update(formal_eligible=False, cohort=cohort.name,
        source_sha256=source_manifest(ROOT)['sha256'], context_profile='selective-compact-v1',
        expected_recovery_context_variant=CONTEXTS[policy])
    secrets = private_values()
    save(folder/'result.json', redact_credentials(row, secrets))
    cleanup = purge_private_codex_auth(folder, secrets)
    if cleanup:
        save(folder/'auth-cleanup.json', cleanup)
    for path in [folder, *folder.rglob('*')]:
        if not path.is_symlink():
            try:
                os.chown(path, 1003, 1003)
            except OSError:
                pass
    print(json.dumps({k: row.get(k) for k in ('task','mode','success','environment_error','error','total_tokens','tests_rc')}), flush=True)


def prepare(cohort):
    reference = json.loads(REFERENCE.read_text())
    assert reference['fault_seed']=='20261018' and reference['model_wall_timeout_per_session_s']==900
    assert reference['policies']==list(POLICIES) and reference['context_by_policy']==CONTEXTS
    cohort.mkdir(parents=True, exist_ok=False)
    frozen = Path('/home/bfq/agenttx-workspace/agenttx-executions')/cohort.name/'project'
    frozen.mkdir(parents=True, exist_ok=False)
    manifest = source_manifest(ROOT)
    for entry in manifest['files']:
        destination = frozen/entry['path']
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT/entry['path'], destination)
    assert source_manifest(frozen)['sha256']==manifest['sha256']
    canary = 'psf__requests-1963'
    families = {}
    images = {r['task']: r for r in reference['images']}
    assert len(images)==300
    for task in sorted(images):
        if task!=canary:
            families.setdefault(task.split('__')[0], []).append(task)
    tasks = [canary]+[t for group in zip_longest(*families.values()) for t in group if t]
    cells = [dict(task=t, policy=p, state='pending') for t in tasks for p in POLICIES]
    reg = dict(reference)
    reg.update(protocol='agenttx.swe-codex-recovery/v1', registered_at=shared.now(),
        code_root=str(frozen), frozen_source=str(frozen), harness='codex',
        source_sha256=manifest['sha256'], comparison_reference=str(REFERENCE),
        comparison_reference_sha256=shared.digest_file(REFERENCE),
        purpose='missing fourth workload/harness group, new six-policy campaign',
        continuation=None, passed_canary=None, canary=dict(task=canary, cells=6,
            gate='all six infrastructure/context/usage valid, independent of task success'),
        automatic_outcome_retry=False, prior_results_inherited=False,
        operational_changes=['Privileged Docker coordinator with host toolchain and private worker mount namespaces; no host sudo required.'],
        retention='Keep outcomes, main/replay API usage and diagnostics for every attempt; keep full Clean traces. No global pruning.',
        profile_sha256={p: shared.digest_file(Path('/home/bfq/.config/agenttx/experiment-20261017')/p)
                        for p in ('config.toml','auth.json')})
    save(cohort/'registration.json', reg)
    save(cohort/'source-manifest.json', manifest)
    save(cohort/'cells.json', cells)
    print(json.dumps(dict(cohort=str(cohort), frozen_source=str(frozen), cells=len(cells), workers=8)))


def run(cohort):
    assert os.geteuid()==0
    lock = (cohort/'coordinator.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
    reg = json.loads((cohort/'registration.json').read_text())
    assert source_manifest(ROOT)['sha256']==reg['source_sha256']
    cells = json.loads((cohort/'cells.json').read_text())
    assert not any(r['state']=='running' for r in cells), 'archive interrupted attempts before resuming'
    images = {r['task']: r for r in reg['images']}
    assert all(subprocess.run(['docker','image','inspect',r['image_id']], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL).returncode==0 for r in images.values())
    active = []
    stop_reason = None
    canary_passed = (cohort/'canary-proof.json').exists()
    next_cell = 0
    hydrated = set()
    last_report = 0

    def report():
        save(cohort/'cells.json', cells)
        save(cohort/'progress.json', dict(updated_at=shared.now(), coordinator_pid=os.getpid(),
            counts=dict(Counter(r['state'] for r in cells)),
            active=[dict(task=r['task'],policy=r['policy'],pid=p.pid) for r,p,_ in active],
            stage='attention_required' if stop_reason else 'running_full_suite' if canary_passed else 'six_policy_canary',
            stop_reason=stop_reason, workers=8, formal_eligible=False,
            disk_free_gib=round(shutil.disk_usage(DATA).free/2**30,1)))

    def stop(sig, frame):
        nonlocal stop_reason
        stop_reason = 'received '+signal.Signals(sig).name

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    report()
    while next_cell<len(cells) or active:
        for item in list(active):
            row, process, log = item
            if process.poll() is None:
                continue
            log.close()
            active.remove(item)
            path = cohort/'cells'/row['task']/row['policy']/'result.json'
            result = json.loads(path.read_text()) if path.exists() else {}
            worker_log = (path.parent/'worker.log').read_text(errors='replace')
            issue = shared.infrastructure_issue(result, worker_log, expected_context=CONTEXTS[row['policy']]) if result else 'worker_terminated_without_result'
            row.update(state='infrastructure_error' if issue else 'completed', finished_at=shared.now(),
                success=result.get('success'), returncode=process.returncode, infrastructure_issue=issue,
                result_path=str(path), tokens=result.get('total_tokens'), error=result.get('error'))
            if issue and not stop_reason:
                stop_reason = row['task']+':'+row['policy']+':'+issue
            report()
        if not canary_passed and all(r['state']=='completed' for r in cells[:6]):
            proof = dict(checked_at=shared.now(), infrastructure_valid=6, task_success_required=False,
                rows=[dict(policy=r['policy'],result=r['result_path'],sha256=shared.digest_file(Path(r['result_path']))) for r in cells[:6]])
            save(cohort/'canary-proof.json', proof)
            canary_passed = True
        if shutil.disk_usage(DATA).free<200*2**30 and not stop_reason:
            stop_reason = 'data_disk_below_200_gib_guard'
        if stop_reason:
            if not active:
                break
        else:
            ceiling = len(cells) if canary_passed else 6
            while len(active)<8 and next_cell<ceiling:
                row = cells[next_cell]
                if row['state']!='pending':
                    next_cell += 1
                    continue
                if row['task'] not in hydrated:
                    image = images[row['task']]
                    try:
                        shared.hydrate(row['task'], image['image_id'], image['base_commit'])
                        hydrated.add(row['task'])
                    except Exception as exc:
                        row.update(state='infrastructure_error', error=str(exc))
                        stop_reason = 'source_hydration_failed'
                        report()
                        break
                folder = cohort/'cells'/row['task']/row['policy']
                folder.mkdir(parents=True, exist_ok=False)
                log = (folder/'worker.log').open('w')
                command = ['timeout','--signal=TERM','--kill-after=30','3600',
                    *worker_command(sys.executable,'-u',str(Path(__file__).resolve()),
                        'worker','--cohort',str(cohort),'--task',row['task'],'--policy',row['policy'])]
                process = subprocess.Popen(command, cwd=ROOT, env=dict(os.environ, AGENTTX_RECOVERY_CONTEXT=CONTEXTS[row['policy']]),
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                row.update(state='running', started_at=shared.now(), pid=process.pid)
                active.append((row,process,log))
                next_cell += 1
                report()
        if time.monotonic()-last_report>=30:
            report()
            last_report = time.monotonic()
        time.sleep(5)
    report()
    save(cohort/'execution.json',dict(finished_at=shared.now(),stop_reason=stop_reason,
        counts=dict(Counter(r['state'] for r in cells)),formal_eligible=False))
    return int(bool(stop_reason))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare','run','worker'))
    parser.add_argument('--cohort', type=Path, required=True)
    parser.add_argument('--task')
    parser.add_argument('--policy', choices=POLICIES)
    args = parser.parse_args()
    if args.action=='prepare':
        prepare(args.cohort)
    elif args.action=='worker':
        worker(args.cohort,args.task,args.policy)
    else:
        return run(args.cohort)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
