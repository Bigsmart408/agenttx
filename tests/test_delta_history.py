"""Immutable delta history must retain the same rollback semantics as full trees."""
import json
import os
import shutil
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from agenttx.layers import LayerStore, _clone_snapshot_tree_python


def setup_store(tmp_path):
    upper = tmp_path / "upper"
    (upper / "app").mkdir(parents=True)
    (upper / "app/a").write_text("a0")
    (upper / "app/b").write_text("b0")
    store = LayerStore(tmp_path / "layers", defer_materialization=True)
    store.snapshot_before(0, upper)
    return store, upper


def test_capture_and_selective_restore_never_clone_unrelated_history(tmp_path):
    store, upper = setup_store(tmp_path)
    with patch("agenttx.layers._clone_snapshot_tree", side_effect=AssertionError("full clone")):
        (upper / "app/a").write_text("a1")
        store.snapshot_before(1, upper, changed_paths=["/app/a"])
        (upper / "app/b").write_text("b2")
        store.snapshot_before(2, upper, changed_paths=["/app/b"])
        store.snapshot_before(3, upper, changed_paths=[])
        (upper / "app/a").write_text("bad")
        store.restore_paths(3, upper, ["/app/a"])
        assert (upper / "app/a").read_text() == "a1"
        assert (upper / "app/b").read_text() == "b2"
        store.restore_paths(1, upper, ["/app/b"])
        assert (upper / "app/b").read_text() == "b0"
    assert store.history_stats() == {"points": 4, "deltas": 2, "aliases": 1, "materialized": 1}


def test_directory_restore_combines_multiple_deltas_and_is_cached(tmp_path):
    store, upper = setup_store(tmp_path)
    (upper / "app/a").write_text("a1")
    store.snapshot_before(1, upper, changed_paths=["/app/a"])
    (upper / "app/b").unlink()
    (upper / "app/c").write_text("c2")
    store.snapshot_before(2, upper, changed_paths=["/app/b", "/app/c"])
    (upper / "app/a").write_text("bad")
    (upper / "unrelated").write_text("keep")
    store.restore_paths(2, upper, ["/app"])
    assert sorted(p.name for p in (upper / "app").iterdir()) == ["a", "c"]
    assert (upper / "app/a").read_text() == "a1"
    assert (upper / "unrelated").read_text() == "keep"
    assert store.history_stats()["materialized"] == 1
    store.resolve_before(2)
    assert store.history_stats()["materialized"] == 2
    with patch("agenttx.layers._clone_snapshot_tree", side_effect=AssertionError("duplicate clone")):
        assert (store.resolve_before(2) / "app/c").read_text() == "c2"


def test_rename_delete_recreate_symlink_and_whiteout(tmp_path):
    store, upper = setup_store(tmp_path)
    (upper / "app/a").rename(upper / "app/moved")
    (upper / "app/b").unlink()
    (upper / "app/.wh.b").touch()
    (upper / "app/link").symlink_to("moved")
    store.snapshot_before(1, upper, changed_paths=["/app/a", "/app/b", "/app/moved", "/app/link"])
    (upper / "app/.wh.b").unlink()
    (upper / "app/b").mkdir()
    (upper / "app/b/child").write_text("new type")
    store.snapshot_before(2, upper, changed_paths=["/app/b", "/app/b/child"])
    first = store.resolve_before(1)
    assert not (first / "app/a").exists()
    assert (first / "app/moved").read_text() == "a0"
    assert (first / "app/.wh.b").exists()
    assert (first / "app/link").is_symlink()
    second = store.resolve_before(2)
    assert not (second / "app/.wh.b").exists()
    assert (second / "app/b/child").read_text() == "new type"


