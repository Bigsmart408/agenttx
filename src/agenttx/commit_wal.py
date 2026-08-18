"""Crash-recovery write-ahead log for host materialization.

The external ``try commit`` operation can materialize several host paths before
returning. This module records a durable pre-commit image of those paths and
the overlay upperdir so a session reload can either finish an acknowledged
commit or restore the exact pre-commit state.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .layers import _copy_overlay_tree, _remove_overlay_tree


def _lexists(path: Path) -> bool:
    return os.path.lexists(str(path))


def _remove_any(path: Path) -> None:
    if not _lexists(path):
        return
    mode = path.lstat().st_mode
    if stat.S_ISDIR(mode) and not stat.S_ISLNK(mode):
        _grant_tree_access(path)
        shutil.rmtree(path)
    else:
        path.unlink()


def _grant_tree_access(directory: Path) -> None:
    mode = stat.S_IMODE(directory.lstat().st_mode)
    accessible = mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
    if accessible != mode:
        directory.chmod(accessible)
    with os.scandir(directory) as entries:
        children = list(entries)
    for entry in children:
        child = Path(entry.path)
        child_mode = entry.stat(follow_symlinks=False).st_mode
        if stat.S_ISDIR(child_mode):
            _grant_tree_access(child)


def _copy_regular(source: Path, destination: Path, mode: int) -> None:
    original_mode = stat.S_IMODE(mode)
    readable = original_mode | stat.S_IRUSR
    changed = readable != original_mode
    if changed:
        source.chmod(readable)
    try:
        shutil.copy2(source, destination, follow_symlinks=False)
        if changed:
            destination.chmod(original_mode)
    finally:
        if changed:
            source.chmod(original_mode)


def _copy_host_entry(
    source: Path,
    destination: Path,
    inode_memo: Optional[Dict[tuple[int, int], Path]] = None,
) -> None:
    if inode_memo is None:
        inode_memo = {}
    source_stat = source.lstat()
    mode = source_stat.st_mode
    if stat.S_ISDIR(mode):
        destination.mkdir(parents=True, exist_ok=False)
        original_mode = stat.S_IMODE(mode)
        accessible = original_mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
        changed = accessible != original_mode
        if changed:
            source.chmod(accessible)
        try:
            with os.scandir(source) as entries:
                children = list(entries)
            for entry in children:
                _copy_host_entry(
                    Path(entry.path), destination / entry.name, inode_memo
                )
            shutil.copystat(source, destination, follow_symlinks=False)
            if changed:
                destination.chmod(original_mode)
        finally:
            if changed:
                source.chmod(original_mode)
    elif stat.S_ISLNK(mode):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(os.readlink(str(source)))
        shutil.copystat(source, destination, follow_symlinks=False)
    elif stat.S_ISREG(mode):
        destination.parent.mkdir(parents=True, exist_ok=True)
        if inode_memo is None:
            inode_memo = {}
        key = (source_stat.st_dev, source_stat.st_ino)
        previous = inode_memo.get(key)
        if previous is not None:
            os.link(previous, destination, follow_symlinks=False)
            shutil.copystat(source, destination, follow_symlinks=False)
        else:
            _copy_regular(source, destination, mode)
            inode_memo[key] = destination
    else:
        raise ValueError(f"unsupported host entry in commit WAL: {source}")


def _fsync_tree(root: Path) -> None:
    """Best-effort durability barrier for a completed backup tree."""
    if not _lexists(root):
        return
    mode = root.lstat().st_mode
    if stat.S_ISLNK(mode):
        return
    if stat.S_ISREG(mode):
        original_mode = stat.S_IMODE(mode)
        readable = original_mode | stat.S_IRUSR
        changed = readable != original_mode
        if changed:
            root.chmod(readable)
        try:
            try:
                with root.open("rb") as handle:
                    os.fsync(handle.fileno())
            except OSError:
                pass
        finally:
            if changed:
                root.chmod(original_mode)
        return
    if stat.S_ISDIR(mode):
        original_mode = stat.S_IMODE(mode)
        accessible = original_mode | stat.S_IRUSR | stat.S_IXUSR
        changed = accessible != original_mode
        if changed:
            root.chmod(accessible)
        try:
            with os.scandir(root) as entries:
                children = list(entries)
            for entry in children:
                _fsync_tree(Path(entry.path))
            try:
                fd = os.open(
                    str(root), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                )
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except OSError:
                pass
        finally:
            if changed:
                root.chmod(original_mode)


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
        directory_fd = os.open(
            str(path.parent), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _collapse_paths(paths: Iterable[str], workspace: Path) -> List[Path]:
    collapsed: List[Path] = []
    for raw in sorted(
        set(paths), key=lambda value: (len(Path(value).parts), value)
    ):
        path = Path(raw)
        if not path.is_absolute() or path == workspace:
            raise ValueError(f"invalid commit WAL path: {raw}")
        try:
            relative = path.relative_to(workspace)
        except ValueError as exc:
            raise ValueError(f"commit path outside workspace: {raw}") from exc
        if relative == Path("."):
            raise ValueError(f"refusing to WAL the workspace root: {raw}")
        if any(
            relative == parent or parent in relative.parents
            for parent in collapsed
        ):
            continue
        collapsed.append(relative)
    return collapsed


class CommitWAL:
    """Durable intent and pre-image for one host materialization attempt."""

    VERSION = 1
    WAL_NAME = "commit_wal.json"
    BACKUP_NAME = ".commit-wal-backup"

    def __init__(self, session_dir: Path, payload: dict) -> None:
        self.session_dir = Path(session_dir)
        self.payload = payload

    @property
    def path(self) -> Path:
        return self.session_dir / self.WAL_NAME

    @property
    def backup_dir(self) -> Path:
        return self.session_dir / self.BACKUP_NAME

    @property
    def up_to(self) -> int:
        return int(self.payload["up_to"])

    @property
    def phase(self) -> str:
        return str(self.payload["phase"])

    @classmethod
    def load(cls, session_dir: Path) -> Optional["CommitWAL"]:
        path = Path(session_dir) / cls.WAL_NAME
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != cls.VERSION:
            raise RuntimeError(
                f"unsupported commit WAL version: {payload.get('version')}"
            )
        backup = Path(session_dir) / cls.BACKUP_NAME
        if not backup.is_dir():
            raise RuntimeError("commit WAL backup directory is missing")
        return cls(Path(session_dir), payload)

    @classmethod
    def prepare(
        cls,
        session_dir: Path,
        workspace: Path,
        upperdir: Path,
        paths: List[str],
        up_to: int,
        ledger_before: dict,
    ) -> "CommitWAL":
        session_dir = Path(session_dir)
        workspace = Path(workspace).resolve()
        existing = cls.load(session_dir)
        if existing is not None:
            raise RuntimeError("an earlier commit WAL requires recovery")
        backup_dir = session_dir / cls.BACKUP_NAME
        _remove_any(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        host_root = backup_dir / "host"
        host_root.mkdir()
        entries = []
        host_inode_memo: Dict[tuple[int, int], Path] = {}
        for relative in _collapse_paths(paths, workspace):
            source = workspace / relative
            present = _lexists(source)
            entries.append({"relative": relative.as_posix(), "present": present})
            if present:
                _copy_host_entry(source, host_root / relative, host_inode_memo)

        upper_backup = backup_dir / "upper"
        if upperdir.exists():
            _copy_overlay_tree(upperdir, upper_backup)
        else:
            upper_backup.mkdir()
        _fsync_tree(backup_dir)
        payload = {
            "version": cls.VERSION,
            "workspace": str(workspace),
            "up_to": int(up_to),
            "paths": sorted(set(paths)),
            "entries": entries,
            "phase": "prepared",
            "ledger_before": ledger_before,
        }
        wal = cls(session_dir, payload)
        _atomic_write_json(wal.path, payload)
        return wal

    def mark(self, phase: str) -> None:
        if phase not in {"prepared", "applying", "materialized", "committed"}:
            raise ValueError(f"invalid commit WAL phase: {phase}")
        self.payload["phase"] = phase
        _atomic_write_json(self.path, self.payload)

    def restore(self, workspace: Path, upperdir: Path) -> None:
        expected = Path(self.payload["workspace"]).resolve()
        workspace = Path(workspace).resolve()
        if expected != workspace:
            raise RuntimeError(
                f"commit WAL workspace mismatch: {expected} != {workspace}"
            )
        host_root = self.backup_dir / "host"
        parent_modes: Dict[Path, int] = {}
        host_inode_memo: Dict[tuple[int, int], Path] = {}
        try:
            for item in self.payload["entries"]:
                relative = Path(item["relative"])
                if relative.is_absolute() or ".." in relative.parts:
                    raise RuntimeError(f"invalid relative path in commit WAL: {relative}")
                destination = workspace / relative
                cursor = destination.parent
                while cursor != workspace.parent and cursor not in parent_modes:
                    if _lexists(cursor) and stat.S_ISDIR(cursor.lstat().st_mode):
                        mode = stat.S_IMODE(cursor.lstat().st_mode)
                        parent_modes[cursor] = mode
                        accessible = mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
                        if accessible != mode:
                            cursor.chmod(accessible)
                    cursor = cursor.parent
                _remove_any(destination)
                if item["present"]:
                    _copy_host_entry(
                        host_root / relative, destination, host_inode_memo
                    )
        finally:
            for path, mode in sorted(
                parent_modes.items(), key=lambda item: len(item[0].parts), reverse=True
            ):
                try:
                    path.chmod(mode)
                except FileNotFoundError:
                    pass

        _remove_overlay_tree(upperdir)
        _copy_overlay_tree(self.backup_dir / "upper", upperdir)

    def cleanup(self) -> None:
        # Remove the intent first. If the process dies while deleting the
        # backup, the next commit can safely reclaim the orphaned backup; the
        # reverse order would leave an unrecoverable WAL without its pre-image.
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        _remove_any(self.backup_dir)
        try:
            fd = os.open(
                str(self.session_dir), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass
