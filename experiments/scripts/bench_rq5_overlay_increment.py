#!/usr/bin/env python3
"""Measure snapshot-mode overhead over the same OverlayFS primitives."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time


RUNTIME = Path(os.environ['AGENTTX_OVERLAY_RUNTIME'])
BEFORE = Path('/opt/rq5/agenttx-snapshot-only-v7')
ROOT = Path(os.environ['AGENTTX_OVERLAY_BENCH_ROOT'])
sys.path.insert(0, str(RUNTIME/'src'))
from agenttx import snapshot as primitive
import bench_rq5_storage_fairness as fairness

spec = importlib.util.spec_from_file_location('snapshot_before_optimization', BEFORE/'src/agenttx/snapshot.py')
before = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = before
spec.loader.exec_module(before)


class CompactOverlay(fairness.OverlayOnly):
    """Same flat-layer operations and lifetime, without AgentTX records."""
    def __init__(self, workspace, state_dir, policy):
        super().__init__(workspace, state_dir, policy)
        self._visible_layers = []

    def snapshot(self):
        generation = len(self._layers)+1
        visible = primitive._compact_lowers([
            (self.upperdir, self._upper, primitive._flat_names(self._upper)),
            *self._visible_layers])
        path, upper, work = self._new_layer(generation)
        primitive._readonly(self.view, True)
        self._pins.append(os.open(self.view, os.O_PATH | os.O_CLOEXEC))
        primitive._unmount(self.view, detach=True)
        self._mounted = False
        primitive._mount(self.view, [*(fd for _, fd, _ in visible), self._base], upper, work)
        self._mounted = True
        self._layers.append((self.upperdir, self._upper))
        self._visible_layers = visible
        self.upperdir, self._upper, self._work = path, upper, work
        return generation

    def commit(self):
        self.seal()
        visible = (primitive._compact_lowers([
            (self.upperdir, self._upper, primitive._flat_names(self._upper)),
            *self._visible_layers]) if self._visible_layers else
            [(self.upperdir, self._upper, None)])
        whiteouts = set()
        for _, fd, _ in reversed(visible):
            primitive._merge(fd, self._base, whiteouts)


class Storage(fairness.Storage):
    def __init__(self, root, variant, case):
        if variant != 'agenttx-before':
            super().__init__(root, variant, case)
            return
        self.tag, self.variant, self.case, self.root = variant, 'agenttx', case, root
        self.base, self.bare, self.pool = root/'b', None, None
        self.data = self.base/'data'
        self.data.mkdir(parents=True)
        if case['kind'] == 'commit' and case['operation'] != 'create':
            fairness.base.rust_workload('write-files', self.data, case['files'])
        self.sandbox = root/'a'
        self.session = before.SnapshotSession(self.data, self.sandbox)
        self.view = self.dest = self.session.view
        self.upper, self.active = self.session.upperdir, True
        self.layers, self.generations, self.snapshot_rows = [], [], []

    def snapshot(self, index):
        if self.tag not in ('agenttx', 'agenttx-before'):
            return super().snapshot(index)
        started = time.perf_counter_ns()
        point = self.session.snapshot()
        elapsed = time.perf_counter_ns()-started
        assert point.generation == index
        self.snapshot_rows.append(dict(index=index, snapshot_ns=elapsed,
                                      lower_count=len(point.lowerdirs)))
        return elapsed

    def finish(self):
        if self.tag != 'agenttx-before':
            return super().finish()
        started = time.perf_counter_ns()
        self.session.commit()
        self.session.close(destroy=True)
        self.active = False
        return time.perf_counter_ns()-started

    def discard(self):
        if self.tag == 'agenttx-before':
            self.session.close(destroy=True)
            self.active = False
        else:
            super().discard()


fairness.ROOT = ROOT
fairness.OverlayOnly = CompactOverlay
fairness.Storage = Storage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=10)
    parser.add_argument('--one')
    args = parser.parse_args()
    if args.one:
        raise SystemExit(0 if fairness.one(json.loads(args.one), args.output) else 1)
    assert os.geteuid() == 0 and args.repeats >= 2
    assert not args.output.exists() and not ROOT.exists()
    args.output.mkdir(parents=True)
    plan = []
    snapshots = ('agenttx-before', 'agenttx', 'overlay-compact-api')
    commits = (*snapshots, 'ext4')
    def add(phase, variant, case, repeat):
        plan.append(dict(serial=len(plan), phase=phase, variant=variant, case=case, repeat=repeat))
    for variant in snapshots:
        add('history-canary', variant, dict(kind='snapshot', depth=3), -2)
    for operation in fairness.base.WORKLOADS:
        for variant in commits:
            add('commit-canary', variant, dict(kind='commit', operation=operation, files=32), -2)
    cases = [dict(kind='snapshot', depth=depth) for depth in (1, 10, 30)]
    cases += [dict(kind='commit', operation=operation, files=10000)
              for operation in fairness.base.WORKLOADS]
    cases += [dict(kind='commit', operation='create', files=100000)]
    randomizer = random.Random(20261115)
    for case in cases:
        for repeat in range(-1, args.repeats):
            variants = list(snapshots if case['kind'] == 'snapshot' else commits)
            randomizer.shuffle(variants)
            for variant in variants:
                add('warmup' if repeat == -1 else 'measured', variant, case, repeat)
    files = [Path(__file__), Path(fairness.__file__), Path(fairness.base.__file__),
             Path(fairness.comparison.__file__), Path(fairness.snapshot_bench.__file__)]
    source_files = ['src/agenttx/snapshot.py', 'src/agenttx/snapshot_native.c',
                    'tests/test_snapshot_session.py']
    fairness.base.atomic(args.output/'manifest.json', dict(plan=plan,
        repeats=args.repeats, runtime=str(RUNTIME), baseline_runtime=str(BEFORE),
        scripts_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        runtime_sha256={name:hashlib.sha256((RUNTIME/name).read_bytes()).hexdigest() for name in source_files},
        before_sha256=hashlib.sha256((BEFORE/'src/agenttx/snapshot.py').read_bytes()).hexdigest(),
        common_settings=dict(filesize=4096, snapshot_targets=100, snapshot_seeds=10,
            snapshot_depths=[1,10,30], commit_files=[10000,100000],
            file_operations='Unmodified pinned Rust workload bodies',
            controls='Same in-process mount, metadata, compaction, merge and native-delete primitives.',
            overlay_options='index=off,metacopy=off,redirect_dir=off; volatile DISABLED for all.',
            layout='Optimized AgentTX and OverlayFS both use compact uN/wN directories.',
            finish='seal + publish + close + unmount + destroy staging for all systems.',
            validation='No history travel or file reads between operation sequence and timed finish.',
            durability='Normal OverlayFS mount; no additional fsync; all cleanup included.'),
        model_calls=0, yolofs_measured=False, formal_campaign_paused=True))
    for item in plan:
        fairness.base.atomic(args.output/'progress.json', dict(state='running', completed=item['serial'],
                              total=len(plan), current=item))
        result = subprocess.run([sys.executable, __file__, '--output', str(args.output),
                                 '--one', json.dumps(item)])
        if result.returncode:
            fairness.base.atomic(args.output/'progress.json', dict(state='failed', completed=item['serial'],
                                  total=len(plan), current=item))
            raise SystemExit(result.returncode)
    fairness.base.atomic(args.output/'progress.json', dict(state='complete', completed=len(plan), total=len(plan)))


if __name__ == '__main__':
    main()
