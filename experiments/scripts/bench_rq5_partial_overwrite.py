#!/usr/bin/env python3
"""RQ5 non-truncating 4 KiB edits: device writes and checkpoint diagnostics."""
from __future__ import annotations

import argparse
import ctypes
import fcntl
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import time
import traceback

import bench_rq5_checkpoint_lifetime as common
from rq5_deltafs_backend import CHECKPOINT, session_type
from rq5_storage_accounting import backing_usage


FILESYSTEMS = ('ext4', 'xfs-noreflink', 'xfs-reflink')
VARIANTS = ('native', 'overlay-sync', 'deltafs', 'agenttx-overlay')
SIZES_KIB = (4, 8, 16, 32, 64, 128, 256, 512, 1024)
DEPTHS = (1, 10, 30)
BLOCK = 4096
LIBC = ctypes.CDLL(None, use_errno=True)
LIBC.syncfs.argtypes = [ctypes.c_int]
LIBC.syncfs.restype = ctypes.c_int


def sync_volume(fd):
    if LIBC.syncfs(fd):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def device_stats(device):
    fields = [int(x) for x in (Path('/sys/class/block') / device / 'stat').read_text().split()]
    return dict(writes=fields[4], sectors_written=fields[6], write_bytes=fields[6]*512,
                inflight=fields[8], raw=fields)


def write_delta(before, after):
    assert after['write_bytes'] >= before['write_bytes']
    return after['write_bytes']-before['write_bytes']


