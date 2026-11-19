import os
from pathlib import Path
import shutil
import stat
import subprocess

import pytest

from agenttx import layers
from agenttx.layers import LayerStore


@pytest.fixture(autouse=True)
def exercise_batching_with_small_test_fixtures(monkeypatch):
    monkeypatch.setattr(layers, "_BATCH_COPY_MIN_FILES", 128)


def batch(tmp_path):
    upper = tmp_path / "upper"
    upper.mkdir()
    store = LayerStore(tmp_path / "layers")
    store.snapshot_before(0, upper)
    paths = []
    for index in range(280):
        name = f"file {index}\n.dat"
        (upper / name).write_bytes(bytes([index % 256]) * 4096)
        paths.append("/" + name)
    return upper, store, paths


def test_batched_capture_retains_content_metadata_and_independent_live_inodes(tmp_path):
    upper, store, paths = batch(tmp_path)
    file = upper / paths[0].lstrip("/")
    file.chmod(0o640)
    os.utime(file, ns=(1234567890000000000, 1234567890123456789))
    has_xattr = True
    try:
        os.setxattr(file, "user.agenttx-test", b"snapshot metadata")
    except OSError:
        has_xattr = False
    saved = store.snapshot_before(1, upper, changed_paths=paths)
    captured = saved / file.name
    assert captured.read_bytes() == file.read_bytes()
    assert stat.S_IMODE(captured.stat().st_mode) == 0o640
    assert captured.stat().st_mtime_ns == 1234567890123456789
    if has_xattr:
        assert os.getxattr(captured, "user.agenttx-test") == b"snapshot metadata"
    assert captured.stat().st_ino != file.stat().st_ino
    file.write_bytes(b"changed")
    assert captured.read_bytes() == b"\x00" * 4096
    store.restore_before(1, upper)
    for index, logical in enumerate(paths):
        assert (upper / logical.lstrip("/")).read_bytes() == bytes([index % 256]) * 4096
    assert not list((store.root / "blobs").glob(".capture-*"))


@pytest.mark.skipif(shutil.which("cp") is None, reason="requires native batch copy")
def test_staged_files_publish_without_an_extra_temporary_inode(tmp_path, monkeypatch):
    upper, store, paths = batch(tmp_path)

    def unexpected_temporary_file(*args, **kwargs):
        raise AssertionError("a complete staged file can be renamed directly into the blob store")

    monkeypatch.setattr(layers.tempfile, "mkstemp", unexpected_temporary_file)
    saved = store.snapshot_before(1, upper, changed_paths=paths)
    assert (saved / paths[-1].lstrip("/")).read_bytes() == bytes([279 % 256]) * 4096


@pytest.mark.parametrize("failure", ["returncode", "oserror"])
def test_native_batch_failure_uses_complete_python_capture(tmp_path, monkeypatch, failure):
    upper, store, paths = batch(tmp_path)
    native = subprocess.run

    def fail_batch(args, **kwargs):
        if "--reflink=auto" in args:
            if failure == "oserror":
                raise OSError("native copy unavailable")
            return subprocess.CompletedProcess(args, 1, stderr=b"failed")
        return native(args, **kwargs)

    monkeypatch.setattr(layers.subprocess, "run", fail_batch)
    saved = store.snapshot_before(1, upper, changed_paths=paths)
    assert len(list(saved.iterdir())) == len(paths)
    assert (saved / paths[-1].lstrip("/")).read_bytes() == bytes([279 % 256]) * 4096
    assert not list((store.root / "blobs").glob(".capture-*"))


def test_source_changed_after_batch_copy_is_recaptured(tmp_path, monkeypatch):
    upper, store, paths = batch(tmp_path)
    target = upper / paths[0].lstrip("/")
    copy_entry = layers._copy_snapshot_entry

    def modify_after_staging(source, *args, **kwargs):
        if source == target:
            target.write_text("updated after staging")
        return copy_entry(source, *args, **kwargs)

    monkeypatch.setattr(layers, "_copy_snapshot_entry", modify_after_staging)
    saved = store.snapshot_before(1, upper, changed_paths=paths)
    assert (saved / target.name).read_text() == "updated after staging"


def test_source_removed_during_batched_capture_retries_without_partial_snapshot(tmp_path, monkeypatch):
    upper, store, paths = batch(tmp_path)
    target = upper / paths[0].lstrip("/")
    copy_entry = layers._copy_snapshot_entry

    def remove_after_staging(source, *args, **kwargs):
        if source == target and target.exists():
            target.unlink()
        return copy_entry(source, *args, **kwargs)

    monkeypatch.setattr(layers, "_copy_snapshot_entry", remove_after_staging)
    saved = store.snapshot_before(1, upper, changed_paths=paths)
    assert not (saved / target.name).exists()
    assert len(list(saved.iterdir())) == 279
    assert not list((store.root / "blobs").glob(".capture-*"))


@pytest.mark.skipif(os.geteuid() != 0, reason="requires preserving a different task uid/gid")
def test_batched_capture_preserves_owner_and_setid_mode(tmp_path):
    upper, store, paths = batch(tmp_path)
    target = upper / paths[0].lstrip("/")
    os.chown(target, 1100, 1100)
    target.chmod(0o4755)
    saved = store.snapshot_before(1, upper, changed_paths=paths)
    captured = (saved / target.name).stat()
    assert (captured.st_uid, captured.st_gid) == (1100, 1100)
    assert stat.S_IMODE(captured.st_mode) == 0o4755


def test_batch_directory_and_children_are_captured_once_with_correct_versions(tmp_path):
    upper, store, paths = batch(tmp_path)
    directory = upper / "group"
    directory.mkdir()
    for logical in paths:
        shutil.move(upper / logical.lstrip("/"), directory)
    paths = ["/group" + logical for logical in paths]
    saved = store.snapshot_before(1, upper, changed_paths=["/group", *paths])
    assert len(list((saved / "group").iterdir())) == 280
    assert len(list((store.root / "blobs").glob("*/*"))) == 280
    (directory / paths[1].split("/")[-1]).unlink()
    assert (saved / paths[1].lstrip("/")).exists()
