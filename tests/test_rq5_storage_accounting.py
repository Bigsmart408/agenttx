"""Resource reporting must not count shared reflink data twice."""
import errno
import fcntl
import os

import pytest

from experiments.scripts.rq5_storage_accounting import backing_usage, union_bytes


def test_extent_union_handles_partial_overlap_and_nested_versions():
    assert union_bytes([(4096, 8192), (0, 8192), (4096, 12288), (0, 4096)]) == 12288
    assert union_bytes([]) == 0


def test_hardlinks_do_not_add_storage_and_holes_are_not_allocated(tmp_path):
    source = tmp_path/'source'
    with source.open('wb') as stream:
        stream.seek(1024*1024)
        stream.write(b'X'*4096)
    before = backing_usage(tmp_path)
    os.link(source, tmp_path/'alias')
    after = backing_usage(tmp_path)
    assert before['regular_files'] == after['regular_files'] == 1
    assert before['data_unique_bytes'] == after['data_unique_bytes']
    assert 0 < after['data_unique_bytes'] < source.stat().st_size


def test_actual_reflink_counts_physical_allocation_once(tmp_path):
    source, clone = tmp_path/'source', tmp_path/'clone'
    original = bytes(range(256))*4096
    source.write_bytes(original)
    with source.open('rb') as reader, clone.open('wb') as writer:
        try:
            fcntl.ioctl(writer.fileno(), 0x40049409, reader.fileno())
        except OSError as error:
            if error.errno in (errno.EOPNOTSUPP, errno.ENOTTY, errno.EXDEV, errno.EINVAL):
                pytest.skip('requires reflink-enabled backing storage')
            raise
    saved = backing_usage(tmp_path)
    assert saved['regular_files'] == 2
    assert saved['data_reference_bytes'] == 2*len(original)
    assert saved['data_unique_bytes'] == saved['duplicate_extent_bytes'] == len(original)
    with source.open('r+b') as stream:
        stream.write(b'changed!')
    changed = backing_usage(tmp_path)
    assert len(original) < changed['data_unique_bytes'] < 2*len(original)
    assert 0 < changed['duplicate_extent_bytes'] < len(original)
    assert clone.read_bytes() == original
