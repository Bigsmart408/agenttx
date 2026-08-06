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

                if stat.S_ISDIR(mode):
                    _copy_overlay_tree(source_entry, destination_entry)
                elif stat.S_ISLNK(mode):
                    destination_entry.symlink_to(os.readlink(source_entry))
                    shutil.copystat(
                        source_entry,
                        destination_entry,
                        follow_symlinks=False,
                    )
                elif stat.S_ISREG(mode):
                    _copy_regular(source_entry, destination_entry, mode)
                elif (
                    stat.S_ISCHR(mode)
                    and entry_stat.st_rdev == os.makedev(0, 0)
                ):
                    os.link(
                        source_entry,
                        destination_entry,
                        follow_symlinks=False,
                    )
                else:
                    raise shutil.SpecialFileError(
                        f"unsupported upperdir entry: {source_entry}"
                    )

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
