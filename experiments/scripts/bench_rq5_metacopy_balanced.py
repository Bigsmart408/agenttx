#!/usr/bin/env python3
"""Repeat the four filesystem workloads with balanced adjacent metacopy pairs."""
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
ROOT = Path('/var/lib/agenttx-metacopy-balanced-20261115')
VARIANTS = ('agenttx-metacopy', 'overlay-metacopy', 'ext4', 'yolofs-api')
REPEATS = 20


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

    class AuditedStorage(bench.Storage):
        def execute(self):
            # Inspect the active mount outside the existing operation timer.
            mounts = []
            for line in Path('/proc/self/mountinfo').read_text().splitlines():
                fields = line.split()
                mountpoint = Path(fields[4])
                if self.view == mountpoint or mountpoint in self.view.parents:
                    mounts.append((len(str(mountpoint)), line))
            mount_info = max(mounts)[1]
            if self.tag.endswith('metacopy'):
                assert ' - overlay ' in mount_info
                assert 'metacopy=on' in mount_info
                assert 'redirect_dir=on' in mount_info
            bench.base.atomic(args.output/'mount-evidence'/f'{self.root.name}.json',
                              dict(variant=self.tag, view=str(self.view), mount_info=mount_info))
            return super().execute()

    bench.Storage = AuditedStorage
    if args.one:
        raise SystemExit(0 if bench.one(json.loads(args.one), args.output) else 1)
    assert not args.output.exists() and not ROOT.exists()
    args.output.mkdir(parents=True)
    plan = []

    def add(phase, variant, operation, count, repeat, pair_order):
        plan.append(dict(serial=len(plan), phase=phase, variant=variant,
                         case=dict(kind='commit', operation=operation, files=count),
                         repeat=repeat, pair_order=pair_order))

    for operation in bench.base.WORKLOADS:
        for variant in VARIANTS:
            add('commit-canary', variant, operation, 32, -2, None)
    rng = random.Random(2026111502)
    for operation in bench.base.WORKLOADS:
        pair_orders = ['agenttx-first']*(REPEATS//2)+['overlay-first']*(REPEATS//2)
        rng.shuffle(pair_orders)
        for repeat in range(-1, REPEATS):
            order = pair_orders[repeat] if repeat >= 0 else 'agenttx-first'
            pair = list(VARIANTS[:2])
            if order == 'overlay-first':
                pair.reverse()
            blocks = [pair, ['ext4'], ['yolofs-api']]
            rng.shuffle(blocks)
            for block in blocks:
                for variant in block:
                    add('warmup' if repeat == -1 else 'measured', variant,
                        operation, 10000, repeat, order)
    files = [Path(__file__), Path(adapters.__file__), Path(bench.__file__),
             Path(bench.fairness.__file__), Path(bench.base.__file__),
             Path(bench.fairness.snapshot_bench.__file__),
             Path(bench.fairness.comparison.__file__)]
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    bench.base.atomic(args.output/'manifest.json', dict(
        plan=plan, runtime=str(RUNTIME), runtime_sha256=sha(Path(snapshot.__file__)),
        native_sha256=sha(RUNTIME/'src/agenttx/_snapshot_native.so'),
        script_sha256={path.name: sha(path) for path in files},
        workload_binary_sha256=sha(bench.base.PERF),
        yolo_binary_sha256=sha(Path('/usr/local/bin/yolo')), model_calls=0,
        formal_campaign_paused=True,
        settings=dict(files=10000, filesize=4096, warmups=1, repeats=REPEATS,
            workloads='Unchanged upstream Rust create, overwrite, rename and unlink.',
            backends='Current own storage adapters, runtime v14.',
            overlay='Same metacopy=on,redirect_dir=on mount and publication primitives.',
            yolo='Unmodified native module and CLI; permission=true, auto_snapshot=false.',
            lifecycle='Initial population/setup + operation + publication, seal/unmount and staging destruction.',
            validation='Every output name, full content and mode checked after timing.',
            ordering='Adjacent AgentTX/OverlayFS pair; ten rounds each order per workload; blocks shuffled.',
            sample_policy='Exactly 20 measured samples per configuration; all retained, no outcome-based stopping.',
            precondition='sync before every session and before timed setup/data generation.')))
    for item in plan:
        bench.base.atomic(args.output/'progress.json', dict(
            state='running', completed=item['serial'], total=len(plan), current=item))
        started = time.perf_counter_ns()
        os.sync()
        item['precondition_sync_ns'] = time.perf_counter_ns()-started
        result = subprocess.run([sys.executable, __file__, '--output', str(args.output),
                                 '--one', json.dumps(item)])
        if result.returncode:
            bench.base.atomic(args.output/'progress.json', dict(
                state='failed', completed=item['serial'], total=len(plan), current=item))
            raise SystemExit(result.returncode)
    bench.base.atomic(args.output/'progress.json', dict(
        state='complete', completed=len(plan), total=len(plan)))


if __name__ == '__main__':
    main()
