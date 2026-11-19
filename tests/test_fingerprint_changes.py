import mmap
import os
from pathlib import Path

from agenttx.semisolate import SharedSemisolate


def pool_for(tmp_path):
    pool = object.__new__(SharedSemisolate)
    pool.sandbox_dir = tmp_path / "session"
    pool.workspace = Path("/app")
    upper = pool.sandbox_dir / "upperdir" / "app"
    upper.mkdir(parents=True)
    return pool, upper


def test_repeated_writes_through_existing_memory_mapping_are_detected(tmp_path):
    pool, upper = pool_for(tmp_path)
    target = upper / "mapped"
    target.write_bytes(b"0" * 4096)
    with target.open("r+b") as handle:
        with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_WRITE) as memory:
            memory[0:1] = b"A"
            first = pool.upperdir_digests()
            assert pool.upperdir_digests() == first
            memory[0:1] = b"B"
            assert pool.upperdir_digests()["/app/mapped"] != first["/app/mapped"]


def test_same_size_write_with_restored_mtime_is_detected(tmp_path):
    pool, upper = pool_for(tmp_path)
    target = upper / "target"
    target.write_text("old")
    first = pool.upperdir_digests()
    original = target.stat()
    target.write_text("new")
    os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert pool.upperdir_digests()["/app/target"] != first["/app/target"]


def test_mode_zero_content_change_is_detected_without_changing_permissions(tmp_path):
    pool, upper = pool_for(tmp_path)
    target = upper / "target"
    target.write_text("old")
    target.chmod(0)
    first = pool.upperdir_digests()
    target.chmod(0o600)
    target.write_text("new")
    target.chmod(0)
    assert pool.upperdir_digests()["/app/target"] != first["/app/target"]
    assert target.stat().st_mode & 0o777 == 0


def test_directory_rename_does_not_leave_old_fingerprints(tmp_path):
    pool, upper = pool_for(tmp_path)
    directory = upper / "before"
    directory.mkdir()
    (directory / "value").write_text("old")
    pool.upperdir_digests()
    directory.rename(upper / "after")
    moved = pool.upperdir_digests()
    assert "/app/before/value" not in moved
    assert "/app/after/value" in moved
    (upper / "after/value").unlink()
    assert "/app/after/value" not in pool.upperdir_digests()
