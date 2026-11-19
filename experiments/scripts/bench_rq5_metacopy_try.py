#!/usr/bin/env python3
"""Compare ordinary/metacopy OverlayFS and upstream try on the Ext4 guest."""
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
import time
import traceback

RUNTIME = Path('/opt/rq5/agenttx-snapshot-only-v11')
ROOT = Path('/var/lib/agenttx-metacopy-try-20261115')
TRY = Path('/opt/rq5/try-comparison-20261115/try')
sys.path.insert(0, str(RUNTIME/'src'))
from agenttx import snapshot as primitive
import bench_rq5_storage_fairness as fairness
base = fairness.base

VARIANTS = ('ext4', 'agenttx', 'overlay', 'agenttx-metacopy', 'overlay-metacopy',
            'try-native', 'try-commit-cli', 'try-full')
SNAPSHOT_VARIANTS = ('agenttx', 'overlay', 'agenttx-metacopy', 'overlay-metacopy')


class OverlaySession(fairness.OverlayOnly):
    _mount_view = primitive.SnapshotSession._mount_view
    _layer_names = primitive.SnapshotSession._layer_names
    seal = primitive.SnapshotSession.seal
    _seal_mounts = primitive.SnapshotSession._seal_mounts
    _materialize_metadata = primitive.SnapshotSession._materialize_metadata
    commit = primitive.SnapshotSession.commit

    def __init__(self, workspace, state_dir, metadata_copy):
        self.workspace, self.state_dir = workspace, state_dir
        self.metadata_copy = metadata_copy
        self._mounted, self._closed, self._publishing = False, False, False
        self._fds, self._layers, self._pins, self._visible_layers = [], [], [], []
        state_dir.mkdir()
        self.view = state_dir/'view'
        self.view.mkdir()
        self._base = self._open(workspace)
        self.upperdir, self._upper, self._work = self._new_layer(0)
        self._mount_view(self.view, [self._base], self._upper, self._work)
        self._mounted = True

    def snapshot(self):
        generation = len(self._layers)+1
        visible = primitive._compact_lowers([
            (self.upperdir, self._upper, self._layer_names(self._upper)),
            *self._visible_layers])
        path, upper, work = self._new_layer(generation)
        primitive._readonly(self.view, True)
        self._pins.append(os.open(self.view, os.O_PATH | os.O_CLOEXEC))
        primitive._unmount(self.view, detach=True)
        self._mounted = False
        self._mount_view(self.view, [*(fd for _, fd, _ in visible), self._base], upper, work)
        self._mounted = True
        self._layers.append((self.upperdir, self._upper))
        self._visible_layers = visible
        self.upperdir, self._upper, self._work = path, upper, work
        return generation

class Storage(fairness.Storage):
    def __init__(self, root, variant, case):
        self.root, self.tag, self.variant, self.case = root, variant, variant, case
        self.base, self.bare, self.pool = root/'b', None, None
        self.data = self.base/'data'
        self.data.mkdir(parents=True)
        if case['kind'] == 'commit' and case['operation'] != 'create':
            base.rust_workload('write-files', self.data, case['files'])
        self.snapshot_rows, self.layers, self.generations = [], [], []
        self.active = True
        if variant.startswith('agenttx'):
            self.session = primitive.SnapshotSession(self.data, root/'a',
                metadata_copy=variant.endswith('metacopy'))
            self.view = self.dest = self.session.view
        elif variant in ('ext4', 'try-full'):
            self.view = self.dest = self.data
        else:
            self.bare = OverlaySession(self.data, root/'a', variant.endswith('metacopy'))
            self.view = self.dest = self.bare.view
        if variant.startswith('try-'):
            self.try_sandbox = root/'t'
            self.try_sandbox.mkdir()
            if variant != 'try-full':
                self.try_upper = self.try_sandbox/'upperdir'/str(self.data).lstrip('/')
                self.try_upper.parent.mkdir(parents=True)
                self.exclude, self.include = root/'exclude', root/'include'
                self.exclude.write_text('/run/mount\n/run/try_trace.log\n')
                self.include.write_text('^'+re.escape(str(self.data))+'(/|$)\n')

    def snapshot(self, index):
        started = time.perf_counter_ns()
        generation = (self.session.snapshot().generation if self.tag.startswith('agenttx')
                      else self.bare.snapshot())
        assert generation == index
        elapsed = time.perf_counter_ns()-started
        self.snapshot_rows.append(dict(index=index, snapshot_ns=elapsed))
        return elapsed

    def execute(self):
        if self.tag != 'try-full':
            return super().execute()
        command = ([str(base.PERF), 'exec-workload', '--name', base.WORKLOADS[self.case['operation']],
                    '--dest', str(self.data)] if self.case['kind'] == 'commit' else ['true'])
        started = time.perf_counter_ns()
        base.command([TRY, '-N', self.try_sandbox, '-y', '-I', '^'+re.escape(str(self.data))+'(/|$)',
                      '--', *command], cwd=self.data,
                     env=dict(os.environ, PWD=str(self.data), AGENTTX_RQ5_FILE_COUNT=str(self.case.get('files',0))))
        return time.perf_counter_ns()-started

    def finish(self):
        started = time.perf_counter_ns()
        if self.tag.startswith('agenttx'):
            self.session.commit()
            self.session.close(destroy=True)
        elif self.bare:
            if self.tag in ('try-native', 'try-commit-cli'):
                self.bare.seal()
                # try's helper expects an absolute-path upper layout. Account
                # for the bridge in its measured publication cost.
                self.bare.upperdir.rename(self.try_upper)
                if self.tag == 'try-native':
                    base.command(['try-commit', '-E', self.exclude, '-I', self.include, self.try_sandbox])
                else:
                    base.command([TRY, '-y', '-I', '^'+re.escape(str(self.data))+'(/|$)',
                                  'commit', self.try_sandbox], cwd=self.data,
                                 env=dict(os.environ, PWD=str(self.data)))
                self.bare.close(destroy=True)
                shutil.rmtree(self.try_sandbox)
            else:
                self.bare.commit()
                self.bare.close(destroy=True)
        elif self.tag == 'try-full':
            shutil.rmtree(self.try_sandbox)
        self.active = False
        return 0 if self.tag == 'ext4' else time.perf_counter_ns()-started

    def discard(self):
        if self.tag.startswith('agenttx'):
            self.session.close(destroy=True)
        elif self.bare:
            self.bare.close(destroy=True)
        self.active = False


