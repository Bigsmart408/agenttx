#!/usr/bin/env python3
"""Measure retained snapshot resources against identical writes without snapshots.

Resource sampling is a separate experiment from the uninstrumented latency runs.
Each session runs in a fresh memory cgroup, with accounting before the workload
and while history is held before publication. Backing Ext4 blocks are counted
once per inode; mounted views are excluded.
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
import stat
import subprocess
import sys
import time
import traceback

VARIANTS = ('overlay-sync', 'agenttx-overlay', 'yolofs-api', 'agenttx-yolofs')


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2)+'\n')
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def backing_usage(root):
    device = root.stat().st_dev
    seen, stack = set(), [root]
    result = dict(allocated_bytes=0, regular_file_bytes=0, regular_files=0,
                  directories=0, skipped_mounts=0)
    while stack:
        path = stack.pop()
        metadata = path.lstat()
        if metadata.st_dev != device:
            result['skipped_mounts'] += 1
            continue
        key = (metadata.st_dev, metadata.st_ino)
        if key in seen:
            continue
        seen.add(key)
        result['allocated_bytes'] += metadata.st_blocks*512
        if stat.S_ISDIR(metadata.st_mode):
            result['directories'] += 1
            with os.scandir(path) as entries:
                stack.extend(Path(entry.path) for entry in entries)
        elif stat.S_ISREG(metadata.st_mode):
            result['regular_files'] += 1
            result['regular_file_bytes'] += metadata.st_size
    result['unique_inodes'] = len(seen)
    return result


def resources(root, cgroup):
    # Read memory before walking history, so the walk's own allocations do
    # not enter the retained-memory observation.
    fields = {k: int(v) for k, v in
              (line.split() for line in (cgroup/'memory.stat').read_text().splitlines())}
    memory = dict(current_bytes=int((cgroup/'memory.current').read_text()),
                  accounted_bytes=sum(fields[k] for k in ('anon', 'file', 'kernel')),
                  stat=fields)
    rss = next(line for line in Path('/proc/self/status').read_text().splitlines()
               if line.startswith('VmRSS:'))
    memory['process_rss_bytes'] = int(rss.split()[1])*1024
    return dict(memory=memory, backing=backing_usage(root),
                open_fds=len(list(Path('/proc/self/fd').iterdir())))


def adapters(args):
    port = load('rq5_resource_port', args.port/'scripts/rq5.py')
    lifetime = load('rq5_resource_lifetime', args.lifetime)
    primitive = port.primitive

    class Overlay:
        execute = port.base.Storage.execute
        validate = port.Storage.validate

        def __init__(self, root, variant, case):
            self.root, self.variant, self.case = root, variant, case
            self.data = root/'b/data'
            self.data.mkdir(parents=True)
            cls = primitive.SnapshotSession if variant == 'agenttx-overlay' else lifetime.reference_type(primitive)
            self.session = cls(self.data, root/'a', metadata_copy=True)
            self.session.variant = variant
            self.dest = self.session.view
            self.snapshot_rows = []
            self.active = True

        def snapshot(self, index):
            begin = time.perf_counter_ns()
            point = self.session.snapshot()
            elapsed = time.perf_counter_ns()-begin
            assert point.generation == index
            self.snapshot_rows.append(dict(index=index, snapshot_ns=elapsed))
            return elapsed

        def finish(self):
            self.session.commit()
            self.session.close(destroy=True)
            self.active = False

        def discard(self):
            self.session.close(destroy=True)
            self.active = False

        def check_history(self):
            checks = []
            for index, point in enumerate(self.session.snapshots, 1):
                target = self.root/'history-view'
                self.session.mount_snapshot(point, target)
                try:
                    lifetime.validate(target, index)
                    checks.append(dict(generation=index, distinct_seed_byte=index, validated_files=110))
                finally:
                    primitive._unmount(target)
            return checks

    def make(root, variant, case):
        if variant in ('overlay-sync', 'agenttx-overlay'):
            return Overlay(root, variant, case)
        return port.Storage(root, variant, case)
    return port, primitive, make


def one(args, item, cgroup):
    # Only the isolated worker and its children enter the measurement cgroup.
    (cgroup/'cgroup.procs').write_text(str(os.getpid()))
    port, primitive, make = adapters(args)
    root = args.root/f's{item["serial"]:06}'
    storage = None
    record = dict(item, model_calls=0)
    try:
        storage = make(root, item['variant'], dict(kind='snapshot', depth=item['depth']))
        if item['phase'] == 'canary':
            for i in range(100):
                (storage.dest/f'target-{i:06}.dat').write_bytes(b'\0'*4096)
            for generation in (1, 2, 3):
                for i in range(10):
                    (storage.dest/f'seed-{i:06}.dat').write_bytes(bytes([generation])*4096)
                storage.snapshot(generation)
            for i in range(10):
                (storage.dest/f'seed-{i:06}.dat').write_bytes(b'\xfe'*4096)
            record['history_checks'] = storage.check_history()
            storage.discard()
        else:
            if not item['snapshots_enabled']:
                def no_snapshot(index):
                    storage.snapshot_rows.append(dict(index=index, snapshot_ns=0, created=False))
                    return 0
                storage.snapshot = no_snapshot
            record['setup'] = resources(root, cgroup)
            storage.execute()
            # Let exited workload processes release their accounting. No
            # memory reclaim, sync or cache drop is requested here.
            time.sleep(.03)
            record['held'] = resources(root, cgroup)
            record['checkpoint_requests'] = len(storage.snapshot_rows)
            record['checkpoints_created'] = item['depth'] if item['snapshots_enabled'] else 0
            storage.finish()
            record['validation'] = storage.validate(storage.data)
            assert record['checkpoint_requests'] == item['depth']
            record['after_finish'] = resources(root, cgroup)
        record['status'] = 'passed'
    except BaseException:
        record.update(status='failed', error=traceback.format_exc())
    finally:
        if storage is not None and storage.active:
            try:
                storage.discard()
            except BaseException:
                record['cleanup_error'] = traceback.format_exc()
        if root.exists() and not record.get('cleanup_error'):
            shutil.rmtree(root)
        save(args.output/'sessions'/f'{item["serial"]:06}.json', record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=Path, default=Path('/opt/rq5/agenttx-yolofs-port/port'))
    parser.add_argument('--lifetime', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cgroup', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=10)
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--one')
    args = parser.parse_args()
    assert os.geteuid() == 0
    if args.one:
        record = one(args, json.loads(args.one), args.cgroup)
        raise SystemExit(record['status'] != 'passed')
    assert not args.root.exists() and not args.output.exists() and not args.cgroup.exists()
    args.root.mkdir(parents=True)
    args.output.mkdir(parents=True)
    args.cgroup.mkdir()
    (args.cgroup/'cgroup.subtree_control').write_text('+memory')
    plan = []
    for variant in VARIANTS:
        plan.append(dict(phase='canary', variant=variant, depth=3, repeat=-2,
                         snapshots_enabled=True))
    for repeat in range(-1, args.repeats if not args.smoke else 0):
        batch = []
        for variant in VARIANTS:
            for depth in ((1, 10, 30) if not args.smoke else (3,)):
                modes = [False, True]
                random.Random(20261116+repeat+depth).shuffle(modes)
                batch.append([dict(phase='warmup' if repeat == -1 else 'measure',
                    variant=variant, depth=depth, repeat=repeat, snapshots_enabled=mode)
                    for mode in modes])
        random.Random(20261116+repeat).shuffle(batch)
        plan.extend(item for pair in batch for item in pair)
    for serial, item in enumerate(plan):
        item['serial'] = serial
    port, primitive, _ = adapters(args)
    files = [Path(__file__), args.lifetime, Path(primitive.__file__),
             args.port/'scripts/rq5.py', args.port/'agenttx_yolofs/storage.py']
    save(args.output/'manifest.json', dict(plan=plan, repeats=args.repeats,
        sources={str(p):sha(p) for p in files},
        filesystem=subprocess.check_output(['findmnt','-T',str(args.root),'-no','FSTYPE,SOURCE'],text=True).strip(),
        kernel=os.uname().release, workload_sha256=sha(port.base.PERF),
        protocol=dict(workload='Original Rust checkpoint-scaling, 100 targets and 10 seeds of 4 KiB.',
            control='Same writes and checkpoint requests, acknowledged without creating snapshots.',
            snapshot_latency='Use earlier uninstrumented latency measurements, not these resource probes.',
            memory='Fresh worker cgroup; anon+file+kernel charged memory growth, sampled before publication.',
            storage='Allocated Ext4 blocks, 512*st_blocks, unique (device,inode); mounted views skipped.',
            increment='Paired enabled-minus-disabled difference of held-minus-setup resource growth.',
            order='Randomized paired modes and system/depth blocks; retain all samples.',
            validation='Separate three distinct historical generations; final 110 file contents and modes every run.')))
    for item in plan:
        group = args.cgroup/f's{item["serial"]:06}'
        group.mkdir()
        cmd = [sys.executable, str(Path(__file__)), '--port', str(args.port),
               '--lifetime',str(args.lifetime),'--root',str(args.root),
               '--output',str(args.output),'--cgroup',str(group),'--one',json.dumps(item)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        group.rmdir()
        record = json.loads((args.output/'sessions'/f'{item["serial"]:06}.json').read_text())
        if result.returncode or record['status'] != 'passed':
            print(result.stdout+result.stderr+record.get('error',''), flush=True)
            raise RuntimeError(f'Failed session {item["serial"]}')
        progress = dict(done=item['serial']+1,total=len(plan),failed=0)
        save(args.output/'progress.json',progress)
        if progress['done'] % 12 == 0:
            print(json.dumps(progress),flush=True)
    args.root.rmdir()
    args.cgroup.rmdir()
    save(args.output/'complete.json', dict(passed=len(plan),failed=0,
        measured=sum(i['phase']=='measure' for i in plan)))
    print(json.dumps(dict(complete=True,total=len(plan))),flush=True)


if __name__ == '__main__':
    main()
