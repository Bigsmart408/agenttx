"""Run on a real XFS test directory, e.g. pytest --basetemp=/mnt/xfs/tests."""
import fcntl
import os
from pathlib import Path
import stat
import struct
import subprocess
import tempfile

import pytest

from agenttx import snapshot as storage
from agenttx.commit_wal import _copy_host_entry
from agenttx.layers import LayerStore


@pytest.fixture
def xfs_root(tmp_path):
    filesystem = subprocess.check_output(
        ['stat', '-f', '-c', '%T', tmp_path], text=True).strip()
    if filesystem != 'xfs':
        pytest.skip('requires a real XFS test directory')
    return tmp_path


def _extents(path):
    # linux/fiemap.h: header (32 bytes), then fiemap_extent (56 bytes).
    header = struct.Struct('=QQIIII')
    extent = struct.Struct('=QQQQQIIII')
    capacity = 128
    buffer = bytearray(header.size + capacity * extent.size)
    header.pack_into(buffer, 0, 0, (1 << 64)-1, 1, 0, capacity, 0)
    with Path(path).open('rb') as stream:
        fcntl.ioctl(stream.fileno(), 0xC020660B, buffer)
    count = header.unpack_from(buffer)[3]
    assert count < capacity, 'increase the FIEMAP buffer to cover this file'
    return [(values[0], values[1], values[2], values[5])
            for index in range(count)
            for values in [extent.unpack_from(buffer, header.size + index * extent.size)]]


def _shared_bytes(first, second):
    shared = 0
    for logical, physical, length, flags in _extents(first):
        for other_logical, other_physical, other_length, other_flags in _extents(second):
            if (flags & other_flags & 0x2000
                    and physical-logical == other_physical-other_logical):
                shared += max(0, min(logical+length, other_logical+other_length)
                              - max(logical, other_logical))
    return shared


def test_selective_restore_shares_data_and_keeps_independent_progress(xfs_root):
    upper = xfs_root/'upper'
    upper.mkdir()
    original = bytes(range(256))*4096
    current = upper/'affected'
    current.write_bytes(original)
    current.chmod(0o640)
    timestamp = 1_600_000_000_123_456_789
    os.utime(current, ns=(timestamp, timestamp))
    os.setxattr(current, 'user.history', b'keep')
    independent = upper/'independent'
    independent.write_text('before')
    store = LayerStore(xfs_root/'history', defer_materialization=True)
    saved = store.snapshot_before(0, upper)/'affected'
    assert saved.stat().st_ino != current.stat().st_ino
    assert _shared_bytes(saved, current) == len(original)
    with current.open('r+b') as stream:
        stream.write(b'bad!'*1024)
    independent.write_text('retained progress')
    assert saved.read_bytes() == original
    store.restore_paths(0, upper, ['/affected'])
    assert current.read_bytes() == original
    assert independent.read_text() == 'retained progress'
    assert stat.S_IMODE(current.stat().st_mode) == 0o640
    assert current.stat().st_mtime_ns == timestamp
    assert os.getxattr(current, 'user.history') == b'keep'
    assert _shared_bytes(saved, current) == len(original)
    current.write_bytes(b'rewritten after restoration')
    assert saved.read_bytes() == original


def test_commit_backup_keeps_aliases_and_uses_independent_shared_inodes(xfs_root):
    source, backup = xfs_root/'source', xfs_root/'backup'
    source.mkdir()
    original = b'original'*131072
    (source/'first').write_bytes(original)
    os.link(source/'first', source/'alias')
    _copy_host_entry(source, backup)
    assert (backup/'first').stat().st_ino == (backup/'alias').stat().st_ino
    assert (backup/'first').stat().st_ino != (source/'first').stat().st_ino
    assert _shared_bytes(source/'first', backup/'first') == len(original)
    (source/'alias').write_bytes(b'new live contents')
    assert (backup/'first').read_bytes() == original
    assert (backup/'alias').read_bytes() == original


def test_cross_filesystem_copy_falls_back_without_losing_contents(xfs_root):
    source = xfs_root/'source'
    source.write_bytes(bytes(range(256))*4096)
    with tempfile.TemporaryDirectory(prefix='agenttx-cross-fs-') as name:
        target = Path(name)/'target'
        if Path(name).stat().st_dev == xfs_root.stat().st_dev:
            pytest.skip('requires /tmp on a different filesystem')
        storage._copy_file_data(source, target)
        assert target.read_bytes() == source.read_bytes()
        target.write_bytes(b'changed destination')
        assert source.stat().st_size == 1024*1024


@pytest.mark.parametrize('metadata_copy', [False, True])
def test_overlay_copyup_and_snapshots_share_xfs_extents(xfs_root, metadata_copy):
    workspace = xfs_root/'workspace'
    workspace.mkdir()
    original = bytes(range(256))*4096
    base = workspace/'large'
    base.write_bytes(original)
    session = storage.SnapshotSession(workspace, xfs_root/'session',
                                      metadata_copy=metadata_copy)
    try:
        with (session.view/'large').open('r+b') as stream:
            stream.write(b'A'*4096)
        expected = b'A'*4096 + original[4096:]
        assert _shared_bytes(base, session.upperdir/'large') >= len(original)-4096
        point = session.snapshot()
        frozen = point.lowerdirs[0]/'large'
        with (session.view/'large').open('r+b') as stream:
            stream.seek(4096)
            stream.write(b'B'*4096)
        assert _shared_bytes(frozen, session.upperdir/'large') >= len(original)-4096
        assert base.read_bytes() == original
        assert frozen.read_bytes() == expected
        view = xfs_root/'historical-view'
        session.mount_snapshot(point, view)
        try:
            assert (view/'large').read_bytes() == expected
        finally:
            storage._unmount(view)
        session.commit()
        assert base.read_bytes() == b'A'*4096+b'B'*4096+original[8192:]
    finally:
        session.close(destroy=True)
