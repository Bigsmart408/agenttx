#!/usr/bin/env python3
"""Fixed-trajectory comparison of retaining and clearing application processes."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = 'experiments/scripts/bench_process_state_20261110.py'
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
FAMILIES = ('independent', 'read_through', 'cached', 'mixed')
POLICIES = ('retain_all', 'clear_all')
DOCKER = ['docker', '--host', 'unix:///run/agenttx-home-docker/docker.sock']


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.replace(path)


def rpc(port, payload):
    with socket.create_connection(('127.0.0.1', port), timeout=5) as conn:
        conn.sendall(json.dumps(payload).encode() + b'\n')
        with conn.makefile('rb') as stream:
            return json.loads(stream.readline())


def serve(args):
    memo, cached = [], None
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        ready = dict(port=listener.getsockname()[1], pid=os.getpid())
        if args.ready_file:
            args.ready_file.write_text(json.dumps(ready))
        print(json.dumps(ready), flush=True)
        while True:
            conn, _ = listener.accept()
            with conn, conn.makefile('rb') as stream:
                request = json.loads(stream.readline())
                if request['op'] == 'warm':
                    memo = [hashlib.sha256((request['data'] + str(i)).encode()).hexdigest()
                            for i in range(request['items'])]
                elif request['op'] == 'prime' and args.family in ('read_through', 'cached', 'mixed'):
                    cached = request['data']
                elif request['op'] == 'query' and args.family == 'read_through':
                    cached = request['data']
                elif request['op'] not in ('prime', 'query', 'inspect'):
                    raise ValueError(request['op'])
                value = cached if cached is not None else request.get('data')
                conn.sendall(json.dumps(dict(pid=os.getpid(), memo=memo,
                    cached=cached, value=value)).encode() + b'\n')


def start_service(family, log):
    stream = log.open('w')
    process = subprocess.Popen([sys.executable, '-u', str(ROOT / SCRIPT),
        'service', '--family', family], stdout=subprocess.PIPE, stderr=stream,
        text=True, start_new_session=True)
    try:
        ready = json.loads(process.stdout.readline())
        assert ready['pid'] == process.pid
        return dict(process=process, log=stream, **ready)
    except BaseException:
        stop_service(dict(process=process, log=stream))
        raise


def stop_service(service):
    process = service['process']
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if process.stdout:
        process.stdout.close()
    service['log'].close()


CLIENT = '''import json,socket,sys
from pathlib import Path
port=int(sys.argv[1]); payload=json.loads(sys.argv[2])
if 'file' in payload: payload['data']=Path(payload.pop('file')).read_text()
with socket.create_connection(('127.0.0.1',port),timeout=5) as s:
 s.sendall(json.dumps(payload).encode()+b'\\n')
 with s.makefile('rb') as f: print(f.readline().decode().strip())
'''


def trial(output, family, seed, repeat, policy):
    from agenttx.runtime import AgentTX
    output.mkdir(parents=True)
    workspace = output / 'workspace'
    workspace.mkdir()
    good = f'correct-{seed}\n'
    bad = f'faulty-{seed}\n'
    valid = (f'independent-{seed}\n' * 256)
    (workspace / 'fault.txt').write_text(good)
    (workspace / 'valid.txt').write_text(valid)
    items = 32 * (seed + 1)
    tx, services = None, []
    started = time.perf_counter()
    row = dict(family=family, seed=seed, repeat=repeat, policy=policy,
               model_calls=0, model_api_requests=0, task_processes=2,
               items_per_warm_service=items, started_at=now())
    try:
        # Application services outlive individual tool workers, as do the
        # separately managed services in the native benchmark harness.
        services = [start_service(family, output / 'application-a.log'),
                    start_service('independent', output / 'application-b.log')]
        before_pids = [s['pid'] for s in services]
        tx = AgentTX.begin(workspace, output / 'session', trace_backend='strace')
        tx.conversation.seed('Execute the fixed workload.', 'Preserve independent progress.')

        def tool(name, argv):
            record = tx.run_tool(name, argv, timeout_s=10)
            if record.returncode:
                raise RuntimeError(f'{name}: {record.stderr}')
            call_id = f'call-{record.step_id}'
            tx.conversation.append_turn([
                dict(role='assistant', content=None, tool_calls=[dict(id=call_id,
                    type='function', function=dict(name=name, arguments=json.dumps(dict(argv=argv))))]),
                dict(role='tool', tool_call_id=call_id, content=record.stdout)],
                step_ids=[record.step_id], tool_names=[name], kind='effect')
            tx._persist()
            return record

        def request(index, payload, name):
            record = tool(name, [sys.executable, '-c', CLIENT,
                                 str(services[index]['port']), json.dumps(payload)])
            return json.loads(record.stdout)

        warm_a = family != 'cached'
        if warm_a:
            request(0, dict(op='warm', file='valid.txt', items=items), 'warm-independent-a')
        request(1, dict(op='warm', file='valid.txt', items=items), 'warm-independent-b')
        root = tool('faulty-write', [sys.executable, '-c',
                    'from pathlib import Path; Path("fault.txt").write_text(' + repr(bad) + ')'])
        source_file = 'valid.txt' if family == 'independent' else 'fault.txt'
        request(0, dict(op='prime', file=source_file), 'consume-application-input')
        derived = tool('derive-from-fault', [sys.executable, '-c',
            'from pathlib import Path; Path("derived.txt").write_text(Path("fault.txt").read_text())'])
        independent = tool('independent-write', [sys.executable, '-c',
            'from pathlib import Path; Path("independent.txt").write_text(Path("valid.txt").read_text())'])
        before = [rpc(s['port'], dict(op='inspect')) for s in services]
        save(output / 'processes-before.json', before)
        save(output / 'conversation-before.json', tx.conversation.to_dict())
        recovery_start = time.perf_counter()
        selected = tx.rollback_causal(root.step_id)
        recovery_s = time.perf_counter() - recovery_start
        active = set(tx.conversation.active_step_ids())

        def content(path):
            entry = tx._overlay_entry(workspace / path)
            return entry.read_text() if entry is not None and entry.is_file() else None

        state_checks = dict(fault_file_restored=content('fault.txt') == good,
            dependent_file_removed=content('derived.txt') is None,
            independent_file_retained=content('independent.txt') == valid,
            context_withdraws_fault=root.step_id not in active,
            context_withdraws_dependent=derived.step_id not in active,
            context_retains_independent=independent.step_id in active)
        save(output / 'conversation-after.json', tx.conversation.to_dict())
        # Inspect the task services before applying either process policy.
        surviving = [rpc(s['port'], dict(op='inspect')) for s in services]
        assert [s['pid'] for s in surviving] == before_pids
        process_start = time.perf_counter()
        if policy == 'clear_all':
            for service in services:
                stop_service(service)
            services = [start_service(family, output / 'application-a-restarted.log'),
                        start_service('independent', output / 'application-b-restarted.log')]
        process_policy_s = time.perf_counter() - process_start
        after = [rpc(s['port'], dict(op='inspect')) for s in services]
        save(output / 'processes-after.json', after)
        result = request(0, dict(op='query', file='fault.txt'), 'query-after-recovery')
        independent_result = request(1, dict(op='inspect'), 'query-independent-service')
        expected_memos = [p['memo'] for p in before]
        surviving_items = sum(sum(x == y for x, y in zip(old, new['memo']))
                              for old, new in zip(expected_memos, after))
        total_items = sum(map(len, expected_memos))
        row.update(execution_passed=all(state_checks.values()), state_checks=state_checks,
            rollback_ids=selected, before_pids=before_pids, after_pids=[p['pid'] for p in after],
            task_processes_survived_rollback=True,
            task_processes_kept=before_pids == [p['pid'] for p in after],
            subsequent_result_correct=result['value'] == good,
            independent_service_matches=independent_result['memo'] == expected_memos[1],
            valid_memory_items=total_items, retained_memory_items=surviving_items,
            valid_memory_retention=surviving_items / total_items,
            all_valid_memory_retained=surviving_items == total_items,
            file_and_process_result_agree=result['value'] == content('fault.txt'),
            recovery_wall_s=recovery_s, process_policy_wall_s=process_policy_s,
            actual_result=result['value'], expected_result=good)
        row['fully_synchronized_and_progress_preserved'] = (
            all(state_checks.values()) and row['subsequent_result_correct']
            and row['all_valid_memory_retained'])
        row['expected_process_policy_observed'] = (
            row['task_processes_kept'] == (policy == 'retain_all')
            and row['subsequent_result_correct'] == (policy == 'clear_all' or family not in ('cached', 'mixed'))
            and row['all_valid_memory_retained'] == (policy == 'retain_all'))
        row['execution_passed'] &= row['expected_process_policy_observed']
        tx.dump_ledger(output / 'ledger.json')
    except Exception as exc:
        import traceback
        row.update(execution_passed=False, error=f'{type(exc).__name__}: {exc}')
        (output / 'error.txt').write_text(traceback.format_exc())
    finally:
        for service in services:
            stop_service(service)
        if tx is not None:
            tx.close(destroy=False)
        row.update(finished_at=now(), full_wall_s=time.perf_counter() - started)
        save(output / 'result.json', row)
    return row


def probe(output):
    """Check whether an unmodified rollback preserves a worker-owned daemon."""
    from agenttx.runtime import AgentTX
    output.mkdir()
    workspace = output / 'workspace'
    workspace.mkdir()
    (workspace / 'fault.txt').write_text('correct\n')
    tx = AgentTX.begin(workspace, output / 'session', trace_backend='strace')
    try:
        launch = shlex.join([sys.executable, '-u', str(ROOT / SCRIPT), 'service',
                            '--family', 'independent', '--ready-file', 'ready.json'])
        command = launch + ' </dev/null >/var/tmp/agenttx-process-probe.log 2>&1 &\n' + \
            'for i in $(seq 1 100); do test -f ready.json && exit 0; sleep .02; done; exit 1'
        rec = tx.run_tool('start-worker-owned-service', ['bash', '-c', command], timeout_s=10)
        assert rec.returncode == 0, rec.stderr
        ready = json.loads(tx._overlay_entry(workspace / 'ready.json').read_text())
        before = rpc(ready['port'], dict(op='inspect'))
        root = tx.run_tool('faulty-write', [sys.executable, '-c',
            'from pathlib import Path; Path("fault.txt").write_text("faulty\\n")'])
        selected = tx.rollback_causal(root.step_id)
        try:
            rpc(ready['port'], dict(op='inspect'))
            alive = True
        except (OSError, ValueError):
            alive = False
        row = dict(worker_owned_service_survives=alive, before=before,
                   rollback_ids=selected, model_calls=0, model_api_requests=0)
        save(output / 'result.json', row)
        return row
    finally:
        tx.close(destroy=False)


def worker(args):
    cohort = args.cohort
    try:
        save(cohort / 'worker-process-probe.json', probe(cohort / 'worker-process-probe'))
        pairs = [(f, seed, repeat) for repeat in range(3) for f in FAMILIES for seed in range(10)]

        def pair(case):
            family, seed, repeat = case
            policies = POLICIES if (seed + repeat) % 2 == 0 else tuple(reversed(POLICIES))
            rows = []
            for policy in policies:
                name = f'{family}/seed-{seed:02d}/repeat-{repeat:02d}/{policy}'
                row = trial(cohort / 'trials' / name, family, seed, repeat, policy)
                rows.append(row)
                print(json.dumps({k: row.get(k) for k in ('family', 'seed', 'repeat', 'policy',
                    'execution_passed', 'subsequent_result_correct', 'valid_memory_retention', 'error')}), flush=True)
                if not row['execution_passed']:
                    raise RuntimeError(name + ': ' + str(row.get('error', 'runtime check failed')))
            return rows

        rows = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            for pair_rows in pool.map(pair, pairs):
                rows.extend(pair_rows)
                save(cohort / 'progress.json', dict(updated_at=now(), completed=len(rows),
                    expected=240, model_calls=0, model_api_requests=0))
        groups = []
        for family in FAMILIES:
            for policy in POLICIES:
                selected = [r for r in rows if r['family'] == family and r['policy'] == policy]
                groups.append(dict(family=family, policy=policy, n=len(selected),
                    execution_passed=sum(r['execution_passed'] for r in selected),
                    subsequent_correct=sum(r['subsequent_result_correct'] for r in selected),
                    all_valid_memory_retained=sum(r['all_valid_memory_retained'] for r in selected),
                    strict_sync=sum(r['fully_synchronized_and_progress_preserved'] for r in selected),
                    mean_process_policy_ms=sum(r['process_policy_wall_s'] for r in selected) / len(selected) * 1000,
                    mean_valid_memory_retention=sum(r['valid_memory_retention'] for r in selected) / len(selected)))
        save(cohort / 'summary.json', dict(completed=len(rows), expected=240,
            model_calls=0, model_api_requests=0, groups=groups,
            worker_process_probe=json.loads((cohort / 'worker-process-probe.json').read_text())))
    finally:
        for directory, dirs, files in os.walk(cohort, followlinks=False):
            for path in [Path(directory), *(Path(directory) / n for n in dirs + files)]:
                try:
                    os.chown(path, args.result_uid, args.result_gid, follow_symlinks=False)
                except FileNotFoundError:
                    pass


def prepare(args):
    from experiments.scripts.experiment_provenance import source_manifest
    cohort = args.cohort.resolve()
    cohort.mkdir(mode=0o700)
    source = ROOT.parent / 'agenttx-executions' / cohort.name / 'project'
    source.mkdir(parents=True)
    manifest = source_manifest(ROOT)
    for item in manifest['files']:
        dest = source / item['path']
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / item['path'], dest)
        assert hashlib.sha256(dest.read_bytes()).hexdigest() == item['sha256']
    reference = json.loads((ROOT.parent / 'agenttx-experiment-data/results/rq1-strategy-only-6000-20261110-r1/registration.json').read_text())
    save(cohort / 'source-manifest.json', manifest)
    save(cohort / 'registration.json', dict(registered_at=now(), frozen_source=str(source),
        cohort=str(cohort), image_id=reference['image_id'], python=reference['python'],
        python_mounts=reference['python_mounts'], uid=os.getuid(), gid=os.getgid(),
        families=FAMILIES, seeds=10, repetitions=3, policies=POLICIES, paired_cases=120,
        expected_trials=240, parallel_pairs=2, trajectory='fixed-scripted-no-agent',
        fault_location='provided; identical filesystem recovery in both arms',
        process_scope='all two application services outside the disposable tool worker',
        clear_policy='terminate both application services; relaunch empty services; no memory warmup',
        retain_policy='retain both application processes and their current memory without selective cleanup',
        source_sha256=manifest['sha256'], model_calls=0, model_api_requests=0, network='none'))
    print(str(cohort), flush=True)


def run(args):
    cohort = args.cohort.resolve()
    reg = json.loads((cohort / 'registration.json').read_text())
    source = Path(reg['frozen_source'])
    for item in json.loads((cohort / 'source-manifest.json').read_text())['files']:
        assert hashlib.sha256((source / item['path']).read_bytes()).hexdigest() == item['sha256']
    mounts = sorted({source, *map(Path, reg['python_mounts'])})
    cmd = DOCKER + ['run', '--rm', '--pull', 'never', '--privileged', '--network', 'none',
        '--name', cohort.name, '--tmpfs', '/root:exec', '--tmpfs', '/tmp:exec',
        '--tmpfs', '/sys', '--tmpfs', '/run', '--workdir', str(source)]
    for path in mounts:
        cmd += ['--mount', f'type=bind,src={path},dst={path},readonly']
    cmd += ['--mount', f'type=bind,src={cohort},dst=/results']
    for k, v in dict(PYTHONPATH=f'{source}/src:{source}', PYTHONDONTWRITEBYTECODE='1',
        AGENTTX_MOTIVATION_BWRAP='1', AGENTTX_NO_PROXY='1', AGENTTX_SKIP_ORPHAN_REAP='1',
        AGENTTX_WORKER_PYTHON=reg['python'], AGENTTX_TRACE_SPOOL_ROOT='/var/tmp',
        TRY_BIND_READONLY=':'.join(map(str, mounts))).items():
        cmd += ['--env', f'{k}={v}']
    cmd += ['--entrypoint', reg['python'], reg['image_id'], '-u', str(source / SCRIPT),
        'worker', '--cohort', '/results', '--result-uid', str(reg['uid']), '--result-gid', str(reg['gid'])]
    return subprocess.call(cmd)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    for action in ('prepare', 'run', 'worker'):
        q = sub.add_parser(action)
        q.add_argument('--cohort', type=Path, required=True)
        if action == 'worker':
            q.add_argument('--result-uid', type=int, required=True)
            q.add_argument('--result-gid', type=int, required=True)
    q = sub.add_parser('service')
    q.add_argument('--family', choices=FAMILIES, required=True)
    q.add_argument('--ready-file', type=Path)
    args = p.parse_args()
    if args.action == 'service':
        return serve(args)
    if args.action == 'worker':
        worker(args)
    elif args.action == 'prepare':
        prepare(args)
    else:
        return run(args)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
