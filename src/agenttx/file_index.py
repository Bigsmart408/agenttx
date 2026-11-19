"""Cache upperdir topology, never file contents or file metadata.

Linux notifications invalidate directory listings. Queue loss, moved/deleted
watched directories and incomplete coverage force a fresh recursive enumeration.
Every returned inode is still stat'ed and every regular file is still hashed by
the caller, including files modified through an already-dirty mmap page.
"""
from __future__ import annotations

import ctypes
import os
import stat
import struct


class UpperdirTopology:
    _CREATE = 0x100
    _DIRECTORY = 0x40000000
    _STRUCTURE = 0x40 | 0x80 | 0x100 | 0x200
    _LOST = 0x2000 | 0x4000 | 0x8000 | 0x400 | 0x800
    # Attribute changes are checked by the fresh stat below. Subscribing to
    # them needlessly fills the queue during large OverlayFS copy-up batches.
    _MASK = _STRUCTURE | 0x400 | 0x800 | 0x01000000 | 0x02000000

    def __init__(self):
        self.fd = -1
        self.root = None
        self.identity = None
        self.watches = {}
        self.children = {}
        self.paths = {}
        self.valid = False
        self.enumerated_directories = 0
        self.rebuilds = 0
        self.disabled = False

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1
        self.valid = False
        self.watches.clear()
        self.children.clear()
        self.paths.clear()

    def __del__(self):
        self.close()

    def _watch(self, directory, original_mode):
        directory = os.fspath(directory)
        self.enumerated_directories += 1
        # The ordinary walker temporarily opens restrictive ancestors. A flat
        # cached walk cannot do that, so retain the ordinary path for such trees.
        if original_mode & 0o500 != 0o500:
            self.valid = False
        number = self.libc.inotify_add_watch(self.fd, os.fsencode(directory), self._MASK)
        if number < 0:
            self.valid = False
        else:
            self.watches[number] = directory
        self.children[directory] = set()

    def _remember(self, path):
        self.paths.setdefault(path, None)
        self.children[os.path.dirname(path)].add(path)

    def _rebuild(self, root, walker):
        self.close()
        self.rebuilds += 1
        self.root = os.fspath(root)
        metadata = os.lstat(root)
        self.identity = (metadata.st_dev, metadata.st_ino)
        try:
            self.libc = ctypes.CDLL(None, use_errno=True)
            self.libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
            self.fd = self.libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        except (AttributeError, OSError):
            self.disabled = True
        if self.fd < 0:
            self.disabled = True
            yield from walker(root, with_stat=True, raw_paths=True)
            return
        self.valid = True
        try:
            for path, metadata in walker(root, with_stat=True, raw_paths=True, observer=self._watch):
                self._remember(path)
                yield path, metadata
        except BaseException:
            self.close()
            raise
        if not self.valid:
            self.close()

    def _drain(self):
        dirty, reset = set(), not self.valid
        while self.fd >= 0:
            try:
                data = os.read(self.fd, 1024 * 1024)
            except BlockingIOError:
                break
            except OSError:
                return dirty, True
            if not data:
                return dirty, True
            offset = 0
            while offset + 16 <= len(data):
                number, mask, _, size = struct.unpack_from("iIII", data, offset)
                offset += 16 + size
                directory = self.watches.get(number)
                if mask & self._LOST or directory is None:
                    reset = True
                elif mask & self._DIRECTORY:
                    # A new subtree can be enumerated and watched locally.
                    # Rename, deletion or permission changes invalidate watches.
                    if mask & self._CREATE:
                        dirty.add(directory)
                    else:
                        reset = True
                elif mask & self._STRUCTURE:
                    dirty.add(directory)
        return dirty, reset

    def _refresh(self, directory, walker, fresh):
        previous = self.children[directory]
        current = set()
        self.enumerated_directories += 1
        with os.scandir(directory) as stream:
            for entry in stream:
                path = entry.path
                try:
                    metadata = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                fresh[path] = metadata
                current.add(path)
                self.paths.setdefault(path, None)
                if stat.S_ISDIR(metadata.st_mode) and path not in self.children:
                    # Install the watch before enumerating each new directory,
                    # including children created before its parent was watched.
                    for child, metadata in walker(path, with_stat=True, raw_paths=True, observer=self._watch):
                        self._remember(child)
                        fresh[child] = metadata
        for path in previous - current:
            if path in self.children:
                raise OSError("watched directory disappeared")
            self.paths.pop(path, None)
        self.children[directory] = current

    def entries(self, root, walker):
        if self.disabled:
            yield from walker(root, with_stat=True, raw_paths=True)
            return
        metadata = os.lstat(root)
        identity = (metadata.st_dev, metadata.st_ino)
        dirty, reset = self._drain()
        fresh = {}
        if (self.root != os.fspath(root) or identity != self.identity
                or metadata.st_mode & 0o500 != 0o500):
            reset = True
        if not reset:
            try:
                for directory in dirty:
                    self._refresh(directory, walker, fresh)
                reset = not self.valid
            except OSError:
                reset = True
        if reset:
            yield from self._rebuild(root, walker)
            return
        # Stable topology does not imply stable contents: stat and hash every
        # inode, even when no notification or timestamp change was observed.
        for path in self.paths:
            try:
                metadata = fresh.get(path)
                if metadata is None:
                    metadata = os.lstat(path)
                if stat.S_ISDIR(metadata.st_mode) and metadata.st_mode & 0o500 != 0o500:
                    yield from self._rebuild(root, walker)
                    return
                yield path, metadata
            except FileNotFoundError:
                continue
            except PermissionError:
                yield from self._rebuild(root, walker)
                return
