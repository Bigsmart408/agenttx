#!/usr/bin/env python3
"""Run native BranchFS under the existing RQ5 workloads and accounting."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import random
import shutil
import socket
import subprocess
import sys
import time
import traceback
from types import SimpleNamespace

import bench_rq5_checkpoint_lifetime as common
from bench_rq5_backing_filesystems import observe
from bench_rq5_partial_overwrite import device_stats, edit, sync_volume, verify_file, write_delta
from rq5_storage_accounting import backing_usage

COMMIT = 'a4b6592d31aacb4d2f94d0c80f43d47d38063fa9'
FILESYSTEMS = ('ext4', 'xfs')
DEPTHS = (1, 10, 30)


class BranchSession:
    def __init__(self, binary, workspace, state, log):
        self.binary, self.workspace, self.state_dir = binary, workspace, state
        self.state_dir.mkdir()
        self.view = self.state_dir/'view'
        self.view.mkdir()
        self.storage = self.state_dir/'storage'
        self.storage.mkdir()
        self.snapshots, self.branches = [], []
        self.current = 'main'
        self.log = log.open('wb')
        self.daemon = subprocess.Popen([str(binary), 'run-daemon', '--base', str(workspace),
            '--storage', str(self.storage)], stdin=subprocess.DEVNULL, stdout=self.log,
            stderr=subprocess.STDOUT, env=dict(os.environ, RUST_LOG='warn'))
        self.closed = False
        try:
            deadline = time.monotonic()+15
            while True:
                if self.daemon.poll() is not None:
                    raise RuntimeError(f'BranchFS daemon exited: {log.read_text()}')
                try:
                    with socket.socket(socket.AF_UNIX) as connection:
                        connection.connect(str(self.storage/'daemon.sock'))
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise TimeoutError('BranchFS daemon socket')
                    time.sleep(.005)
            self.cli('mount', '--storage', str(self.storage), str(self.view))
            assert self.view.is_mount()
            self.create_and_switch('b000')
        except BaseException:
            self.close()
            raise

    def cli(self, *args):
        return subprocess.run([str(self.binary), *args], check=True,
                              capture_output=True, text=True, timeout=20)

    def control(self, command):
        payload = command.encode()
        fd = os.open(self.view/'.branchfs_ctl', os.O_WRONLY | os.O_CLOEXEC)
        try:
            assert os.write(fd, payload) == len(payload)
        finally:
            os.close(fd)

    def create_and_switch(self, name):
        self.control('create:'+name)
        self.control('switch:'+name)
        self.current = name
        self.branches.append(name)

    def snapshot(self):
        point = SimpleNamespace(generation=len(self.snapshots)+1, branch=self.current)
        self.create_and_switch(f'b{point.generation:03}')
        self.snapshots.append(point)
        return point

    def history_view(self, point):
        return self.view/('@'+point.branch)

    def commit(self):
        for name in reversed(self.branches):
            assert self.current == name
            self.control('commit')
            index = self.branches.index(name)
            self.current = self.branches[index-1] if index else 'main'
        self.branches.clear()

    def close(self):
        if self.closed:
            return
        if self.view.is_mount():
            self.cli('unmount', '--storage', str(self.storage), str(self.view))
        try:
            self.daemon.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.daemon.terminate()
            self.daemon.wait(timeout=5)
            raise RuntimeError('BranchFS daemon did not exit after unmount')
        finally:
            self.log.close()
        assert not self.view.is_mount()
        self.closed = True
        shutil.rmtree(self.state_dir)


def verify_tree(view, byte):
    for index in range(100):
        verify_file(view/f'target-{index:06}.dat', b'\0'*4096)
    for index in range(10):
        verify_file(view/f'seed-{index:06}.dat', bytes([byte])*4096)
    return 110


def history_canary(session):
    for index in range(100):
        (session.view/f'target-{index:06}.dat').write_bytes(b'\0'*4096)
    for generation in (1, 2, 3):
        for index in range(10):
            (session.view/f'seed-{index:06}.dat').write_bytes(bytes([generation])*4096)
        session.snapshot()
    for index in range(10):
        (session.view/f'seed-{index:06}.dat').write_bytes(b'\xfe'*4096)
    return [dict(generation=point.generation, files=verify_tree(session.history_view(point), point.generation))
            for point in session.snapshots]


def partial_update(session, root, volume, device, record):
    size = 1024*1024
    path = session.view/'payload.bin'
    initial = bytes([0x31])*size
    path.write_bytes(initial)
    os.chmod(path, 0o644)
    history = [(session.snapshot(), initial)]
    sync_volume(volume)
    record['baseline_storage'] = backing_usage(root)
    sync_volume(volume)
    before = device_stats(device)
    expected, writes = bytearray(initial), []
    for step in range(5):
        start = time.perf_counter_ns()
        edit_ns, offset, payload = edit(path, size, step)
        sync_volume(volume)
        edit_synced_ns = time.perf_counter_ns()-start
        after_edit = device_stats(device)
        expected[offset:offset+4096] = payload
        start = time.perf_counter_ns()
        point = session.snapshot()
        snapshot_ns = time.perf_counter_ns()-start
        history.append((point, bytes(expected)))
        sync_volume(volume)
        after_snapshot = device_stats(device)
        writes.append(dict(step=step, offset=offset, bytes=4096, edit_ns=edit_ns,
            edit_synced_ns=edit_synced_ns, snapshot_ns=snapshot_ns,
            edit_write_bytes=write_delta(before, after_edit),
            snapshot_write_bytes=write_delta(after_edit, after_snapshot),
            total_write_bytes=write_delta(before, after_snapshot),
            device_before=before, device_after_edit=after_edit, device_after_snapshot=after_snapshot))
        before = after_snapshot
    record['writes'] = writes
    record['held_storage'] = backing_usage(root)
    verify_file(path, expected)
    record['history_checks'] = []
    for point, contents in history:
        verify_file(session.history_view(point)/'payload.bin', contents)
        record['history_checks'].append(dict(generation=point.generation, bytes=size))
    record['final_validation'] = dict(bytes=size, mode='0644', history_versions=6)


def one(args, item):
    root = getattr(args, item['filesystem'])/f's{item["serial"]:06}'
    record, session = dict(item, model_calls=0), None
    volume = None
    try:
        if item['kind'] == 'resources':
            (args.cgroup/'cgroup.procs').write_text(str(os.getpid()))
        started = time.perf_counter_ns()
        workspace = root/'data'
        workspace.mkdir(parents=True)
        session = BranchSession(args.branchfs, workspace, root/'state',
                                args.output/'daemon'/f'{item["serial"]:06}.log')
        record['setup_ns'] = time.perf_counter_ns()-started
        record['mountinfo'] = next(line for line in Path('/proc/self/mountinfo').read_text().splitlines()
                                   if line.split()[4] == str(session.view))
        assert ' - fuse.' in record['mountinfo'] or ' - fuse ' in record['mountinfo']
        if item['phase'] == 'history-canary':
            record['history_checks'] = history_canary(session)
            session.close()
        elif item['kind'] == 'io':
            volume = os.open(getattr(args, item['filesystem']), os.O_RDONLY | os.O_DIRECTORY)
            partial_update(session, root, volume, args.devices[item['filesystem']], record)
            session.close()
        else:
            if item['kind'] == 'resources' and not item['snapshots_enabled']:
                generation = 0
                def no_snapshot():
                    nonlocal generation
                    generation += 1
                    return SimpleNamespace(generation=generation)
                session.snapshot = no_snapshot
            if item['kind'] == 'resources':
                record['setup'] = observe(root, args.cgroup)
                record['daemon_in_worker_cgroup'] = str(session.daemon.pid) in (args.cgroup/'cgroup.procs').read_text().split()
                assert record['daemon_in_worker_cgroup']
            elapsed, rows = common.execute(session, item['depth'], args.perf)
            record.update(operation_ns=elapsed, snapshots=rows,
                          snapshot_total_ns=sum(row['snapshot_ns'] for row in rows))
            if item['kind'] == 'resources':
                time.sleep(.03)
                record['held'] = observe(root, args.cgroup)
                record['checkpoints_created'] = len(session.snapshots)
                assert len(session.snapshots) == (item['depth'] if item['snapshots_enabled'] else 0)
            begin = time.perf_counter_ns()
            session.commit()
            session.close()
            record['finish_ns'] = time.perf_counter_ns()-begin
            record['lifecycle_ns'] = record['setup_ns']+elapsed+record['finish_ns']
            record['validation'] = dict(validated_files=common.validate(workspace), validated_bytes=110*4096)
        record['status'] = 'passed'
    except BaseException:
        record.update(status='failed', error=traceback.format_exc())
    finally:
        if session:
            try:
                session.close()
            except BaseException:
                record.update(status='failed', cleanup_error=traceback.format_exc())
        if volume is not None:
            os.close(volume)
        if root.exists() and not record.get('cleanup_error'):
            assert not any(path.is_mount() for path in (root/'state/view',))
            shutil.rmtree(root)
        record['cleanup_passed'] = not root.exists()
        common.atomic(args.output/'sessions'/f'{item["serial"]:06}.json', record)
    return record


def plan(smoke):
    items, rng = [], random.Random(20261117)
    for fs in FILESYSTEMS:
        items.append(dict(filesystem=fs, kind='checkpoint', phase='history-canary', depth=3, repeat=-2))
    for repeat in range(-1, 0 if smoke else 20):
        batch = [dict(filesystem=fs, kind='io', phase='warmup' if repeat < 0 else 'measure',
                      size_kib=1024, steps=5, repeat=repeat) for fs in FILESYSTEMS]
        batch += [dict(filesystem=fs, kind='checkpoint', phase='warmup' if repeat < 0 else 'measure',
                       depth=d, repeat=repeat) for fs in FILESYSTEMS for d in DEPTHS]
        rng.shuffle(batch)
        items.extend(batch)
    for repeat in range(-1, 0 if smoke else 10):
        pairs = []
        for fs in FILESYSTEMS:
            for d in DEPTHS:
                enabled = [True, False]
                rng.shuffle(enabled)
                pairs.append([dict(filesystem=fs, kind='resources', phase='warmup' if repeat < 0 else 'measure',
                                   depth=d, snapshots_enabled=mode, repeat=repeat) for mode in enabled])
        rng.shuffle(pairs)
        items.extend(item for pair in pairs for item in pair)
    for serial, item in enumerate(items):
        item.update(serial=serial, variant='branchfs')
    return items


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('branchfs', 'perf', 'ext4', 'xfs', 'output', 'cgroup'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--one')
    parser.add_argument('--smoke', action='store_true')
    args = parser.parse_args()
    assert os.geteuid() == 0
    args.devices, backing = {}, {}
    for fs in FILESYSTEMS:
        path = getattr(args, fs)
        info = json.loads(subprocess.check_output(['findmnt', '-J', '-T', str(path.parent),
            '-o', 'TARGET,SOURCE,FSTYPE,OPTIONS'], text=True))['filesystems'][0]
        assert info['fstype'] == fs and info['source'].startswith('/dev/loop')
        args.devices[fs] = Path(info['source']).name
        backing[fs] = dict(mount=info)
        if fs == 'xfs':
            backing[fs]['xfs_info'] = subprocess.check_output(['xfs_info', info['target']], text=True)
            assert 'reflink=1' in backing[fs]['xfs_info']
    if args.one:
        raise SystemExit(one(args, json.loads(args.one))['status'] != 'passed')
    assert not args.output.exists()
    args.output.mkdir(parents=True)
    (args.output/'daemon').mkdir()
    assert not args.cgroup.exists()
    args.cgroup.mkdir()
    (args.cgroup/'cgroup.subtree_control').write_text('+memory')
    for fs in FILESYSTEMS:
        getattr(args, fs).mkdir()
    assert args.ext4.stat().st_dev != args.xfs.stat().st_dev
    items = plan(args.smoke)
    sources = [Path(__file__), Path(common.__file__), args.branchfs, args.perf,
        Path(__file__).with_name('bench_rq5_partial_overwrite.py'),
        Path(__file__).with_name('bench_rq5_backing_filesystems.py'),
        Path(__file__).with_name('rq5_storage_accounting.py')]
    hashes = {str(path):common.sha(path) for path in sources}
    common.atomic(args.output/'manifest.json',dict(plan=items, kernel=os.uname().release,
        artifact=dict(repository='https://github.com/multikernel/branchfs', commit=COMMIT,
                      filesystem_source_modified=False, passthrough=False),
        sources=hashes, backing=backing, model_calls=0, protocol=dict(
            sizes_kib=[1024], steps=5, edit_bytes=4096, non_truncating=True,
            depths=DEPTHS, checkpoint_repeats=20, io_repeats=20, resource_pairs=10, warmups=1,
            snapshot='Native create + switch via .branchfs_ctl writes in the worker; preserve old branches.',
            finish='Native leaf-to-parent commits through main, unmount, daemon exit, storage cleanup.',
            latency='Original checkpoint-scaling Rust workload: 100 target + 10 overwritten seed files, 4 KiB.',
            lifecycle='Preparation, initialization, writes, checkpoints, native publication and cleanup; verification excluded.',
            io='Five non-truncating 4 KiB pwrite edits to 1 MiB; underlying volume syncfs and block counters as existing RQ5.',
            resources='Enabled-minus-disabled of held-minus-initialized growth; fresh worker cgroup includes BranchFS daemon.',
            storage='FIEMAP physical union + non-data blocks; exclude FUSE view; sample outside latency tests.',
            sampling='Randomize filesystems and workloads; adjacent resource pairs; retain every formal sample.')))
    for item in items:
        common.atomic(args.output/'progress.json',dict(state='running', completed=item['serial'],total=len(items),current=item))
        group = args.cgroup/f's{item["serial"]:06}'
        group.mkdir()
        command = [sys.executable, str(Path(__file__).resolve())]
        for name in ('branchfs','perf','ext4','xfs','output'):
            command += ['--'+name,str(getattr(args,name))]
        command += ['--cgroup',str(group),'--one',json.dumps(item)]
        result = subprocess.run(command,capture_output=True,text=True,timeout=180)
        record_path = args.output/'sessions'/f'{item["serial"]:06}.json'
        if not record_path.exists():
            common.atomic(record_path,dict(item,status='failed',error=result.stdout+result.stderr))
        record = json.loads(record_path.read_text())
        group.rmdir()
        if result.returncode or record['status'] != 'passed':
            common.atomic(args.output/'progress.json',dict(state='failed',completed=item['serial']+1,
                total=len(items),current=item,error=record.get('error')))
            raise RuntimeError(record.get('error',result.stdout+result.stderr))
        if (item['serial']+1)%10 == 0:
            print(json.dumps(dict(completed=item['serial']+1,total=len(items),current=item)),flush=True)
    assert all(common.sha(path)==value for path,value in hashes.items())
    for fs in FILESYSTEMS:
        getattr(args,fs).rmdir()
    args.cgroup.rmdir()
    common.atomic(args.output/'complete.json',dict(passed=len(items),failed=0,
        measured=sum(item['phase']=='measure' for item in items),model_calls=0,all_source_hashes_unchanged=True))
    common.atomic(args.output/'progress.json',dict(state='complete',completed=len(items),total=len(items)))


if __name__ == '__main__':
    main()
