"""Native delta capture must preserve immutable and selectively restorable state."""

import os
from pathlib import Path
import shutil
import stat
import subprocess

import pytest

from agenttx import layers
from agenttx.layers import LayerStore


@pytest.fixture(autouse=True)
def small_native_batches(monkeypatch):
    if shutil.which("cp") is None:
        pytest.skip("requires native batch copy")
    monkeypatch.setattr(layers, "_BATCH_DELTA_MIN_FILES", 16)


def fixture(tmp_path):
    upper = tmp_path / "upper"
    upper.mkdir()
    store = LayerStore(tmp_path / "history", defer_materialization=True)
    store.snapshot_before(0, upper)
    paths = []
    for index in range(32):
        logical = f"/app/file {index}\n.dat"
        path = upper / logical.lstrip("/")
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(bytes([index]) * 4096)
        paths.append(logical)
    return store, upper, paths


def test_bulk_history_restores_content_metadata_and_independent_inodes(tmp_path):
    store, upper, paths = fixture(tmp_path)
    target = upper / paths[0].lstrip("/")
    target.chmod(0o640)
    os.utime(target, ns=(1234567890000000000, 1234567890123456789))
    try:
        os.setxattr(target, "user.agenttx-test", b"immutable delta")
        has_xattr = True
    except OSError:
        has_xattr = False
    saved = store.snapshot_before(1, upper, changed_paths=paths)
    captured = saved / paths[0].lstrip("/")
    assert captured.stat().st_ino != target.stat().st_ino
    assert stat.S_IMODE(captured.stat().st_mode) == 0o640
    assert captured.stat().st_mtime_ns == 1234567890123456789
    if has_xattr:
        assert os.getxattr(captured, "user.agenttx-test") == b"immutable delta"
    target.write_bytes(b"bad")
    independent = upper / "independent"
    independent.write_text("keep")
    LayerStore(store.root).restore_paths(1, upper, [paths[0]])
    assert target.read_bytes() == b"\0" * 4096
    assert independent.read_text() == "keep"


def test_bulk_directory_delta_reopens_and_keeps_old_versions(tmp_path):
    store, upper, paths = fixture(tmp_path)
    store.snapshot_before(1, upper, changed_paths=["/app", *paths])
    (upper / "other").write_text("independent v1")
    for index, logical in enumerate(paths):
        (upper / logical.lstrip("/")).write_bytes(bytes([index+1]) * 4096)
    store.snapshot_before(2, upper, changed_paths=paths)
    for logical in paths:
        (upper / logical.lstrip("/")).write_text("bad")
    reopened = LayerStore(store.root)
    reopened.restore_paths(2, upper, ["/app"])
    assert (upper / "other").read_text() == "independent v1"
    for index, logical in enumerate(paths):
        assert (upper / logical.lstrip("/")).read_bytes() == bytes([index+1]) * 4096
        assert (reopened.resolve_before(1) / logical.lstrip("/")).read_bytes() == bytes([index]) * 4096
    assert reopened.history_stats()["deltas"] == 1


def test_hardlinked_changes_keep_alias_identity_and_live_independence(tmp_path):
    store, upper, paths = fixture(tmp_path)
    os.link(upper / paths[0].lstrip("/"), upper / "alias")
    store.snapshot_before(1, upper, changed_paths=[*paths, "/alias"])
    restored = store.resolve_before(1)
    assert (restored / "alias").stat().st_ino == (restored / paths[0].lstrip("/")).stat().st_ino
    assert (restored / "alias").stat().st_ino != (upper / "alias").stat().st_ino
    (upper / "alias").write_text("changed live")
    assert (restored / "alias").read_bytes() == b"\0" * 4096


def test_native_regular_batch_can_coexist_with_hardlinked_and_special_changes(tmp_path):
    store, upper, paths = fixture(tmp_path)
    (upper / "original").write_text("shared")
    os.link(upper / "original", upper / "alias")
    os.mkfifo(upper / "pipe", 0o640)
    store.snapshot_before(1, upper, changed_paths=[*paths, "/original", "/alias", "/pipe"])
    saved = store.resolve_before(1)
    assert (saved / "original").stat().st_ino == (saved / "alias").stat().st_ino
    assert stat.S_ISFIFO((saved / "pipe").stat().st_mode)
    (upper / "original").write_text("live update")
    assert (saved / "original").read_text() == "shared"
    for index, logical in enumerate(paths):
        assert (saved / logical.lstrip("/")).read_bytes() == bytes([index]) * 4096


