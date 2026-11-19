#!/usr/bin/env python3
"""RQ5 on the YoloFS port and the existing matched storage controls."""

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

PORT = Path(__file__).resolve().parents[1]
BASELINE = Path('/opt/rq5/agenttx-snapshot-only-v14')
sys.path.insert(0, str(BASELINE/'src'))
from agenttx import snapshot as primitive
sys.path.insert(0, str(PORT/'bench_support'))
sys.path.insert(0, str(PORT))
from agenttx_yolofs.storage import YoloSession
import bench_rq5_yolofs_followup as legacy

base = legacy.base
VARIANTS = ('agenttx-yolofs', 'yolofs-api', 'agenttx-metacopy', 'overlay-metacopy',
            'ext4', 'try-native', 'try-commit-cli', 'try-full')
SNAPSHOTS = VARIANTS[:4]


class Storage(legacy.current.Storage):
    def __init__(self, root, variant, case):
        self.native_ctl = None
        if variant != 'agenttx-yolofs':
            super().__init__(root, variant, case)
            if variant == 'yolofs-api':
                self.native_ctl = os.open(self.base/'.yolofs/mnt',
                                          os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            return
        self.root, self.variant, self.tag, self.case = root, variant, variant, case
        self.base = root/'b'
        self.data = self.base/'data'
        self.data.mkdir(parents=True)
        if case['kind'] == 'commit' and case['operation'] != 'create':
            base.rust_workload('write-files', self.data, case['files'])
        self.session = YoloSession(self.data, root/'a')
        self.view = self.dest = self.session.view
        self.snapshot_rows, self.layers, self.generations = [], [], []
        self.pool, self.bare, self.active = None, None, True

    def snapshot(self, index):
        if self.tag not in ('agenttx-yolofs', 'yolofs-api'):
            return super().snapshot(index)
        started = time.perf_counter_ns()
        if self.tag == 'agenttx-yolofs':
            point = self.session.snapshot(f'rq5-{index}')
            generation = point.generation
        else:
            # Independent native control, using the same persistent ctl-fd
            # lifetime and the same snapshot ioctl as the port.
            name = ctypes.create_string_buffer(f'rq5-{index}'.encode())
            payload = bytearray(struct.pack('=QQHB5x', 0, ctypes.addressof(name),
                                            len(name.value), 0))
            fcntl.ioctl(self.native_ctl, (3 << 30) | (24 << 16) | (ord('A') << 8) | 40,
                        payload, True)
            generation = struct.unpack_from('=Q', payload)[0]
        elapsed = time.perf_counter_ns()-started
        assert generation > 0
        self.generations.append(generation)
        self.snapshot_rows.append(dict(index=index, snapshot_ns=elapsed, generation=generation))
        return elapsed

    def finish(self):
        if self.tag != 'agenttx-yolofs':
            if self.native_ctl is not None:
                os.close(self.native_ctl)
                self.native_ctl = None
            return super().finish()
        started = time.perf_counter_ns()
        self.session.commit()
        self.session.close(destroy=True)
        self.active = False
        return time.perf_counter_ns()-started

    def discard(self):
        if self.tag != 'agenttx-yolofs':
            if self.native_ctl is not None:
                os.close(self.native_ctl)
                self.native_ctl = None
            return super().discard()
        self.session.close(destroy=True)
        self.active = False

    def check_history(self):
        if self.tag not in ('agenttx-yolofs', 'yolofs-api'):
            rows = []
            for index in (1, 2, 3):
                target = self.root/'history-view'
                if self.tag == 'agenttx-metacopy':
                    self.session.mount_snapshot(self.session.snapshots[index-1],target)
                else:
                    target.mkdir(exist_ok=True)
                    primitive._mount(target,[*(fd for _,fd in reversed(self.bare._layers[:index])),
                                               self.bare._base],metadata_copy=True)
                try:
                    legacy.fairness.comparison.check_distinct(target,index)
                    rows.append(dict(generation=index,distinct_seed_byte=index,validated_files=110))
                finally:
                    primitive._unmount(target)
            return rows
        rows = []
        for index, generation in enumerate(self.generations, 1):
            if self.tag == 'agenttx-yolofs':
                self.session.travel(generation)
            else:
                base.command(['yolo', 'travel', generation], cwd=self.base)
            legacy.fairness.comparison.check_distinct(self.dest, index)
            rows.append(dict(generation=generation, distinct_seed_byte=index, validated_files=110))
        return rows


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def mount_evidence(storage):
    dest = storage.dest.resolve()
    matches = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        mounted = Path(line.split()[4])
        if mounted == dest or mounted in dest.parents:
            matches.append((len(str(mounted)), line))
    mount = max(matches)[1]
    if storage.tag in ('agenttx-yolofs', 'yolofs-api'):
        assert ' - yolofs ' in mount, mount
    elif storage.tag in ('agenttx-metacopy', 'overlay-metacopy'):
        assert ' - overlay ' in mount and 'metacopy=on' in mount and 'redirect_dir=on' in mount, mount
    elif storage.tag in ('ext4', 'try-full'):
        assert ' - ext4 ' in mount, mount
    return dict(destination=str(dest), mountinfo=mount)


def one(item, output, experiment_root):
    root = experiment_root/f"s{item['serial']:06}"
    storage = None
    row = dict(item, model_calls=0)
    try:
        started = time.perf_counter_ns()
        storage = Storage(root, item['variant'], item['case'])
        row['setup_ns'] = time.perf_counter_ns()-started
        row['mount'] = mount_evidence(storage)
        if item['phase'] == 'history-canary':
            for i in range(100):
                (storage.dest/f'target-{i:06}.dat').write_bytes(b'\0'*4096)
            for generation in (1, 2, 3):
                for i in range(10):
                    (storage.dest/f'seed-{i:06}.dat').write_bytes(bytes([generation])*4096)
                storage.snapshot(generation)
            for i in range(10):
                (storage.dest/f'seed-{i:06}.dat').write_bytes(b'\xfe'*4096)
            row['history_checks'] = storage.check_history()
            assert len(row['history_checks']) == 3
            storage.discard()
        else:
            row['operation_and_snapshots_ns'] = storage.execute()
            expected = item['case'].get('depth', 0) if item['case']['kind'] == 'snapshot' else 0
            assert len(storage.snapshot_rows) == expected
            row['finish_ns'] = storage.finish()
            row['commit_validation'] = storage.validate(storage.data)
            row['active_workflow_ns'] = row['operation_and_snapshots_ns'] + row['finish_ns']
            row['lifecycle_ns'] = row['setup_ns'] + row['active_workflow_ns']
        row['snapshots'] = storage.snapshot_rows
        row['snapshot_total_ns'] = sum(point['snapshot_ns'] for point in storage.snapshot_rows)
        row['status'] = 'passed'
        shutil.rmtree(root)
    except BaseException:
        row.update(status='failed', error=traceback.format_exc())
        if storage and storage.active:
            try:
                storage.discard()
            except BaseException:
                row['cleanup_error'] = traceback.format_exc()
    base.atomic(output/'sessions'/f"{item['serial']:06}.json", row)
    print(json.dumps({key:row.get(key) for key in
        ('serial','phase','variant','case','status','lifecycle_ns','error')}), flush=True)
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--repeats', default=10, type=int)
    parser.add_argument('--smoke', action='store_true')
    args = parser.parse_args()
    assert os.geteuid() == 0 and args.repeats >= 1
    assert Path(primitive.__file__).is_relative_to(BASELINE)
    assert not args.output.exists() and not args.root.exists()
    args.output.mkdir(parents=True)
    args.root.mkdir(parents=True)
    plan = []
    def add(phase, variant, case, repeat):
        plan.append(dict(serial=len(plan), phase=phase, variant=variant, case=case, repeat=repeat))
    for variant in SNAPSHOTS:
        add('history-canary',variant,dict(kind='snapshot',depth=3),-2)
    for operation in base.WORKLOADS:
        for variant in VARIANTS:
            add('commit-canary',variant,dict(kind='commit',operation=operation,files=32),-2)
    rng = random.Random(2026111517)
    if not args.smoke:
        cases = [dict(kind='snapshot',depth=depth) for depth in (1,10,30)]
        cases += [dict(kind='commit',operation=op,files=10000) for op in base.WORKLOADS]
        for case in cases:
            variants = SNAPSHOTS if case['kind']=='snapshot' else VARIANTS
            for repeat in range(-1,args.repeats):
                blocks = [list(SNAPSHOTS[:2]),list(SNAPSHOTS[2:])]
                blocks += [[variant] for variant in variants[4:]]
                for block in blocks:
                    rng.shuffle(block)
                rng.shuffle(blocks)
                for block in blocks:
                    for variant in block:
                        add('warmup' if repeat==-1 else 'measured',variant,case,repeat)
    source_files = [path for root in (PORT/'agenttx_yolofs',PORT/'bench_support',PORT/'scripts')
                    for path in root.rglob('*.py')]
    hashes = {str(path):sha(path) for path in source_files}
    manifest = dict(plan=plan, pid=os.getpid(), repeats=args.repeats,
        source_sha256=hashes, baseline=str(BASELINE), baseline_sha256=sha(primitive.__file__),
        baseline_native_sha256=sha(BASELINE/'src/agenttx/_snapshot_native.so'),
        workload_binary_sha256=sha(base.PERF), yolo_binary_sha256=sha('/usr/local/bin/yolo'),
        kernel=os.uname().release, model_calls=0,
        protocol=dict(workloads='Unchanged upstream Rust exec-workload bodies.',
            commit_files=10000, file_bytes=4096, warmups=1, all_samples_retained=True,
            snapshot_depths=[1,10,30], snapshot_workload='100 targets; overwrite 10 seeds per generation.',
            snapshot_api='In-process ioctl; native and port both hold one persistent ctl fd.',
            yolo='permission=true,staging=true,auto_snapshot=false; root preallowed.',
            overlay='metacopy=on,redirect_dir=on; matched original v14 primitives.',
            try_controls='Original C publisher, commit CLI and full entry; original non-metacopy settings.',
            mode='Storage primitives: no model, fault localization, dependency tracing or per-command history capture.',
            history='Validate all three distinct nonzero histories before measurement; no travel inside timed sequences.',
            total='setup (including initial files) + workload and explicit snapshots + publication and session cleanup; validation excluded.'))
    base.atomic(args.output/'manifest.json',manifest)
    passed = 0
    for item in plan:
        row = one(item,args.output,args.root)
        passed += row['status']=='passed'
        base.atomic(args.output/'progress.json',dict(pid=os.getpid(),completed=item['serial']+1,
            total=len(plan),passed=passed,failed=item['serial']+1-passed,latest=row['serial']))
        if row['status']!='passed':
            raise RuntimeError(f"session {row['serial']} failed; artifacts retained")
    assert all(sha(path)==digest for path,digest in hashes.items()), 'source changed during measurement'
    args.root.rmdir()
    base.atomic(args.output/'complete.json',dict(total=len(plan),passed=passed,failed=len(plan)-passed,
        all_source_hashes_unchanged=True,model_calls=0))
    print(json.dumps(dict(complete=True,total=len(plan),passed=passed)),flush=True)


if __name__ == '__main__':
    main()
