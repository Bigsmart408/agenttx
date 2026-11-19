import errno
import os
import shutil
import stat

import pytest

from agenttx import snapshot as storage
from agenttx.layers import _copy_regular


@pytest.mark.parametrize('failure', sorted(storage._CLONE_UNAVAILABLE))
def test_unsupported_clone_copies_complete_data_without_a_stale_tail(tmp_path, monkeypatch, failure):
    source, target = tmp_path/'source', tmp_path/'target'
    source.write_bytes(b'correct contents')
    target.write_bytes(b'old contents with a much longer tail')

    def partial_clone(destination, request, original):
        os.write(destination, b'incomplete clone')
        raise OSError(failure, 'clone unavailable')

    monkeypatch.setattr(storage.fcntl, 'ioctl', partial_clone)
    storage._copy_file_data(source, target)
    assert target.read_bytes() == b'correct contents'
    assert source.read_bytes() == b'correct contents'
    assert source.stat().st_ino != target.stat().st_ino


@pytest.mark.parametrize('failure', [errno.EIO, errno.ENOSPC, errno.EACCES, errno.EPERM])
def test_clone_io_and_permission_errors_propagate(tmp_path, monkeypatch, failure):
    source, target = tmp_path/'source', tmp_path/'target'
    source.write_bytes(b'original')

    def fail_clone(*args):
        raise OSError(failure, 'clone failed')

    def unexpected_fallback(*args, **kwargs):
        raise AssertionError('storage and permission errors must not become a fallback copy')

    monkeypatch.setattr(storage.fcntl, 'ioctl', fail_clone)
    monkeypatch.setattr(storage.shutil, 'copyfile', unexpected_fallback)
    with pytest.raises(OSError) as error:
        storage._copy_file_data(source, target)
    assert error.value.errno == failure
    assert source.read_bytes() == b'original'


@pytest.mark.parametrize('alias', [False, True])
def test_same_inode_is_rejected_before_truncation(tmp_path, alias):
    source = tmp_path/'source'
    source.write_bytes(b'keep the source')
    target = tmp_path/'alias' if alias else source
    if alias:
        os.link(source, target)
    with pytest.raises(shutil.SameFileError):
        storage._copy_file_data(source, target)
    assert source.read_bytes() == b'keep the source'


def test_nonregular_source_is_rejected_without_blocking(tmp_path):
    source = tmp_path/'fifo'
    os.mkfifo(source)
    with pytest.raises(shutil.SpecialFileError):
        storage._copy_file_data(source, tmp_path/'target')


def test_destination_symlink_does_not_modify_an_unrelated_file(tmp_path):
    source, original, target = tmp_path/'source', tmp_path/'original', tmp_path/'target'
    source.write_bytes(b'new data')
    original.write_bytes(b'keep this file')
    target.symlink_to(original.name)
    with pytest.raises(OSError) as error:
        storage._copy_file_data(source, target)
    assert error.value.errno == errno.ELOOP
    assert original.read_bytes() == b'keep this file'


def test_real_reflink_keeps_versions_independent_and_replaces_the_full_file(tmp_path, monkeypatch):
    source, target = tmp_path/'source', tmp_path/'target'
    original = bytes(range(256))*4096
    source.write_bytes(original)
    with source.open('rb') as reader, target.open('wb') as writer:
        if not storage._try_reflink(reader.fileno(), writer.fileno()):
            pytest.skip('requires a reflink-enabled backing filesystem')
    target.write_bytes(b'previous contents'*(len(original)//8))

    def unexpected_copy(*args, **kwargs):
        raise AssertionError('a supported reflink must not copy the file data')

    monkeypatch.setattr(storage.shutil, 'copyfile', unexpected_copy)
    storage._copy_file_data(source, target)
    assert target.read_bytes() == original
    assert source.stat().st_ino != target.stat().st_ino
    with source.open('r+b') as writer:
        writer.write(b'live')
    assert target.read_bytes() == original
    with target.open('r+b') as writer:
        writer.seek(4096)
        writer.write(b'history')
    assert source.read_bytes()[4096:4103] == original[4096:4103]


def test_saved_regular_file_retains_metadata_and_independent_contents(tmp_path):
    source, target = tmp_path/'source', tmp_path/'target'
    source.write_bytes(bytes(range(256))*4096)
    source.chmod(0o640)
    timestamp = 1_600_000_000_123_456_789
    os.utime(source, ns=(timestamp, timestamp))
    os.setxattr(source, 'user.agenttx-reflink', b'history metadata')
    _copy_regular(source, target, source.stat().st_mode)
    assert target.read_bytes() == source.read_bytes()
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert target.stat().st_mtime_ns == timestamp
    assert os.getxattr(target, 'user.agenttx-reflink') == b'history metadata'
    assert source.stat().st_ino != target.stat().st_ino
    source.write_bytes(b'changed live data')
    assert target.stat().st_size == 1024*1024
    assert target.read_bytes()[:256] == bytes(range(256))