def test_reopen_and_copy_preserve_relative_history_and_hardlinks(tmp_path):
    store, upper = setup_store(tmp_path)
    (upper / "app/a").write_text("a1")
    os.link(upper / "app/a", upper / "app/alias")
    store.snapshot_before(1, upper, changed_paths=["/app/a", "/app/alias"])
    store.snapshot_before(2, upper, changed_paths=[])
    cloned_root = tmp_path / "cloned"
    store.copy_tree(store.root, cloned_root)
    cloned = LayerStore(cloned_root)
    store.copy_tree(tmp_path / "missing", upper)
    cloned.restore_paths(2, upper, ["/app/a", "/app/alias"])
    assert (upper / "app/a").read_text() == "a1"
    assert (upper / "app/a").stat().st_ino == (upper / "app/alias").stat().st_ino
    assert (cloned.resolve_before(2) / "app/b").read_text() == "b0"


def test_gc_keeps_delta_ancestors_until_last_reference_is_dropped(tmp_path):
    store, upper = setup_store(tmp_path)
    (upper / "app/a").write_text("a1")
    store.snapshot_before(1, upper, changed_paths=["/app/a"])
    (upper / "app/b").write_text("b2")
    store.snapshot_before(2, upper, changed_paths=["/app/b"])
    store.snapshot_before(3, upper, changed_paths=[])
    store.drop_from([0, 1, 2])
    reopened = LayerStore(store.root)
    reopened.restore_paths(3, upper, ["/app/a"])
    assert (upper / "app/a").read_text() == "a1"
    reopened.drop_from([3])
    assert not list(store.root.glob("before_*"))
    assert not list((store.root / ".history").glob("data_*"))
    assert not list((store.root / "blobs").glob("*/*"))


def test_missing_delta_payload_fails_before_mutating_upper(tmp_path):
    store, upper = setup_store(tmp_path)
    (upper / "app/a").write_text("a1")
    store.snapshot_before(1, upper, changed_paths=["/app/a"])
    shutil.rmtree(store._payload(1))
    with pytest.raises(FileNotFoundError):
        store.restore_paths(1, upper, ["/app/a"])
    assert (upper / "app/a").read_text() == "a1"


def test_failed_capture_never_publishes_partial_delta(tmp_path):
    store, upper = setup_store(tmp_path)
    with patch("agenttx.layers._replay_snapshot_paths", side_effect=OSError("I/O failure")):
        with pytest.raises(OSError, match="I/O failure"):
            store.snapshot_before(1, upper, changed_paths=["/app/a"])
    assert not (store.root / "before_0001").exists()
    assert not store._payload(1).exists()
    assert not store._record_path(1).exists()
    assert (store.resolve_before(0) / "app/a").read_text() == "a0"


def test_restrictive_ancestors_and_selected_directory_modes_survive(tmp_path):
    store, upper = setup_store(tmp_path)
    directory = upper / "app/locked"
    directory.mkdir()
    (directory / "child").write_text("data")
    directory.chmod(0)
    store.snapshot_before(1, upper, changed_paths=["/app/locked"])
    directory.chmod(0o700)
    (directory / "child").write_text("bad")
    (upper / "app").chmod(0o711)
    store.restore_paths(1, upper, ["/app/locked"])
    assert stat.S_IMODE(directory.stat().st_mode) == 0
    assert stat.S_IMODE((upper / "app").stat().st_mode) == 0o711
    directory.chmod(0o700)
    assert (directory / "child").read_text() == "data"
    built = store.resolve_before(1)
    assert stat.S_IMODE((built / "app/locked").stat().st_mode) == 0
    from agenttx.layers import _remove_overlay_tree
    _remove_overlay_tree(store.root)


def test_new_ancestor_metadata_survives_selective_restore(tmp_path):
    store, upper = setup_store(tmp_path)
    (upper / "app/new").mkdir(mode=0o711)
    (upper / "app/new/file").write_text("v1")
    store.snapshot_before(1, upper, changed_paths=["/app/new/file"])
    shutil.rmtree(upper / "app/new")
    store.restore_paths(1, upper, ["/app/new/file"])
    assert stat.S_IMODE((upper / "app/new").stat().st_mode) == 0o711
    assert (upper / "app/new/file").read_text() == "v1"


def test_python_clone_preserves_mode_zero_directories(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "file").write_text("x")
    source.chmod(0)
    try:
        _clone_snapshot_tree_python(source, tmp_path / "clone")
        assert stat.S_IMODE((tmp_path / "clone").stat().st_mode) == 0
        assert stat.S_IMODE(source.stat().st_mode) == 0
    finally:
        source.chmod(0o700)
        (tmp_path / "clone").chmod(0o700)


