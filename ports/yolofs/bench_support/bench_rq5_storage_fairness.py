#!/usr/bin/env python3
"""Audit RQ5 interface, layer-lifetime and end-to-end timing asymmetries."""
from __future__ import annotations

import argparse
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import struct
import subprocess
import sys
import time
import traceback

import bench_rq5_storage_primitives as base
import bench_rq5_try_comparison as comparison
import bench_rq5_snapshot_only as snapshot_bench


ROOT = Path('/var/lib/atx5-fairness-20261115')
RUNTIME = Path('/opt/rq5/agenttx-snapshot-only-v7')
SNAPSHOT_VARIANTS = ('agenttx', 'overlay-retained-api', 'overlay-eager-api',
                     'overlay-eager-cli', 'yolofs-api', 'yolofs-cli')
COMMIT_VARIANTS = ('agenttx', 'overlay-retained-api', 'yolofs-cli', 'ext4')
sys.path.insert(0, str(RUNTIME/'src'))
from agenttx import snapshot as primitive


class OverlayOnly:
    """Bare layer rollover, sharing the same mount/merge primitives as AgentTX.

    There is no snapshot journal or AgentTX generation record. The retained
    policy matches AgentTX's layer lifetime; eager variants expose the effect
    of synchronous superblock destruction and of shell command invocation.
    """
    _open = primitive.SnapshotSession._open
    _new_layer = primitive.SnapshotSession._new_layer
    seal = primitive.SnapshotSession.seal
    close = primitive.SnapshotSession.close

    def __init__(self, workspace, state_dir, policy):
        self.workspace, self.state_dir = workspace.resolve(), state_dir.resolve()
        self.policy = policy
        state_dir.mkdir()
        self.view = state_dir/'view'
        self.view.mkdir()
        self._fds, self._layers, self._pins = [], [], []
        self._mounted, self._closed = False, False
        self._base = self._open(self.workspace)
        self.upperdir, self._upper, self._work = self._new_layer(0)
        primitive._mount(self.view, [self._base], self._upper, self._work)
        self._mounted = True

    def snapshot(self):
        generation = len(self._layers)+1
        path, upper, work = self._new_layer(generation)
        if self.policy == 'overlay-retained-api':
            primitive._readonly(self.view, True)
            self._pins.append(os.open(self.view, os.O_PATH | os.O_CLOEXEC))
            primitive._unmount(self.view, detach=True)
        elif self.policy == 'overlay-eager-api':
            primitive._unmount(self.view)
        else:
            base.command(['umount', self.view])
        self._mounted = False
        self._layers.append((self.upperdir, self._upper))
        if self.policy == 'overlay-eager-cli':
            base.mount([*(path for path, _ in reversed(self._layers)), self.workspace],
                       self.view, path, self.state_dir/f'w{generation}')
        else:
            primitive._mount(self.view, [*(fd for _, fd in reversed(self._layers)),
                                        self._base], upper, work)
        self.upperdir, self._upper, self._work = path, upper, work
        self._mounted = True
        return generation

    def commit(self):
        self.seal()
        whiteouts = set()
        for _, fd in [*self._layers, (self.upperdir, self._upper)]:
            primitive._merge(fd, self._base, whiteouts)


