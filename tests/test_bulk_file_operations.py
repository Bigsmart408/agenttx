import os
from pathlib import Path
import stat

from agenttx.layers import LayerStore
from agenttx.semisolate import (
    SharedSemisolate,
    _grant_upper_commit_access,
    _restore_upper_modes,
)


def test_overlapping_directory_and_file_changes_keep_exact_history(tmp_path):
    upper = tmp_path / "upper"
    upper.mkdir()
    store = LayerStore(tmp_path / "layers")
    store.snapshot_before(0, upper)
    folder = upper / "new" / "nested"
    folder.mkdir(parents=True)
    (folder / "first").write_text("first version")
    (folder / "second").write_text("independent")
    snapshot = store.snapshot_before(1, upper, changed_paths=[
        "/new", "/new/nested", "/new/nested/first", "/new/nested/second"
    ])
    assert (snapshot / "new/nested/first").read_text() == "first version"
    (folder / "first").write_text("changed")
    store.restore_paths(1, upper, ["/new/nested/first"])
    assert (folder / "first").read_text() == "first version"
    assert (folder / "second").read_text() == "independent"
    store.restore_before(0, upper)
    assert not (upper / "new").exists()


def test_redundant_leading_slashes_do_not_stall_ancestor_selection(tmp_path):
    upper = tmp_path / "upper"
    upper.mkdir()
    store = LayerStore(tmp_path / "layers")
    store.snapshot_before(0, upper)
    (upper / "value").write_text("new")
    saved = store.snapshot_before(1, upper, changed_paths=["//value"])
    assert (saved / "value").read_text() == "new"
    store.restore_before(0, upper)
    assert not (upper / "value").exists()


def test_commit_access_restores_restrictive_modes_without_touching_normal_files(tmp_path):
    upper = tmp_path / "upper"
    upper.mkdir()
    regular = upper / "regular"
    regular.write_text("unchanged")
    original = regular.stat()
    restricted = upper / "restricted"
    restricted.mkdir()
    hidden = restricted / "hidden"
    hidden.write_text("retained")
    hidden.chmod(0)
    restricted.chmod(0)
    modes = {}
    try:
        _grant_upper_commit_access(upper, modes)
        assert regular not in modes and os.fspath(regular) not in modes
        assert regular.stat().st_ctime_ns == original.st_ctime_ns
        assert hidden.read_text() == "retained"
    finally:
        _restore_upper_modes(modes)
    assert stat.S_IMODE(restricted.stat().st_mode) == 0
    restricted.chmod(0o700)
    assert stat.S_IMODE(hidden.stat().st_mode) == 0
    assert regular.stat().st_ctime_ns == original.st_ctime_ns


def test_capture_commit_metadata_excludes_workspace_name_prefix_neighbor(tmp_path):
    pool = object.__new__(SharedSemisolate)
    pool.sandbox_dir = tmp_path
    pool.workspace = Path("/app")
    upper = tmp_path / "upperdir"
    (upper / "app").mkdir(parents=True)
    (upper / "application").mkdir()
    (upper / "app" / "line\nbreak").write_text("inside")
    (upper / "application" / "outside").write_text("outside")
    metadata = pool._capture_commit_metadata(None)
    assert "/app/line\nbreak" in metadata
    assert "/application/outside" not in metadata
    selected = pool._capture_commit_metadata(["/app/line\nbreak"])
    assert set(selected) == {"/app/line\nbreak"}


def test_combined_commit_walk_records_modes_before_temporarily_granting_access(tmp_path):
    pool = object.__new__(SharedSemisolate)
    pool.sandbox_dir = tmp_path / "session"
    pool.workspace = tmp_path / "workspace"
    upper = pool.sandbox_dir / "upperdir"
    root = upper / str(pool.workspace).lstrip("/")
    root.mkdir(parents=True)
    folder = root / "restricted"
    folder.mkdir()
    target = folder / "file"
    target.write_text("keep")
    target.chmod(0)
    folder.chmod(0)
    modes = {}
    try:
        captured = pool._capture_commit_metadata(None, upper_modes=modes)
        assert captured[str(pool.workspace / "restricted")][0] == 0
        assert captured[str(pool.workspace / "restricted/file")][0] == 0
        assert target.read_text() == "keep"
    finally:
        _restore_upper_modes(modes)
    assert stat.S_IMODE(folder.stat().st_mode) == 0
    folder.chmod(0o700)
    assert stat.S_IMODE(target.stat().st_mode) == 0


def test_fast_fingerprinting_handles_whiteout_names_and_workspace_prefix(tmp_path):
    pool = object.__new__(SharedSemisolate)
    pool.sandbox_dir = tmp_path
    pool.workspace = Path("/app")
    upper = tmp_path / "upperdir"
    (upper / "app" / "empty").mkdir(parents=True)
    (upper / "application" / "empty").mkdir(parents=True)
    (upper / "tmp").mkdir()
    (upper / "tmp" / ".agenttx-strace-internal").touch()
    (upper / "app" / ".wh.deleted").touch()
    (upper / "app" / "empty" / ".wh..wh..opq").touch()
    (upper / "app" / "link").symlink_to("missing target")
    digests = pool.upperdir_digests()
    assert digests["/app/deleted"] == "<agenttx:whiteout>"
    assert digests["/app/empty"] == "<agenttx:opaque-directory>"
    assert "/app/link" in digests
    assert "/app" not in digests and "/application/empty" not in digests
    assert "/tmp/.agenttx-strace-internal" not in digests
