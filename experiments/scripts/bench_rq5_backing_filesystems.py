#!/usr/bin/env python3
"""Matched RQ5 file, checkpoint, and resource experiments on Ext4 and XFS."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time
import traceback
from types import SimpleNamespace

import bench_rq5_checkpoint_lifetime as common
from rq5_deltafs_backend import session_type
from rq5_storage_accounting import backing_usage


FILESYSTEMS = ('ext4', 'xfs')
LAYERED = ('overlay-sync', 'deltafs', 'agenttx-overlay')
OPERATIONS = {'create': 'write-files', 'overwrite': 'overwrite-files',
              'rename': 'rename-files', 'unlink': 'unlink-files'}
DEPTHS = (1, 10, 30)


def run_workload(args, name, destination, files):
    subprocess.run([str(args.perf), 'exec-workload', '--name', name, '--dest', str(destination)],
        env=dict(os.environ, AGENTTX_RQ5_FILE_COUNT=str(files)),
        check=True, capture_output=True, timeout=180)


def validate_commit(workspace, operation, files):
    names = set() if operation == 'unlink' else {
        f'{"renamed" if operation == "rename" else "file"}-{i:06}.dat' for i in range(files)}
    assert {p.name for p in workspace.iterdir()} == names
    payload = (b'\xff' if operation == 'overwrite' else b'\0')*4096
    for name in names:
        path = workspace/name
        assert path.read_bytes() == payload, path
        assert path.stat().st_mode & 0o777 == 0o644, path
    return dict(validated_files=len(names), validated_bytes=len(names)*4096)


def observe(root, cgroup):
    fields = dict(line.split() for line in (cgroup/'memory.stat').read_text().splitlines())
    fields = {key: int(value) for key, value in fields.items()}
    memory = dict(accounted_bytes=sum(fields[key] for key in ('anon', 'file', 'kernel')),
                  current_bytes=int((cgroup/'memory.current').read_text()), stat=fields)
    return dict(memory=memory, backing=backing_usage(root))


def one(args, item, storage):
    root = getattr(args, item['filesystem'])/f's{item["serial"]:06}'
    session, record = None, dict(item, model_calls=0)
    is_resource = item['kind'] == 'resources'
    try:
        if is_resource:
            (args.cgroup/'cgroup.procs').write_text(str(os.getpid()))
        started = time.perf_counter_ns()
        workspace = root/'data'
        workspace.mkdir(parents=True)
        if item['kind'] == 'commit' and item['operation'] != 'create':
            run_workload(args, 'write-files', workspace, item['files'])
        if item['variant'] != 'native':
            cls = (storage.SnapshotSession if item['variant'] == 'agenttx-overlay' else
                   session_type(storage) if item['variant'] == 'deltafs' else
                   common.reference_type(storage))
            session = cls(workspace, root/'state', metadata_copy=True)
            session.variant = item['variant']
        record['setup_ns'] = time.perf_counter_ns()-started
        view = workspace if session is None else session.view
        mount = next(line for line in Path('/proc/self/mountinfo').read_text().splitlines()
                     if line.split()[4] == str(view)) if session else None
        record['mountinfo'] = mount
        if mount:
            expected = 'deltafs' if item['variant'] == 'deltafs' else 'overlay'
            assert f' - {expected} ' in mount
            assert all(option in mount for option in ('metacopy=on', 'redirect_dir=on'))
        if item['phase'] == 'history-canary':
            record['history_checks'] = common.history_canary(session, storage)
            session.close(destroy=True)
        elif item['kind'] == 'commit':
            begin = time.perf_counter_ns()
            run_workload(args, OPERATIONS[item['operation']], view, item['files'])
            record['operation_ns'] = time.perf_counter_ns()-begin
            begin = time.perf_counter_ns()
            if session:
                session.commit()
                session.close(destroy=True)
            record['finish_ns'] = time.perf_counter_ns()-begin
            record['lifecycle_ns'] = record['setup_ns']+record['operation_ns']+record['finish_ns']
            record['validation'] = validate_commit(workspace, item['operation'], item['files'])
        else:
            if is_resource and not item['snapshots_enabled']:
                generation = 0
                def no_snapshot():
                    nonlocal generation
                    generation += 1
                    return SimpleNamespace(generation=generation)
                session.snapshot = no_snapshot
            if is_resource:
                record['setup'] = observe(root, args.cgroup)
            elapsed, rows = common.execute(session, item['depth'], args.perf)
            record.update(operation_ns=elapsed, snapshots=rows,
                          snapshot_total_ns=sum(row['snapshot_ns'] for row in rows))
            if is_resource:
                time.sleep(.03)
                record['held'] = observe(root, args.cgroup)
                record['checkpoints_created'] = len(session.snapshots)
                assert record['checkpoints_created'] == (item['depth'] if item['snapshots_enabled'] else 0)
            begin = time.perf_counter_ns()
            session.commit()
            session.close(destroy=True)
            record['finish_ns'] = time.perf_counter_ns()-begin
            record['lifecycle_ns'] = record['setup_ns']+elapsed+record['finish_ns']
            record['validation'] = dict(validated_files=common.validate(workspace), validated_bytes=110*4096)
        record['status'] = 'passed'
    except BaseException:
        record.update(status='failed', error=traceback.format_exc())
    finally:
        if session:
            try:
                session.close(destroy=True)
            except BaseException:
                record['cleanup_error'] = traceback.format_exc()
                record['status'] = 'failed'
        if root.exists() and not record.get('cleanup_error'):
            shutil.rmtree(root)
        common.atomic(args.output/'sessions'/f'{item["serial"]:06}.json', record)
    return record


def plan(args):
    items = []
    rng = random.Random(20261117)
    for filesystem in FILESYSTEMS:
        for operation in OPERATIONS:
            for variant in ('native', *LAYERED):
                items.append(dict(kind='commit', phase='commit-canary', filesystem=filesystem,
                    operation=operation, files=32, variant=variant, repeat=-2))
        for variant in LAYERED:
            items.append(dict(kind='checkpoint', phase='history-canary', filesystem=filesystem,
                              variant=variant, depth=3, repeat=-2))
    for repeat in range(-1, 0 if args.smoke else args.commit_repeats):
        batch = [dict(kind='commit', phase='warmup' if repeat == -1 else 'measure',
                      filesystem=fs, variant=v, operation=op, files=args.files, repeat=repeat)
                 for fs in FILESYSTEMS for v in ('native', *LAYERED) for op in OPERATIONS]
        rng.shuffle(batch)
        items.extend(batch)
    for repeat in range(-1, 0 if args.smoke else args.checkpoint_repeats):
        batch = [dict(kind='checkpoint', phase='warmup' if repeat == -1 else 'measure',
                      filesystem=fs, variant=v, depth=d, repeat=repeat)
                 for fs in FILESYSTEMS for v in LAYERED for d in DEPTHS]
        rng.shuffle(batch)
        items.extend(batch)
    for repeat in range(-1, 0 if args.smoke else args.resource_repeats):
        pairs = []
        for fs in FILESYSTEMS:
            for variant in LAYERED:
                for depth in DEPTHS:
                    modes = [False, True]
                    rng.shuffle(modes)
                    pairs.append([dict(kind='resources', phase='warmup' if repeat == -1 else 'measure',
                        filesystem=fs, variant=variant, depth=depth, repeat=repeat,
                        snapshots_enabled=mode) for mode in modes])
        rng.shuffle(pairs)
        items.extend(item for pair in pairs for item in pair)
    for serial, item in enumerate(items):
        item['serial'] = serial
    return items


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('storage', 'perf', 'module', 'ext4', 'xfs', 'output', 'cgroup'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--files', type=int, default=10000)
    parser.add_argument('--commit-repeats', type=int, default=10)
    parser.add_argument('--checkpoint-repeats', type=int, default=20)
    parser.add_argument('--resource-repeats', type=int, default=10)
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--one')
    args = parser.parse_args()
    assert os.geteuid() == 0
    storage = common.load_storage(args.storage)
    if args.one:
        record = one(args, json.loads(args.one), storage)
        raise SystemExit(record['status'] != 'passed')
    assert 'nodev\tdeltafs' in Path('/proc/filesystems').read_text()
    assert all(not path.exists() for path in (args.output, args.ext4, args.xfs, args.cgroup))
    args.output.mkdir(parents=True)
    evidence = {}
    for filesystem in FILESYSTEMS:
        root = getattr(args, filesystem)
        root.mkdir()
        evidence[filesystem] = subprocess.check_output(
            ['findmnt', '-T', str(root), '-no', 'FSTYPE,SOURCE,OPTIONS'], text=True).strip()
        assert evidence[filesystem].split()[0] == filesystem
    assert args.ext4.stat().st_dev != args.xfs.stat().st_dev
    xfs_mount = subprocess.check_output(
        ['findmnt', '-T', str(args.xfs), '-no', 'TARGET'], text=True).strip()
    evidence['xfs_info'] = subprocess.check_output(['xfs_info', xfs_mount], text=True)
    assert 'reflink=1' in evidence['xfs_info']
    args.cgroup.mkdir()
    (args.cgroup/'cgroup.subtree_control').write_text('+memory')
    items = plan(args)
    files = [Path(__file__), Path(common.__file__), args.storage, args.storage.with_name('_snapshot_native.so'),
             Path(__file__).with_name('rq5_deltafs_backend.py'),
             Path(__file__).with_name('rq5_storage_accounting.py'), args.perf, args.module]
    hashes = {str(path): common.sha(path) for path in files}
    common.atomic(args.output/'manifest.json', dict(plan=items, kernel=os.uname().release,
        sources=hashes, backing=evidence, model_calls=0, seed=20261117,
        protocol=dict(files=args.files, file_bytes=4096, depths=DEPTHS, warmups=1,
            commit_repeats=args.commit_repeats, checkpoint_repeats=args.checkpoint_repeats,
            resource_pairs=args.resource_repeats, mount_options='metacopy=on,redirect_dir=on,index=off',
            workloads='Unchanged Rust file operations and checkpoint-scaling commit; 100 targets and 10 seeds.',
            lifecycle='Initial file population, setup, execution, checkpoints, publication, and session cleanup; validation excluded.',
            durability='Buffered execution with no extra fsync in timed runs.',
            resources='Enabled-minus-disabled of held-minus-setup; ten paired runs per filesystem/system/depth.',
            storage='Union of FIEMAP physical extents plus non-data blocks; unique inodes, exclude mounted views. FIEMAP_SYNC only in resource observations.',
            memory='Fresh worker cgroup anon+file+kernel; sampled before physical mapping.',
            samples='Randomized systems/filesystems; adjacent resource pairs; retain all formal samples.')))
    for item in items:
        common.atomic(args.output/'progress.json', dict(state='running', completed=item['serial'],
            total=len(items), current=item))
        if item['kind'] == 'resources':
            group = args.cgroup/f's{item["serial"]:06}'
            group.mkdir()
            command = [sys.executable, str(Path(__file__).resolve())]
            for name in ('storage', 'perf', 'module', 'ext4', 'xfs', 'output'):
                command += ['--'+name, str(getattr(args, name))]
            command += ['--cgroup', str(group), '--one', json.dumps(item)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=180)
            group.rmdir()
            record = json.loads((args.output/'sessions'/f'{item["serial"]:06}.json').read_text())
            if result.returncode:
                print(result.stdout+result.stderr, flush=True)
        else:
            record = one(args, item, storage)
        if record['status'] != 'passed':
            common.atomic(args.output/'progress.json', dict(state='failed', completed=item['serial']+1,
                total=len(items), current=item, error=record.get('error')))
            raise RuntimeError(record.get('error', record.get('cleanup_error')))
        if (item['serial']+1) % 20 == 0:
            print(json.dumps(dict(completed=item['serial']+1, total=len(items), latest=item)), flush=True)
    assert all(common.sha(path) == value for path, value in hashes.items()), 'Source changed during measurement'
    args.ext4.rmdir()
    args.xfs.rmdir()
    args.cgroup.rmdir()
    common.atomic(args.output/'complete.json', dict(passed=len(items), failed=0,
        measured=sum(item['phase']=='measure' for item in items), model_calls=0,
        all_source_hashes_unchanged=True))
    common.atomic(args.output/'progress.json', dict(state='complete', completed=len(items), total=len(items)))


if __name__ == '__main__':
    main()
