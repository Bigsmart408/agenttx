"""Per-step upperdir snapshots for surgical cascade rollback."""

from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import List


def _grant_tree_access(directory: Path) -> None:
    mode = stat.S_IMODE(directory.lstat().st_mode)
    directory.chmod(mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    with os.scandir(directory) as entries:
        for entry in entries:
            entry_mode = entry.stat(follow_symlinks=False).st_mode
            if stat.S_ISDIR(entry_mode):
                _grant_tree_access(Path(entry.path))


def _remove_overlay_tree(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if stat.S_ISDIR(mode):
        _grant_tree_access(path)
        shutil.rmtree(path)
    else:
        path.unlink()


def _copy_regular(source: Path, destination: Path, mode: int) -> None:
    original_mode = stat.S_IMODE(mode)
    readable_mode = original_mode | stat.S_IRUSR
    changed_mode = readable_mode != original_mode
    if changed_mode:
        source.chmod(readable_mode)
    try:
        shutil.copy2(source, destination, follow_symlinks=False)
        if changed_mode:
            destination.chmod(original_mode)
    finally:
        if changed_mode:
            source.chmod(original_mode)


def _copy_overlay_entry(source: Path, destination: Path) -> None:
    entry_stat = source.lstat()
    mode = entry_stat.st_mode
    if stat.S_ISDIR(mode):
        _copy_overlay_tree(source, destination)
    elif stat.S_ISLNK(mode):
        destination.symlink_to(os.readlink(source))
        shutil.copystat(source, destination, follow_symlinks=False)
    elif stat.S_ISREG(mode):
        _copy_regular(source, destination, mode)
    elif stat.S_ISCHR(mode) and entry_stat.st_rdev == os.makedev(0, 0):
        os.link(source, destination, follow_symlinks=False)
    else:
        raise shutil.SpecialFileError(
            f"unsupported upperdir entry: {source}"
        )


def _capture_tree_access(directory: Path, modes: dict[Path, int]) -> None:
    original_mode = stat.S_IMODE(directory.lstat().st_mode)
    modes[directory] = original_mode
    accessible_mode = (
        original_mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
    )
    if accessible_mode != original_mode:
        directory.chmod(accessible_mode)
    with os.scandir(directory) as entries:
        for entry in entries:
            entry_mode = entry.stat(follow_symlinks=False).st_mode
            if stat.S_ISDIR(entry_mode):
                _capture_tree_access(Path(entry.path), modes)


def _restore_tree_modes(modes: dict[Path, int]) -> None:
    for path, mode in sorted(
        modes.items(),
        key=lambda item: len(item[0].parts),
        reverse=True,
    ):
        try:
            path.chmod(mode)
        except FileNotFoundError:
            continue


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _remove_logical_entry(upperdir: Path, logical: Path) -> None:
    relative = logical.relative_to("/")
    direct = upperdir.joinpath(*relative.parts)
    whiteout = direct.parent / f".wh.{direct.name}"
    for candidate in (direct, whiteout):
        if _lexists(candidate):
            _remove_overlay_tree(candidate)


def _copy_logical_entry(
    source_root: Path, destination_root: Path, logical: Path
) -> None:
    relative = logical.relative_to("/")
    source = source_root.joinpath(*relative.parts)
    destination = destination_root.joinpath(*relative.parts)
    if _lexists(source):
        destination.parent.mkdir(parents=True, exist_ok=True)
        _copy_overlay_entry(source, destination)
        return

    source_whiteout = source.parent / f".wh.{source.name}"
    if _lexists(source_whiteout):
        destination.parent.mkdir(parents=True, exist_ok=True)
        _copy_overlay_entry(
            source_whiteout,
            destination.parent / source_whiteout.name,
        )


def _copy_overlay_tree(source: Path, destination: Path) -> None:
    """Copy an unmounted OverlayFS upperdir, preserving native whiteouts.

    OverlayFS represents a deletion as a character device with major/minor 0/0.
    Opening that node as a regular file fails, and recreating it with mknod is
    not permitted for the host user. Snapshot and upperdir live on the same
    filesystem, so a hard link preserves the whiteout inode without requiring
    mknod. Regular files remain independent copies.

    Owner read/search bits are added only while copying mode-000 entries and
    restored before returning; ctime changes are not part of AgentTX's effect
    fingerprint.
    """
    source_stat = source.lstat()
    original_mode = stat.S_IMODE(source_stat.st_mode)
    accessible_mode = original_mode | stat.S_IRUSR | stat.S_IXUSR
    changed_mode = accessible_mode != original_mode
    if changed_mode:
        source.chmod(accessible_mode)

    destination.mkdir(parents=True, exist_ok=False)
    try:
        with os.scandir(source) as entries:
            for entry in entries:
                source_entry = Path(entry.path)
                destination_entry = destination / entry.name
                entry_stat = entry.stat(follow_symlinks=False)
                mode = entry_stat.st_mode

                _copy_overlay_entry(source_entry, destination_entry)

        shutil.copystat(source, destination, follow_symlinks=False)
        if changed_mode:
            destination.chmod(original_mode)
    finally:
        if changed_mode:
            source.chmod(original_mode)


@dataclass
class LayerStore:
    """Before each step, snapshot the shared upperdir.

    Rolling back steps [i..k] restores upperdir to the snapshot taken before step i.
    """

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)

    def snapshot_before(self, step_id: int, upperdir: Path) -> Path:
        dest = self.root / f"before_{step_id:04d}"
        _remove_overlay_tree(dest)
        if upperdir.exists():
            _copy_overlay_tree(upperdir, dest)
        else:
            dest.mkdir(parents=True, exist_ok=True)
        return dest

    def restore_paths(
        self, before_step_id: int, upperdir: Path, paths: List[str]
    ) -> None:
        """Restore only selected logical paths from a pre-step snapshot.

        The operation is safe only when later retained steps do not overlap the
        selected paths; the runtime performs that fail-closed check.
        """
        source_root = self.root / f"before_{before_step_id:04d}"
        if not source_root.exists():
            raise FileNotFoundError(source_root)

        logical_paths = sorted(
            {Path(path) for path in paths},
            key=lambda path: (len(path.parts), str(path)),
        )
        top_level: List[Path] = []
        for logical in logical_paths:
            if not logical.is_absolute() or logical == Path("/"):
                raise ValueError(f"invalid rollback path: {logical}")
            if any(
                logical != parent
                and str(logical).startswith(str(parent).rstrip("/") + "/")
                for parent in top_level
            ):
                continue
            top_level.append(logical)

        source_modes: dict[Path, int] = {}
        destination_modes: dict[Path, int] = {}
        _capture_tree_access(source_root, source_modes)
        _capture_tree_access(upperdir, destination_modes)
        try:
            for logical in top_level:
                _remove_logical_entry(upperdir, logical)
                _copy_logical_entry(source_root, upperdir, logical)
        finally:
            _restore_tree_modes(destination_modes)
            _restore_tree_modes(source_modes)

    def restore_before(self, step_id: int, upperdir: Path) -> None:
        src = self.root / f"before_{step_id:04d}"
        _remove_overlay_tree(upperdir)
        if src.exists():
            _copy_overlay_tree(src, upperdir)
        else:
            upperdir.mkdir(parents=True, exist_ok=True)

    def drop_from(self, step_ids: List[int]) -> None:
        for step_id in step_ids:
            _remove_overlay_tree(self.root / f"before_{step_id:04d}")
