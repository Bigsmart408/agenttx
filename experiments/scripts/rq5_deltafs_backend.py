"""RQ5 adapter for the recovered DeltaBox kernel DeltaFS implementation.

The kernel registers as ``deltafs`` so stock OverlayFS remains available in the
same guest. Snapshot creation uses the authors' OVL_IOCTL_CHECKPOINT interface.
Final publication and cleanup reuse the existing benchmark's implementation.
"""
from __future__ import annotations

import ctypes
import fcntl
import os


class CheckpointArgs(ctypes.Structure):
    _fields_ = [('options', ctypes.c_void_p), ('options_len', ctypes.c_uint32)]


CHECKPOINT = (1 << 30) | (ctypes.sizeof(CheckpointArgs) << 16) | (ord('O') << 8) | 1


def session_type(storage):
    class DeltaSession(storage.SnapshotSession):
        def __init__(self, *args, **kwargs):
            self._control = -1
            super().__init__(*args, **kwargs)
            try:
                self._control = os.open(self.view, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            except BaseException:
                self.close(destroy=True)
                raise

        def _mount_view(self, view, lowers, upper=None, work=None):
            options = 'lowerdir='+':'.join(f'/proc/self/fd/{fd}' for fd in lowers)
            options += (',index=off,metacopy=on,redirect_dir=on' if self.metadata_copy else
                        ',index=off,metacopy=off,redirect_dir=off')
            if upper is not None:
                options += f',upperdir=/proc/self/fd/{upper},workdir=/proc/self/fd/{work}'
            if storage._LIBC.mount(b'deltafs', os.fsencode(view), b'deltafs',
                                   0 if upper is not None else 1, options.encode()):
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error), view)

        def snapshot(self):
            generation = len(self.snapshots)+1
            before = len(self._fds)
            path, upper, work = self._new_layer(generation)
            # The kernel automatically inserts the previous upper ahead of
            # these existing lowers for a checkpoint (but not for restore).
            previous = [*(fd for _, fd in reversed(self._layers)), self._base]
            options = ('lowerdir='+':'.join(f'/proc/self/fd/{fd}' for fd in previous)
                       +f',upperdir=/proc/self/fd/{upper},workdir=/proc/self/fd/{work}'
                       +',ovl_kind=ckpt').encode()
            buffer = ctypes.create_string_buffer(options)
            args = CheckpointArgs(ctypes.cast(buffer, ctypes.c_void_p), len(options))
            try:
                fcntl.ioctl(self._control, CHECKPOINT, args)
            except BaseException:
                self._discard_new_layer(generation, before)
                raise
            self._layers.append((self.upperdir, self._upper))
            lowers = [*(fd for _, fd in reversed(self._layers)), self._base]
            self._visible_layers = [(p, fd, None) for p, fd in reversed(self._layers)]
            self.upperdir, self._upper, self._work = path, upper, work
            point = storage.FilesystemSnapshot(generation,
                tuple(p for p, _ in reversed(self._layers))+(self.workspace,),
                -1, tuple(lowers))
            self.snapshots.append(point)
            return point

        def _seal_mounts(self):
            if self._control != -1:
                os.close(self._control)
                self._control = -1
            super()._seal_mounts()

    return DeltaSession
