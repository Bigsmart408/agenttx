#!/usr/bin/env python3
"""Matched OverlayFS checkpoint ablation using the original Rust workload.

All configurations use the same in-process mount interface, Ext4 directory,
OverlayFS options, publication implementation, and complete cleanup timing.
Only the checkpoint lifetime and AgentTX generation bookkeeping differ.
"""
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
import traceback


VARIANTS = ('overlay-sync', 'overlay-retained', 'agenttx')
DEPTHS = (1, 10, 30)


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2)+'\n')
    temp.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_storage(path):
    spec = importlib.util.spec_from_file_location('rq5_snapshot', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def reference_type(storage):
    class Reference(storage.SnapshotSession):
        def snapshot(self):
            generation = len(self.snapshots)+1
            path, upper, work = self._new_layer(generation)
            if self.variant == 'overlay-retained':
                storage._readonly(self.view, True)
                self._pins.append(os.open(self.view, os.O_PATH | os.O_CLOEXEC))
                storage._unmount(self.view, detach=True)
            else:
                storage._unmount(self.view)
            self._mounted = False
            self._layers.append((self.upperdir, self._upper))
            self._visible_layers = [(p, fd, None) for p, fd in reversed(self._layers)]
            lowers = [*(fd for _, fd in reversed(self._layers)), self._base]
            self._mount_view(self.view, lowers, upper, work)
            self._mounted = True
            self.upperdir, self._upper, self._work = path, upper, work
            point = storage.FilesystemSnapshot(generation,
                tuple(p for p, _ in reversed(self._layers))+(self.workspace,),
                self._pins[-1] if self._pins else -1, tuple(lowers))
            self.snapshots.append(point)
            return point
    return Reference


def validate(view, byte=0):
    expected = {f'target-{i:06}.dat': b'\0'*4096 for i in range(100)}
    expected.update({f'seed-{i:06}.dat': bytes([byte])*4096 for i in range(10)})
    assert set(p.name for p in view.iterdir()) == expected.keys()
    for name, payload in expected.items():
        path = view/name
        assert path.read_bytes() == payload, path
        assert path.stat().st_mode & 0o777 == 0o644, path
    return len(expected)


def execute(session, depth, perf):
    rows = []
    env = dict(os.environ, CHECKPOINT_SCALING_MODE='commit',
               CHECKPOINT_SCALING_DEPTH=str(depth))
    started = time.perf_counter_ns()
    with subprocess.Popen([str(perf), 'exec-workload', '--name', 'checkpoint-scaling',
                           '--dest', str(session.view)], env=env, text=True,
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE) as process:
        for line in process.stdout:
            if line.strip() != 'YOLO_BENCH_CHECKPOINT':
                continue
            checkpoint = json.loads(process.stdout.readline())
            begin = time.perf_counter_ns()
            point = session.snapshot()
            elapsed = time.perf_counter_ns()-begin
            assert checkpoint['step'] == point.generation == len(rows)+1
            rows.append(dict(index=point.generation, snapshot_ns=elapsed))
            process.stdin.write('YOLO_BENCH_RESULTS\n'+json.dumps(dict(
                checkpoint_ms=elapsed//1000000, stop=False,
                next_dest=str(session.view)))+'\n')
            process.stdin.flush()
        errors = process.stderr.read()
        if process.wait():
            raise RuntimeError(errors)
    assert len(rows) == depth
    return time.perf_counter_ns()-started, rows


def history_canary(session, storage):
    for i in range(100):
        (session.view/f'target-{i:06}.dat').write_bytes(b'\0'*4096)
    points = []
    for generation in (1, 2, 3):
        for i in range(10):
            (session.view/f'seed-{i:06}.dat').write_bytes(bytes([generation])*4096)
        points.append(session.snapshot())
    for i in range(10):
        (session.view/f'seed-{i:06}.dat').write_bytes(b'\xfe'*4096)
    checks = []
    for generation, point in enumerate(points, 1):
        view = session.state_dir/'history'
        session.mount_snapshot(point, view)
        try:
            checks.append(dict(generation=generation, files=validate(view, generation)))
        finally:
            storage._unmount(view)
    return checks


def one(item, args, storage):
    root = args.root/f's{item["serial"]:06}'
    session = None
    record = dict(item, model_calls=0)
    started = time.perf_counter_ns()
    try:
        workspace = root/'data'
        workspace.mkdir(parents=True)
        cls = storage.SnapshotSession if item['variant'] == 'agenttx' else reference_type(storage)
        session = cls(workspace, root/'state', metadata_copy=True)
        session.variant = item['variant']
        record['setup_ns'] = time.perf_counter_ns()-started
        record['mountinfo'] = next(line for line in Path('/proc/self/mountinfo').read_text().splitlines()
                                   if line.split()[4] == str(session.view))
        assert 'metacopy=on' in record['mountinfo'] and 'redirect_dir=on' in record['mountinfo']
        if item['phase'] == 'canary':
            record['history_checks'] = history_canary(session, storage)
            session.close(destroy=True)
        else:
            operation_ns, rows = execute(session, item['depth'], args.perf)
            record['operation_and_snapshots_ns'] = operation_ns
            record['snapshots'] = rows
            record['snapshot_total_ns'] = sum(row['snapshot_ns'] for row in rows)
            begin = time.perf_counter_ns()
            session.commit()
            session.close(destroy=True)
            record['finish_ns'] = time.perf_counter_ns()-begin
            record['lifecycle_ns'] = record['setup_ns']+operation_ns+record['finish_ns']
            record['validated_files'] = validate(workspace)
        record['status'] = 'passed'
    except BaseException:
        record.update(status='failed', error=traceback.format_exc())
        if session is not None:
            try:
                session.close(destroy=True)
            except BaseException:
                record['cleanup_error'] = traceback.format_exc()
    finally:
        if root.exists() and not record.get('cleanup_error'):
            shutil.rmtree(root)
        atomic(args.output/'sessions'/f'{item["serial"]:06}.json', record)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--storage', type=Path, required=True)
    parser.add_argument('--perf', type=Path, default=Path('/opt/rq5/sources/YoloFS-YoloFS-8848c24/target/release/yolo-bench'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=20)
    args = parser.parse_args()
    assert os.geteuid() == 0
    assert not args.root.exists() and not args.output.exists()
    args.output.mkdir(parents=True)
    args.root.mkdir(parents=True)
    storage = load_storage(args.storage)
    plan = []
    for variant in VARIANTS:
        plan.append(dict(phase='canary', variant=variant, depth=3, repeat=0))
    for phase, repeats in [('warmup', 1), ('measure', args.repeats)]:
        for repeat in range(repeats):
            batch = [dict(phase=phase, variant=variant, depth=depth, repeat=repeat)
                     for variant in VARIANTS for depth in DEPTHS]
            random.Random(20261116+repeat).shuffle(batch)
            plan.extend(batch)
    for serial, item in enumerate(plan):
        item['serial'] = serial
    atomic(args.output/'manifest.json', dict(plan=plan, seed=20261116,
        storage_sha256=sha(args.storage), workload_sha256=sha(args.perf),
        benchmark_sha256=sha(__file__), kernel=os.uname().release,
        filesystem=subprocess.check_output(['findmnt', '-T', str(args.root), '-no', 'FSTYPE,SOURCE'],text=True).strip(),
        mount_options='metacopy=on,redirect_dir=on,index=off',
        workload='Original checkpoint-scaling commit: 100 targets, 10 overwritten seeds, 4 KiB files',
        variants={'overlay-sync':'synchronous umount/remount, in-process libc',
                  'overlay-retained':'read-only pin and lazy detachment, no generation journal',
                  'agenttx':'unchanged SnapshotSession with generation journal'},
        timing='Snapshot calls and full lifecycle; cleanup and retained-mount release included',
        durability='Buffered execution, no extra fsync; writers quiescent at checkpoint'))
    failed = 0
    for index, item in enumerate(plan, 1):
        row = one(item, args, storage)
        failed += row['status'] != 'passed'
        atomic(args.output/'progress.json', dict(done=index, total=len(plan), failed=failed))
        if index % 9 == 0 or failed:
            print(json.dumps(dict(done=index,total=len(plan),failed=failed)),flush=True)
        if failed:
            raise RuntimeError(row.get('error'))
    atomic(args.output/'complete.json', dict(passed=len(plan)-failed, failed=failed,
        measured=sum(i['phase']=='measure' for i in plan)))


if __name__ == '__main__':
    main()
