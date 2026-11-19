#!/usr/bin/env python3
"""Retest upstream rename on current storage with matched metacopy settings."""
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
ROOT = Path('/var/lib/agenttx-rename-metacopy-retest-20261115')
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

    class AuditedStorage(bench.Storage):
        def execute(self):
            # Capture active options outside the operation timer, equally for
            # every system; verify that metacopy actually reached the kernel.
            mounts = []
            for line in Path('/proc/self/mountinfo').read_text().splitlines():
                fields = line.split()
                mountpoint = Path(fields[4])
                if self.view == mountpoint or mountpoint in self.view.parents:
                    mounts.append((len(str(mountpoint)), line))
            self.mount_info = max(mounts)[1]
            if self.tag.endswith('metacopy'):
                assert ' - overlay ' in self.mount_info
                assert 'metacopy=on' in self.mount_info
                assert 'redirect_dir=on' in self.mount_info
            elif self.tag in ('agenttx', 'overlay'):
                assert ' - overlay ' in self.mount_info
                assert 'metacopy=on' not in self.mount_info
            evidence = dict(variant=self.tag, view=str(self.view), mount_info=self.mount_info)
            bench.base.atomic(args.output/'mount-evidence'/f'{self.root.name}.json', evidence)
            return super().execute()

    bench.Storage = AuditedStorage
    if args.one:
        raise SystemExit(0 if bench.one(json.loads(args.one), args.output) else 1)
    assert not args.output.exists() and not ROOT.exists()
    args.output.mkdir(parents=True)
    plan = []

    def add(phase, variant, count, repeat):
        plan.append(dict(serial=len(plan), phase=phase, variant=variant,
                         case=dict(kind='commit', operation='rename', files=count), repeat=repeat))

    for variant in VARIANTS:
        add('commit-canary', variant, 32, -2)
    rng = random.Random(2026111501)
    for repeat in range(-1, 5):
        order = list(VARIANTS)
        rng.shuffle(order)
        for variant in order:
            add('warmup' if repeat == -1 else 'measured', variant, 10000, repeat)
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
        formal_campaign_paused=True, settings=dict(
            files=10000, filesize=4096, operation='rename', warmups=1, repeats=5,
            workload='Unchanged upstream Rust rename-files; existing base files.',
            backends='Current own storage adapters, runtime v14.',
            overlay='Matched ordinary pair and metacopy pair; active mount options verified.',
            yolo='Unmodified native module and CLI; permission=true, auto_snapshot=false.',
            lifecycle='Initial population/setup + operation + publication, seal/unmount and staging destruction.',
            validation='Every output name, full content and mode checked after timing.',
            sample_policy='Fixed randomized order; all measured samples retained.',
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
