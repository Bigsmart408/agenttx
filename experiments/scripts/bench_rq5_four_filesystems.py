#!/usr/bin/env python3
"""YoloFS fixed workloads on current AgentTX, matched OverlayFS, YoloFS and Ext4."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time

RUNTIME = Path('/opt/rq5/agenttx-snapshot-only-v14')
ROOT = Path('/var/lib/agenttx-four-filesystems-20261115')
VARIANTS = ('ext4', 'agenttx', 'overlay', 'yolofs-api',
            'agenttx-metacopy', 'overlay-metacopy')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--one')
    args = parser.parse_args()
    sys.path.insert(0, str(RUNTIME/'src'))
    from agenttx import snapshot
    import bench_rq5_yolofs_followup as adapters
    bench = adapters.current
    assert Path(snapshot.__file__).is_relative_to(RUNTIME)
    bench.ROOT = ROOT
    if args.one:
        raise SystemExit(0 if bench.one(json.loads(args.one), args.output) else 1)
    assert not args.output.exists() and not ROOT.exists()
    args.output.mkdir(parents=True)
    plan = []
    def add(phase, variant, operation, files, repeat):
        plan.append(dict(serial=len(plan), phase=phase, variant=variant, repeat=repeat,
                         case=dict(kind='commit', operation=operation, files=files)))
    for operation in bench.base.WORKLOADS:
        for variant in VARIANTS:
            add('commit-canary', variant, operation, 32, -2)
    rng = random.Random(20261115)
    for operation in bench.base.WORKLOADS:
        for repeat in range(-1, 5):
            order = list(VARIANTS)
            rng.shuffle(order)
            for variant in order:
                add('warmup' if repeat == -1 else 'measured', variant, operation, 10000, repeat)
    files = [Path(__file__), Path(adapters.__file__), Path(bench.__file__),
             Path(bench.fairness.__file__), Path(bench.base.__file__),
             Path(bench.fairness.snapshot_bench.__file__),
             Path(bench.fairness.comparison.__file__)]
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    bench.base.atomic(args.output/'manifest.json', dict(plan=plan, model_calls=0,
        formal_campaign_paused=True, runtime=str(RUNTIME),
        runtime_sha256=sha(Path(snapshot.__file__)),
        native_sha256=sha(RUNTIME/'src/agenttx/_snapshot_native.so'),
        script_sha256={path.name: sha(path) for path in files},
        workload_binary_sha256=sha(bench.base.PERF),
        yolo_binary_sha256=sha(Path('/usr/local/bin/yolo')),
        settings=dict(files=10000, filesize=4096, warmups=1, repeats=5,
            workloads='Fixed upstream Rust write-files, overwrite-files, rename-files, unlink-files bodies.',
            backends='Current own storage adapters; no substitution with upstream Backend::run_one.',
            overlay='Same mount, publication primitives and metacopy setting as AgentTX for each pair.',
            yolo='Native unmodified filesystem and CLI; permissions enabled, paths preallowed, auto_snapshot=false.',
            lifecycle='Initial population/setup + operation + publication, seal/unmount and staging destruction.',
            finish='Every required materialization, publication, mount/reference release and staging cleanup.',
            validation='All final names, complete contents and modes checked after timing.',
            sample_policy='Fixed randomized order, one warmup and five measured samples per configuration; no samples dropped.',
            precondition='sync before every session, before any timed setup or data generation.')))
    for item in plan:
        bench.base.atomic(args.output/'progress.json', dict(state='running', completed=item['serial'],
                                                           total=len(plan), current=item))
        started = time.perf_counter_ns()
        os.sync()
        item['precondition_sync_ns'] = time.perf_counter_ns()-started
        result = subprocess.run([sys.executable, __file__, '--output', str(args.output), '--one', json.dumps(item)])
        if result.returncode:
            bench.base.atomic(args.output/'progress.json', dict(state='failed', completed=item['serial'],
                                                               total=len(plan), current=item))
            raise SystemExit(result.returncode)
    bench.base.atomic(args.output/'progress.json', dict(state='complete', completed=len(plan), total=len(plan)))


if __name__ == '__main__':
    main()
