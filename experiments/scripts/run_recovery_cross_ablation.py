#!/usr/bin/env python3
"""Run the real-task filesystem/context factorial, with durable accounting."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from contextlib import closing

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from experiments.scripts.experiment_provenance import source_manifest

DATA = Path('/mnt/data/agenttx-runtime')
PYTHON = '/home/bfq/.venvs/agenttx/bin/python'
from experiments.scripts.recovery_cross_ablation import VARIANTS, install
import random
SWE_REFERENCE = DATA / 'results/codex-swe-full-recovery-20261104-r1/registration.json'
TB_REFERENCE = DATA / 'results/tb-dual-native-full-20261030-r72-reviewed-flink-causal-timeout/registration.json'


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(path)
    if os.geteuid() == 0:
        os.chown(path, 1003, 1003)


def prepare(cohort, workers):
    swe, tb = read(SWE_REFERENCE), read(TB_REFERENCE)
    assert swe['model'] == tb['model'] == 'gpt-5.6-luna'
    assert swe['reasoning_effort'] == tb['reasoning_effort'] == 'max'
    assert len(swe['images']) == 300 and len(tb['tasks']) == 241
    inventory = read(tb['inventory'])
    evidence = {r['task']: r['evidence'] for r in
                inventory['official_baseline_tasks'] + inventory.get('audited_task_baseline_tasks', [])}
    cohort.mkdir(parents=True, exist_ok=False)
    frozen = cohort / 'project'
    frozen.mkdir(parents=True, exist_ok=False)
    manifest = source_manifest(ROOT)
    for entry in manifest['files']:
        destination = frozen / entry['path']
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / entry['path'], destination)
    assert source_manifest(frozen)['sha256'] == manifest['sha256']
    groups = [('swe', h, ['psf__requests-1963'] + sorted(
        r['task'] for r in swe['images'] if r['task'] != 'psf__requests-1963'))
        for h in ('sweagent', 'codex')]
    groups += [('tb', h, ['hello-world'] + sorted(set(tb['tasks']) - {'hello-world'}))
               for h in ('sweagent', 'codex')]
    cells = []
    for index in range(300):
        for benchmark, harness, tasks in groups:
            if index < len(tasks):
                variants=list(VARIANTS)
                random.Random(20261118 + index).shuffle(variants)
                for variant in variants:
                    cells.append(dict(key=f'{benchmark}/{harness}/{tasks[index]}/{variant}', benchmark=benchmark,
                    harness=harness, task=tasks[index], policy=VARIANTS[variant][0], variant=variant, pair_key=f'{benchmark}/{harness}/{tasks[index]}', state='pending',
                    canary=index == 0, attempts=[]))
    assert len(cells) == len({c['key'] for c in cells}) == 4328
    profile = Path('/home/bfq/.config/agenttx/experiment-20261017')
    registration = dict(protocol='agenttx.recovery-cross-ablation/v2', registered_at=now(),
        cohort=cohort.name, frozen_source=str(frozen), source_sha256=manifest['sha256'],
        expected_cells=len(cells), variants=VARIANTS, context='full', model='gpt-5.6-luna',
        reasoning_effort='max', workers=workers, canary_keys=[c['key'] for c in cells if c['canary']],
        canary_requires_task_success=False,
        fault_hints='no rollback runs no localizer; component arms use the unchanged verifier-guided causal localizer; no handoff discloses fault locations',
        selection='factorial: causal filesystem recovery off/on x context synchronization off/on',
        no_rollback_baseline=True, context_only_applies_no_file_rollback=True,
        tb_preflight_infrastructure_policy='clean_recovery',
        tb_preflight_component_treatment='registered variant after infrastructure probes',
        shared_prompt_template=True, history_compaction=False, model_attempts_per_cell=1,
        task_count=541, harnesses=['sweagent','codex'], repetitions=1,
        scratch_root=str(cohort / 'scratch'),
        disk_guards_gib={'output': 25, 'docker': 15, 'host': 25},
        retention='terminal TB disposable session payloads removed; full logs, usage, ledgers and lossless traces retained',
        max_tb_workers=2,
        original_turn_grouping_preserved=True, prior_results_modified=False,
        swe_reference=str(SWE_REFERENCE), swe_reference_sha256=sha(SWE_REFERENCE),
        tb_reference=str(TB_REFERENCE), tb_reference_sha256=sha(TB_REFERENCE),
        swe_images=swe['images'], tb_tasks=tb['tasks'], tb_evidence=evidence,
        swe_fault_seed='20261018', tb_fault_seed='20261019',
        swe_model_timeout_s=swe['model_wall_timeout_per_session_s'], swe_cell_timeout_s=3600,
        tb_model_timeout_s=tb['model_session_wall_s'], tb_verifier_timeout_s=tb['verifier_wall_s'],
        tb_cell_timeout_s=tb['cell_wall_s'], tb_protocol=tb['protocol'],
        tb_completion_contract=tb['completion_contract'],
        ordinary_outcome_retries=dict(swe=0, tb=0), automatic_infrastructure_retries=0,
        all_attempt_costs_retained=True, unknown_usage_imputed=False, formal_eligible=False,
        profile_sha256={p: sha(profile / p) for p in ('config.toml', 'auth.json', 'sweagent.toml')})
    save(cohort / 'registration.json', registration)
    save(cohort / 'source-manifest.json', manifest)
    save(cohort / 'cells.json', cells)
    print(json.dumps(dict(cohort=str(cohort), frozen_source=str(frozen), cells=len(cells), workers=workers)))


def worker_command(cohort, module, *arguments):
    """Keep all SWE temporary work and trace spools on the cohort volume."""
    scratch = cohort / 'scratch'
    scratch.mkdir(exist_ok=True)
    os.chmod(scratch, 0o1777)
    command = module.worker_command(*arguments)
    old = str(DATA / 'tmp')
    assert old in command
    command[command.index(old)] = str(scratch)
    return command


def swe_worker(cohort, task, harness, variant, folder):
    from experiments.scripts import run_swe_monitored_local as shared
    from experiments.scripts import bench_official_tasks as bench
    from experiments.scripts.experiment_credentials import private_values, redact_credentials, purge_private_codex_auth
    from experiments.workloads import swe_bench_suite as swe
    policy = VARIANTS[variant][0]
    install(variant, folder, harness=harness)
    os.environ['AGENTTX_RECOVERY_CONTEXT'] = 'full'
    os.environ['AGENTTX_FAULT_SEED'] = '20261018'
    row = bench.run_once(suite='swe', task_name=task, mode=policy, repeat=0,
        cache_root=shared.CACHE, model='gpt-5.6-luna', provider=None, max_turns=None,
        trace_backend='strace', python=sys.executable, oracle=False, harness_backend=harness,
        harness_root=Path(os.environ['AGENTTX_SWE_AGENT_ROOT']) if harness == 'sweagent' else None,
        harness_timeout_s=900, harness_output_dir=folder, reasoning_effort='max',
        swe_tasks=swe.load_tasks(shared.CACHE))
    row.update(cohort=cohort.name, source_sha256=read(cohort / 'registration.json')['source_sha256'],
               context_profile='full', expected_recovery_context_variant='full', formal_eligible=False,
               cross_variant=variant, cross_treatment=read(folder/'cross-treatment.json') if (folder/'cross-treatment.json').exists() else None)
    secrets = private_values()
    save(folder / 'result.json', redact_credentials(row, secrets))
    cleanup = purge_private_codex_auth(folder, secrets)
    if cleanup:
        save(folder / 'auth-cleanup.json', cleanup)
    for path in [folder, *folder.rglob('*')]:
        if not path.is_symlink():
            os.chown(path, 1003, 1003)


def zero_swe_probe(task_name, variant, output):
    from agenttx.harness import CodingAgentHarness
    from experiments.workloads import swe_bench_suite as swe
    from experiments.workloads import recovery_inject as injection
    from experiments.scripts import bench_official_tasks as bench
    from experiments.scripts.run_swe_monitored_local import CACHE
    import tempfile
    task=swe.load_tasks(CACHE)[task_name]
    repo,instance=swe.ensure_repo(task,CACHE)
    work=Path(tempfile.mkdtemp(prefix='cross-swe-',dir=os.environ.get('TMPDIR','/tmp')))
    session=work.parent/(work.name+'-session')
    swe.copy_repo(repo,work)
    python=swe.ensure_workspace_venv(work,swe.ensure_venv(task,sys.executable,CACHE,instance))
    swe.prepare_workspace_runtime(work,instance,python)
    swe.seed_task_workspace(work,task,instance)
    subprocess.run(['git','init','--quiet'],cwd=work,check=True)
    treatment=install(variant,output,harness='sweagent')
    with closing(CodingAgentHarness(work,session_dir=session,trace_backend='strace',ephemeral_roots=(work/'.tmp',))) as h:
        agent=type('Probe',(),{'harness':h})()
        docs,after,_=bench._randomized_crash_docs(task,task.name,0)
        injected=swe.inject_task_trajectory(agent,task,instance,python,docs=docs,fault_after_docs=after)
        assert injection.dag_is_valid(injected)
        policy=VARIANTS[variant][0]
        targets,_=(bench._auto_localize_policy(agent,injected) if policy=='causal' else
                   bench._apply_blind_policy(agent,injected,policy))
        paths=[injected['faulty_path'],*injected['derived_paths']]
        manifest=injection.build_recovery_manifest(policy=policy,ledger=h.tx.ledger,
            injected=injected,docs=docs,document_contents=injection.read_recovery_documents(agent,docs),
            workdir=work,rollback_targets=targets,path_exists={p:h.tx.path_exists(work/p) for p in paths})
        assert manifest['authoritative'] and treatment.applied
        save(output/'proof.json',dict(passed=True,external_model_calls=0,treatment=treatment.proof))


def preflight(cohort):
    from experiments.scripts import run_codex_swe_recovery as swe
    from experiments.scripts import run_tb_dual_local as tb
    registration=read(cohort/'registration.json')
    folder=cohort/'preflight'
    folder.mkdir(exist_ok=False)
    os.chown(folder,1003,1003)
    for variant in VARIANTS:
        output=folder/('swe-'+variant)
        command=worker_command(cohort,swe,PYTHON,str(Path(__file__).resolve()),'zero-swe-worker',
            '--cohort',str(cohort),'--task','psf__requests-1963','--variant',variant,'--output-dir',str(output))
        subprocess.run(command,check=True)
        assert read(output/'proof.json')['passed']
    for task in ('hello-world','privilege-escalation'):
        for harness in ('sweagent','codex'):
            for variant,(policy,_) in VARIANTS.items():
                # Keep preceding infrastructure probes identical; the wrapper
                # separately injects and checks the actual registered variant.
                cell=dict(task=task,harness=harness,policy='clean_recovery')
                output=folder/f'tb-{task}-{harness}-{variant}'
                command=tb.cell_command(ROOT,output,cell,registration['tb_evidence'].get(task))
                command[command.index(str(ROOT/'experiments/scripts/bench_tb_image_native.py'))]=str(ROOT/'experiments/scripts/bench_recovery_cross_tb.py')
                env=dict(os.environ,AGENTTX_CROSS_VARIANT=variant)
                subprocess.run(['runuser','-u','bfq','--',*command,'--preflight-only'],env=env,check=True)
                result=read(output/'clean_recovery'/'host-result.json')
                assert result['completed'] and result['status']=='preflight_passed'
                assert result['completion_contract_preflight']['cross_treatment']['original_observation_treatment_verified']
    save(folder/'proof.json',dict(checked_at=now(),passed=True,external_model_calls=0,
        swe_variants=list(VARIANTS),tb_variants=list(VARIANTS),tb_harnesses=['sweagent','codex'],
        tb_tasks=['hello-world','privilege-escalation']))


def result_status(cell, folder):
    from experiments.scripts import run_swe_monitored_local as swe
    from experiments.scripts import run_tb_dual_local as tb
    path = folder / ('result.json' if cell['benchmark'] == 'swe' else 'native/' + cell['policy'] + '/host-result.json')
    if not path.exists():
        return path, {}, 'missing_result', False
    result = read(path)
    if cell['benchmark'] == 'swe':
        issue = swe.infrastructure_issue(result, expected_context='full')
        success = bool(result.get('success'))
    else:
        issue = tb.infrastructure_issue(result, 'full')
        success = tb.result_success(result)
    treatment=path.parent/'cross-treatment.json'
    if not issue and (not treatment.exists() or read(treatment).get('variant')!=cell['variant']):
        issue='missing_or_mismatched_cross_treatment'
    return path, result, issue, success


def run(cohort):
    assert os.geteuid() == 0
    lock = (cohort / 'coordinator.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    registration = read(cohort / 'registration.json')
    assert source_manifest(ROOT)['sha256'] == registration['source_sha256']
    assert read(cohort / 'preflight/proof.json')['passed']
    cells = read(cohort / 'cells.json')
    assert not any(c['state'] == 'running' for c in cells)
    from experiments.scripts import run_swe_monitored_local as swe
    from experiments.scripts import run_codex_swe_recovery as codex
    from experiments.scripts import run_tb_dual_local as tb
    images = {r['task']: r for r in registration['swe_images']}
    active, hydrated = [], set()
    stopped = None
    canary_passed = (cohort / 'canary-proof.json').exists()

    def report():
        save(cohort / 'cells.json', cells)
        save(cohort / 'progress.json', dict(updated_at=now(), coordinator_pid=os.getpid(),
            stage='attention_required' if stopped else 'running' if canary_passed else 'sixteen_variant_combination_canary',
            counts=dict(Counter(c['state'] for c in cells)), stop_reason=stopped,
            active=[dict(key=c['key'], pid=p.pid, started_at=c['started_at']) for c, p, _, _ in active],
            total=len(cells), workers=registration['workers'],
            disk_free_gib={'output':round(shutil.disk_usage(cohort).free/2**30,1),
                'docker':round(shutil.disk_usage(DATA).free/2**30,1),
                'host':round(shutil.disk_usage('/home/bfq').free/2**30,1)},
            attempts=sum(len(c['attempts']) for c in cells),
            groups={f'{b}/{h}': dict(Counter(c['state'] for c in cells if c['benchmark'] == b and c['harness'] == h))
                    for b in ('swe', 'tb') for h in ('sweagent', 'codex')}))

    def stop(signum, frame):
        nonlocal stopped
        stopped = 'received ' + signal.Signals(signum).name

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while True:
        for item in list(active):
            cell, process, log, folder = item
            if process.poll() is None:
                continue
            log.close()
            active.remove(item)
            path, result, issue, success = result_status(cell, folder)
            attempt = dict(number=len(cell['attempts']) + 1, started_at=cell['started_at'], finished_at=now(),
                folder=str(folder), result_path=str(path), result_sha256=sha(path) if path.exists() else None,
                returncode=process.returncode, success=success, infrastructure_issue=issue,
                total_tokens=result.get('total_tokens'), api_usage=result.get('api_usage'))
            cell['attempts'].append(attempt)
            cell.update(state='infrastructure_error' if issue else 'completed', success=success,
                        finished_at=now(), result_path=str(path), infrastructure_issue=issue)
            # Preserve a registered timeout as terminal and continue other tasks.
            timeout = cell['benchmark'] == 'swe' and result.get('harness_returncode') == 124 and not result.get('host_leak_before_commit')
            if issue and not timeout and not stopped:
                stopped = cell['key'] + ':' + issue
            report()
            if cell['benchmark'] == 'tb' and path.exists():
                from experiments.scripts.retain_recovery_cross_session import retain
                try:
                    retain(cohort, folder)
                except Exception as exc:
                    stopped = 'retention_requires_review:' + cell['key'] + ':' + str(exc)
                    report()
        if not canary_passed and all(c['state'] in ('completed', 'infrastructure_error') for c in cells if c['canary']):
            if all(c['state'] == 'completed' for c in cells if c['canary']):
                canary_passed = True
                save(cohort / 'canary-proof.json', dict(checked_at=now(), task_success_required=False,
                    cells=[dict(key=c['key'], attempts=c['attempts']) for c in cells if c['canary']]))
        for label, location in (('output', cohort), ('docker', DATA), ('host', Path('/home/bfq'))):
            minimum = registration['disk_guards_gib'][label]
            if shutil.disk_usage(location).free < minimum * 2**30 and not stopped:
                stopped = f'{label}_disk_below_{minimum}_gib'
        if not stopped:
            for cell in cells:
                if len(active) >= registration['workers']:
                    break
                if cell['state'] != 'pending' or not (canary_passed or cell['canary']):
                    continue
                if cell['benchmark']=='tb' and sum(c['benchmark']=='tb' for c,_,_,_ in active)>=registration['max_tb_workers']:
                    continue
                folder = cohort / 'cells' / cell['benchmark'] / cell['harness'] / cell['task'] / cell['variant'] / f'attempt-{len(cell["attempts"]) + 1:03d}'
                folder.mkdir(parents=True, exist_ok=False)
                os.chown(folder, 1003, 1003)
                if cell['benchmark'] == 'swe':
                    if cell['task'] not in hydrated:
                        image = images[cell['task']]
                        swe.hydrate(cell['task'], image['image_id'], image['base_commit'])
                        hydrated.add(cell['task'])
                    command = worker_command(cohort, codex,PYTHON, '-u', str(Path(__file__).resolve()), 'swe-worker',
                        '--cohort', str(cohort), '--task', cell['task'], '--harness', cell['harness'], '--variant', cell['variant'], '--output-dir', str(folder))
                    limit = registration['swe_cell_timeout_s']
                else:
                    command = ['runuser', '-u', 'bfq', '--', *tb.cell_command(
                        ROOT, folder / 'native', cell, registration['tb_evidence'].get(cell['task']))]
                    original_entry=str(ROOT/'experiments/scripts/bench_tb_image_native.py')
                    command[command.index(original_entry)]=str(ROOT/'experiments/scripts/bench_recovery_cross_tb.py')
                    limit = registration['tb_cell_timeout_s']
                command = ['timeout', '--signal=TERM', '--kill-after=30', str(limit), *command]
                save(folder / 'launch-command.json', command)
                log = (folder / 'worker.log').open('w')
                env = dict(os.environ, AGENTTX_CROSS_VARIANT=cell['variant'], AGENTTX_RECOVERY_CONTEXT='full', AGENTTX_FAULT_SEED='20261018' if cell['benchmark'] == 'swe' else '20261019')
                process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                           stdin=subprocess.DEVNULL, start_new_session=True)
                cell.update(state='running', started_at=now(), pid=process.pid)
                active.append((cell, process, log, folder))
                report()
        report()
        if not active and (stopped or not any(c['state'] == 'pending' for c in cells)):
            break
        time.sleep(5)
    save(cohort / 'execution.json', dict(finished_at=now(), stop_reason=stopped,
        counts=dict(Counter(c['state'] for c in cells)), cells=len(cells)))
    return int(bool(stopped))


def monitor(cohort):
    while True:
        path = cohort / 'progress.json'
        if path.exists():
            progress = read(path)
            cells = read(cohort / 'cells.json')
            errors = []
            for cell in cells:
                for attempt in cell['attempts']:
                    result = Path(attempt['result_path'])
                    if attempt['result_sha256'] and (not result.exists() or sha(result) != attempt['result_sha256']):
                        errors.append('terminal_result_changed:' + cell['key'])
            snapshot = dict(checked_at=now(), progress=progress, terminal_evidence_errors=errors)
            save(cohort / 'monitor/latest.json', snapshot)
            with (cohort / 'monitor/history.jsonl').open('a') as stream:
                stream.write(json.dumps(snapshot, ensure_ascii=False) + '\n')
            if errors or progress['stop_reason']:
                save(cohort / 'monitor/attention.json', snapshot)
            if (cohort / 'execution.json').exists() and not progress['active']:
                return
        time.sleep(60)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'preflight', 'run', 'swe-worker', 'zero-swe-worker', 'monitor'))
    parser.add_argument('--cohort', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--task')
    parser.add_argument('--variant',choices=tuple(VARIANTS))
    parser.add_argument('--harness', choices=('sweagent', 'codex'))
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.cohort, args.workers)
    elif args.action == 'swe-worker':
        swe_worker(args.cohort, args.task, args.harness, args.variant, args.output_dir)
    elif args.action == 'zero-swe-worker':
        zero_swe_probe(args.task,args.variant,args.output_dir)
    else:
        return globals()[args.action](args.cohort)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
