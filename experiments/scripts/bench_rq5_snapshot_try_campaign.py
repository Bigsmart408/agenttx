#!/usr/bin/env python3
"""Measure current storage commits and explicit snapshots, including upstream try."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time

RUNTIME = Path('/opt/rq5/agenttx-snapshot-only-v14')
ROOT = Path('/var/lib/agenttx-snapshot-try-campaign-20261115')
COMMIT_VARIANTS = ('agenttx-metacopy', 'overlay-metacopy', 'yolofs-api', 'ext4',
                   'try-native', 'try-commit-cli', 'try-full')
SNAPSHOT_VARIANTS = COMMIT_VARIANTS[:3]
REPEATS = 10


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
        def audit_mount(self):
            mounts = []
            for line in Path('/proc/self/mountinfo').read_text().splitlines():
                mountpoint = Path(line.split()[4])
                if self.view == mountpoint or mountpoint in self.view.parents:
                    mounts.append((len(str(mountpoint)), line))
            mount_info = max(mounts)[1]
            if self.tag.endswith('metacopy'):
                assert ' - overlay ' in mount_info
                assert 'metacopy=on' in mount_info and 'redirect_dir=on' in mount_info
            bench.base.atomic(args.output/'mount-evidence'/f'{self.root.name}.json',
                dict(variant=self.tag, view=str(self.view), mount_info=mount_info))

        def execute(self):
            # Inspection is outside the inherited operation/snapshot timer.
            self.audit_mount()
            elapsed = super().execute()
            expected = self.case.get('depth', 0) if self.case['kind'] == 'snapshot' else 0
            assert len(self.snapshot_rows) == expected
            assert [r['index'] for r in self.snapshot_rows] == list(range(1, expected+1))
            return elapsed

    bench.Storage = AuditedStorage
    if args.one:
        item = json.loads(args.one)
        if item['phase'] == 'history-canary':
            bench.fairness.ROOT = ROOT
            bench.fairness.Storage = AuditedStorage
            ok = bench.fairness.one(item, args.output)
        else:
            ok = bench.one(item, args.output)
        raise SystemExit(0 if ok else 1)

    assert not args.output.exists() and not ROOT.exists()
    args.output.mkdir(parents=True)
    plan = []

    def add(phase, variant, case, repeat, order=None):
        plan.append(dict(serial=len(plan), phase=phase, variant=variant, case=case,
                         repeat=repeat, pair_order=order))

    for variant in SNAPSHOT_VARIANTS:
        add('history-canary', variant, dict(kind='snapshot', depth=3), -2)
    for operation in bench.base.WORKLOADS:
        for variant in COMMIT_VARIANTS:
            add('commit-canary', variant, dict(kind='commit', operation=operation, files=32), -2)
    cases = [dict(kind='snapshot', depth=depth) for depth in (1, 10, 30)]
    cases += [dict(kind='commit', operation=op, files=10000) for op in bench.base.WORKLOADS]
    rng = random.Random(2026111503)
    for case in cases:
        variants = SNAPSHOT_VARIANTS if case['kind'] == 'snapshot' else COMMIT_VARIANTS
        orders = ['agenttx-first']*(REPEATS//2)+['overlay-first']*(REPEATS//2)
        rng.shuffle(orders)
        for repeat in range(-1, REPEATS):
            order = orders[repeat] if repeat >= 0 else 'agenttx-first'
            pair = list(SNAPSHOT_VARIANTS[:2])
            if order == 'overlay-first':
                pair.reverse()
            blocks = [pair, *([variant] for variant in variants[2:])]
            rng.shuffle(blocks)
            for block in blocks:
                for variant in block:
                    add('warmup' if repeat == -1 else 'measured', variant, case, repeat, order)
    files = [Path(__file__), Path(adapters.__file__), Path(bench.__file__),
             Path(bench.fairness.__file__), Path(bench.base.__file__),
             Path(bench.fairness.snapshot_bench.__file__), Path(bench.fairness.comparison.__file__)]
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    bench.base.atomic(args.output/'manifest.json', dict(
        plan=plan, runtime=str(RUNTIME), runtime_sha256=sha(Path(snapshot.__file__)),
        native_sha256=sha(RUNTIME/'src/agenttx/_snapshot_native.so'),
        script_sha256={path.name: sha(path) for path in files},
        workload_binary_sha256=sha(bench.base.PERF),
        yolo_binary_sha256=sha(Path('/usr/local/bin/yolo')),
        try_binary_sha256=sha(bench.TRY),
        try_commit_sha256=sha(Path(shutil.which('try-commit'))),
        model_calls=0, formal_campaign_paused=True,
        settings=dict(files=10000, filesize=4096, warmups=1, repeats=REPEATS,
            workloads='Unchanged upstream Rust write/overwrite/rename/unlink and checkpoint-scaling.',
            backends='Current own storage adapters; no upstream Backend::run_one substitution.',
            snapshots='100 targets and 10 seeds, 4 KiB each; overwrite 10 seeds before every explicit snapshot; depths 1,10,30.',
            snapshot_interfaces='In-process APIs for all three; YoloFS invokes its unmodified snapshot ioctl.',
            history_validation='Three distinct nonzero generations, later overwritten; inspect all three immutable histories before measured runs.',
            overlay='AgentTX and OverlayFS: same metacopy=on,redirect_dir=on and publication primitives.',
            yolo='Unmodified native module/CLI; permission=true, preallowed paths, auto_snapshot=false; explicit snapshots are still requested.',
            try_native='Original try C helper after ordinary OverlayFS operations and seal.',
            try_commit_cli='Original try commit CLI after ordinary OverlayFS operations and seal.',
            try_full='Original try -y executes the actual Rust workload using its own native mount configuration.',
            unsupported='Ext4 and upstream try have no snapshot API; not assigned zero snapshot latency.',
            lifecycle='Initial population/setup + operation and snapshots + all publication, unmount, reference release and staging destruction.',
            final_validation='Full final names, contents and modes after timing; no travel/validation inserted before timed finish.',
            ordering='Adjacent AgentTX/OverlayFS pair; five repetitions in each order per case; blocks shuffled.',
            sample_policy='Ten fixed measured repetitions per configuration; all samples retained.',
            precondition='sync before every session and before any timed setup/data generation.')))
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