def test_directory_restore_never_clones_an_unselected_subtree(tmp_path):
    from agenttx import layers
    store, upper = setup_store(tmp_path)
    (upper / "app/small").mkdir()
    (upper / "app/small/old").write_text("keep")
    store.snapshot_before(1, upper, changed_paths=["/app/small"])
    (upper / "app/small/new").write_text("new")
    (upper / "app/a").write_text("independent")
    store.snapshot_before(2, upper, changed_paths=["/app/small/new", "/app/a"])
    (upper / "app/small/old").write_text("bad")
    original = layers._clone_snapshot_tree
    def selected_only(source, target):
        assert source.name == "small", "unrelated history must not be cloned"
        original(source, target)
    with patch.object(layers, "_clone_snapshot_tree", side_effect=selected_only):
        store.restore_paths(2, upper, ["/app/small"])
    assert (upper / "app/small/old").read_text() == "keep"
    assert (upper / "app/small/new").read_text() == "new"
    assert (upper / "app/a").read_text() == "independent"
    assert store.history_stats()["materialized"] == 1
    assert not list(store.root.glob(".selected-history-*"))


@pytest.mark.skipif(os.geteuid() != 0, reason="requires chown and mknod")
def test_delta_restores_ownership_setid_and_native_whiteout(tmp_path):
    store, upper = setup_store(tmp_path)
    owned = upper / "app/owned"
    owned.mkdir()
    os.chown(owned, 1234, 2345)
    owned.chmod(0o2710)
    file = owned / "file"
    file.write_text("data")
    os.chown(file, 1234, 2345)
    file.chmod(0o4750)
    whiteout = upper / "app/gone"
    os.mknod(whiteout, stat.S_IFCHR, os.makedev(0, 0))
    store.snapshot_before(1, upper, changed_paths=["/app/owned", "/app/gone"])
    os.chown(owned, 0, 0)
    owned.chmod(0o700)
    file.write_text("bad")
    whiteout.unlink()
    whiteout.write_text("bad")
    store.restore_paths(1, upper, ["/app/owned", "/app/gone"])
    assert (owned.stat().st_uid, owned.stat().st_gid, stat.S_IMODE(owned.stat().st_mode)) == (1234, 2345, 0o2710)
    assert (file.stat().st_uid, file.stat().st_gid, stat.S_IMODE(file.stat().st_mode)) == (1234, 2345, 0o4750)
    assert stat.S_ISCHR(whiteout.lstat().st_mode) and whiteout.lstat().st_rdev == 0


def test_missing_descriptor_is_not_treated_as_an_empty_full_snapshot(tmp_path):
    store, upper = setup_store(tmp_path)
    (upper / "app/a").write_text("a1")
    store.snapshot_before(1, upper, changed_paths=["/app/a"])
    store._record_path(1).unlink()
    with pytest.raises(FileNotFoundError):
        store.restore_paths(1, upper, ["/app/a"])
    assert (upper / "app/a").read_text() == "a1"


def test_capture_after_empty_base_is_already_a_complete_version(tmp_path):
    upper = tmp_path / "upper"
    upper.mkdir()
    store = LayerStore(tmp_path / "layers", defer_materialization=True)
    store.snapshot_before(0, upper)
    (upper / "app").mkdir()
    (upper / "app/file").write_text("first batch")
    with patch("agenttx.layers._clone_snapshot_tree", side_effect=AssertionError("unnecessary clone")):
        store.snapshot_before(1, upper, changed_paths=["/app/file"])
        first = store.resolve_before(1)
        assert (first / "app/file").read_text() == "first batch"
    assert store.history_stats() == {"points": 2, "deltas": 0, "aliases": 0, "materialized": 2}
    (upper / "app/file").write_text("second batch")
    store.snapshot_before(2, upper, changed_paths=["/app/file"])
    assert store.history_stats()["deltas"] == 1
    assert (store.resolve_before(2) / "app/file").read_text() == "second batch"
    assert (first / "app/file").read_text() == "first batch"


