#!/usr/bin/env python3
"""RQ5 storage primitives using YoloFS's original fixed workloads.

Root-only dedicated guest benchmark. Times snapshot and filesystem commit APIs;
does not run an agent, fault injection, localization, dependency capture, or the
SharedSemisolate.run command/digest pipeline. The storage API's changed paths
are the fixed workload write set, not fault locations. File operations and
correctness checks are recorded separately from snapshot/commit latency.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
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

SHARE = Path('/hostshare/optimization-storage-20261115')
GUEST = Path(os.environ.get('AGENTTX_RQ5_PRIMITIVE_ROOT','/var/lib/atx5-storage-primitives-20261115'))
PERF = Path('/opt/rq5/sources/YoloFS-YoloFS-8848c24/target/release/yolo-bench')
VARIANTS = ('agenttx-before', 'agenttx-after', 'overlayfs', 'yolofs')
WORKLOADS = {'create': 'write-files', 'overwrite': 'overwrite-files',
             'rename': 'rename-files', 'unlink': 'unlink-files'}


def command(argv, **kwargs):
    result = subprocess.run([str(a) for a in argv], text=True, capture_output=True,
                            timeout=1800, **kwargs)
    if result.returncode:
        raise RuntimeError(f'{argv}: {result.stderr[-4000:]} {result.stdout[-1000:]}')
    return result


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2)+'\n')
    temporary.replace(path)


def rust_workload(name, dest, files=10000, **extra):
    env = dict(os.environ, AGENTTX_RQ5_FILE_COUNT=str(files), **extra)
    return command([PERF, 'exec-workload', '--name', name, '--dest', dest], env=env)


def mount(lowers, view, upper=None, work=None):
    view.mkdir(parents=True, exist_ok=True)
    if upper is None and len(lowers) == 1:
        command(['mount', '--bind', lowers[0], view])
        return
    options = 'lowerdir=' + ':'.join(str(p) for p in lowers)
    if upper is not None:
        upper.mkdir(parents=True, exist_ok=True)
        work.mkdir(parents=True, exist_ok=True)
        options += f',upperdir={upper},workdir={work}'
    command(['mount', '-t', 'overlay', 'overlay', '-o', options, view])


class Storage:
    def __init__(self, root, variant, case):
        self.root, self.variant, self.case = root, variant, case
        self.base, self.view = root/'b', root/'m'
        self.data = self.base/'data'
        self.data.mkdir(parents=True)
        self.layers, self.generations = [], []
        self.snapshot_rows = []
        self.pool = None
        self.active = False
        if case['kind'] == 'commit' and case['operation'] != 'create':
            rust_workload('write-files', self.data, case['files'])
        if variant.startswith('agenttx'):
            from agenttx.semisolate import SharedSemisolate
            self.pool = SharedSemisolate(
                self.data, sandbox_dir=root/'a', trace_reads=False,
                persistent_worker=False,
                try_bin=Path(sys.modules[SharedSemisolate.__module__].__file__).resolve().parents[2]/'third_party/try/try')
            # The benchmark exercises storage APIs directly; accidentally
            # entering the command/dependency pipeline is a hard failure.
            def no_command_pipeline(*args, **kwargs):
                raise AssertionError('RQ5 must not enter SharedSemisolate.run')
            self.pool.run = no_command_pipeline
            self.upper = self.pool.sandbox_dir/'upperdir'
            self.data_upper = self.upper/str(self.data).lstrip('/')
            mount([self.data], self.view, self.data_upper, root/'w')
            self.dest = self.view
            if case['kind'] == 'snapshot':
                self.pool.layers.snapshot_before(0, self.upper)
        elif variant == 'overlayfs':
            self.upper = root/'u0'
            mount([self.base], self.view, self.upper, root/'w0')
            self.dest = self.view/'data'
        else:
            (self.base/'yolofs.toml').write_text(
                'permission = true\nstaging = true\nauto_snapshot = false\nprompt_timeout = 5\n'
                '[rules]\n"." = "allow"\n"/" = "allow"\n')
            command(['yolo', 'mount'], cwd=self.base)
            self.view = self.base/'.yolofs/mnt'/self.base.relative_to('/')
            self.dest = self.view/'data'
        self.active = True

    def snapshot(self, index):
        started = time.perf_counter_ns()
        if self.pool:
            names = [f'seed-{i:06}.dat' for i in range(10)]
            if index == 1:
                names += [f'target-{i:06}.dat' for i in range(100)]
            paths = [str(self.data/name) for name in names]
            self.pool.layers.snapshot_before(index, self.upper, changed_paths=paths)
        elif self.variant == 'overlayfs':
            next_upper, next_work = self.root/f'u{index}', self.root/f'w{index}'
            next_upper.mkdir()
            next_work.mkdir()
            command(['umount', self.view])
            self.layers.append(self.upper)
            self.upper = next_upper
            mount([*reversed(self.layers), self.base], self.view, next_upper, next_work)
        else:
            result = command(['yolo', 'snapshot', f'rq5-{index}'], cwd=self.base)
            match = re.search(r'snapshot (\d+)', result.stdout+result.stderr)
            if not match:
                raise RuntimeError('missing YoloFS snapshot generation')
            self.generations.append(int(match[1]))
        elapsed = time.perf_counter_ns()-started
        self.snapshot_rows.append(dict(index=index, snapshot_ns=elapsed))
        return elapsed

    def execute(self):
        case = self.case
        started = time.perf_counter_ns()
        if case['kind'] == 'commit':
            rust_workload(WORKLOADS[case['operation']], self.dest, case['files'])
        else:
            env = dict(os.environ, CHECKPOINT_SCALING_MODE='commit',
                       CHECKPOINT_SCALING_DEPTH=str(case['depth']))
            with subprocess.Popen([str(PERF), 'exec-workload', '--name', 'checkpoint-scaling',
                                   '--dest', str(self.dest)],
                                  env=env, text=True, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
                for line in process.stdout:
                    if line.strip() != 'YOLO_BENCH_CHECKPOINT':
                        continue
                    checkpoint = json.loads(process.stdout.readline())
                    elapsed = self.snapshot(checkpoint['step'])
                    process.stdin.write('YOLO_BENCH_RESULTS\n')
                    process.stdin.write(json.dumps(dict(checkpoint_ms=elapsed//1000000,
                        stop=False, next_dest=str(self.dest)))+'\n')
                    process.stdin.flush()
                stderr = process.stderr.read()
                if process.wait():
                    raise RuntimeError(stderr)
            assert len(self.snapshot_rows) == case['depth']
        return time.perf_counter_ns()-started

    def validate(self, dest):
        case = self.case
        if case['kind'] == 'snapshot':
            expected = {f'target-{i:06}.dat': b'\0'*4096 for i in range(100)}
            expected.update({f'seed-{i:06}.dat': b'\0'*4096 for i in range(10)})
        else:
            expected = {}
            if case['operation'] != 'unlink':
                prefix = 'renamed' if case['operation'] == 'rename' else 'file'
                payload = (b'\xff' if case['operation']=='overwrite' else b'\0')*4096
                expected = {f'{prefix}-{i:06}.dat':payload for i in range(case['files'])}
        found = {p.name:p for p in dest.iterdir()}
        assert found.keys()==expected.keys(), (list(found)[:5],list(expected)[:5])
        for name, payload in expected.items():
            assert found[name].read_bytes()==payload, name
            assert found[name].stat().st_mode & 0o777 == 0o644, name
        return dict(validated_files=len(expected),validated_bytes=sum(map(len,expected.values())))

    def check_history(self):
        if self.case['kind'] != 'snapshot':
            return []
        rows = []
        for index in sorted({1, max(1,self.case['depth']//2), self.case['depth']}):
            view = self.root/'history-view'
            if self.pool:
                before = self.pool.layers.resolve_before(index)
                historical = before/str(self.data).lstrip('/')
                lowers = [historical, self.data]
            elif self.variant == 'overlayfs':
                lowers = [*reversed(self.layers[:index]), self.base]
            else:
                command(['yolo','travel',str(self.generations[index-1])],cwd=self.base)
                rows.append(dict(index=index,**self.validate(self.dest)))
                continue
            mount(lowers, view)
            try:
                rows.append(dict(index=index,**self.validate(view if self.pool else view/'data')))
            finally:
                command(['umount',view])
        if self.variant == 'yolofs':
            command(['yolo','travel',str(self.generations[-1])],cwd=self.base)
        return rows

    def commit(self):
        if self.variant != 'yolofs':
            command(['umount', self.view])
            self.active = False
        started = time.perf_counter_ns()
        if self.pool:
            result = self.pool.commit()
            if result.returncode:
                raise RuntimeError(result.stderr+result.stdout)
            elapsed = time.perf_counter_ns()-started
        elif self.variant == 'overlayfs':
            spec = self.root/'commit-layers.json'
            spec.write_text(json.dumps([(str(p),str(self.base)) for p in [*self.layers,self.upper]]))
            result = command([PERF],env=dict(os.environ,AGENTTX_RQ5_COMMIT_LAYERS=str(spec)))
            elapsed = json.loads(result.stdout)['commit_ns']
        else:
            command(['yolo','commit'],cwd=self.base)
            elapsed = time.perf_counter_ns()-started
        return elapsed

    def close(self, destroy):
        if self.active:
            if self.variant == 'yolofs':
                command(['yolo','unmount'],cwd=self.base)
            else:
                command(['umount',self.view])
            self.active = False
        if self.pool:
            self.pool.close(destroy=destroy)
        if destroy:
            shutil.rmtree(self.root)


def one(item, output):
    variant = item['variant']
    if variant.startswith('agenttx'):
        runtime = ('baseline' if variant.endswith('before') else
                   os.environ.get('AGENTTX_RQ5_AFTER_RUNTIME','optimized-v1'))
        sys.path.insert(0, '/opt/rq5/agenttx-storage-'+runtime+'/src')
    root = GUEST/f"s{item['serial']:06}"
    record = dict(**item,started_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()))
    storage = None
    try:
        storage = Storage(root,variant,item['case'])
        elapsed = storage.execute()
        record.update(operation_and_snapshots_ns=elapsed,snapshots=storage.snapshot_rows,
                      snapshot_total_ns=sum(r['snapshot_ns'] for r in storage.snapshot_rows))
        record['live_validation'] = storage.validate(storage.dest)
        # Validate all history before commit; outside snapshot/commit timers.
        record['history_checks'] = storage.check_history()
        record['commit_ns'] = storage.commit()
        record['commit_validation'] = storage.validate(storage.data)
        record['run_pipeline_calls'] = 0
        record['status'] = 'passed'
        storage.close(destroy=True)
    except BaseException:
        record.update(status='failed',error=traceback.format_exc())
        if storage is not None:
            try:
                storage.close(destroy=False)
            except Exception:
                record['cleanup_error']=traceback.format_exc()
    atomic(output/'sessions'/f"{item['serial']:06}.json",record)
    print(json.dumps({k:record.get(k) for k in ('serial','variant','case','status','snapshot_total_ns','commit_ns','error')}),flush=True)
    return record['status']=='passed'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=SHARE/'comparison-v1')
    parser.add_argument('--one')
    parser.add_argument('--variants',nargs='+',choices=VARIANTS,default=list(VARIANTS))
    args=parser.parse_args()
    if args.one:
        raise SystemExit(0 if one(json.loads(args.one),args.output) else 1)
    assert os.geteuid()==0
    assert not args.output.exists(),'refusing to overwrite measurements'
    assert not GUEST.exists(),'refusing to reuse session roots'
    args.output.mkdir(parents=True)
    cases=[dict(kind='commit',operation=op,files=10000) for op in WORKLOADS]
    cases += [dict(kind='commit',operation='create',files=100000)]
    cases += [dict(kind='snapshot',depth=depth) for depth in (1,10,30)]
    plan=[]
    order=list(args.variants)
    random.Random(20261115).shuffle(order)
    for case_index,case in enumerate(cases):
        for repeat in range(2):
            variants=order if repeat==0 else list(reversed(order))
            for variant in variants:
                plan.append(dict(serial=len(plan),variant=variant,case_index=case_index,
                                 case=case,repeat=repeat))
    atomic(args.output/'manifest.json',dict(plan=plan,
        yolo_source='8848c243ba28ff91432c1da868671d6ed8910ed6',
        perf_source='bc32d0835ed8b9d28a799e59574f31855999a89a',
        perf_binary_sha256=hashlib.sha256(PERF.read_bytes()).hexdigest(),
        scope='Storage APIs only; original upstream Rust workload bodies; 100k create changes only the count parameter.',
        snapshot_input='Fixed checkpoint-scaling write set: first 100 targets + 10 seeds; then 10 seeds.',
        excluded='Fault injection, localization, agent/LLM, dependency capture, per-command digest pipeline, setup and file operation execution.',
        storage_checks_in_timers='Snapshot capture, immutable history publication and internal commit bookkeeping remain included.',
        historical_view='Materialization and verification outside API timers; validated before commit.',
        warmup='No additional performance warmup; two exploratory correctness-checked repeats, not the paused formal campaign.',
        formal_campaign_paused=True))
    for item in plan:
        atomic(args.output/'progress.json',dict(state='running',completed=item['serial'],total=len(plan),current=item))
        done=subprocess.run([sys.executable,__file__,'--output',str(args.output),'--one',json.dumps(item)])
        if done.returncode:
            atomic(args.output/'progress.json',dict(state='failed',completed=item['serial'],total=len(plan),current=item))
            raise SystemExit(done.returncode)
    atomic(args.output/'progress.json',dict(state='complete',completed=len(plan),total=len(plan)))


if __name__=='__main__':
    main()
