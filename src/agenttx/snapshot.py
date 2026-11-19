"""OverlayFS storage sessions for whole snapshots and final publication.

This API records frozen filesystem layers, without building the per-object
history and dependency information used by the selective recovery runtime.
Callers quiesce writers before taking a snapshot or publishing the session.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import errno
import fcntl
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import sys


_LIBC = ctypes.CDLL(None, use_errno=True)
_LIBC.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                       ctypes.c_ulong, ctypes.c_char_p]
_LIBC.mount.restype = ctypes.c_int
_LIBC.umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]
_LIBC.umount2.restype = ctypes.c_int
_LIBC.name_to_handle_at.argtypes = [ctypes.c_int, ctypes.c_char_p,
                                  ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.c_int]
_LIBC.name_to_handle_at.restype = ctypes.c_int
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_PRIVATE_XATTRS = ('trusted.overlay.', 'user.overlay.')
_FICLONE = 0x40049409
_CLONE_UNAVAILABLE = {errno.EOPNOTSUPP, errno.ENOTTY, errno.EXDEV,
                      errno.EINVAL, errno.ENOSYS}
try:
    _NATIVE = ctypes.CDLL(str(Path(__file__).with_name('_snapshot_native.so')))
    _NATIVE.agenttx_unlink_many.argtypes = [ctypes.c_int, ctypes.c_char_p,
                                          ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    _NATIVE.agenttx_unlink_many.restype = ctypes.c_int
    _NATIVE.agenttx_delete_only.argtypes = [ctypes.c_int, ctypes.c_int]
    _NATIVE.agenttx_delete_only.restype = ctypes.c_int
except (OSError, AttributeError):
    _NATIVE = None


def _try_reflink(source: int, destination: int) -> bool:
    """Clone regular-file data into an empty destination when supported.

    Source and destination remain separate inodes. Callers retain their
    metadata and durability handling and truncate the destination first.
    """
    try:
        fcntl.ioctl(destination, _FICLONE, source)
    except OSError as error:
        if error.errno in _CLONE_UNAVAILABLE:
            return False
        raise
    return True


def _copy_file_data(source: str | Path, destination: str | Path) -> None:
    """Copy regular-file data, preferring CoW on XFS and other reflink FSes."""
    flags = os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    source_fd = os.open(source, os.O_RDONLY | flags)
    try:
        original = os.fstat(source_fd)
        if not stat.S_ISREG(original.st_mode):
            raise shutil.SpecialFileError(f'not a regular file: {source}')
        target_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | flags, 0o666)
        try:
            target = os.fstat(target_fd)
            if not stat.S_ISREG(target.st_mode):
                raise shutil.SpecialFileError(f'not a regular file: {destination}')
            if (original.st_dev, original.st_ino) == (target.st_dev, target.st_ino):
                raise shutil.SameFileError(f'{source!r} and {destination!r} are the same file')
            os.ftruncate(target_fd, 0)
            if _try_reflink(source_fd, target_fd):
                return
        finally:
            os.close(target_fd)
    finally:
        os.close(source_fd)
    # Reopen through the existing fast-copy implementation. Its truncation
    # also discards any partial clone before a supported fallback copy.
    shutil.copyfile(source, destination, follow_symlinks=False)


def _unmount(view: Path, *, detach: bool = False) -> None:
    if _LIBC.umount2(os.fsencode(view), 2 if detach else 0):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), view)


def _readonly(view: Path, enabled: bool) -> None:
    if _LIBC.mount(None, os.fsencode(view), None, 4096 | 32 | int(enabled), None):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), view)


def _mount(view: Path, lowers: list[int], upper: int | None = None,
           work: int | None = None, *, metadata_copy: bool = False) -> None:
    options = 'lowerdir='+':'.join(f'/proc/self/fd/{fd}' for fd in lowers)
    # Flat-layer compaction relies on complete upper files, not metacopy
    # references or redirected directories. Use these settings for every view.
    options += (',index=off,metacopy=on,redirect_dir=on' if metadata_copy else
                ',index=off,metacopy=off,redirect_dir=off')
    if upper is not None:
        options += f',upperdir=/proc/self/fd/{upper},workdir=/proc/self/fd/{work}'
    if _LIBC.mount(b'overlay', os.fsencode(view), b'overlay',
                   0 if upper is not None else 1, options.encode()):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), view)


def _xattr(fd: int, name: str) -> bytes | None:
    try:
        return os.getxattr(fd, name)
    except OSError as error:
        if error.errno not in (errno.ENODATA, errno.ENOTSUP):
            raise
        return None


def _remove(fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=fd)
    except FileNotFoundError:
        pass
    except IsADirectoryError:
        child = os.open(name, _DIRECTORY, dir_fd=fd)
        try:
            _clear(child)
        finally:
            os.close(child)
        os.rmdir(name, dir_fd=fd)


def _clear(fd: int) -> None:
    with os.scandir(fd) as entries:
        for entry in entries:
            _remove(fd, entry.name)


def _remove_many(fd: int, names: list[str]) -> None:
    if _NATIVE is None:
        for name in names:
            _remove(fd, name)
        return
    remaining = b''.join(os.fsencode(name)+b'\0' for name in names)
    while remaining:
        consumed = ctypes.c_size_t()
        error = _NATIVE.agenttx_unlink_many(fd, remaining, len(remaining), ctypes.byref(consumed))
        if error == 0:
            return
        failed, tail = remaining[consumed.value:].split(b'\0', 1)
        if error != errno.EISDIR:
            raise OSError(error, os.strerror(error), os.fsdecode(failed))
        _remove(fd, os.fsdecode(failed))
        remaining = tail


def _directory_metadata(source: int, destination: int, metadata: os.stat_result) -> None:
    current = os.fstat(destination)
    owner_changed = (current.st_uid, current.st_gid) != (metadata.st_uid, metadata.st_gid)
    if owner_changed:
        os.fchown(destination, metadata.st_uid, metadata.st_gid)
    mode = stat.S_IMODE(metadata.st_mode)
    if owner_changed or stat.S_IMODE(current.st_mode) != mode:
        os.fchmod(destination, mode)
    attributes = {name for name in os.listxattr(source) if not name.startswith(_PRIVATE_XATTRS)}
    for name in os.listxattr(destination):
        if not name.startswith(_PRIVATE_XATTRS) and name not in attributes:
            os.removexattr(destination, name)
    for name in attributes:
        os.setxattr(destination, name, os.getxattr(source, name))
    os.utime(destination, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))


class _FileHandle(ctypes.Structure):
    _fields_ = [('size', ctypes.c_uint), ('kind', ctypes.c_int),
                ('data', ctypes.c_ubyte * 128)]


class _XfsGeometryV1(ctypes.Structure):
    # xfs_fs.h, struct xfs_fsop_geom_v1. Keep native alignment for the ioctl.
    _fields_ = [('geometry', ctypes.c_uint32 * 8),
                ('blocks', ctypes.c_uint64 * 4),
                ('uuid', ctypes.c_ubyte * 16),
                ('features', ctypes.c_uint32 * 7)]


def _filesystem_uuid(directory: int) -> bytes | None:
    # Linux EXT4_IOC_GETFSUUID, struct fsuuid (two u32s followed by the UUID).
    value = bytearray(struct.pack('=II', 16, 0)+bytes(16))
    try:
        fcntl.ioctl(directory, 0x8008662c, value)
    except OSError as error:
        if error.errno == errno.EPERM:
            return None
        if error.errno not in (errno.ENOTTY, errno.EOPNOTSUPP, errno.EINVAL):
            raise
    else:
        return bytes(value[8:])
    # XFS exposes its UUID through XFS_IOC_FSGEOMETRY_V1. This keeps the
    # verified-origin rename path available on XFS without reading a device.
    size = ctypes.sizeof(_XfsGeometryV1)
    geometry = bytearray(size)
    request = (2 << 30) | (size << 16) | (ord('X') << 8) | 100
    try:
        fcntl.ioctl(directory, request, geometry)
    except OSError as error:
        if error.errno in (errno.ENOTTY, errno.EOPNOTSUPP, errno.EINVAL, errno.EPERM):
            return None
        raise
    offset = _XfsGeometryV1.uuid.offset
    return bytes(geometry[offset:offset+16])


def _matches_origin(directory: int, name: str, origin: bytes, uuid: bytes) -> bool:
    # fs/overlayfs/overlayfs.h: packed ovl_fb, followed by the native fid.
    # Unknown versions, upper origins and foreign-endian fids use copying.
    if (len(origin) <= 21 or origin[:2] != b'\x00\xfb' or origin[2] != len(origin)
            or origin[5:21] != uuid or origin[3] & ~3
            or not origin[3] & 2 and bool(origin[3] & 1) != (sys.byteorder == 'big')):
        return False
    handle = _FileHandle(size=128)
    mount_id = ctypes.c_int()
    if _LIBC.name_to_handle_at(directory, os.fsencode(name), ctypes.byref(handle),
                               ctypes.byref(mount_id), 0):
        error = ctypes.get_errno()
        if error in (errno.EOPNOTSUPP, errno.EOVERFLOW, errno.EPERM, errno.ENOENT):
            return False
        raise OSError(error, os.strerror(error), name)
    return handle.kind == origin[4] and bytes(handle.data[:handle.size]) == origin[21:]


def _flat_publication(session) -> dict[str, Path] | None:
    """Latest entries when no directory merging or redirection is involved."""
    latest: dict[str, Path] = {}
    for layer, fd in [*session._layers, (session.upperdir, session._upper)]:
        if any(_xattr(fd, prefix+attribute) is not None
               for prefix in _PRIVATE_XATTRS for attribute in ('opaque', 'redirect')):
            return None
        with os.scandir(layer) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    return None
                latest[entry.name] = Path(entry.path)
    return latest


def _merge(source: int, destination: int, whiteouts: set[tuple[int, int]]) -> None:
    metadata = os.fstat(source)
    if _xattr(source, 'trusted.overlay.redirect') is not None:
        raise OSError(errno.ENOTSUP, 'redirected OverlayFS directories need recovery-mode publication')
    if any(_xattr(source, name) == b'y' for name in
           ('trusted.overlay.opaque', 'user.overlay.opaque')):
        _clear(destination)
    if _NATIVE is not None:
        result = _NATIVE.agenttx_delete_only(source, destination)
        if result == 0:
            _directory_metadata(source, destination, metadata)
            return
        if result not in (errno.ENOTSUP, errno.EISDIR):
            raise OSError(result, os.strerror(result))
    deletions = []
    with os.scandir(source) as entries:
        for entry in entries:
            name = entry.name
            if entry.is_dir(follow_symlinks=False):
                try:
                    os.mkdir(name, dir_fd=destination)
                except FileExistsError:
                    pass
                try:
                    child = os.open(name, _DIRECTORY, dir_fd=destination)
                except OSError as error:
                    if error.errno not in (errno.ENOTDIR, errno.ELOOP):
                        raise
                    _remove(destination, name)
                    os.mkdir(name, dir_fd=destination)
                    child = os.open(name, _DIRECTORY, dir_fd=destination)
                source_child = os.open(name, _DIRECTORY, dir_fd=source)
                try:
                    _merge(source_child, child, whiteouts)
                finally:
                    os.close(source_child)
                    os.close(child)
            elif not entry.is_file(follow_symlinks=False) and not entry.is_symlink():
                identity = (metadata.st_dev, entry.inode())
                if identity not in whiteouts:
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISCHR(info.st_mode) and info.st_rdev == 0:
                        whiteouts.add(identity)
                if identity in whiteouts:
                    deletions.append(name)
                else:
                    _move(source, destination, name)
            else:
                # rename preserves inode metadata and atomically replaces a
                # regular destination. Avoid lstat/unlink/re-stat per file.
                _move(source, destination, name)
    _remove_many(destination, deletions)
    _directory_metadata(source, destination, metadata)


def _move(source: int, destination: int, name: str) -> None:
    try:
        os.rename(name, name, src_dir_fd=source, dst_dir_fd=destination)
    except OSError as error:
        if error.errno not in (errno.ENOTDIR, errno.EISDIR, errno.ENOTEMPTY, errno.EEXIST):
            raise
        _remove(destination, name)
        os.rename(name, name, src_dir_fd=source, dst_dir_fd=destination)


_FrozenLayer = tuple[Path, int, frozenset[str] | None]
_FLAT_LAYER_LIMIT = 256


def _flat_names(directory: int) -> frozenset[str] | None:
    """Names in a flat, ordinary layer; None means keep it conservatively.

    A non-directory entry masks the same name in every older layer, including
    when it is a native whiteout. Directories can merge with older children;
    opaque/redirected layers have additional visibility rules.
    """
    harmless = {'trusted.overlay.uuid', 'user.overlay.uuid',
                'trusted.overlay.impure', 'user.overlay.impure'}
    if any(name.startswith(_PRIVATE_XATTRS) and name not in harmless
           for name in os.listxattr(directory)):
        return None
    names = set()
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                return None
            names.add(entry.name)
            if len(names) > _FLAT_LAYER_LIMIT:
                return None
    return frozenset(names)


def _compact_lowers(layers: list[_FrozenLayer]) -> list[_FrozenLayer]:
    """Keep newest-first layers whose entries still contribute to the view."""
    visible: list[_FrozenLayer] = []
    covered: set[str] = set()
    for layer in layers:
        names = layer[2]
        # Always retain the newest root and its metadata, even when empty.
        if not visible or names is None or not names.issubset(covered):
            visible.append(layer)
        if names is not None:
            covered.update(names)
    return visible


@dataclass(frozen=True)
class FilesystemSnapshot:
    generation: int
    lowerdirs: tuple[Path, ...]
    mount_fd: int
    lower_fds: tuple[int, ...] = ()


class SnapshotSession:
    """A mounted OverlayFS workspace supporting snapshots and final commit.

    Frozen layers are complete mountable snapshots; no directory flattening
    or per-file historical index is needed. Readonly mount references live
    until seal/commit/close; commit consumes staged history.
    Selective rollback continues to use SharedSemisolate and LayerStore.
    """

    def __init__(self, workspace: Path, state_dir: Path, *, metadata_copy: bool = False):
        self.workspace, self.state_dir = Path(workspace).resolve(), Path(state_dir).resolve()
        self.metadata_copy = metadata_copy
        if self.state_dir == self.workspace or self.state_dir.is_relative_to(self.workspace):
            raise ValueError('snapshot state must be outside the workspace')
        self.state_dir.mkdir(parents=True, exist_ok=False)
        self.view = self.state_dir/'view'
        self.view.mkdir()
        self._fds: list[int] = []
        self._layers: list[tuple[Path, int]] = []
        self._visible_layers: list[_FrozenLayer] = []
        self._pins: list[int] = []
        self.snapshots: list[FilesystemSnapshot] = []
        self._mounted, self._closed, self._publishing = False, False, False
        try:
            self._base = self._open(self.workspace)
            self.upperdir, self._upper, self._work = self._new_layer(0)
            self._journal = os.open(self.state_dir/'snapshots.jsonl',
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_APPEND | os.O_CLOEXEC, 0o600)
            self._fds.append(self._journal)
            self._mount_view(self.view, [self._base], self._upper, self._work)
            self._mounted = True
        except BaseException:
            self.close()
            raise

    def _open(self, path: Path) -> int:
        fd = os.open(path, _DIRECTORY)
        self._fds.append(fd)
        return fd

    def _mount_view(self, view: Path, lowers: list[int], upper: int | None = None,
                    work: int | None = None) -> None:
        if self.metadata_copy:
            _mount(view, lowers, upper, work, metadata_copy=True)
        else:
            _mount(view, lowers, upper, work)

    def _layer_names(self, directory: int) -> frozenset[str] | None:
        # Metadata-only files may refer to data in an older layer even when
        # all of that layer's names are shadowed in the visible namespace.
        return None if self.metadata_copy else _flat_names(directory)

    def _new_layer(self, generation: int) -> tuple[Path, int, int]:
        upper = self.state_dir/f'u{generation}'
        upper.mkdir()
        work = self.state_dir/f'w{generation}'
        work.mkdir()
        upper_fd, work_fd = self._open(upper), self._open(work)
        # Inherit the current root metadata, including changes made since the
        # last snapshot. Never hold a descriptor into the view while unmounting.
        source = os.open(self.view if self._mounted else self.workspace, _DIRECTORY)
        try:
            _directory_metadata(source, upper_fd, os.fstat(source))
        finally:
            os.close(source)
        return upper, upper_fd, work_fd

    def snapshot(self) -> FilesystemSnapshot:
        if self._closed or self._publishing or not self._mounted:
            raise RuntimeError('snapshot session is not writable')
        generation = len(self.snapshots)+1
        visible = _compact_lowers([
            (self.upperdir, self._upper, self._layer_names(self._upper)), *self._visible_layers])
        old_fd_count = len(self._fds)
        try:
            next_path, next_upper, next_work = self._new_layer(generation)
        except BaseException:
            self._discard_new_layer(generation, old_fd_count)
            raise
        pin = None
        try:
            _readonly(self.view, True)
            pin = os.open(self.view, os.O_PATH | os.O_CLOEXEC)
            # The readonly reference retains the superblock and its buffered
            # data. Detachment therefore needs no synchronous superblock
            # destruction. Publication/close release all retained references.
            _unmount(self.view, detach=True)
        except BaseException:
            if pin is not None:
                os.close(pin)
            _readonly(self.view, False)
            self._discard_new_layer(generation, old_fd_count)
            raise
        self._mounted = False
        frozen = [*(fd for _, fd, _ in visible), self._base]
        try:
            self._mount_view(self.view, frozen, next_upper, next_work)
        except BaseException:
            os.close(pin)
            self._mount_view(self.view, [*(fd for _, fd, _ in self._visible_layers), self._base],
                             self._upper, self._work)
            self._mounted = True
            self._discard_new_layer(generation, old_fd_count)
            raise
        self._mounted = True
        self._pins.append(pin)
        self._layers.append((self.upperdir, self._upper))
        self._visible_layers = visible
        self.upperdir, self._upper, self._work = next_path, next_upper, next_work
        point = FilesystemSnapshot(generation,
            tuple(path for path, _, _ in visible)+(self.workspace,), pin, tuple(frozen))
        record = json.dumps(dict(generation=generation), separators=(',', ':')).encode()+b'\n'
        try:
            written = os.write(self._journal, record)
        except OSError:
            self._publishing = True
            raise
        if written != len(record):
            self._publishing = True
            raise OSError(errno.EIO, 'short snapshot journal write')
        self.snapshots.append(point)
        return point

    def _discard_new_layer(self, generation: int, old_fd_count: int) -> None:
        for fd in self._fds[old_fd_count:]:
            os.close(fd)
        del self._fds[old_fd_count:]
        for path in (self.state_dir/f'u{generation}', self.state_dir/f'w{generation}'):
            if path.exists():
                shutil.rmtree(path)

    def mount_snapshot(self, point: FilesystemSnapshot, view: Path) -> None:
        if self._closed or self._publishing or not self._mounted or point not in self.snapshots:
            raise ValueError('snapshot does not belong to an active session')
        Path(view).mkdir(parents=True, exist_ok=True)
        lowers = list(point.lower_fds)
        self._mount_view(Path(view), lowers)

    def seal(self) -> None:
        if self.metadata_copy and self._mounted:
            self._materialize_metadata()
        else:
            self._seal_mounts()

    def _seal_mounts(self) -> None:
        if self._mounted:
            _unmount(self.view)
            self._mounted = False
        for fd in reversed(self._pins):
            os.close(fd)
        self._pins.clear()

    def _materialize_metadata(self, *, reuse_base: bool = False) -> list[tuple[Path, str]]:
        """Resolve metadata-only entries through the live view before sealing.

        Reading the merged view lets OverlayFS resolve data redirects across
        all generations. Final publication can reuse verified base inodes for
        flat metadata-only files. Explicit seal() leaves the base untouched.
        Only after every mount reference is released do we replace entries.
        Publication consumes history, so older entries can use final contents
        or be omitted when later layers delete or replace them.
        """
        prepared = self.state_dir/'materialized'
        prepared.mkdir()
        replacements: list[tuple[Path, Path | None]] = []
        links: dict[tuple[int, int], Path] = {}
        latest = None
        uuid = None
        base_device = os.fstat(self._base).st_dev
        reused: list[tuple[Path, Path, str, os.stat_result, bool]] = []
        origins: set[str] = set()
        directories: list[tuple[Path, os.stat_result]] = []

        def reuse_payload(path: Path, rel: Path, target: Path,
                          info: os.stat_result, attrs: list[str]) -> bool:
            if latest is None or uuid is None or info.st_nlink != 1:
                return False
            prefix = next(prefix for prefix in _PRIVATE_XATTRS if prefix+'metacopy' in attrs)
            origin = os.getxattr(path, prefix+'origin') if prefix+'origin' in attrs else b''
            name = (os.fsdecode(os.getxattr(path, prefix+'redirect')).removeprefix('/')
                    if prefix+'redirect' in attrs else rel.name)
            if (not name or name in ('.', '..') or '/' in name or name in origins
                    or name != rel.name and name not in latest):
                return False
            try:
                original = os.stat(name, dir_fd=self._base, follow_symlinks=False)
            except FileNotFoundError:
                return False
            if (not stat.S_ISREG(original.st_mode) or original.st_nlink != 1
                    or original.st_dev != base_device or original.st_size != info.st_size
                    or path.stat().st_dev != base_device
                    or not _matches_origin(self._base, name, origin, uuid)):
                return False
            fields = ('st_mode', 'st_uid', 'st_gid', 'st_atime_ns', 'st_mtime_ns')
            metadata_changed = (any(getattr(original, key) != getattr(info, key) for key in fields)
                or any(not key.startswith(_PRIVATE_XATTRS) for key in attrs)
                or any(not key.startswith(_PRIVATE_XATTRS)
                       for key in os.listxattr(self.workspace/name)))
            reused.append((path, target, name, info, metadata_changed))
            origins.add(name)
            return True

        def copy_payload(source: str | Path, target: str | Path) -> str:
            source, target = Path(source), Path(target)
            info = source.lstat()
            identity = (info.st_dev, info.st_ino)
            if stat.S_ISREG(info.st_mode) and info.st_nlink > 1 and identity in links:
                os.link(links[identity], target)
                return str(target)
            if stat.S_ISREG(info.st_mode):
                _copy_file_data(source, target)
                if info.st_nlink > 1:
                    links[identity] = target
            elif stat.S_ISLNK(info.st_mode):
                target.symlink_to(os.readlink(source))
            elif stat.S_ISFIFO(info.st_mode):
                os.mkfifo(target, stat.S_IMODE(info.st_mode))
            elif stat.S_ISCHR(info.st_mode) or stat.S_ISBLK(info.st_mode):
                os.mknod(target, info.st_mode, info.st_rdev)
            else:
                raise OSError(errno.ENOTSUP, 'cannot publish redirected special object', source)
            os.chown(target, info.st_uid, info.st_gid, follow_symlinks=False)
            shutil.copystat(source, target, follow_symlinks=False)
            return str(target)

        def copy_directory(source: Path, target: Path) -> None:
            target.mkdir()
            with os.scandir(source) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        copy_directory(Path(entry.path), target/entry.name)
                    else:
                        copy_payload(entry.path, target/entry.name)
            info = source.stat()
            os.chown(target, info.st_uid, info.st_gid)
            shutil.copystat(source, target)

        def visit(directory: Path, relative: Path) -> None:
            directories.append((directory, directory.stat()))
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    rel = relative/entry.name
                    attrs = os.listxattr(path, follow_symlinks=False)
                    redirected = entry.is_dir(follow_symlinks=False) and any(
                        name in ('trusted.overlay.redirect', 'user.overlay.redirect') for name in attrs)
                    metacopy = any(name in ('trusted.overlay.metacopy', 'user.overlay.metacopy')
                                   for name in attrs)
                    if redirected or metacopy:
                        if latest is not None and latest.get(rel.name) != path:
                            replacements.append((path, None))
                            continue
                        source = self.view/rel
                        try:
                            info = source.lstat()
                        except (FileNotFoundError, NotADirectoryError):
                            replacements.append((path, None))
                            continue
                        if (redirected and not stat.S_ISDIR(info.st_mode) or
                                metacopy and not stat.S_ISREG(info.st_mode)):
                            replacements.append((path, None))
                            continue
                        target = prepared/str(len(replacements))
                        if redirected:
                            copy_directory(source, target)
                            # This is the complete final subtree, including
                            # children that were untouched by the workload.
                            os.setxattr(target, 'trusted.overlay.opaque', b'y')
                        else:
                            if reuse_payload(path, rel, target, info, attrs):
                                # Reused inodes publish after the layer merges;
                                # avoid another trip through an upper directory.
                                replacements.append((path, None))
                                continue
                            copy_payload(source, target)
                        replacements.append((path, target))
                    elif entry.is_dir(follow_symlinks=False):
                        visit(path, rel)
        complete = False
        try:
            latest = _flat_publication(self) if reuse_base else None
            uuid = _filesystem_uuid(self._base) if latest is not None else None
            for layer, _ in [*self._layers, (self.upperdir, self._upper)]:
                visit(layer, Path())
            self._seal_mounts()
            # Extract every source before publishing any destination. This
            # preserves payloads for swaps, overwrite chains and cycles.
            for path, target, name, info, changed in reused:
                os.rename(name, target, src_dir_fd=self._base)
                if changed:
                    source_fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
                    try:
                        destination_fd = os.open(target, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
                        try:
                            _directory_metadata(source_fd, destination_fd, info)
                        finally:
                            os.close(destination_fd)
                    finally:
                        os.close(source_fd)
            for path, target in replacements:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                if target is not None:
                    target.rename(path)
            for path, info in directories:
                os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
            complete = True
            return [(target, path.name) for path, target, _, _, _ in reused]
        except BaseException:
            if not self._mounted:
                self._publishing = True
            raise
        finally:
            # Once publication has begun, keep extracted originals on failure.
            # Before sealing, a failed preparation leaves the session retryable.
            if complete and not reused or self._mounted:
                shutil.rmtree(prepared)

    def commit(self) -> None:
        if self._closed or self._publishing:
            raise RuntimeError('snapshot session is already closed or published')
        reused = []
        if self.metadata_copy and self._mounted:
            reused = self._materialize_metadata(reuse_base=True)
        else:
            self.seal()
        self._publishing = True
        root_metadata = os.fstat(self._upper) if reused else None
        visible = (_compact_lowers([
            (self.upperdir, self._upper, self._layer_names(self._upper)), *self._visible_layers])
            if self._visible_layers else [(self.upperdir, self._upper, None)])
        whiteouts: set[tuple[int, int]] = set()
        for _, fd, _ in reversed(visible):
            _merge(fd, self._base, whiteouts)
        for source, name in reused:
            try:
                os.rename(source, name, dst_dir_fd=self._base)
            except OSError as error:
                if error.errno not in (errno.ENOTDIR, errno.EISDIR, errno.ENOTEMPTY, errno.EEXIST):
                    raise
                _remove(self._base, name)
                os.rename(source, name, dst_dir_fd=self._base)
        if root_metadata is not None:
            os.utime(self._base, ns=(root_metadata.st_atime_ns, root_metadata.st_mtime_ns))
            (self.state_dir/'materialized').rmdir()

    def close(self, *, destroy: bool = False) -> None:
        self._seal_mounts()
        self._closed = True
        for fd in reversed(self._fds):
            os.close(fd)
        self._fds.clear()
        if destroy and self.state_dir.exists():
            shutil.rmtree(self.state_dir)