def test_large_history_replay_batches_immutable_links_and_falls_back(tmp_path):
    from agenttx import layers
    from subprocess import CompletedProcess
    for fail in (False, True):
        root = tmp_path / str(fail)
        root.mkdir()
        store, upper = setup_store(root)
        changes = []
        for index in range(300):
            logical = f'/app/file-{index}'
            (upper / logical.lstrip('/')).write_text(f'value-{index}')
            changes.append(logical)
        store.snapshot_before(1, upper, changed_paths=changes)
        real = layers.subprocess.run
        calls = []
        def copied(argv, **kwargs):
            if '--target-directory' in argv and '--link' in argv:
                calls.append(argv)
                if fail:
                    target = Path(argv[argv.index('--target-directory')+1])
                    # Simulate a native copy that partially succeeded.
                    os.link(Path(kwargs['cwd'])/argv[-1], target/argv[-1])
                    return CompletedProcess(argv,1,stderr=b'failure')
            return real(argv,**kwargs)
        with patch.object(layers.subprocess, 'run', side_effect=copied):
            built = store.resolve_before(1)
        assert len(calls) == 1
        assert (built / 'app/a').read_text() == 'a0'
        for index in range(300):
            assert (built / f'app/file-{index}').read_text() == f'value-{index}'


@pytest.mark.skipif(os.geteuid() != 0, reason='requires native whiteouts')
def test_large_whiteout_replay_batches_and_recovers_after_native_failure(tmp_path):
    from agenttx import layers
    import errno
    from subprocess import CompletedProcess
    for fail in (False, True):
        root = tmp_path / str(fail)
        root.mkdir()
        store, upper = setup_store(root)
        changes = []
        for index in range(300):
            logical = f'/app/whiteout-{index}'
            os.mknod(upper / logical.lstrip('/'), stat.S_IFCHR, os.makedev(0, 0))
            changes.append(logical)
        store.snapshot_before(1, upper, changed_paths=changes)
        real = layers.subprocess.run
        real_link = layers.os.link
        calls = []
        def copied(argv, **kwargs):
            if '--target-directory' in argv and '--link' in argv:
                calls.append(argv)
                if fail:
                    target = Path(argv[argv.index('--target-directory')+1])
                    real_link(Path(kwargs['cwd'])/argv[-1], target/argv[-1])
                    return CompletedProcess(argv,1,stderr=b'whiteout link limit')
            return real(argv,**kwargs)
        def limited(source, destination, **kwargs):
            if fail and stat.S_ISCHR(os.lstat(source).st_mode):
                raise OSError(errno.EMLINK, 'whiteout link limit')
            return real_link(source, destination, **kwargs)
        with patch.object(layers.subprocess,'run',side_effect=copied), \
                patch.object(layers.os, 'link', side_effect=limited):
            built = store.resolve_before(1)
        assert len(calls) == 1
        for index in range(300):
            metadata = (built / f'app/whiteout-{index}').lstat()
            assert stat.S_ISCHR(metadata.st_mode) and metadata.st_rdev == 0
        assert (built / 'app/a').read_text() == 'a0'
        if fail:
            assert (built / 'app/whiteout-0').lstat().st_ino != (store._payload(1) / 'app/whiteout-0').lstat().st_ino


def test_bulk_replay_supports_a_relative_layer_store_root(tmp_path, monkeypatch):
    from agenttx import layers
    monkeypatch.chdir(tmp_path)
    upper = Path('upper')
    (upper / 'app').mkdir(parents=True)
    (upper / 'app/base').write_text('base')
    store = LayerStore(Path('layers'), defer_materialization=True)
    store.snapshot_before(0, upper)
    paths = []
    for index in range(300):
        logical = f'/app/file-{index}'
        (upper / logical.lstrip('/')).write_text(str(index))
        paths.append(logical)
    store.snapshot_before(1, upper, changed_paths=paths)
    with patch.object(layers, '_link_snapshot_path', side_effect=AssertionError('native batch failed')):
        built = store.resolve_before(1)
    assert (built / 'app/base').read_text() == 'base'
    assert (built / 'app/file-299').read_text() == '299'
