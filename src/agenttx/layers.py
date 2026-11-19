"""Immutable upperdir history for selective and complete rollback."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from bisect import bisect_left
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .snapshot import _copy_file_data


_SNAPSHOT_DIRECTORIES = ContextVar("snapshot_directories", default=None)
_SNAPSHOT_COPIES = ContextVar("snapshot_copies", default=None)
_BATCH_COPY_MIN_FILES = 4096
_BATCH_LINK_MIN_FILES = 256
_BATCH_DELTA_MIN_FILES = 64


def _file_version(source_stat: os.stat_result) -> tuple:
    return (
        source_stat.st_dev, source_stat.st_ino, source_stat.st_size,
        source_stat.st_mtime_ns, source_stat.st_ctime_ns, source_stat.st_mode,
        source_stat.st_uid, source_stat.st_gid, source_stat.st_nlink,
    )


@contextmanager
def _stage_regular_copies(upperdir: Path, blob_root: Path, changed_paths: List[str]):
    """Batch independent regular-file copies; retain the ordinary fallback.

    Copied files are moved into the usual immutable blob store, so batching
    adds no second retained content copy. Never hard-link the writable upper.
    """
    if len(changed_paths) < _BATCH_COPY_MIN_FILES or not shutil.which("cp"):
        yield
        return
    groups = {}
    for logical in set(changed_paths):
        source = os.path.join(upperdir, logical.lstrip("/"))
        try:
            source_stat = os.lstat(source)
        except OSError:
            continue
        if stat.S_ISREG(source_stat.st_mode):
            parent = os.path.dirname(source)
            groups.setdefault(parent, []).append((source, _file_version(source_stat)))
    groups = {parent: files for parent, files in groups.items() if len(files) >= 256}
    if sum(map(len, groups.values())) < _BATCH_COPY_MIN_FILES:
        yield
        return
    staging = Path(tempfile.mkdtemp(prefix=".capture-", dir=blob_root)).absolute()
    token = None
    try:
        copies = {}
        succeeded = True
        for parent, files in groups.items():
            destination = staging / os.path.relpath(parent, upperdir)
            destination.mkdir(parents=True, exist_ok=True)
            # Bound argv independently of both the number and depth of paths.
            batches, batch, size = [], [], 0
            argument_budget = min(262144, os.sysconf("SC_ARG_MAX") // 4)
            for source, signature in files:
                basename = os.path.basename(source)
                size += len(os.fsencode(basename)) + 1
                if size > argument_budget and batch:
                    batches.append(batch)
                    batch, size = [], len(os.fsencode(basename)) + 1
                batch.append(basename)
                copies[source] = (os.path.join(destination, basename), signature)
            batches.append(batch)
            for batch in batches:
                try:
                    copied = subprocess.run(
                        ["cp", "--archive", "--reflink=auto", "--target-directory", os.fspath(destination), "--", *batch],
                        cwd=parent, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    )
                except OSError:
                    succeeded = False
                    break
                if copied.returncode:
                    succeeded = False
                    break
            if not succeeded:
                break
        if succeeded:
            token = _SNAPSHOT_COPIES.set(copies)
        yield
    finally:
        if token is not None:
            _SNAPSHOT_COPIES.reset(token)
        _remove_overlay_tree(staging)


def _snapshot_mkdir(directory: Path) -> None:
    directory = os.fspath(directory)
    known = _SNAPSHOT_DIRECTORIES.get()
    if known is not None and directory in known:
        return
    os.makedirs(directory, exist_ok=True)
    if known is not None:
        known.add(directory)


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
        known = _SNAPSHOT_DIRECTORIES.get()
        if known is not None:
            prefix = os.fspath(path).rstrip("/")
            known.difference_update([entry for entry in known if entry == prefix or entry.startswith(prefix + "/")])
        _grant_tree_access(path)
        shutil.rmtree(path)
    else:
        path.unlink()


def _copy_metadata(source: Path, destination: Path) -> None:
    # copystat/copy2 deliberately omit uid/gid. Upperdir directory owners and
    # set-id bits are part of the task filesystem and must survive rollback.
    original = os.lstat(source)
    copied = os.lstat(destination)
    if (original.st_uid, original.st_gid) != (copied.st_uid, copied.st_gid):
        os.chown(destination, original.st_uid, original.st_gid, follow_symlinks=False)
    shutil.copystat(source, destination, follow_symlinks=False)


def _copy_regular(source: Path, destination: Path, mode: int) -> None:
    original_mode = stat.S_IMODE(mode)
    readable_mode = original_mode | stat.S_IRUSR
    changed_mode = readable_mode != original_mode
    if changed_mode:
        os.chmod(source, readable_mode)
    try:
        _copy_file_data(source, destination)
        _copy_metadata(source, destination)
        if changed_mode:
            os.chmod(destination, original_mode)
    finally:
        if changed_mode:
            os.chmod(source, original_mode)


def _copy_special_node(source: Path, destination: Path, entry_stat: os.stat_result) -> None:
    """Copy a special inode's filesystem metadata, never its kernel state.

    A socket snapshot is an independent, unbound socket node. Listening
    processes and connections are not filesystem state or process checkpoints.
    Taking a snapshot must not open, unlink or mutate the live endpoint.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    if stat.S_ISFIFO(entry_stat.st_mode):
        os.mkfifo(destination, stat.S_IMODE(entry_stat.st_mode))
    else:
        os.mknod(destination, entry_stat.st_mode, entry_stat.st_rdev)
    _copy_metadata(source, destination)


