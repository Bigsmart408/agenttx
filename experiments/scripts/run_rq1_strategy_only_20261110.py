#!/usr/bin/env python3
"""Run fixed-trajectory recovery strategies without agents or model requests."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
POLICIES = ('causal', 'crash_no_rollback', 'turn_checkpoint',
            'temporal_checkpoint', 'whole_branch_abort')
POLICY_CHOICES = (*POLICIES, 'root_only')
PROTOCOL = 'agenttx.rq1-scripted-recovery/v1'
DOCKER = ['docker', '--host', 'unix:///run/agenttx-home-docker/docker.sock']
REFERENCE = ROOT.parent / 'agenttx-experiment-data/results/system-micro-codex-6000-20261107-r4'
SCRIPT = 'experiments/scripts/run_rq1_strategy_only_20261110.py'
RUNTIME_SCRIPT = 'experiments/scripts/bench_runtime_recovery_system.py'


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cases():
    from experiments.scripts.bench_external_recovery_artifacts import motivation_400_cases
    return motivation_400_cases()


def expected_targets(plan, policy):
    affected = set(plan.oracle_closure)
    if policy == 'causal':
        return affected
    if policy == 'crash_no_rollback':
        return set()
    if policy == 'root_only':
        return set(plan.fault_roots)
    if policy == 'whole_branch_abort':
        return set(range(len(plan.steps)))
    if policy == 'temporal_checkpoint':
        return set(range(min(affected), len(plan.steps)))
    if policy == 'turn_checkpoint':
        turns = {s.turn_index for s in plan.steps if s.index in affected}
        return {s.index for s in plan.steps if s.turn_index in turns}
    raise ValueError(policy)


def audit_policy(row, plan, policy):
    # Generator truth is used only for scoring after the runtime has selected S.
    expected = expected_targets(plan, policy)
    actual = {i for i in row.get('rollback_ids', []) if i < len(plan.steps)}
    affected = set(plan.oracle_closure)
    checks = dict(
        runtime_finished=row.get('completed') is True,
        selection_matches_policy=actual == expected,
        invalid_retained_matches=sorted(row.get('invalid_retained', [])) == sorted(affected - expected),
        independent_discarded_matches=sorted(row.get('useful_discarded', [])) == sorted(expected - affected),
        filesystem_context_agree=row.get('conversation_matches_rollback') is True,
        retained_bytes_preserved=row.get('retained_bytes_unchanged') is True,
        captured_required_dependencies=not row.get('missing_direct_edges'),
        no_model_execution=row.get('runtime_only') is True and not row.get('model_calls', 0),
    )
    if policy == 'causal':
        checks['localized_injected_root'] = row.get('localization_matches_injected_roots') is True
    return checks


def prepare(args):
    cohort = args.cohort.resolve()
    policies = tuple(args.policies)
    assert policies and len(set(policies)) == len(policies)
    assert set(policies) <= set(POLICY_CHOICES)
    old = read(REFERENCE / 'registration.json')
    original_source = Path(old['frozen_source'])
    inventory = read(REFERENCE / 'source-manifest.json')['files']
    assert all(digest(original_source / x['path']) == x['sha256'] for x in inventory)
    plans = cases()
    original_hashes = {c['trace_id']: c['manifest_hash'] for c in read(REFERENCE / 'cells.json')['main']}
    assert {c.plan.trace_id: c.plan.manifest_hash for c in plans} == original_hashes
    cohort.mkdir(parents=True, exist_ok=False, mode=0o700)
    source = ROOT.parent / 'agenttx-executions' / cohort.name / 'project'
    source.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(original_source, source, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(ROOT / SCRIPT, source / SCRIPT)
    changed = {RUNTIME_SCRIPT} if 'root_only' in policies else set()
    if changed:
        shutil.copy2(ROOT / RUNTIME_SCRIPT, source / RUNTIME_SCRIPT)
    frozen = [{'path': str(p.relative_to(source)), 'sha256': digest(p)}
              for p in sorted(source.rglob('*')) if p.is_file() and not p.is_symlink()]
    source_hash = hashlib.sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest()
    save(cohort / 'source-manifest.json', dict(sha256=source_hash, files=frozen))
    assert all(digest(source / x['path']) == x['sha256'] for x in inventory
               if x['path'] not in changed)
    image = subprocess.check_output(DOCKER + ['image', 'inspect', old['image_id'], '--format', '{{.Id}}'], text=True).strip()
    assert image == old['image_id']

    def cell(case, policy, repeat, phase):
        return dict(key=f'{case.plan.trace_id}/{policy}/repeat-{repeat:02d}',
                    trace_id=case.plan.trace_id, manifest_hash=case.plan.manifest_hash,
                    family=case.suite_slice, operation_count=len(case.plan.steps),
                    policy=policy, repeat=repeat, harness='scripted', phase=phase, state='pending')

    # Cover every family and the dense-dependency endpoint before the main matrix.
    selected = [next(c for c in plans if c.suite_slice == family)
                for family in ('mb2_task_length', 'mb3_fault_position', 'mb4_dependency')]
    selected.append(plans[-1])
    stages = dict(runtime_gate=[cell(c, p, 0, 'runtime_gate') for c in selected for p in policies],
                  main=[cell(c, p, repeat, 'main') for repeat in range(3) for c in plans for p in policies])
    expected_cells = len(plans) * len(policies) * 3
    assert len(stages['main']) == expected_cells
    reg = dict(protocol=PROTOCOL, registered_at=now(), cohort=str(cohort),
               frozen_source=str(source), source_sha256=source_hash, image_id=image,
               reference=str(REFERENCE), original_runtime_files_unchanged=len(inventory) - len(changed),
               benchmark_files_updated=sorted(changed),
               manifests_identical=True, manifest_count=400, repeats=3, policies=policies,
               harnesses=['scripted'], execution_kind='fixed-trajectory-runtime-only',
               expected_cells=expected_cells, expected_cells_by_policy=dict.fromkeys(policies, 1200),
               runtime_gate_cells=len(stages['runtime_gate']), workers=args.workers,
               python=old['python'], python_mounts=old['python_mounts'], uid=os.getuid(), gid=os.getgid(),
               model=None, model_calls=0, api_requests=0, network_mode='none',
               model_profiles_mounted=False, native_agents_started=False,
               cell_timeout_s=3600, max_probes='2 * operation_count + 8',
               ordinary_retries=0, report_interval_s=300,
               measurements=['invalid_removal', 'valid_retention', 'useful_discarded',
                             'recovery_safe', 'conversation_matches_rollback',
                             'retained_bytes_unchanged', 'localization_probe_count', 'timings'],
               scoring_note='passed means execution follows its policy; recovery_complete is separate. '
                            'No rollback and Selected effects can retain erroneous effects.',
               previous_model_runs_inherited=False)
    save(cohort / 'registration.json', reg)
    save(cohort / 'cells.json', stages)
    (cohort / 'logs').mkdir()
    (cohort / 'events.jsonl').touch()
    print(json.dumps(dict(cohort=str(cohort), cells=expected_cells, workers=args.workers,
                          runtime_gate_cells=len(stages['runtime_gate']), model_calls=0)), flush=True)


def worker(args):
    from experiments.scripts import bench_runtime_recovery_system as runtime

    class ModelExecutionForbidden:
        def __init__(self, *a, **kw):
            raise RuntimeError('This campaign forbids model execution')

    runtime.CodexHarness = ModelExecutionForbidden
    plan = next(c.plan for c in cases() if c.plan.trace_id == args.trace_id)
    options = SimpleNamespace(harness='scripted', runtime_only=True, model=None,
                              max_probes=2 * len(plan.steps) + 8)
    try:
        row = runtime.run_cell(options, plan, args.policy, None, args.output)
        checks = audit_policy(row, plan, args.policy)
        row.update(protocol=PROTOCOL, execution_kind='fixed-trajectory-runtime-only',
                   model=None, reasoning_effort=None, model_calls=0, api_requests=0,
                   native_agent_started=False, policy_checks=checks,
                   policy_contract_passed=all(checks.values()),
                   recovery_complete=row.get('recovery_safe') is True)
        save(args.output / 'result.json', row)
        print(json.dumps(dict(policy=args.policy, checks=checks,
                              recovery_complete=row['recovery_complete'], model_calls=0)), flush=True)
        return 0 if row['policy_contract_passed'] else 1
    finally:
        # Runtime snapshots are retained as evidence; return ownership after exit.
        for directory, dirs, files in os.walk(args.output, followlinks=False):
            for path in [Path(directory), *(Path(directory) / x for x in dirs + files)]:
                try:
                    os.chown(path, args.result_uid, args.result_gid, follow_symlinks=False)
                except FileNotFoundError:
                    pass


def container_name(cohort, cell):
    return 'atx-rq1-' + hashlib.sha256((str(cohort) + cell['phase'] + cell['key']).encode()).hexdigest()[:18]


def container_command(reg, cell):
    cohort, source = Path(reg['cohort']), Path(reg['frozen_source'])
    output = cohort / cell['phase'] / cell['key']
    name = container_name(cohort, cell)
    mounts = {source, *map(Path, reg['python_mounts'])}
    cmd = DOCKER + ['run', '--rm', '--pull', 'never', '--privileged', '--name', name,
                   '--network', 'none', '--tmpfs', '/root:exec', '--tmpfs', '/tmp:exec',
                   '--tmpfs', '/sys', '--tmpfs', '/run', '--workdir', str(source)]
    for p in sorted(mounts):
        cmd += ['--mount', f'type=bind,src={p},dst={p},readonly']
    cmd += ['--mount', f'type=bind,src={cohort},dst=/results']
    env = dict(PYTHONPATH=f'{source}/src:{source}', PYTHONDONTWRITEBYTECODE='1',
               AGENTTX_MOTIVATION_BWRAP='1', AGENTTX_NO_PROXY='1', AGENTTX_SKIP_ORPHAN_REAP='1',
               AGENTTX_WORKER_PYTHON=reg['python'], AGENTTX_TRACE_SPOOL_ROOT='/var/tmp',
               TRY_BIND_READONLY=':'.join(map(str, sorted(mounts))))
    for k, v in env.items():
        cmd += ['--env', f'{k}={v}']
    cmd += ['--entrypoint', reg['python'], reg['image_id'], '-u', str(source / SCRIPT),
            'worker', '--trace-id', cell['trace_id'], '--policy', cell['policy'],
            '--output', str(Path('/results') / output.relative_to(cohort)),
            '--result-uid', str(reg['uid']), '--result-gid', str(reg['gid'])]
    return cmd


def execute(reg, cell):
    cohort = Path(reg['cohort'])
    output = cohort / cell['phase'] / cell['key']
    output.parent.mkdir(parents=True, exist_ok=True)
    log = cohort / 'logs' / (container_name(cohort, cell) + '.log')
    started = time.monotonic()
    try:
        with log.open('w') as stream:
            p = subprocess.run(container_command(reg, cell), stdout=stream, stderr=subprocess.STDOUT,
                               timeout=reg['cell_timeout_s'])
        result = output / 'result.json'
        if not result.exists():
            raise RuntimeError('Worker did not produce a result')
        row = read(result)
        assert row['trace_id'] == cell['trace_id'] and row['manifest_hash'] == cell['manifest_hash']
        assert row['policy'] == cell['policy']
        assert row['runtime_only'] and row['model_calls'] == row['api_requests'] == 0
        assert not (output / 'model-result.json').exists() and not (output / 'api-usage.json').exists()
        good = p.returncode == 0 and row['policy_contract_passed']
        return dict(state='passed' if good else 'infrastructure_error', result=str(result), log=str(log),
                    wall_s=time.monotonic() - started, recovery_complete=row['recovery_complete'],
                    model_calls=0, api_requests=0, result_sha256=digest(result),
                    error=None if good else 'policy_or_runtime_check_failed')
    except Exception as exc:
        subprocess.run(DOCKER + ['rm', '-f', container_name(cohort, cell)], capture_output=True, timeout=30)
        return dict(state='infrastructure_error', log=str(log), wall_s=time.monotonic() - started,
                    error=f'{type(exc).__name__}: {exc}')


def run(args):
    cohort = args.cohort.resolve()
    reg, stages = read(cohort / 'registration.json'), read(cohort / 'cells.json')
    workers = getattr(args, 'workers', None) or reg['workers']
    assert isinstance(workers, int) and 1 <= workers <= 128
    assert reg['network_mode'] == 'none' and reg['model_calls'] == 0
    assert all(digest(Path(reg['frozen_source']) / f['path']) == f['sha256']
               for f in read(cohort / 'source-manifest.json')['files'])
    assert not any(c['state'] in ('running', 'infrastructure_error') for rows in stages.values() for c in rows)
    docker_root = subprocess.check_output(DOCKER + ['info', '--format', '{{.DockerRootDir}}'], text=True).strip()
    previous_progress = read(cohort / 'progress.json') if (cohort / 'progress.json').exists() else {}
    started, stop = previous_progress.get('started_at', now()), None

    def progress(phase):
        terminal = [c for c in stages['main'] if c['state'] == 'passed']
        counts = {k: dict(Counter(c['state'] for c in v)) for k, v in stages.items()}
        storage = {k: shutil.disk_usage(p).free / 1024**3
                   for k, p in [('results', cohort), ('docker', docker_root)]}
        row = dict(updated_at=now(), started_at=started, phase=phase, counts=counts,
                   stop_reason=stop, workers=workers, storage_free_gib=storage,
                   expected_cells=reg['expected_cells'], completed=len(terminal),
                   complete_recovery=sum(c.get('recovery_complete', False) for c in terminal),
                   model_calls=0, api_requests=0)
        save(cohort / 'cells.json', stages)
        save(cohort / 'progress.json', row)
        return storage

    for phase in ('runtime_gate', 'main'):
        pending = iter(c for c in stages[phase] if c['state'] == 'pending')
        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            while True:
                storage = progress(phase)
                if min(storage.values()) < 100:
                    stop = 'free_disk_below_100_GiB'
                while not stop and len(futures) < workers:
                    try:
                        cell = next(pending)
                    except StopIteration:
                        break
                    cell.update(state='running', started_at=now())
                    futures[pool.submit(execute, reg, cell)] = cell
                progress(phase)
                if not futures:
                    break
                done, _ = wait(futures, timeout=15, return_when=FIRST_COMPLETED)
                for f in done:
                    cell = futures.pop(f)
                    cell.update(f.result(), finished_at=now())
                    with (cohort / 'events.jsonl').open('a') as stream:
                        stream.write(json.dumps(cell) + '\n')
                    print(json.dumps({k: cell.get(k) for k in ('phase', 'key', 'state', 'error')}), flush=True)
                    if cell['state'] != 'passed':
                        stop = 'runtime_or_policy_check_failed'
        if stop:
            break
    progress('stopped' if stop else 'finished')
    save(cohort / 'execution.json', dict(finished_at=now(), stop_reason=stop, model_calls=0, api_requests=0))


def monitor(args):
    output = args.cohort / 'monitor'
    output.mkdir(exist_ok=True)
    last_report, cache = 0, {}
    while True:
        if not (args.cohort / 'progress.json').exists():
            time.sleep(2)
            continue
        progress = read(args.cohort / 'progress.json')
        stages = read(args.cohort / 'cells.json')
        reg = read(args.cohort / 'registration.json')
        counts = {p: dict(Counter(c['state'] for c in stages['main'] if c['policy'] == p)) for p in reg['policies']}
        issues = []
        for c in stages['main'] + stages['runtime_gate']:
            if c['state'] == 'passed' and c['key'] + c['phase'] not in cache:
                r = read(c['result'])
                assert r['model_calls'] == r['api_requests'] == 0 and r['runtime_only']
                assert digest(c['result']) == c['result_sha256']
                cache[c['key'] + c['phase']] = True
        active = [c for rows in stages.values() for c in rows if c['state'] == 'running']
        for c in active:
            if (datetime.now(timezone.utc) - datetime.fromisoformat(c['started_at'])).total_seconds() > 1800:
                issues.append('worker_over_30min:' + c['key'])
        if progress.get('stop_reason'):
            issues.append(progress['stop_reason'])
        status = subprocess.run(['systemctl', '--user', 'is-active', args.service], capture_output=True, text=True).stdout.strip()
        if progress['phase'] not in ('finished', 'stopped') and status != 'active':
            issues.append('coordinator_not_active')
        row = dict(at=now(), phase=progress['phase'], counts_by_policy=counts,
                   completed=progress['completed'], expected_cells=reg['expected_cells'],
                   worker_limit=progress.get('workers', reg['workers']),
                   active_workers=len(active), source_results_audited=len(cache),
                   model_calls=0, api_requests=0, alerts=issues, storage_free_gib=progress['storage_free_gib'])
        save(output / 'latest.json', row)
        with (output / 'samples.jsonl').open('a') as stream:
            stream.write(json.dumps(row) + '\n')
        if time.monotonic() - last_report >= 300 or progress['phase'] in ('finished', 'stopped'):
            lines = ['# RQ1 固定轨迹纯策略实验', '',
                     datetime.now(ZoneInfo('Asia/Shanghai')).strftime('北京时间 %Y-%m-%d %H:%M:%S'), '',
                     f"阶段：{row['phase']}；主实验完成 {row['completed']}/{row['expected_cells']}；运行中 {len(active)}/{row['worker_limit']}。", '',
                     '| 策略 | 已通过执行检查 | 异常 | 运行中 | 待运行 |', '|---|---:|---:|---:|---:|']
            for p, c in counts.items():
                lines.append('| ' + p + ' | ' + ' | '.join(str(c.get(k, 0)) for k in ('passed', 'infrastructure_error', 'running', 'pending')) + ' |')
            lines += ['', '真实模型调用：0；API 请求：0。容器网络关闭，不挂载模型配置或凭据。', '',
                      '执行检查通过与完整恢复分开计分；No rollback 保留错误影响。', '',
                      '告警：' + ('；'.join(issues) or '无') + '。']
            (output / 'latest-report.md').write_text('\n'.join(lines) + '\n')
            with (output / 'reports.jsonl').open('a') as stream:
                stream.write(json.dumps(row) + '\n')
            last_report = time.monotonic()
        print(json.dumps(row), flush=True)
        if progress['phase'] in ('finished', 'stopped') or args.once:
            break
        time.sleep(60)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--cohort', required=True, type=Path)
    p.add_argument('--workers', type=int, default=8)
    p.add_argument('--policies', nargs='+', choices=POLICY_CHOICES, default=list(POLICIES))
    for name in ('run', 'monitor'):
        p = sub.add_parser(name)
        p.add_argument('--cohort', required=True, type=Path)
        if name == 'run':
            p.add_argument('--workers', type=int, choices=range(1, 129))
        if name == 'monitor':
            p.add_argument('--service', required=True)
            p.add_argument('--once', action='store_true')
    p = sub.add_parser('worker')
    p.add_argument('--trace-id', required=True)
    p.add_argument('--policy', required=True, choices=POLICY_CHOICES)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--result-uid', required=True, type=int)
    p.add_argument('--result-gid', required=True, type=int)
    args = parser.parse_args()
    if args.action == 'worker':
        return worker(args)
    if args.action == 'prepare':
        assert 1 <= args.workers <= 128
        prepare(args)
    elif args.action == 'run':
        run(args)
    else:
        monitor(args)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