def edit(path, size, step):
    """Keep length and all unedited bytes; never O_TRUNC or unlink/replace."""
    offset = ((step*7) % (size//BLOCK))*BLOCK
    payload = bytes([0x80+step % 64])*BLOCK
    start = time.perf_counter_ns()
    fd = os.open(path, os.O_WRONLY | os.O_CLOEXEC)
    try:
        assert os.pwrite(fd, payload, offset) == BLOCK
    finally:
        os.close(fd)
    return time.perf_counter_ns()-start, offset, payload


def snapshot_timed(session, variant):
    """Measure full request, and additionally just DeltaFS ioctl in-process."""
    captured = []
    original = fcntl.ioctl
    if variant == 'deltafs':
        def observed(fd, command, *args):
            if command != CHECKPOINT:
                return original(fd, command, *args)
            start = time.perf_counter_ns()
            result = original(fd, command, *args)
            captured.append(time.perf_counter_ns()-start)
            return result
        fcntl.ioctl = observed
    try:
        start = time.perf_counter_ns()
        point = session.snapshot()
        elapsed = time.perf_counter_ns()-start
    finally:
        fcntl.ioctl = original
    result = dict(snapshot_ns=elapsed)
    if variant == 'deltafs':
        assert len(captured) == 1 and 0 < captured[0] <= elapsed
        result.update(ioctl_ns=captured[0], outside_ioctl_ns=elapsed-captured[0])
    return point, result


def new_session(storage, variant, workspace, state):
    if variant == 'native':
        return None
    cls = (session_type(storage) if variant == 'deltafs' else
           common.reference_type(storage) if variant == 'overlay-sync' else storage.SnapshotSession)
    session = cls(workspace, state, metadata_copy=True)
    session.variant = variant
    return session


def verify_file(path, expected):
    assert path.stat().st_size == len(expected), path
    assert path.read_bytes() == expected, path
    assert path.stat().st_mode & 0o777 == 0o644, path


def io_sample(args, item, storage, root, volume_fd, device):
    workspace = root/'data'
    workspace.mkdir(parents=True)
    session = new_session(storage, item['variant'], workspace, root/'state')
    record = dict(item, model_calls=0, status='running', writes=[])
    try:
        view = workspace if session is None else session.view
        path = view/'payload.bin'
        size = item['size_kib']*1024
        original = bytes([0x31])*size
        path.write_bytes(original)
        os.chmod(path, 0o644)
        history = []
        if session:
            point = session.snapshot()
            history.append((point, original))
        sync_volume(volume_fd)
        record['baseline_storage'] = backing_usage(root)
        sync_volume(volume_fd)
        before = device_stats(device)
        record['device_before'] = before
        expected = bytearray(original)
        for step in range(item['steps']):
            started = time.perf_counter_ns()
            edit_ns, offset, payload = edit(path, size, step)
            sync_volume(volume_fd)
            edit_synced_ns = time.perf_counter_ns()-started
            after_edit = device_stats(device)
            row = dict(step=step, offset=offset, bytes=BLOCK, edit_ns=edit_ns,
                       edit_synced_ns=edit_synced_ns,
                       edit_write_bytes=write_delta(before, after_edit),
                       device_before=before, device_after_edit=after_edit)
            expected[offset:offset+BLOCK] = payload
            if session:
                point, timing = snapshot_timed(session, item['variant'])
                row.update(timing)
                history.append((point, bytes(expected)))
                sync_volume(volume_fd)
            after_snapshot = device_stats(device)
            row.update(snapshot_write_bytes=write_delta(after_edit, after_snapshot),
                       total_write_bytes=write_delta(before, after_snapshot),
                       device_after_snapshot=after_snapshot)
            assert row['total_write_bytes'] == row['edit_write_bytes']+row['snapshot_write_bytes']
            record['writes'].append(row)
            before = after_snapshot
        record['device_after'] = before
        record['held_storage'] = backing_usage(root)
        verify_file(path, expected)
        checked = []
        for point, contents in history:
            # Each edit creates a complete regular file in that generation's
            # upper. Checking these saved objects does not mount extra views.
            saved = point.lowerdirs[0]/'payload.bin'
            verify_file(saved, contents)
            checked.append(dict(generation=point.generation, inode=saved.stat().st_ino,
                                bytes=len(contents)))
        assert len({entry['inode'] for entry in checked}) == len(checked)
        record['history_checks'] = checked
        if session and item['phase'] == 'canary':
            for point, contents in history:
                check_view = root/'history-view'
                session.mount_snapshot(point, check_view)
                try:
                    verify_file(check_view/'payload.bin', contents)
                finally:
                    storage._unmount(check_view)
        record['final_validation'] = dict(bytes=size, mode='0644', history_versions=len(history))
        record['status'] = 'passed'
    finally:
        if session:
            session.close(destroy=True)
    return record


def checkpoint_sample(args, item, storage, root):
    workspace = root/'data'
    workspace.mkdir(parents=True)
    session = new_session(storage, item['variant'], workspace, root/'state')
    record = dict(item, model_calls=0, snapshots=[])
    try:
        for index in range(100):
            (session.view/f'target-{index}.bin').write_bytes(bytes([0x31])*BLOCK)
        for index in range(10):
            (session.view/f'seed-{index}.bin').write_bytes(bytes([0x31])*BLOCK)
        history = []
        for step in range(item['depth']):
            for index in range(10):
                edit(session.view/f'seed-{index}.bin', BLOCK, step)
            point, timing = snapshot_timed(session, item['variant'])
            record['snapshots'].append(dict(step=step, **timing))
            history.append((point, bytes([0x80+step % 64])*BLOCK))
        for index in range(100):
            verify_file(session.view/f'target-{index}.bin', bytes([0x31])*BLOCK)
        for index in range(10):
            verify_file(session.view/f'seed-{index}.bin', history[-1][1])
        for point, expected in history:
            for index in range(10):
                verify_file(point.lowerdirs[0]/f'seed-{index}.bin', expected)
        record['history_checks'] = len(history)*10
        record['status'] = 'passed'
    finally:
        session.close(destroy=True)
    return record


def plan(args):
    rng = random.Random(20261118)
    items = []
    for filesystem in FILESYSTEMS:
        for variant in VARIANTS:
            for size in (4, 64, 1024):
                items.append(dict(kind='io', phase='canary', filesystem=filesystem,
                    variant=variant, size_kib=size, steps=3, repeat=-2))
    for repeat in range(-1, 0 if args.smoke else args.io_repeats):
        batch = [dict(kind='io', phase='warmup' if repeat < 0 else 'measure',
                 filesystem=filesystem, variant=variant, size_kib=size, steps=args.steps,
                 repeat=repeat) for filesystem in FILESYSTEMS for variant in VARIANTS for size in SIZES_KIB]
        rng.shuffle(batch)
        items.extend(batch)
    for repeat in range(-1, 0 if args.smoke else args.checkpoint_repeats):
        batch = [dict(kind='checkpoint', phase='warmup' if repeat < 0 else 'measure',
                 filesystem=filesystem, variant=variant, depth=depth, repeat=repeat)
                 for filesystem in FILESYSTEMS for variant in VARIANTS[1:] for depth in DEPTHS]
        rng.shuffle(batch)
        items.extend(batch)
    return [dict(serial=index, **item) for index, item in enumerate(items)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('storage', 'output', 'roots', 'module'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--io-repeats', type=int, default=20)
    parser.add_argument('--checkpoint-repeats', type=int, default=20)
    parser.add_argument('--steps', type=int, default=5)
    parser.add_argument('--smoke', action='store_true')
    args = parser.parse_args()
    assert os.geteuid() == 0
    assert not args.output.exists()
    roots = {key: Path(value) for key, value in json.loads(args.roots.read_text()).items()}
    assert set(roots) == set(FILESYSTEMS)
    args.output.mkdir(parents=True)
    storage = common.load_storage(args.storage)
    volumes, backing = {}, {}
    for filesystem, root in roots.items():
        info = json.loads(subprocess.check_output(['findmnt', '-J', '-T', str(root),
                                                  '-o', 'TARGET,SOURCE,FSTYPE,OPTIONS'], text=True))['filesystems'][0]
        assert info['fstype'] == ('ext4' if filesystem == 'ext4' else 'xfs')
        assert info['source'].startswith('/dev/loop')
        geometry = '' if filesystem == 'ext4' else subprocess.check_output(['xfs_info', info['target']],text=True)
        if geometry:
            assert ('reflink=0' if filesystem == 'xfs-noreflink' else 'reflink=1') in geometry
        volumes[filesystem] = (os.open(root, os.O_RDONLY | os.O_DIRECTORY),Path(info['source']).name)
        backing[filesystem] = dict(mount=info, xfs_info=geometry, root=str(root))
    assert len({root.stat().st_dev for root in roots.values()}) == 3
    sources = [Path(__file__),Path(common.__file__),Path(__file__).with_name('rq5_deltafs_backend.py'),
               Path(__file__).with_name('rq5_storage_accounting.py'), args.storage,
               args.storage.with_name('_snapshot_native.so'), args.module]
    hashes = {str(path): common.sha(path) for path in sources}
    items = plan(args)
    common.atomic(args.output/'manifest.json',dict(plan=items, sources=hashes, backing=backing,
        kernel=os.uname().release, model_calls=0, protocol=dict(
            sizes_kib=SIZES_KIB, edit_bytes=BLOCK, non_truncating=True, steps=args.steps,
            io_repeats=args.io_repeats, checkpoint_repeats=args.checkpoint_repeats,
            device_writes='Loop block-device sectors_written * 512; syncfs before and after edit/checkpoint; includes filesystem journal and metadata.',
            edit_latency='Open O_WRONLY, pwrite 4096 bytes, close; separate elapsed through syncfs.',
            snapshot_latency='Full snapshot method; DeltaFS additionally measures fcntl.ioctl only in-process.',
            checkpoint_diagnostic='100 target and 10 seed files, 4 KiB; non-truncating writes; depths 1/10/30; buffered, no added flush within the run.',
            storage='FIEMAP physical union plus non-data blocks; sampled outside latency and I/O windows.',
            validation='Every file and generation verified for exact bytes, size and mode; canaries mount all saved views.',
            sampling='All formal samples retained, randomized configurations, one warmup per cell.',
            mount_options='metacopy=on,redirect_dir=on,index=off')))
    try:
        for item in items:
            common.atomic(args.output/'progress.json', dict(state='running',completed=item['serial'],total=len(items),current=item))
            root = roots[item['filesystem']]/f'partial-{item["serial"]:06}'
            assert not root.exists()
            try:
                record = (io_sample(args,item,storage,root,*volumes[item['filesystem']]) if item['kind']=='io'
                          else checkpoint_sample(args,item,storage,root))
                shutil.rmtree(root)
                sync_volume(volumes[item['filesystem']][0])
                record['cleanup_passed'] = True
            except BaseException:
                common.atomic(args.output/'sessions'/f'{item["serial"]:06}.json',dict(item,status='failed',error=traceback.format_exc()))
                common.atomic(args.output/'progress.json',dict(state='failed',completed=item['serial']+1,total=len(items),current=item,error=traceback.format_exc()))
                raise
            common.atomic(args.output/'sessions'/f'{item["serial"]:06}.json',record)
            if (item['serial']+1) % 20 == 0:
                print(json.dumps(dict(completed=item['serial']+1,total=len(items),latest=item)),flush=True)
        assert all(common.sha(path)==value for path,value in hashes.items())
        common.atomic(args.output/'complete.json',dict(passed=len(items),failed=0,
                      measured=sum(item['phase']=='measure' for item in items),model_calls=0,
                      all_source_hashes_unchanged=True))
        common.atomic(args.output/'progress.json',dict(state='complete',completed=len(items),total=len(items)))
    finally:
        for fd,_ in volumes.values():
            os.close(fd)


if __name__ == '__main__':
    main()