def _link_whiteout(source: Path, destination: Path, entry_stat: os.stat_result) -> None:
    """Retain a whiteout even when shared OverlayFS inodes reach LINK_MAX."""
    try:
        os.link(source, destination, follow_symlinks=False)
    except OSError as exc:
        if exc.errno != errno.EMLINK:
            raise
        _copy_special_node(source, destination, entry_stat)


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
        _copy_metadata(source, destination)
    elif stat.S_ISREG(mode):
        if inode_memo is None:
            inode_memo = {}
        key = (entry_stat.st_dev, entry_stat.st_ino)
        previous = inode_memo.get(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if previous is not None:
            os.link(previous, destination, follow_symlinks=False)
            _copy_metadata(source, destination)
        else:
            _copy_regular(source, destination, mode)
            inode_memo[key] = destination
    elif stat.S_ISCHR(mode) and entry_stat.st_rdev == os.makedev(0, 0):
        _link_whiteout(source, destination, entry_stat)
    elif stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode):
        _copy_special_node(source, destination, entry_stat)
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
    name = os.fspath(logical)
    if not name.startswith("/") or name == "/" or ".." in name.split("/"):
        raise ValueError(f"invalid logical path: {logical}")
    direct = os.path.join(upperdir, name.lstrip("/"))
    parent, basename = os.path.split(direct)
    whiteout = os.path.join(parent, ".wh." + basename)
    for candidate in (direct, whiteout):
        try:
            mode = os.lstat(candidate).st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISDIR(mode):
            _remove_overlay_tree(Path(candidate))
        else:
            os.unlink(candidate)


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
        with _logical_parents(source_root, destination_root, relative):
            _copy_overlay_entry(source, destination, inode_memo)
        return

    source_whiteout = source.parent / f".wh.{source.name}"
    if _lexists(source_whiteout):
        with _logical_parents(source_root, destination_root, relative):
            _copy_overlay_entry(
                source_whiteout,
                destination.parent / source_whiteout.name,
                inode_memo,
            )


@contextmanager
def _logical_parents(source_root: Path, destination_root: Path, relative: Path):
    """Keep copied-up ancestor metadata when a changed child is copied alone.

    Ancestors can be absent from the effect list, including the workspace
    directory itself. Plain mkdir would replace their uid/gid and mode with
    those of the coordinator. Apply metadata after the child copy so even
    restrictive source directory permissions can be preserved.
    """
    created = []
    known = _SNAPSHOT_DIRECTORIES.get()
    source, destination = source_root, destination_root
    try:
        for part in relative.parts[:-1]:
            source, destination = source / part, destination / part
            if known is not None and os.fspath(destination) in known:
                continue
            if not _lexists(destination):
                destination.mkdir()
                created.append((source, destination))
            if known is not None:
                known.add(os.fspath(destination))
        yield
    finally:
        for source, destination in reversed(created):
            _copy_metadata(source, destination)