class Storage(snapshot_bench.Storage):
    def __init__(self, root, variant, case):
        self.tag, self.bare = variant, None
        if variant == 'agenttx' or variant.startswith('yolofs-'):
            super().__init__(root, 'yolofs' if variant.startswith('yolofs-') else variant, case)
            return
        self.root, self.variant, self.case = root, variant, case
        self.base = root/'b'
        self.data = self.base/'data'
        self.data.mkdir(parents=True)
        if case['kind'] == 'commit' and case['operation'] != 'create':
            base.rust_workload('write-files', self.data, case['files'])
        self.snapshot_rows, self.layers, self.generations = [], [], []
        self.pool, self.active = None, True
        if variant == 'ext4':
            self.view = self.dest = self.data
            self.sandbox = None
        else:
            self.sandbox = root/'a'
            self.bare = OverlayOnly(self.data, self.sandbox, variant)
            self.view = self.dest = self.bare.view

    def snapshot(self, index):
        if self.tag == 'yolofs-api':
            started = time.perf_counter_ns()
            name = ctypes.create_string_buffer(f'rq5-{index}'.encode())
            payload = bytearray(struct.pack('=QQHB5x', 0, ctypes.addressof(name),
                                            len(name.value), 0))
            assert len(payload) == 24
            fd = os.open(self.base/'.yolofs/mnt', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                fcntl.ioctl(fd, (3 << 30) | (24 << 16) | (ord('A') << 8) | 40,
                            payload, True)
            finally:
                os.close(fd)
            generation = struct.unpack_from('=Q', payload)[0]
            if not generation:
                raise RuntimeError('YoloFS ioctl returned no snapshot generation')
            self.generations.append(generation)
            elapsed = time.perf_counter_ns()-started
        elif self.bare:
            started = time.perf_counter_ns()
            generation = self.bare.snapshot()
            assert generation == index
            elapsed = time.perf_counter_ns()-started
        else:
            return super().snapshot(index)
        self.snapshot_rows.append(dict(index=index, snapshot_ns=elapsed))
        return elapsed

    def check_history(self, canary=False):
        if not self.bare:
            return super().check_history(canary)
        depth, rows = self.case['depth'], []
        for index in sorted({1, max(1, depth//2), depth}):
            target = self.root/'history-view'
            target.mkdir(exist_ok=True)
            primitive._mount(target, [*(fd for _, fd in reversed(self.bare._layers[:index])),
                                      self.bare._base])
            try:
                if canary:
                    comparison.check_distinct(target, index)
                    checked = dict(distinct_generation=index)
                else:
                    checked = self.validate(target)
                rows.append(dict(index=index, **checked))
            finally:
                primitive._unmount(target)
        return rows

    def finish(self):
        """Include seal, publication, unmount, close, and staging destruction."""
        started = time.perf_counter_ns()
        if self.tag == 'agenttx':
            self.session.commit()
            self.session.close(destroy=True)
        elif self.bare:
            self.bare.commit()
            self.bare.close(destroy=True)
        elif self.tag.startswith('yolofs-'):
            base.command(['yolo', 'commit'], cwd=self.base)
            base.command(['yolo', 'unmount'], cwd=self.base)
            shutil.rmtree(self.base/'.yolofs')
            (self.base/'yolofs.toml').unlink()
        elif self.tag == 'ext4':
            return 0  # Changes already published by the same workload syscalls.
        self.active = False
        return time.perf_counter_ns()-started

    def discard(self):
        if self.tag == 'agenttx':
            self.session.close(destroy=True)
        elif self.bare:
            self.bare.close(destroy=True)
        elif self.tag.startswith('yolofs-'):
            base.command(['yolo', 'unmount'], cwd=self.base)
        self.active = False


def one(item, output):
    root = ROOT/f"s{item['serial']:06}"
    record = dict(item)
    storage = None
    try:
        started = time.perf_counter_ns()
        storage = Storage(root, item['variant'], item['case'])
        record['setup_ns'] = time.perf_counter_ns()-started
        if item['phase'] == 'history-canary':
            for i in range(100):
                (storage.dest/f'target-{i:06}.dat').write_bytes(b'\0'*4096)
            for generation in range(1, 4):
                for i in range(10):
                    (storage.dest/f'seed-{i:06}.dat').write_bytes(bytes([generation])*4096)
                storage.snapshot(generation)
            for i in range(10):
                (storage.dest/f'seed-{i:06}.dat').write_bytes(b'\xfe'*4096)
            record['history_checks'] = storage.check_history(canary=True)
            storage.discard()
        else:
            record['operation_and_snapshots_ns'] = storage.execute()
            # Do not warm the mounted view or alter it through history travel
            # between the timed operation sequence and timed finalization.
            record['finish_ns'] = storage.finish()
            record['commit_validation'] = storage.validate(storage.data)
            record['active_workflow_ns'] = record['operation_and_snapshots_ns']+record['finish_ns']
            record['lifecycle_ns'] = record['setup_ns']+record['active_workflow_ns']
        record['snapshots'] = storage.snapshot_rows
        record['snapshot_total_ns'] = sum(row['snapshot_ns'] for row in storage.snapshot_rows)
        shutil.rmtree(root)
        record.update(status='passed', model_calls=0)
    except BaseException:
        record.update(status='failed', error=traceback.format_exc())
        if storage and storage.active:
            try:
                storage.discard()
            except BaseException:
                record['cleanup_error'] = traceback.format_exc()
    base.atomic(output/'sessions'/f"{item['serial']:06}.json", record)
    print(json.dumps({name: record.get(name) for name in
          ('serial', 'phase', 'variant', 'case', 'status', 'snapshot_total_ns', 'finish_ns', 'error')}), flush=True)
    return record['status'] == 'passed'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--one')
    args = parser.parse_args()
    os.umask(0o022)
    if args.one:
        raise SystemExit(0 if one(json.loads(args.one), args.output) else 1)
    assert os.geteuid() == 0
    assert not args.output.exists() and not ROOT.exists()
    assert args.repeats >= 1
    args.output.mkdir(parents=True)
    plan = []
    def add(phase, variant, case, repeat):
        plan.append(dict(serial=len(plan), phase=phase, variant=variant, case=case, repeat=repeat))
    for variant in SNAPSHOT_VARIANTS:
        add('history-canary', variant, dict(kind='snapshot', depth=3), -2)
    randomizer = random.Random(20261115)
    cases = [dict(kind='snapshot', depth=depth) for depth in (1, 10, 30)]
    cases += [dict(kind='commit', operation=operation, files=10000)
              for operation in ('create', 'unlink')]
    for case in cases:
        for repeat in range(-1, args.repeats):
            variants = list(SNAPSHOT_VARIANTS if case['kind'] == 'snapshot' else COMMIT_VARIANTS)
            randomizer.shuffle(variants)
            for variant in variants:
                add('warmup' if repeat == -1 else 'measured', variant, case, repeat)
    base.atomic(args.output/'manifest.json', dict(plan=plan,
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        runtime=str(RUNTIME), repetitions=args.repeats,
        snapshot_interfaces='All API variants include their in-process syscall/ioctl cost. CLI variants are diagnostic only.',
        overlay_retained='Same shared mount, metadata, merge and native-delete primitives as AgentTX; no snapshot journal.',
        finish_scope='seal + publish + close + unmount + destroy staging, for all systems; no pre-finish validation/travel.',
        ext4_scope='Direct same Rust file operations, no snapshot or independent commit stage.',
        counts='4 KiB files; 100 targets and 10 seeds for snapshots; 10k commit files.',
        history_validation='Separate distinct-content canaries; final state checked after finish in every timed session.',
        durability='Buffered filesystem state. No added fsync guarantee.',
        model_calls=0, formal_campaign_paused=True))
    for item in plan:
        base.atomic(args.output/'progress.json', dict(state='running', completed=item['serial'],
                    total=len(plan), current=item))
        run = subprocess.run([sys.executable, __file__, '--output', str(args.output),
                              '--one', json.dumps(item)])
        if run.returncode:
            base.atomic(args.output/'progress.json', dict(state='failed', completed=item['serial'],
                        total=len(plan), current=item))
            raise SystemExit(run.returncode)
    base.atomic(args.output/'progress.json', dict(state='complete', completed=len(plan), total=len(plan)))


if __name__ == '__main__':
    main()
