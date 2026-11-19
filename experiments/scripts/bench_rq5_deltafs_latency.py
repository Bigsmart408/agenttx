#!/usr/bin/env python3
"""Measure stock OverlayFS, AgentTX and kernel DeltaFS on one kernel/Ext4."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import random
import shutil
import subprocess
import time
import traceback

import bench_rq5_checkpoint_lifetime as common
from rq5_deltafs_backend import session_type


VARIANTS = ('overlay-sync', 'agenttx-overlay', 'deltafs')
DEPTHS = (1, 10, 30)


def one(item, args, storage):
    root = args.root/f's{item["serial"]:06}'
    session = None
    record = dict(item, model_calls=0)
    started = time.perf_counter_ns()
    try:
        workspace = root/'data'
        workspace.mkdir(parents=True)
        cls = (session_type(storage) if item['variant'] == 'deltafs' else
               storage.SnapshotSession if item['variant'] == 'agenttx-overlay' else
               common.reference_type(storage))
        session = cls(workspace, root/'state', metadata_copy=True)
        session.variant = item['variant']
        record['setup_ns'] = time.perf_counter_ns()-started
        record['mountinfo'] = next(line for line in Path('/proc/self/mountinfo').read_text().splitlines()
                                   if line.split()[4] == str(session.view))
        assert 'metacopy=on' in record['mountinfo'] and 'redirect_dir=on' in record['mountinfo']
        if item['phase'] == 'canary':
            record['history_checks'] = common.history_canary(session, storage)
            session.close(destroy=True)
        else:
            operation_ns, rows = common.execute(session, item['depth'], args.perf)
            record['operation_and_snapshots_ns'] = operation_ns
            record['snapshots'] = rows
            record['snapshot_total_ns'] = sum(row['snapshot_ns'] for row in rows)
            begin = time.perf_counter_ns()
            session.commit()
            session.close(destroy=True)
            record['finish_ns'] = time.perf_counter_ns()-begin
            record['lifecycle_ns'] = record['setup_ns']+operation_ns+record['finish_ns']
            record['validated_files'] = common.validate(workspace)
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
        common.atomic(args.output/'sessions'/f'{item["serial"]:06}.json', record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--storage', type=Path, required=True)
    parser.add_argument('--perf', type=Path, default=Path('/opt/rq5/sources/YoloFS-YoloFS-8848c24/target/release/yolo-bench'))
    parser.add_argument('--module', type=Path, default=Path('/opt/rq5/deltafs-20261117/deltafs.ko'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=20)
    parser.add_argument('--include-retained', action='store_true',
                        help='Include the retained-mount-only ablation in the same cohort.')
    args = parser.parse_args()
    assert os.geteuid() == 0
    assert not args.root.exists() and not args.output.exists()
    assert 'nodev\tdeltafs' in Path('/proc/filesystems').read_text()
    args.output.mkdir(parents=True)
    args.root.mkdir(parents=True)
    storage = common.load_storage(args.storage)
    variants = (('overlay-sync', 'overlay-retained', 'deltafs', 'agenttx-overlay')
                if args.include_retained else VARIANTS)
    plan = [dict(phase='canary', variant=v, depth=3, repeat=-2) for v in variants]
    for repeat in range(-1, args.repeats):
        batch = [dict(phase='warmup' if repeat == -1 else 'measure',
                      variant=v, depth=d, repeat=repeat) for v in variants for d in DEPTHS]
        random.Random(20261117+repeat).shuffle(batch)
        plan.extend(batch)
    for serial, item in enumerate(plan):
        item['serial'] = serial
    files = [Path(__file__), Path(common.__file__), args.storage,
             Path(__file__).with_name('rq5_deltafs_backend.py'), args.module, args.perf]
    common.atomic(args.output/'manifest.json', dict(plan=plan, seed=20261117, variants=variants,
        sources={str(p): common.sha(p) for p in files}, kernel=os.uname().release,
        filesystem=subprocess.check_output(['findmnt','-T',str(args.root),'-no','FSTYPE,SOURCE'],text=True).strip(),
        mount_options='metacopy=on,redirect_dir=on,index=off',
        workload='Original Rust checkpoint-scaling commit; 100 targets and 10 overwritten seeds, 4 KiB.',
        deltafs='Recovered paper kernel implementation; independent deltafs registration, checkpoint ioctl.',
        publication='Same final-publication code for all systems; final contents and full cleanup verified.',
        durability='Buffered execution; no additional fsync for any system.',
        console_printk=Path('/proc/sys/kernel/printk').read_text().strip()))
    for i, item in enumerate(plan, 1):
        row = one(item, args, storage)
        progress = dict(done=i,total=len(plan),failed=int(row['status'] != 'passed'))
        common.atomic(args.output/'progress.json', progress)
        if progress['failed']:
            raise RuntimeError(row.get('error'))
        if i % 9 == 0:
            print(progress, flush=True)
    args.root.rmdir()
    common.atomic(args.output/'complete.json', dict(passed=len(plan),failed=0,
        measured=sum(i['phase']=='measure' for i in plan)))


if __name__ == '__main__':
    main()