def _copy_overlay_tree(
    source: Path,
    destination: Path,
    inode_memo: Optional[Dict[tuple[int, int], Path]] = None,
) -> None:
    """Copy an unmounted OverlayFS upperdir, preserving native whiteouts.

    OverlayFS represents a deletion as a character device with major/minor 0/0.
    Opening that node as a regular file fails, and recreating it with mknod is
    not permitted for the host user. Snapshot and upperdir live on the same
    filesystem, so a hard link normally preserves the whiteout inode. If the
    filesystem link limit is reached, recreate the whiteout node instead.
    Regular files remain independent copies.

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

        _copy_metadata(source, destination)
        if changed_mode:
            destination.chmod(original_mode)
    finally:
        if changed_mode:
            source.chmod(original_mode)


def _clone_snapshot_tree(source: Path, destination: Path) -> None:
    """Clone a snapshot tree while hard-linking immutable file entries."""
    # Coreutils performs the same inode sharing in native code. Fall back to
    # the metadata-aware walker for restrictive modes and saturated whiteouts.
    if shutil.which("cp"):
        copied = subprocess.run(
            ["cp", "--archive", "--link", "--", str(source), str(destination)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        if copied.returncode == 0:
            return
        _remove_overlay_tree(destination)
    _clone_snapshot_tree_python(source, destination)


def _clone_snapshot_tree_python(source: Path, destination: Path) -> None:
    """Fallback without retrying a failed native clone at each directory."""
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
                    _clone_snapshot_tree_python(source_entry, destination_entry)
                elif stat.S_ISLNK(mode):
                    destination_entry.parent.mkdir(parents=True, exist_ok=True)
                    destination_entry.symlink_to(os.readlink(source_entry))
                    _copy_metadata(source_entry, destination_entry)
                elif stat.S_ISCHR(mode) and entry_stat.st_rdev == os.makedev(0, 0):
                    _link_whiteout(source_entry, destination_entry, entry_stat)
                elif stat.S_ISREG(mode) or stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode):
                    destination_entry.parent.mkdir(parents=True, exist_ok=True)
                    os.link(source_entry, destination_entry, follow_symlinks=False)
                else:
                    raise shutil.SpecialFileError(
                        f"unsupported snapshot entry: {source_entry}"
                    )
        _copy_metadata(source, destination)
        if changed_mode:
            destination.chmod(original_mode)
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
        with _logical_parents(source_root, destination_root, relative):
            _copy_snapshot_entry(source, destination, blob_root, logical, fingerprints)
        return
    source_whiteout = source.parent / f".wh.{source.name}"
    if _lexists(source_whiteout):
        with _logical_parents(source_root, destination_root, relative):
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
    _replay_snapshot_paths(upperdir, destination, blob_root, changed_paths, fingerprints)


def _minimal_paths(paths: List[str]) -> List[str]:
    changed = {os.path.normpath(path).replace("//", "/") for path in paths}
    result = []
    covered_parents = {"/": False}
    for logical in sorted(changed, key=lambda path: (path.count("/"), path)):
        if not logical.startswith("/") or logical == "/":
            raise ValueError(f"invalid snapshot path: {logical}")
        parent = os.path.dirname(logical)
        visited = []
        while parent not in covered_parents and parent not in changed:
            visited.append(parent)
            parent = os.path.dirname(parent)
        covered = parent in changed or covered_parents[parent]
        for ancestor in visited:
            covered_parents[ancestor] = covered
        if not covered:
            result.append(logical)
    return result


def _bulk_capture_delta(
    upperdir: Path, destination: Path, paths: List[str],
    blob_root: Path, fingerprints: Optional[Dict[str, str]],
) -> bool:
    """Copy a large plain-file delta directly into its immutable payload.

    Existing delta versions already share all unchanged entries through their
    parent reference. A batch therefore needs only one independent copy of its
    changed files, without publishing an extra blob and hard link per file.
    Special nodes and hard-linked files retain the ordinary capture path.
    """
    if not shutil.which("cp"):
        return False
    upper = os.fspath(upperdir)
    versions = {}
    selected = []
    ordinary = []
    regular_files = 0
    try:
        for logical in paths:
            source = os.path.join(upper, logical.lstrip("/"))
            try:
                source_metadata = os.lstat(source)
            except FileNotFoundError:
                parent, name = os.path.split(source)
                if _lexists(os.path.join(parent, ".wh." + name)):
                    ordinary.append(logical)
                continue
            root_versions = {}
            pending = [(source, source_metadata)]
            root_files = 0
            parent = os.path.dirname(source)
            while parent != upper and parent not in versions:
                metadata = os.lstat(parent)
                if not stat.S_ISDIR(metadata.st_mode):
                    return False
                root_versions[parent] = _file_version(metadata)
                parent = os.path.dirname(parent)
            # Special nodes share ancestors with native-copy candidates too.
            # Retain those checks even when this root uses ordinary capture.
            versions.update(root_versions)
            while pending:
                current, metadata = pending.pop()
                if metadata is None:
                    metadata = os.lstat(current)
                if stat.S_ISDIR(metadata.st_mode):
                    with os.scandir(current) as entries:
                        pending.extend((entry.path, None) for entry in entries)
                elif stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1:
                    root_files += 1
                else:
                    ordinary.append(logical)
                    break
                root_versions[current] = _file_version(metadata)
            else:
                selected.append(logical.lstrip("/"))
                versions.update(root_versions)
                regular_files += root_files
    except OSError:
        return False
    if regular_files < _BATCH_DELTA_MIN_FILES:
        return False

    # Keep arbitrary names as argv entries, and bound each invocation by bytes.
    budget = min(262144, os.sysconf("SC_ARG_MAX") // 4)
    batches, batch, size = [], [], 0
    for relative in selected:
        required = len(os.fsencode(relative)) + 1
        if batch and size + required > budget:
            batches.append(batch)
            batch, size = [], 0
        batch.append(relative)
        size += required
    if batch:
        batches.append(batch)
    succeeded = False
    try:
        for batch in batches:
            copied = subprocess.run(
                ["cp", "--archive", "--reflink=auto", "--parents", "--target-directory",
                 os.fspath(destination.absolute()), "--", *batch],
                cwd=upperdir, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
            if copied.returncode:
                break
        else:
            if all(_file_version(os.lstat(source)) == version
                   for source, version in versions.items()):
                succeeded = True
    except OSError:
        pass
    if succeeded:
        if ordinary:
            with _stage_regular_copies(upperdir, blob_root, ordinary):
                _replay_snapshot_paths(upperdir, destination, blob_root, ordinary, fingerprints)
        return True
    # An interrupted or racing native copy is never published. Rebuild the
    # whole payload through the established capture path before proceeding.
    _remove_overlay_tree(destination)
    destination.mkdir()
    return False


def _replay_snapshot_paths(
    upperdir: Path, destination: Path, blob_root: Path,
    changed_paths: List[str], fingerprints: Optional[Dict[str, str]],
) -> None:
    logical_paths = _minimal_paths(changed_paths)
    # All changed siblings share their ancestor lookup and metadata work.
    # Restore directory modes only after the entire batch has been copied.
    previous_parent = None
    parent_context = None
    try:
        for logical in logical_paths:
            relative = logical.lstrip("/")
            source = os.path.join(upperdir, relative)
            target = os.path.join(destination, relative)
            source_exists = _lexists(source)
            source_parent, basename = os.path.split(source)
            whiteout = os.path.join(source_parent, ".wh." + basename)
            whiteout_exists = not source_exists and _lexists(whiteout)
            _remove_logical_entry(destination, logical)
            if not source_exists and not whiteout_exists:
                continue
            parent = os.path.dirname(logical)
            if parent != previous_parent:
                if parent_context is not None:
                    parent_context.__exit__(None, None, None)
                    parent_context = None
                parent_context = _logical_parents(upperdir, destination, Path(relative))
                parent_context.__enter__()
                previous_parent = parent
            if source_exists:
                _copy_snapshot_entry(Path(source), Path(target), blob_root, Path(logical), fingerprints)
            else:
                if whiteout_exists:
                    whiteout_name = ".wh." + basename
                    _copy_snapshot_entry(Path(whiteout), Path(os.path.dirname(target)) / whiteout_name,
                                         blob_root, Path(parent) / whiteout_name, fingerprints)
    finally:
        if parent_context is not None:
            parent_context.__exit__(None, None, None)


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
    source_stat: Optional[os.stat_result] = None,
) -> None:
    if fingerprint is None:
        blob_name = _snapshot_blob_key(source)
    else:
        if source_stat is None:
            source_stat = os.lstat(source)
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
    blob_directory = os.path.join(blob_root, blob_name[:2])
    blob = os.path.join(blob_directory, blob_name)
    _snapshot_mkdir(blob_directory)
    if not os.path.exists(blob):
        copies = _SNAPSHOT_COPIES.get()
        staged = copies.pop(os.fspath(source), None) if copies else None
        staged_valid = staged is not None and _file_version(os.lstat(source)) == staged[1]
        if staged_valid:
            temporary_name = staged[0]
        else:
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{blob_name}.", suffix=".tmp", dir=blob_directory
            )
            os.close(fd)
        try:
            if not staged_valid:
                _copy_regular(os.fspath(source), temporary_name, mode)
            try:
                os.replace(temporary_name, blob)
            except FileExistsError:
                pass
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    _snapshot_mkdir(os.path.dirname(destination))
    os.link(blob, destination, follow_symlinks=False)


def _copy_snapshot_entry(
    source: Path,
    destination: Path,
    blob_root: Path,
    logical: Path,
    fingerprints: Optional[Dict[str, str]],
) -> None:
    entry_stat = os.lstat(source)
    mode = entry_stat.st_mode
    if stat.S_ISDIR(mode):
        _copy_snapshot_tree(source, destination, blob_root, logical, fingerprints)
    elif stat.S_ISLNK(mode):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(os.readlink(source))
        _copy_metadata(source, destination)
    elif stat.S_ISREG(mode):
        fingerprint = fingerprints.get(str(logical)) if fingerprints else None
        _copy_snapshot_regular(
            source, destination, blob_root, mode, fingerprint, entry_stat
        )
    elif stat.S_ISCHR(mode) and entry_stat.st_rdev == os.makedev(0, 0):
        destination.parent.mkdir(parents=True, exist_ok=True)
        _link_whiteout(source, destination, entry_stat)
    elif stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode):
        _copy_special_node(source, destination, entry_stat)
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
        _copy_metadata(source, destination)
        if changed_mode:
            destination.chmod(original_mode)
    finally:
        if changed_mode:
            source.chmod(original_mode)


def _link_snapshot_path(source_root: Path, destination_root: Path, logical: str) -> None:
    """Replay an immutable version without copying its regular-file contents."""
    relative = Path(logical.lstrip("/"))
    source = source_root / relative
    destination = destination_root / relative
    if not _lexists(source):
        whiteout = source.parent / (".wh." + source.name)
        if not _lexists(whiteout):
            return
        source = whiteout
        destination = destination.parent / whiteout.name
    with _logical_parents(source_root, destination_root, relative):
        mode = source.lstat().st_mode
        if stat.S_ISDIR(mode):
            _clone_snapshot_tree(source, destination)
        elif stat.S_ISLNK(mode):
            destination.symlink_to(os.readlink(source))
            _copy_metadata(source, destination)
        elif stat.S_ISCHR(mode) and source.lstat().st_rdev == 0:
            _link_whiteout(source, destination, source.lstat())
        else:
            os.link(source, destination, follow_symlinks=False)


def _capture_path_access(root: Path, logical: str, modes: dict[Path, int], *, subtree=True) -> None:
    """Grant access only along one selected path and its selected subtree."""
    current = root
    parts = Path(logical).relative_to("/").parts
    for index in range(len(parts) + 1):
        if index == len(parts) and not subtree:
            return
        if current in modes and index < len(parts):
            current = current / parts[index]
            continue
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(mode):
            return
        original = stat.S_IMODE(mode)
        if current not in modes:
            modes[current] = original
        accessible = original | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
        if accessible != original:
            current.chmod(accessible)
        if index == len(parts):
            selected_mode = modes[current]
            _capture_tree_access(current, modes)
            modes[current] = selected_mode
            return
        current = current / parts[index]


def _restore_copied_directory_modes(source_root, target_root, source_modes, target_modes):
    copied_modes = {}
    for source, mode in source_modes.items():
        target = target_root / source.relative_to(source_root)
        if target not in target_modes and target.is_dir() and not target.is_symlink():
            copied_modes[target] = mode
    _restore_tree_modes(copied_modes)


@dataclass
class LayerStore:
    """Immutable filesystem versions, optionally materialized only on demand."""

    root: Path
    defer_materialization: bool = False
    _records: dict = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve_before(self, step_id: int) -> Path:
        """Resolve a logical recovery point to an owned, materialized snapshot."""
        resolved = self._point_owner(step_id)
        owner_id = int(resolved.name.removeprefix("before_"))
        record = self._record(owner_id)
        if record is not None and not self._ready(owner_id).exists():
            self._materialize(owner_id)
        return resolved

    def _point_owner(self, step_id: int) -> Path:
        snapshot = self.root / f"before_{step_id:04d}"
        resolved = snapshot.resolve(strict=True)
        if (resolved.parent != self.root.resolve() or not resolved.is_dir()
                or not resolved.name.startswith("before_")
                or not resolved.name.removeprefix("before_").isdigit()):
            raise ValueError(f"invalid snapshot reference: {snapshot}")
        return resolved

    def _record_path(self, step_id: int) -> Path:
        return self.root / ".history" / f"point_{step_id:04d}.json"

    def _payload(self, step_id: int) -> Path:
        return self.root / ".history" / f"data_{step_id:04d}"

    def _ready(self, step_id: int) -> Path:
        return self.root / ".history" / f"ready_{step_id:04d}"

    def _record(self, step_id: int):
        path = self._record_path(step_id)
        try:
            metadata = path.stat()
        except FileNotFoundError:
            self._records.pop(step_id, None)
            if self._payload(step_id).exists():
                raise
            return None
        signature = (metadata.st_ino, metadata.st_mtime_ns, metadata.st_ctime_ns, metadata.st_size)
        cached = self._records.get(step_id)
        if cached is not None and cached[0] == signature:
            if not self._payload(step_id).is_dir():
                raise FileNotFoundError(self._payload(step_id))
            return cached[1]
        record = json.loads(path.read_text())
        if (record.get("version") != 1 or record.get("parent", step_id) >= step_id
                or record["parent"] < 0 or record.get("changes") != _minimal_paths(record["changes"])):
            raise ValueError(f"invalid filesystem history record: {path}")
        if not self._payload(step_id).is_dir():
            raise FileNotFoundError(self._payload(step_id))
        record["change_set"] = frozenset(record["changes"])
        record["sorted_changes"] = sorted(record["changes"])
        self._records[step_id] = signature, record
        return record

    def _materialize(self, step_id: int) -> None:
        chain = []
        current = step_id
        while True:
            owner = self._point_owner(current)
            current = int(owner.name.removeprefix("before_"))
            record = self._record(current)
            if record is None or self._ready(current).exists():
                break
            chain.append((current, record))
            current = record["parent"]
        temporary = Path(tempfile.mkdtemp(prefix=".history-build-", dir=self.root))
        built = temporary / "tree"
        directory_token = _SNAPSHOT_DIRECTORIES.set(set())
        try:
            _clone_snapshot_tree(owner, built)
            for identifier, record in reversed(chain):
                payload = self._payload(identifier)
                self._apply_versions(payload, built, record["changes"])
            destination = self.root / f"before_{step_id:04d}"
            _remove_overlay_tree(destination)
            os.replace(built, destination)
            self._ready(step_id).touch()
        finally:
            _SNAPSHOT_DIRECTORIES.reset(directory_token)
            _remove_overlay_tree(temporary)

    @staticmethod
    def _apply_versions(source: Path, target: Path, paths: List[str]) -> None:
        source_modes, target_modes = {}, {}
        try:
            groups = {}
            if len(paths) >= _BATCH_LINK_MIN_FILES and shutil.which("cp"):
                for logical in paths:
                    path = os.path.join(source, logical.lstrip("/"))
                    try:
                        metadata = os.lstat(path)
                        if (stat.S_ISREG(metadata.st_mode)
                                or stat.S_ISCHR(metadata.st_mode) and metadata.st_rdev == 0):
                            groups.setdefault(os.path.dirname(logical), []).append(logical)
                    except FileNotFoundError:
                        pass
            selected = [logical for group in groups.values() for logical in group]
            linked = set()
            if len(selected) >= _BATCH_LINK_MIN_FILES:
                # Grant access before creating any parents, so newly created
                # directories are restored from source metadata, not mistaken
                # for pre-existing target directories by a later group.
                for group in groups.values():
                    _capture_path_access(source, group[0], source_modes, subtree=False)
                    _capture_path_access(target, group[0], target_modes, subtree=False)
                with ExitStack() as parents:
                    for group in groups.values():
                        parents.enter_context(_logical_parents(
                            source, target, Path(group[0].lstrip("/"))))
                    for logical in selected:
                        _remove_logical_entry(target, logical)
                    # Link immutable files across all parents in bounded native
                    # batches. Parent metadata belongs to the selected history;
                    # cp must not overwrite existing independent directories.
                    budget = min(262144, os.sysconf("SC_ARG_MAX") // 4)
                    batches, batch, size = [], [], 0
                    for logical in selected:
                        relative = logical.lstrip("/")
                        argument_size = len(os.fsencode(relative)) + 1
                        if batch and size + argument_size > budget:
                            batches.append(batch)
                            batch, size = [], 0
                        batch.append(relative)
                        size += argument_size
                    batches.append(batch)
                    copied = True
                    for batch in batches:
                        try:
                            result = subprocess.run(
                                ["cp", "--link", "--parents",
                                 "--no-preserve=mode,ownership,timestamps,xattr",
                                 "--target-directory", os.path.abspath(target), "--", *batch],
                                cwd=source, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            )
                            copied = result.returncode == 0
                        except OSError:
                            copied = False
                        if not copied:
                            break
                    if copied:
                        linked.update(selected)
                    else:
                        # A failed native copy may have published a prefix.
                        for logical in selected:
                            _remove_logical_entry(target, logical)
            for logical in paths:
                if logical in linked:
                    continue
                _capture_path_access(source, logical, source_modes)
                _capture_path_access(target, logical, target_modes, subtree=False)
                _remove_logical_entry(target, logical)
                _link_snapshot_path(source, target, logical)
        finally:
            _restore_copied_directory_modes(source, target, source_modes, target_modes)
            _restore_tree_modes(target_modes)
            _restore_tree_modes(source_modes)

    @contextmanager
    def _path_source(self, step_id: int, logical: str):
        """Assemble only the selected subtree when it spans multiple versions."""
        current = step_id
        updates = []
        while True:
            owner = self._point_owner(current)
            current = int(owner.name.removeprefix("before_"))
            record = self._record(current)
            if record is None or self._ready(current).exists():
                source = owner
                break
            parent = logical
            while parent != "/":
                if parent in record["change_set"]:
                    source = self._payload(current)
                    break
                parent = os.path.dirname(parent)
            else:
                source = None
            if source is not None:
                break
            prefix = logical + "/"
            position = bisect_left(record["sorted_changes"], prefix)
            descendants = []
            for index in range(position, len(record["sorted_changes"])):
                changed = record["sorted_changes"][index]
                if not changed.startswith(prefix):
                    break
                descendants.append(changed)
            if descendants:
                updates.append((self._payload(current), descendants))
            current = record["parent"]
        if not updates:
            yield source
            return
        temporary = Path(tempfile.mkdtemp(prefix=".selected-history-", dir=self.root))
        directory_token = _SNAPSHOT_DIRECTORIES.set(set())
        try:
            self._apply_versions(source, temporary, [logical])
            for payload, paths in reversed(updates):
                self._apply_versions(payload, temporary, paths)
            yield temporary
        finally:
            _SNAPSHOT_DIRECTORIES.reset(directory_token)
            _remove_overlay_tree(temporary)

    def history_stats(self) -> dict:
        points = list(self.root.glob("before_*"))
        deltas = sum(self._record_path(int(p.name.removeprefix("before_"))).is_file()
                     for p in points if p.name.removeprefix("before_").isdigit())
        aliases = sum(p.is_symlink() for p in points)
        full = sum(not p.is_symlink() and (not self._record_path(int(p.name.removeprefix("before_"))).exists()
                   or self._ready(int(p.name.removeprefix("before_"))).exists()) for p in points)
        return {"points": len(points), "deltas": deltas, "aliases": aliases, "materialized": full}

    def snapshot_before(
        self,
        step_id: int,
        upperdir: Path,
        fingerprints: Optional[Dict[str, str]] = None,
        changed_paths: Optional[List[str]] = None,
    ) -> Path:
        """Capture a pre-step view with content-addressed file snapshots.

        Regular files are retained in immutable blobs or batched delta trees.
        Deferred history retains changed paths and a parent reference;
        resolve_before constructs a complete tree only when one is requested.
        The returned path is a recovery-point marker in deferred mode.
        """
        # Retry the entire capture if a live background process removes a
        # source path mid-copy. Never publish a partial tree or silently skip
        # a failed copy; immutable snapshot/blob/destination errors still fail.
        dest = self.root / f"before_{step_id:04d}"
        for attempt in range(3):
            directory_token = _SNAPSHOT_DIRECTORIES.set(set())
            try:
                return self._snapshot_before_once(
                    step_id, upperdir, fingerprints, changed_paths
                )
            except FileNotFoundError as exc:
                _remove_overlay_tree(dest)
                source = Path(exc.filename) if exc.filename else None
                if (
                    source is None
                    or not source.is_relative_to(upperdir)
                    or attempt == 2
                ):
                    raise
            finally:
                _SNAPSHOT_DIRECTORIES.reset(directory_token)
        raise AssertionError("snapshot retry loop exhausted")

    def _snapshot_before_once(
        self,
        step_id: int,
        upperdir: Path,
        fingerprints: Optional[Dict[str, str]],
        changed_paths: Optional[List[str]],
    ) -> Path:
        dest = self.root / f"before_{step_id:04d}"
        self._remove_point(step_id)
        previous = self.root / f"before_{step_id - 1:04d}"
        if previous.is_symlink() and not previous.exists():
            raise FileNotFoundError(previous)
        if step_id > 0 and changed_paths == [] and previous.exists():
            dest.symlink_to(self._point_owner(step_id - 1).name, target_is_directory=True)
            return dest
        blob_root = self.root / "blobs"
        blob_root.mkdir(parents=True, exist_ok=True)
        if (
            upperdir.exists()
            and changed_paths is not None
            and step_id > 0
            and previous.exists()
        ):
            if self.defer_materialization:
                return self._capture_delta(step_id, upperdir, fingerprints, changed_paths)
            with _stage_regular_copies(upperdir, blob_root, changed_paths):
                _incremental_snapshot_tree(
                    self.resolve_before(step_id - 1),
                    dest,
                    upperdir,
                    blob_root,
                    changed_paths,
                    fingerprints,
                )
        elif upperdir.exists():
            _copy_snapshot_tree(
                upperdir, dest, blob_root, Path("/"), fingerprints
            )
        else:
            dest.mkdir(parents=True, exist_ok=True)
        return dest

    def _capture_delta(self, step_id, upperdir, fingerprints, changed_paths):
        changes = _minimal_paths(changed_paths)
        previous = self._point_owner(step_id - 1)
        parent = int(previous.name.removeprefix("before_"))
        complete = False
        if not self._record_path(parent).exists() and not self._payload(parent).exists():
            try:
                with os.scandir(previous) as entries:
                    complete = next(entries, None) is None
            except PermissionError:
                pass
        payload = self._payload(step_id)
        payload.parent.mkdir(parents=True, exist_ok=True)
        payload.mkdir()
        destination = self.root / f"before_{step_id:04d}"
        record = {"version": 1, "parent": parent, "changes": changes}
        temporary_record = self._record_path(step_id).with_suffix(".tmp")
        try:
            if not _bulk_capture_delta(upperdir, payload, changes, self.root / "blobs", fingerprints):
                with _stage_regular_copies(upperdir, self.root / "blobs", changed_paths):
                    _replay_snapshot_paths(upperdir, payload, self.root / "blobs", changes, fingerprints)
            if complete:
                # There is no unchanged state to merge into an empty base.
                # The captured payload already is the complete new version.
                os.replace(payload, destination)
                _copy_metadata(previous, destination)
                return destination
            temporary_record.write_text(json.dumps(record, separators=(",", ":")))
            os.replace(temporary_record, self._record_path(step_id))
            destination.mkdir()
            return destination
        except BaseException:
            self._remove_point(step_id)
            temporary_record.unlink(missing_ok=True)
            raise

    def _remove_point(self, step_id):
        self._records.pop(step_id, None)
        _remove_overlay_tree(self.root / f"before_{step_id:04d}")
        _remove_overlay_tree(self._payload(step_id))
        self._record_path(step_id).unlink(missing_ok=True)
        self._ready(step_id).unlink(missing_ok=True)

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
        top_level = _minimal_paths(paths)
        # Resolve every source before mutating the current upperdir.
        with ExitStack() as stack:
            sources = [(logical, stack.enter_context(self._path_source(before_step_id, logical)))
                       for logical in top_level]
            inode_memo: Dict[tuple[int, int], Path] = {}
            for logical, source_root in sources:
                source_modes, destination_modes = {}, {}
                try:
                    _capture_path_access(source_root, logical, source_modes)
                    _capture_path_access(upperdir, logical, destination_modes, subtree=False)
                    _remove_logical_entry(upperdir, Path(logical))
                    _copy_logical_entry(source_root, upperdir, Path(logical), inode_memo)
                finally:
                    _restore_copied_directory_modes(source_root, upperdir, source_modes, destination_modes)
                    _restore_tree_modes(destination_modes)
                    _restore_tree_modes(source_modes)

    def restore_before(self, step_id: int, upperdir: Path) -> None:
        src = self.root / f"before_{step_id:04d}"
        resolved = self.resolve_before(step_id) if src.exists() else None
        if resolved is None and src.is_symlink():
            raise FileNotFoundError(src)
        _remove_overlay_tree(upperdir)
        if resolved is not None:
            _copy_overlay_tree(resolved, upperdir)
        else:
            upperdir.mkdir(parents=True, exist_ok=True)

    def drop_from(self, step_ids: List[int]) -> None:
        dropping = set(step_ids)
        marked = {int(p.name.removeprefix(".retained_before_")) for p in self.root.glob(".retained_before_*")}
        roots = {int(p.name.removeprefix("before_")) for p in self.root.glob("before_*")
                 if p.name.removeprefix("before_").isdigit()} - dropping - marked
        retained = set()
        for identifier in roots:
            current = identifier
            while current not in retained:
                retained.add(current)
                owner = self._point_owner(current)
                owner_id = int(owner.name.removeprefix("before_"))
                retained.add(owner_id)
                record = self._record(owner_id)
                if record is None or self._ready(owner_id).exists():
                    break
                current = record["parent"]
        for step_id in step_ids:
            if step_id in retained:
                (self.root / f".retained_before_{step_id:04d}").touch()
            else:
                self._remove_point(step_id)
        for marker in self.root.glob(".retained_before_*"):
            identifier = int(marker.name.removeprefix(".retained_before_"))
            if identifier not in retained:
                self._remove_point(identifier)
                marker.unlink()
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
