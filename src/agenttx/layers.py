"""Per-step upperdir snapshots for surgical cascade rollback."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


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


def _copy_overlay_entry(
    source: Path,
    destination: Path,
    inode_memo: Optional[Dict[tuple[int, int], Path]] = None,
) -> None:
    entry_stat = source.lstat()
    mode = entry_stat.st_mode
    if stat.S_ISDIR(mode):
        _copy_overlay_tree(source, destination, inode_memo)
    elif stat.S_ISLNK(mode):
        destination.symlink_to(os.readlink(source))
        shutil.copystat(source, destination, follow_symlinks=False)
    elif stat.S_ISREG(mode):
        if inode_memo is None:
            inode_memo = {}
        key = (entry_stat.st_dev, entry_stat.st_ino)
        previous = inode_memo.get(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if previous is not None:
            os.link(previous, destination, follow_symlinks=False)
            shutil.copystat(source, destination, follow_symlinks=False)
        else:
            _copy_regular(source, destination, mode)
            inode_memo[key] = destination
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
    source_root: Path,
    destination_root: Path,
    logical: Path,
    inode_memo: Optional[Dict[tuple[int, int], Path]] = None,
) -> None:
    relative = logical.relative_to("/")
    source = source_root.joinpath(*relative.parts)
    destination = destination_root.joinpath(*relative.parts)
    if _lexists(source):
        destination.parent.mkdir(parents=True, exist_ok=True)
        _copy_overlay_entry(source, destination, inode_memo)
        return

    source_whiteout = source.parent / f".wh.{source.name}"
    if _lexists(source_whiteout):
        destination.parent.mkdir(parents=True, exist_ok=True)
        _copy_overlay_entry(
            source_whiteout,
            destination.parent / source_whiteout.name,
            inode_memo,
        )


def _copy_overlay_tree(
    source: Path,
    destination: Path,
    inode_memo: Optional[Dict[tuple[int, int], Path]] = None,
) -> None:
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
    if inode_memo is None:
        inode_memo = {}
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

                _copy_overlay_entry(
                    source_entry, destination_entry, inode_memo
                )

        shutil.copystat(source, destination, follow_symlinks=False)
        if changed_mode:
            destination.chmod(original_mode)
    finally:
        if changed_mode:
            source.chmod(original_mode)


def _clone_snapshot_tree(source: Path, destination: Path) -> None:
    """Clone a snapshot tree while hard-linking immutable file entries."""
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
                    _clone_snapshot_tree(source_entry, destination_entry)
                elif stat.S_ISLNK(mode):
                    destination_entry.parent.mkdir(parents=True, exist_ok=True)
                    destination_entry.symlink_to(os.readlink(source_entry))
                    shutil.copystat(source_entry, destination_entry, follow_symlinks=False)
                elif stat.S_ISREG(mode) or (
                    stat.S_ISCHR(mode) and entry_stat.st_rdev == os.makedev(0, 0)
                ):
                    destination_entry.parent.mkdir(parents=True, exist_ok=True)
                    os.link(source_entry, destination_entry, follow_symlinks=False)
                else:
                    raise shutil.SpecialFileError(
                        f"unsupported snapshot entry: {source_entry}"
                    )
        shutil.copystat(source, destination, follow_symlinks=False)
    finally:
        if changed_mode:
            source.chmod(original_mode)


def _copy_snapshot_logical_entry(
    source_root: Path,
    destination_root: Path,
    blob_root: Path,
    logical: Path,
    fingerprints: Optional[Dict[str, str]],
) -> None:
    """Apply one changed upperdir path while retaining content-addressed blobs."""
    relative = logical.relative_to("/")
    source = source_root.joinpath(*relative.parts)
    destination = destination_root.joinpath(*relative.parts)
    if _lexists(source):
        destination.parent.mkdir(parents=True, exist_ok=True)
        _copy_snapshot_entry(source, destination, blob_root, logical, fingerprints)
        return
    source_whiteout = source.parent / f".wh.{source.name}"
    if _lexists(source_whiteout):
        destination.parent.mkdir(parents=True, exist_ok=True)
        _copy_snapshot_entry(
            source_whiteout,
            destination.parent / source_whiteout.name,
            blob_root,
            logical.parent / source_whiteout.name,
            fingerprints,
        )


def _incremental_snapshot_tree(
    previous: Path,
    destination: Path,
    upperdir: Path,
    blob_root: Path,
    changed_paths: List[str],
    fingerprints: Optional[Dict[str, str]],
) -> None:
    """Clone the prior state and replay only paths changed by the prior step."""
    _clone_snapshot_tree(previous, destination)
    logical_paths = sorted(
        {Path(path) for path in changed_paths},
        key=lambda path: (len(path.parts), str(path)),
    )
    for logical in logical_paths:
        if not logical.is_absolute() or logical == Path("/"):
            raise ValueError(f"invalid incremental snapshot path: {logical}")
        _remove_logical_entry(destination, logical)
        _copy_snapshot_logical_entry(
            upperdir, destination, blob_root, logical, fingerprints
        )


def _snapshot_blob_key(source: Path) -> str:
    source_stat = source.lstat()
    original_mode = stat.S_IMODE(source_stat.st_mode)
    readable_mode = original_mode | stat.S_IRUSR
    changed_mode = readable_mode != original_mode
    if changed_mode:
        source.chmod(readable_mode)
    digest = hashlib.sha256()
    try:
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    finally:
        if changed_mode:
            source.chmod(original_mode)
    fields = (
        source_stat.st_dev,
        source_stat.st_ino,
        source_stat.st_size,
        source_stat.st_mtime_ns,
        source_stat.st_ctime_ns,
        source_stat.st_mode,
        source_stat.st_uid,
        source_stat.st_gid,
        digest.hexdigest(),
    )
    encoded = ":".join(str(field) for field in fields).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _copy_snapshot_regular(
    source: Path,
    destination: Path,
    blob_root: Path,
    mode: int,
    fingerprint: Optional[str] = None,
) -> None:
    if fingerprint is None:
        blob_name = _snapshot_blob_key(source)
    else:
        source_stat = source.lstat()
        fields = (
            source_stat.st_dev,
            source_stat.st_ino,
            source_stat.st_size,
            source_stat.st_mtime_ns,
            source_stat.st_ctime_ns,
            source_stat.st_mode,
            source_stat.st_uid,
            source_stat.st_gid,
            fingerprint,
        )
        encoded = ":".join(str(field) for field in fields).encode("ascii")
        blob_name = hashlib.sha256(encoded).hexdigest()
    blob = blob_root / blob_name[:2] / blob_name
    blob.parent.mkdir(parents=True, exist_ok=True)
    if not blob.exists():
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{blob_name}.", suffix=".tmp", dir=str(blob.parent)
        )
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            _copy_regular(source, temporary, mode)
            try:
                os.replace(str(temporary), str(blob))
            except FileExistsError:
                pass
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.link(blob, destination, follow_symlinks=False)


def _copy_snapshot_entry(
    source: Path,
    destination: Path,
    blob_root: Path,
    logical: Path,
    fingerprints: Optional[Dict[str, str]],
) -> None:
    entry_stat = source.lstat()
    mode = entry_stat.st_mode
    if stat.S_ISDIR(mode):
        _copy_snapshot_tree(source, destination, blob_root, logical, fingerprints)
    elif stat.S_ISLNK(mode):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(os.readlink(source))
        shutil.copystat(source, destination, follow_symlinks=False)
    elif stat.S_ISREG(mode):
        fingerprint = fingerprints.get(str(logical)) if fingerprints else None
        _copy_snapshot_regular(
            source, destination, blob_root, mode, fingerprint
        )
    elif stat.S_ISCHR(mode) and entry_stat.st_rdev == os.makedev(0, 0):
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.link(source, destination, follow_symlinks=False)
    else:
        raise shutil.SpecialFileError(
            f"unsupported upperdir entry: {source}"
        )


def _copy_snapshot_tree(
    source: Path,
    destination: Path,
    blob_root: Path,
    logical: Path = Path("/"),
    fingerprints: Optional[Dict[str, str]] = None,
) -> None:
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
                _copy_snapshot_entry(
                    Path(entry.path),
                    destination / entry.name,
                    blob_root,
                    logical / entry.name,
                    fingerprints,
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

    def snapshot_before(
        self,
        step_id: int,
        upperdir: Path,
        fingerprints: Optional[Dict[str, str]] = None,
        changed_paths: Optional[List[str]] = None,
    ) -> Path:
        """Capture a pre-step view with content-addressed file snapshots.

        Regular files are copied into immutable blobs once per observed inode
        state; each snapshot then hard-links the blob. The snapshot tree still
        owns its directory and metadata entries, so later upperdir writes cannot
        mutate a pre-step image.
        """
        dest = self.root / f"before_{step_id:04d}"
        _remove_overlay_tree(dest)
        blob_root = self.root / "blobs"
        blob_root.mkdir(parents=True, exist_ok=True)
        previous = self.root / f"before_{step_id - 1:04d}"
        if (
            upperdir.exists()
            and changed_paths is not None
            and step_id > 0
            and previous.exists()
        ):
            _incremental_snapshot_tree(
                previous, dest, upperdir, blob_root, changed_paths, fingerprints
            )
        elif upperdir.exists():
            _copy_snapshot_tree(
                upperdir, dest, blob_root, Path("/"), fingerprints
            )
        else:
            dest.mkdir(parents=True, exist_ok=True)
        return dest

    def copy_tree(self, source: Path, destination: Path) -> None:
        """Replace ``destination`` with an overlay-safe copy of ``source``."""
        _remove_overlay_tree(destination)
        if source.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            _copy_overlay_tree(source, destination)
        else:
            destination.mkdir(parents=True, exist_ok=True)

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
        inode_memo: Dict[tuple[int, int], Path] = {}
        _capture_tree_access(source_root, source_modes)
        _capture_tree_access(upperdir, destination_modes)
        try:
            for logical in top_level:
                _remove_logical_entry(upperdir, logical)
                _copy_logical_entry(
                    source_root, upperdir, logical, inode_memo
                )
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
        self._gc_blobs()

    def gc_blobs(self) -> None:
        """Remove content blobs no longer referenced by retained snapshots."""
        self._gc_blobs()

    def _gc_blobs(self) -> None:
        blob_root = self.root / "blobs"
        if not blob_root.exists():
            return
        for blob in blob_root.glob("*/*"):
            try:
                if blob.is_file() and blob.stat().st_nlink <= 1:
                    blob.unlink()
            except FileNotFoundError:
                continue
