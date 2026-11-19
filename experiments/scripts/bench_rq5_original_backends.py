#!/usr/bin/env python3
"""Add AgentTX to the original YoloFS session-microbenchmark comparison.

Native, YoloFS and OverlayFS execute their original Rust backend, including
the author's OverlayFS mount/flatten code. No AgentTX publisher is substituted.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

PERF = Path('/opt/rq5/sources/YoloFS-YoloFS-8848c24/target/release/yolo-bench')
RUNTIME = Path('/opt/rq5/agenttx-snapshot-only-v14')
SOURCE = Path('/opt/rq5/perf-primitives-20261115')
UPSTREAM = Path('/opt/rq5/sources/YoloFS-perf-eval-bc32d08')
WORKLOADS = ('write-files', 'overwrite-files', 'rename-files', 'unlink-files')
BACKENDS = ('native', 'yolo-realistic', 'overlayfs', 'agenttx')


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2)+'\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_upstream(output, workload, backend, env):
    command = [str(PERF), '--workload', workload, '--backend', backend,
               '--runs', '5', '--timestamped-results']
    started = time.perf_counter_ns()
    result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=1800)
    elapsed = time.perf_counter_ns()-started
    (output/'stdout.log').write_text(result.stdout)
    (output/'stderr.log').write_text(result.stderr)
    if result.returncode:
        raise RuntimeError(result.stderr[-5000:])
    matches = re.findall(r'Results written to (\S+/report/results\.json)', result.stdout+result.stderr)
    assert matches, result.stdout+result.stderr
    original = Path(matches[-1].rstrip('.'))
    document = json.loads(original.read_text())
    row = next(row for row in document['workloads'] if row['workload'] == workload)
    data = next(row for row in row['backends'] if row['backend'] == backend)
    assert len(data['iterations']) == 5
    shutil.copy2(original, output/'upstream-results.json')
    assert not any('failed:' in line or 'verbose rerun' in line for line in result.stderr.splitlines()), result.stderr
    return dict(workload=workload, backend=backend, status='passed',
                command=command, cli_wall_ns=elapsed, iterations=data['iterations'],
                model_calls=0, source='Original Rust Backend::run_one, including original publisher.')


def run_agenttx(output, workload, env):
    sys.path.insert(0, str(RUNTIME/'src'))
    from agenttx.snapshot import SnapshotSession
    results = []
    # Match the original backend's lower/<workload> directory layout.
    cache = Path(env['XDG_CACHE_HOME'])/'yolofs-bench'
    cache.mkdir(parents=True, exist_ok=True)
    for repeat in range(-1, 5):
        root = Path(tempfile.mkdtemp(prefix='yolofs-bench-atx-', dir=cache))
        lower = root/'lower'
        base = lower/workload
        base.mkdir(parents=True)
        if workload != 'write-files':
            subprocess.run([PERF, 'exec-workload', '--name', 'write-files', '--dest', base],
                           env=env, check=True, capture_output=True, timeout=1800)
        session = None
        try:
            start = time.perf_counter_ns()
            session = SnapshotSession(lower, root/'state', metadata_copy=True)
            init_ns = time.perf_counter_ns()-start
            dest = session.view/workload
            error_log = output/f'agenttx-{repeat}-workload-stderr.log'
            with error_log.open('w') as errors:
                started = time.perf_counter_ns()
                child = subprocess.Popen([PERF, 'exec-workload', '--name', workload, '--dest', '.'],
                    cwd=dest, env=env, text=True, stdout=subprocess.PIPE, stderr=errors)
                assert child.stdout.readline().strip() == 'YOLO_BENCH_READY'
                ready = time.perf_counter_ns()
                child.communicate(timeout=1800)
                finished = time.perf_counter_ns()
                assert child.returncode == 0, error_log.read_text()
            stage_ns = finished-ready
            start = time.perf_counter_ns()
            session.commit()
            commit_ns = time.perf_counter_ns()-start
            start = time.perf_counter_ns()
            session.close(destroy=True)
            cleanup_ns = time.perf_counter_ns()-start
            entries = sorted(base.iterdir())
            if workload == 'unlink-files':
                assert entries == []
            else:
                prefix = 'renamed' if workload == 'rename-files' else 'file'
                assert [path.name for path in entries] == [f'{prefix}-{index:06}.dat' for index in range(10000)]
                expected = bytes([255 if workload == 'overwrite-files' else 0])*4096
                assert all(path.read_bytes() == expected for path in entries)
            row = dict(repeat=repeat, init_ms=init_ns/1e6, staging_ms=stage_ns/1e6,
                       commit_ms=commit_ns/1e6, total_ms=(init_ns+stage_ns+commit_ns)/1e6,
                       init_ns=init_ns, staging_ns=stage_ns, commit_ns=commit_ns,
                       subprocess_startup_ns=ready-started, cleanup_ns=cleanup_ns,
                       validation='All final names and all file contents checked.')
            save(output/f'agenttx-{repeat}.json', row)
            if repeat >= 0:
                results.append(row)
        finally:
            if session is not None:
                session.close(destroy=True)
            shutil.rmtree(root)
    return dict(workload=workload, backend='agenttx', status='passed',
                iterations=results, model_calls=0, metadata_copy=True,
                source='SnapshotSession v14; original exec-workload body and READY timing.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert not args.output.exists()
    args.output.mkdir(parents=True)
    env = dict(os.environ, XDG_CACHE_HOME='/var/cache/agenttx-yolo-original-20261115',
               XDG_RUNTIME_DIR='/run/user/0')
    env.pop('AGENTTX_RQ5_FILE_COUNT', None)
    env.pop('AGENTTX_RQ5_COMMIT_LAYERS', None)
    Path(env['XDG_RUNTIME_DIR']).mkdir(mode=0o700, parents=True, exist_ok=True)
    # The only OverlayFS source edit exposes a private helper; its body is identical.
    original = (UPSTREAM/'src/backends/overlayfs.rs').read_text()
    actual = (SOURCE/'src/backends/overlayfs.rs').read_text()
    assert actual.replace('pub(crate) fn commit_upper_to_lower', 'fn commit_upper_to_lower') == original
    for backend in ('native', 'yolofs'):
        assert (SOURCE/f'src/backends/{backend}.rs').read_bytes() == (UPSTREAM/f'src/backends/{backend}.rs').read_bytes()
    plan = []
    rng = random.Random(20261115)
    for workload in WORKLOADS:
        order = list(BACKENDS)
        rng.shuffle(order)
        plan.extend(dict(workload=workload, backend=backend) for backend in order)
    save(args.output/'manifest.json', dict(plan=plan, files=10000, file_size=4096,
        measured_repeats=5, warmups=1, model_calls=0,
        runtime_sha256=sha(RUNTIME/'src/agenttx/snapshot.py'),
        binary_sha256=sha(PERF), driver_sha256=sha(Path(__file__)),
        source_sha256={f'src/backends/{name}.rs': sha(SOURCE/f'src/backends/{name}.rs')
                       for name in ('native', 'overlayfs', 'yolofs')},
        original_comparison='Native Ext4, YoloFS realistic, original OverlayFS backend; AgentTX added.',
        accounting='Paper total = init + run + commit; base population, status and teardown are separate.',
        commit_note='Original OverlayFS unmount precedes commit timer; AgentTX commit includes its own seal.',
        agenttx_config='metadata_copy=True for every workload; original lower/<workload> layout.',
        precondition='sync before each backend block; author runner supplies its own warmup.'))
    rows = []
    for serial, item in enumerate(plan):
        save(args.output/'progress.json', dict(state='running', completed=serial, total=len(plan), current=item))
        output = args.output/f'{serial:02}-{item["workload"]}-{item["backend"]}'
        output.mkdir()
        os.sync()
        if item['backend'] == 'agenttx':
            row = run_agenttx(output, item['workload'], env)
        else:
            row = run_upstream(output, item['workload'], item['backend'], env)
        save(output/'record.json', row)
        rows.append(row)
    summary = []
    for row in rows:
        metrics = {}
        for key in ('init_ms', 'staging_ms', 'commit_ms', 'total_ms'):
            values = [sample.get(key) for sample in row['iterations']]
            metrics[key] = (dict(mean=statistics.mean(values), median=statistics.median(values),
                                 stdev=statistics.stdev(values), samples=values)
                            if all(value is not None for value in values) else None)
        summary.append(dict(workload=row['workload'], backend=row['backend'], metrics=metrics))
    save(args.output/'summary.json', dict(rows=summary, completed_blocks=len(rows),
                                         measured_sessions=sum(len(row['iterations']) for row in rows)))
    save(args.output/'progress.json', dict(state='complete', completed=len(plan), total=len(plan)))


if __name__ == '__main__':
    main()