@pytest.mark.skipif(os.geteuid() != 0, reason="requires native OverlayFS whiteouts")
def test_bulk_rename_style_batch_keeps_native_whiteout_and_file_history(tmp_path):
    store, upper, paths = fixture(tmp_path)
    os.mknod(upper / "deleted", stat.S_IFCHR, os.makedev(0, 0))
    store.snapshot_before(1, upper, changed_paths=[*paths, "/deleted"])
    saved = store.resolve_before(1)
    metadata = (saved / "deleted").lstat()
    assert stat.S_ISCHR(metadata.st_mode) and metadata.st_rdev == 0
    (upper / "deleted").unlink()
    (upper / "deleted").write_text("new")
    store.restore_paths(1, upper, ["/deleted", paths[0]])
    assert stat.S_ISCHR((upper / "deleted").lstat().st_mode)
    assert (upper / paths[0].lstrip("/")).read_bytes() == b"\0" * 4096


@pytest.mark.parametrize("failure", ["returncode", "oserror"])
def test_native_copy_failure_discards_partial_payload_and_falls_back(tmp_path, monkeypatch, failure):
    store, upper, paths = fixture(tmp_path)
    native = subprocess.run

    def fail(argv, **kwargs):
        if "--parents" in argv:
            dest = Path(argv[argv.index("--target-directory")+1])
            (dest / "must-not-survive").write_text("partial copy")
            if failure == "oserror":
                raise OSError("native copy unavailable")
            return subprocess.CompletedProcess(argv, 1, stderr=b"copy failed")
        return native(argv, **kwargs)

    monkeypatch.setattr(layers.subprocess, "run", fail)
    saved = store.snapshot_before(1, upper, changed_paths=paths)
    assert not (saved / "must-not-survive").exists()
    for index, logical in enumerate(paths):
        assert (saved / logical.lstrip("/")).read_bytes() == bytes([index]) * 4096


def test_source_change_during_native_copy_is_recaptured(tmp_path, monkeypatch):
    store, upper, paths = fixture(tmp_path)
    target = upper / paths[0].lstrip("/")
    native = subprocess.run

    def change_after_copy(argv, **kwargs):
        result = native(argv, **kwargs)
        if "--parents" in argv:
            target.write_text("changed during copy")
        return result

    monkeypatch.setattr(layers.subprocess, "run", change_after_copy)
    saved = store.snapshot_before(1, upper, changed_paths=paths)
    assert (saved / paths[0].lstrip("/")).read_text() == "changed during copy"


def test_delta_tombstone_does_not_leak_a_deleted_file(tmp_path):
    store, upper, paths = fixture(tmp_path)
    store.snapshot_before(1, upper, changed_paths=paths)
    (upper / paths[0].lstrip("/")).unlink()
    for logical in paths[1:]:
        (upper / logical.lstrip("/")).write_bytes(b"new")
    store.snapshot_before(2, upper, changed_paths=paths)
    restored = store.resolve_before(2)
    assert not (restored / paths[0].lstrip("/")).exists()
    assert (restored / paths[1].lstrip("/")).read_bytes() == b"new"


@pytest.mark.skipif(os.geteuid() != 0, reason="requires task uid/gid preservation")
def test_native_delta_preserves_owner_and_setid(tmp_path):
    store, upper, paths = fixture(tmp_path)
    target = upper / paths[0].lstrip("/")
    os.chown(target, 1100, 1200)
    target.chmod(0o4750)
    saved = store.snapshot_before(1, upper, changed_paths=paths)
    metadata = (saved / paths[0].lstrip("/")).stat()
    assert (metadata.st_uid, metadata.st_gid) == (1100, 1200)
    assert stat.S_IMODE(metadata.st_mode) == 0o4750


@pytest.mark.parametrize("failed_copy", [False, True])
def test_replay_across_small_directories_preserves_existing_directory_modes(tmp_path, monkeypatch, failed_copy):
    monkeypatch.setattr(layers, "_BATCH_LINK_MIN_FILES", 16)
    native = subprocess.run
    seen = []
    def copy(argv, **kwargs):
        seen.append(argv)
        if failed_copy:
            native(argv, **kwargs)
            return subprocess.CompletedProcess(argv, 1, stderr=b"interrupted after prefix")
        return native(argv, **kwargs)
    monkeypatch.setattr(layers.subprocess, "run", copy)
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    paths = []
    for group in range(8):
        parent = f"app/dir-{group}"
        (source / parent).mkdir(parents=True)
        (source / parent).chmod(0o750)
        if group < 4:
            (target / parent).mkdir(parents=True)
            (target / parent).chmod(0o711)
        for item in range(4):
            relative = f"{parent}/file-{item}"
            (source / relative).write_text(relative)
            paths.append("/" + relative)
    (target / "app/independent").write_text("retain")
    LayerStore._apply_versions(source, target, paths)
    assert len(seen) == 1
    assert "--parents" in seen[0]
    for logical in paths:
        relative = logical.lstrip("/")
        assert (target / relative).read_text() == relative
        assert (target / relative).stat().st_ino == (source / relative).stat().st_ino
    for group in range(8):
        expected = 0o711 if group < 4 else 0o750
        assert stat.S_IMODE((target / f"app/dir-{group}").stat().st_mode) == expected
    assert (target / "app/independent").read_text() == "retain"