fairness.ROOT = ROOT
fairness.Storage = Storage


def one(item, output):
    root = ROOT/f"s{item['serial']:06}"
    storage, record = None, dict(item)
    try:
        started = time.perf_counter_ns()
        storage = Storage(root, item['variant'], item['case'])
        record['setup_ns'] = time.perf_counter_ns()-started
        if item['case']['kind'] == 'noop':
            if item['variant'] == 'try-full':
                record['operation_and_snapshots_ns'] = storage.execute()
            else:
                started = time.perf_counter_ns()
                base.command(['true'])
                record['operation_and_snapshots_ns'] = time.perf_counter_ns()-started
        else:
            record['operation_and_snapshots_ns'] = storage.execute()
        record['finish_ns'] = storage.finish()
        if item['case']['kind'] != 'noop':
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
    print(json.dumps({key:record.get(key) for key in
        ('serial','phase','variant','case','status','finish_ns','error')}), flush=True)
    return record['status'] == 'passed'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--repeats', type=int, default=5)
    p.add_argument('--one')
    p.add_argument('--probe', action='store_true')
    args = p.parse_args()
    if args.one:
        raise SystemExit(0 if one(json.loads(args.one), args.output) else 1)
    assert not args.output.exists() and not ROOT.exists()
    args.output.mkdir(parents=True)
    plan = []
    def add(phase, variant, case, repeat):
        plan.append(dict(serial=len(plan),phase=phase,variant=variant,case=case,repeat=repeat))
    if args.probe:
        for variant in VARIANTS:
            add('probe', variant,dict(kind='commit', operation='rename', files=1000),0)
    else:
        for operation in base.WORKLOADS:
            for variant in VARIANTS:
                add('commit-canary',variant,dict(kind='commit',operation=operation,files=32),-2)
        cases = [dict(kind='commit',operation=op,files=10000) for op in base.WORKLOADS]
        cases += [dict(kind='commit',operation='create',files=100000),dict(kind='noop')]
        cases += [dict(kind='snapshot',depth=depth) for depth in (1,10,30)]
        randomizer = random.Random(20261115)
        for case in cases:
            for repeat in range(-1,args.repeats):
                variants = list(SNAPSHOT_VARIANTS if case['kind']=='snapshot' else VARIANTS)
                randomizer.shuffle(variants)
                for variant in variants:
                    add('warmup' if repeat==-1 else 'measured',variant,case,repeat)
    files = [Path(__file__),Path(fairness.__file__),Path(base.__file__)]
    base.atomic(args.output/'manifest.json',dict(plan=plan,runtime=str(RUNTIME),
        script_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        runtime_sha256={p:hashlib.sha256((RUNTIME/p).read_bytes()).hexdigest() for p in
                        ['src/agenttx/snapshot.py','src/agenttx/_snapshot_native.so']},
        try_sha256=hashlib.sha256(TRY.read_bytes()).hexdigest(),
        helper_sha256=hashlib.sha256(Path(shutil.which('try-commit')).read_bytes()).hexdigest(),
        model_calls=0, formal_campaign_paused=True,
        settings=dict(filesize=4096,warmups=1,repeats=args.repeats,volatile=False,
            metadata_copy='Optional; all materialization, seal, publication and cleanup timed.',
            try_native='Same ordinary OverlayFS operations, upstream C publication helper.',
            try_cli_commit='Same ordinary OverlayFS operations; upstream try commit command entry.',
            try_full='Unmodified upstream try -y wraps actual Rust workload; native userxattr mounts.',
            full_lifecycle='Initial file population, setup, operation, publication, unmount and staging destruction.',
            sample_policy='Fixed randomized order; all measured runs retained.')))
    for item in plan:
        base.atomic(args.output/'progress.json',dict(state='running',completed=item['serial'],total=len(plan),current=item))
        result = subprocess.run([sys.executable,__file__,'--output',str(args.output),'--one',json.dumps(item)])
        if result.returncode:
            base.atomic(args.output/'progress.json',dict(state='failed',completed=item['serial'],total=len(plan),current=item))
            raise SystemExit(result.returncode)
    base.atomic(args.output/'progress.json',dict(state='complete',completed=len(plan),total=len(plan)))


if __name__ == '__main__':
    main()
