import os
from pathlib import Path
from unittest.mock import patch

from agenttx.file_index import UpperdirTopology
from agenttx.semisolate import SharedSemisolate, _iter_upper_entries


def setup_pool(tmp_path):
    pool = object.__new__(SharedSemisolate)
    pool.sandbox_dir = tmp_path / "session"
    pool.workspace = Path("/app")
    upper = pool.sandbox_dir / "upperdir/app"
    upper.mkdir(parents=True)
    return pool, upper


def test_unchanged_directory_listings_are_not_enumerated_again(tmp_path):
    pool, upper = setup_pool(tmp_path)
    for i in range(20):
        directory = upper / str(i)
        directory.mkdir()
        (directory / "file").write_text(str(i))
    first = pool.upperdir_digests()
    enumerations = pool._upper_topology.enumerated_directories
    assert pool.upperdir_digests() == first
    assert pool._upper_topology.enumerated_directories == enumerations
    (upper / "2/file").write_text("changed")
    assert pool.upperdir_digests()["/app/2/file"] != first["/app/2/file"]
    assert pool._upper_topology.enumerated_directories == enumerations


def test_new_subtree_is_added_without_rewalking_old_directories(tmp_path):
    pool, upper = setup_pool(tmp_path)
    old = upper / "old"
    old.mkdir()
    (old / "file").write_text("keep")
    pool.upperdir_digests()
    enumerations = pool._upper_topology.enumerated_directories
    new = upper / "new/a/b"
    new.mkdir(parents=True)
    (new / "file").write_text("new")
    after = pool.upperdir_digests()
    assert "/app/new/a/b/file" in after
    assert "/app/old/file" in after
    assert pool._upper_topology.enumerated_directories == enumerations + 4
    (new / "second").write_text("next command")
    assert "/app/new/a/b/second" in pool.upperdir_digests()


def test_queue_loss_forces_full_rebuild(tmp_path):
    pool, upper = setup_pool(tmp_path)
    pool.upperdir_digests()
    (upper / "new").write_text("found even if event was lost")
    index = pool._upper_topology
    rebuilds = index.rebuilds
    with patch.object(index, "_drain", return_value=(set(), True)):
        assert "/app/new" in pool.upperdir_digests()
    assert index.rebuilds == rebuilds + 1


def test_unavailable_notifications_use_complete_walk(tmp_path):
    pool, upper = setup_pool(tmp_path)
    with patch("agenttx.file_index.ctypes.CDLL", side_effect=OSError("unavailable")):
        pool.upperdir_digests()
    (upper / "new").write_text("detected")
    assert "/app/new" in pool.upperdir_digests()
    assert pool._upper_topology.disabled


def test_same_path_replaced_with_directory_and_back(tmp_path):
    pool, upper = setup_pool(tmp_path)
    target = upper / "item"
    target.write_text("file")
    pool.upperdir_digests()
    target.unlink()
    target.mkdir()
    (target / "child").write_text("nested")
    assert "/app/item/child" in pool.upperdir_digests()
    (target / "child").unlink()
    target.rmdir()
    target.symlink_to("missing")
    result = pool.upperdir_digests()
    assert "/app/item" in result
    assert "/app/item/child" not in result


def test_opaque_marker_keeps_precedence_after_cached_scan(tmp_path):
    pool, upper = setup_pool(tmp_path)
    (upper / "dir").mkdir()
    (upper / "dir/.wh..wh..opq").touch()
    first = pool.upperdir_digests()
    assert first["/app/dir"] == "<agenttx:opaque-directory>"
    assert pool.upperdir_digests() == first


def test_root_replacement_discards_stale_paths(tmp_path):
    pool, upper = setup_pool(tmp_path)
    (upper / "old").write_text("old")
    pool.upperdir_digests()
    root = pool.sandbox_dir / "upperdir"
    root.rename(pool.sandbox_dir / "old-upper")
    upper.mkdir(parents=True)
    (upper / "new").write_text("new")
    result = pool.upperdir_digests()
    assert "/app/old" not in result
    assert "/app/new" in result


def test_mode_zero_ancestors_fall_back_without_losing_children(tmp_path):
    pool, upper = setup_pool(tmp_path)
    locked = upper / "locked"
    locked.mkdir()
    target = locked / "file"
    target.write_text("old")
    pool.upperdir_digests()
    locked.chmod(0)
    try:
        first = pool.upperdir_digests()
        locked.chmod(0o700)
        target.write_text("new")
        locked.chmod(0)
        assert pool.upperdir_digests()["/app/locked/file"] != first["/app/locked/file"]
        assert locked.stat().st_mode & 0o777 == 0
    finally:
        locked.chmod(0o700)


def test_accessible_directory_metadata_changes_do_not_rebuild_topology(tmp_path):
    pool, upper = setup_pool(tmp_path)
    directory = upper / "directory"
    directory.mkdir()
    (directory / "file").write_text("preserved")
    before = pool.upperdir_digests()
    rebuilds = pool._upper_topology.rebuilds
    directory.chmod(0o755 if directory.stat().st_mode & 0o777 != 0o755 else 0o711)
    after = pool.upperdir_digests()
    assert before["/app/directory"] != after["/app/directory"]
    assert "/app/directory/file" in after
    assert pool._upper_topology.rebuilds == rebuilds


def test_dirty_directory_stats_are_reused_only_within_current_scan(tmp_path):
    pool, upper = setup_pool(tmp_path)
    (upper / "old").write_text("old")
    pool.upperdir_digests()
    (upper / "new").write_text("new")
    original = os.lstat
    calls = []
    def counted(path, *args, **kwargs):
        calls.append(os.fspath(path))
        return original(path, *args, **kwargs)
    with patch("agenttx.file_index.os.lstat", side_effect=counted):
        pool.upperdir_digests()
    assert str(upper / "new") not in calls
    assert str(upper / "old") not in calls
    before = pool.upperdir_digests()
    (upper / "old").write_text("changed")
    assert pool.upperdir_digests()["/app/old"] != before["/app/old"]


def test_attribute_changes_do_not_fill_the_topology_queue(tmp_path):
    pool, upper = setup_pool(tmp_path)
    target = upper / 'file'
    target.write_text('content')
    before = pool.upperdir_digests()
    for index in range(10):
        target.chmod(0o600 if index % 2 else 0o644)
    dirty, reset = pool._upper_topology._drain()
    assert not dirty and not reset
    assert pool.upperdir_digests()['/app/file'] != before['/app/file']


def test_restrictive_root_uses_the_access_preserving_walker(tmp_path):
    pool, upper = setup_pool(tmp_path)
    (upper / 'file').write_text('content')
    pool.upperdir_digests()
    root = pool.sandbox_dir / 'upperdir'
    root.chmod(0)
    try:
        assert '/app/file' in pool.upperdir_digests()
        assert root.stat().st_mode & 0o777 == 0
    finally:
        root.chmod(0o700)
