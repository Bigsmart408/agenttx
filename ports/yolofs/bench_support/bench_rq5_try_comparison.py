#!/usr/bin/env python3
"""Compare storage publication with upstream try on the same Ext4 guest.

Uses the original YoloFS Rust workloads through bench_rq5_storage_primitives.
All snapshots must publish immutable history that is ready to mount, while
the current view remains writable. AgentTX's deferred view preparation is
included and also reported separately. There is no try snapshot emulation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

import bench_rq5_storage_primitives as base


VARIANTS = ('agenttx', 'overlayfs', 'yolofs', 'try-native', 'try-cli')
SNAPSHOT_VARIANTS = VARIANTS[:3]
SHARE = Path('/hostshare/try-comparison-20261115')
GUEST = Path(os.environ.get('AGENTTX_RQ5_TRY_ROOT', '/var/lib/atx5-try-comparison-20261115'))
RUNTIME = Path('/opt/rq5/agenttx-storage-optimized-v3')
UPSTREAM_TRY = Path('/opt/rq5/try-comparison-20261115/try')


class Storage(base.Storage):
    def __init__(self, root, variant, case):
        if variant in ('agenttx', 'yolofs'):
            super().__init__(root, 'agenttx-after' if variant == 'agenttx' else variant, case)
            self.variant = variant
            self.sandbox = self.pool.sandbox_dir if self.pool else None
        else:
            self.root, self.variant, self.case = root, variant, case
            self.base, self.view = root/'b', root/'m'
            self.data = self.base/'data'
            self.data.mkdir(parents=True)
            self.layers, self.generations, self.snapshot_rows = [], [], []
            self.pool, self.active = None, False
            self.sandbox = root/'a'
            self.upper = self.sandbox/'upperdir'
            self.data_upper = self.upper/str(self.data).lstrip('/')
            if case['kind'] == 'commit' and case['operation'] != 'create':
                base.rust_workload('write-files', self.data, case['files'])
            # Same lower, absolute-path upper layout, writable view and mount
            # options as AgentTX. Only the publication implementation differs.
            base.mount([self.data], self.view, self.data_upper, root/'w')
            self.dest, self.active = self.view, True

    def snapshot(self, index):
        if self.variant not in SNAPSHOT_VARIANTS:
            raise RuntimeError('upstream try has no native snapshot API')
        started = time.perf_counter_ns()
        prepare_ns = 0
        if self.pool:
            names = [f'seed-{i:06}.dat' for i in range(10)]
            if index == 1:
                names += [f'target-{i:06}.dat' for i in range(100)]
            self.pool.layers.snapshot_before(index, self.upper,
                changed_paths=[str(self.data/name) for name in names])
            captured = time.perf_counter_ns()
            # Match completion semantics: an immutable history directory is
            # directly mountable on return, with no deferred tree construction.
            self.pool.layers.resolve_before(index)
            prepare_ns = time.perf_counter_ns()-captured
        elif self.variant == 'overlayfs':
            next_upper, next_work = self.sandbox/f'upper-{index}', self.root/f'w{index}'
            next_data = next_upper/str(self.data).lstrip('/')
            next_data.mkdir(parents=True)
            next_work.mkdir()
            base.command(['umount', self.view])
            self.layers.append(self.upper)
            self.upper = next_upper
            lowers = [layer/str(self.data).lstrip('/') for layer in reversed(self.layers)]
            base.mount([*lowers, self.data], self.view, next_data, next_work)
        else:
            result = base.command(['yolo', 'snapshot', f'rq5-{index}'], cwd=self.base)
            match = re.search(r'snapshot (\d+)', result.stdout+result.stderr)
            if not match:
                raise RuntimeError('missing YoloFS snapshot generation')
            self.generations.append(int(match[1]))
        elapsed = time.perf_counter_ns()-started
        self.snapshot_rows.append(dict(index=index, snapshot_ns=elapsed,
            capture_ns=elapsed-prepare_ns, history_prepare_ns=prepare_ns))
        return elapsed

    def check_history(self, canary=False):
        if self.case['kind'] != 'snapshot':
            return []
        rows = []
        depth = self.case['depth']
        for index in sorted({1, max(1, depth//2), depth}):
            view = self.root/'history-view'
            if self.pool:
                before = self.pool.layers.resolve_before(index)
                lowers = [before/str(self.data).lstrip('/'), self.data]
            elif self.variant == 'overlayfs':
                lowers = [layer/str(self.data).lstrip('/') for layer in reversed(self.layers[:index])]
                lowers.append(self.data)
            else:
                base.command(['yolo', 'travel', str(self.generations[index-1])], cwd=self.base)
                if canary:
                    check_distinct(self.dest, index)
                    checked = dict(distinct_generation=index)
                else:
                    checked = self.validate(self.dest)
                rows.append(dict(index=index, **checked))
                continue
            base.mount(lowers, view)
            try:
                if canary:
                    check_distinct(view, index)
                    checked = dict(distinct_generation=index)
                else:
                    checked = self.validate(view)
                rows.append(dict(index=index, **checked))
            finally:
                base.command(['umount', view])
        if self.variant == 'yolofs':
            base.command(['yolo', 'travel', str(self.generations[-1])], cwd=self.base)
        return rows

    def commit(self):
        if self.variant != 'yolofs':
            base.command(['umount', self.view])
            self.active = False
        if self.variant == 'overlayfs':
            spec = self.root/'commit-layers.json'
            spec.write_text(json.dumps([(str(layer/str(self.data).lstrip('/')), str(self.data))
                for layer in [*self.layers, self.upper]]))
        started = time.perf_counter_ns()
        if self.pool:
            # This sandbox contains only the workload data. The runtime's
            # optional paths argument denotes exact objects, not a recursive
            # directory selector; publish the entire prepared upper here.
            result = self.pool.commit()
            if result.returncode:
                raise RuntimeError(result.stderr+result.stdout)
        elif self.variant == 'overlayfs':
            # Include native-tool invocation overhead, as for the try/YoloFS
            # helpers; do not substitute its shorter internal Rust timer.
            base.command([base.PERF], env=dict(os.environ,
                AGENTTX_RQ5_COMMIT_LAYERS=str(spec)))
        elif self.variant == 'try-native':
            with tempfile.TemporaryDirectory(prefix='.storage-commit-', dir=self.sandbox) as directory:
                filters = Path(directory)
                exclude, include = filters/'exclude', filters/'include'
                exclude.write_text('/run/mount\n/run/try_trace.log\n')
                include.write_text('^'+str(self.data)+'(/|$)\n')
                base.command(['try-commit', '-E', exclude, '-I', include, self.sandbox])
        elif self.variant == 'try-cli':
            # The upstream CLI runs its own setup/dummy-command path before
            # invoking the same helper. Keep this whole API cost visible.
            base.command([UPSTREAM_TRY, '-y', '-I', '^'+str(self.data)+'(/|$)',
                'commit', self.sandbox], cwd=self.data,
                env=dict(os.environ, PWD=str(self.data)))
        else:
            base.command(['yolo', 'commit'], cwd=self.base)
        return time.perf_counter_ns()-started


def check_distinct(view, generation):
    expected = {f'target-{i:06}.dat': b'\0'*4096 for i in range(100)}
    expected.update({f'seed-{i:06}.dat': bytes([generation])*4096 for i in range(10)})
    assert {p.name for p in view.iterdir()} == set(expected)
    for name, content in expected.items():
        assert (view/name).read_bytes() == content, (name, generation)


def run_one(item, output):
    root = GUEST/f"s{item['serial']:06}"
    record = dict(**item, started_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    storage = None
    try:
        storage = Storage(root, item['variant'], item['case'])
        if item['phase'] == 'history-canary':
            for i in range(100):
                (storage.dest/f'target-{i:06}.dat').write_bytes(b'\0'*4096)
            for generation in range(1, 4):
                for i in range(10):
                    (storage.dest/f'seed-{i:06}.dat').write_bytes(bytes([generation])*4096)
                storage.snapshot(generation)
            # A later write must not change any previously captured generation.
            for i in range(10):
                (storage.dest/f'seed-{i:06}.dat').write_bytes(b'\xfe'*4096)
            record['history_checks'] = storage.check_history(canary=True)
        else:
            record['operation_and_snapshots_ns'] = storage.execute()
            record['live_validation'] = storage.validate(storage.dest)
            record['history_checks'] = storage.check_history()
            record['commit_ns'] = storage.commit()
            if hasattr(storage, 'seal_ns'):
                record['seal_ns'] = storage.seal_ns
            record['commit_validation'] = storage.validate(storage.data)
        record.update(snapshots=storage.snapshot_rows,
            snapshot_total_ns=sum(row['snapshot_ns'] for row in storage.snapshot_rows),
            history_prepare_total_ns=sum(row['history_prepare_ns'] for row in storage.snapshot_rows),
            run_pipeline_calls=0, status='passed')
        storage.close(destroy=True)
    except BaseException:
        record.update(status='failed', error=traceback.format_exc())
        if storage:
            try:
                storage.close(destroy=False)
            except Exception:
                record['cleanup_error'] = traceback.format_exc()
    base.atomic(output/'sessions'/f"{item['serial']:06}.json", record)
    print(json.dumps({key: record.get(key) for key in
        ('serial', 'phase', 'variant', 'case', 'status', 'commit_ns', 'snapshot_total_ns', 'error')}), flush=True)
    return record['status'] == 'passed'


def main(*, runner=None, mode=None):
    runner = Path(runner or __file__)
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=SHARE/'results-v1')
    parser.add_argument('--one')
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    os.umask(0o022)
    sys.path.insert(0, str(RUNTIME/'src'))
    if args.one:
        raise SystemExit(0 if run_one(json.loads(args.one), args.output) else 1)
    assert os.geteuid() == 0
    assert not args.output.exists(), 'refusing to overwrite measurements'
    assert not GUEST.exists(), 'refusing to reuse session roots'
    assert args.repeats >= 1
    args.output.mkdir(parents=True)
    plan = []
    def add(phase, variant, case, repeat=0):
        plan.append(dict(serial=len(plan), phase=phase, variant=variant, case=case, repeat=repeat))
    for variant in SNAPSHOT_VARIANTS:
        add('history-canary', variant, dict(kind='snapshot', depth=3))
    for operation in base.WORKLOADS:
        for variant in VARIANTS:
            add('commit-canary', variant, dict(kind='commit', operation=operation, files=32))
    cases = [dict(kind='commit', operation=op, files=10000) for op in base.WORKLOADS]
    cases += [dict(kind='commit', operation='create', files=100000)]
    cases += [dict(kind='snapshot', depth=depth) for depth in (1, 10, 30)]
    randomizer = random.Random(20261115)
    for case in cases:
        variants = list(SNAPSHOT_VARIANTS if case['kind'] == 'snapshot' else VARIANTS)
        for repeat in range(-1, args.repeats):
            randomizer.shuffle(variants)
            for variant in variants:
                add('warmup' if repeat == -1 else 'measured', variant, case, repeat)
    base.atomic(args.output/'manifest.json', dict(plan=plan,
        agenttx_mode=mode or 'selective-recovery-storage',
        try_source='833624c956a4f1a990eb3363dafc88b0dc347c16',
        helper_sha256=hashlib.sha256(Path(shutil.which('try-commit')).read_bytes()).hexdigest(),
        perf_sha256=hashlib.sha256(base.PERF.read_bytes()).hexdigest(),
        script_sha256=hashlib.sha256(runner.read_bytes()).hexdigest(),
        storage_snapshot_completion='Immutable history ready to mount; current view writable. AgentTX view preparation INCLUDED.',
        common_settings=dict(filesize=4096, commit_files=[10000, 100000],
            snapshot_targets=100, snapshot_seeds=10, snapshot_depths=[1, 10, 30],
            repeats=args.repeats, warmups_per_configuration=1, umask='022',
            file_operations='Unmodified upstream Rust workloads',
            overlay_mount_options='Same lower view/absolute-path upper layout/default kernel options',
            commit_scope='All files under the workload data directory',
            timers='Outer wall clock for every storage API, including helper invocation',
            durability='Buffered filesystem state; no added fsync'),
        try_snapshot='Not supported; omitted rather than emulated or reported as zero.',
        excluded='Agent/LLM, fault localization, dependency/context tracking, workload execution, setup, result verification.',
        formal_campaign_paused=True))
    for item in plan:
        base.atomic(args.output/'progress.json', dict(state='running', completed=item['serial'],
            total=len(plan), current=item))
        result = subprocess.run([sys.executable, str(runner), '--output', str(args.output),
            '--one', json.dumps(item)])
        if result.returncode:
            base.atomic(args.output/'progress.json', dict(state='failed', completed=item['serial'],
                total=len(plan), current=item))
            raise SystemExit(result.returncode)
    base.atomic(args.output/'progress.json', dict(state='complete', completed=len(plan), total=len(plan)))


if __name__ == '__main__':
    main()
