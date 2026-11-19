"""Native YoloFS storage sessions; no model, tracing or recovery search."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import time


def command(argv, *, cwd=None):
    result = subprocess.run([str(v) for v in argv], cwd=cwd, text=True,
                            capture_output=True, timeout=1800)
    if result.returncode:
        raise RuntimeError(f"{argv}: {result.stderr[-4000:]}")
    return result


def atomic_json(path: Path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(data, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class YoloSnapshot:
    generation: int
    name: str


class YoloSession:
    """Native snapshot/commit backend shared by RQ5 and the recovery runtime.

    Control and history directories live outside the published workspace.
    An existing session is mounted rather than reinitialized on load.
    """

    def __init__(self, workspace: Path, state_dir: Path, *, yolo="yolo"):
        self.workspace = Path(workspace).resolve(strict=True)
        self.state_dir = Path(state_dir).resolve()
        if self.state_dir == self.workspace or self.workspace in self.state_dir.parents:
            raise ValueError("session state must live outside the workspace")
        self.yolo = yolo
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.control = self.state_dir / "control"
        self.control.mkdir(exist_ok=True)
        self.config = self.control / "yolofs.toml"
        if not self.config.exists():
            self.config.write_text('permission = true\nstaging = true\nauto_snapshot = false\n'
                                   'prompt_timeout = 5\n[rules]\n"/" = "allow"\n')
        self._closed = False
        self._mounted = False
        self._ctl = None
        self.snapshots = []
        self.mount()

    @property
    def artifact(self):
        return self.control / ".yolofs"

    @property
    def root(self):
        return (self.artifact / "mnt").resolve(strict=True)

    @property
    def view(self):
        return self.root / self.workspace.relative_to("/")

    def path(self, logical):
        logical = Path(os.path.normpath(str(logical)))
        if not logical.is_absolute():
            logical = self.workspace / logical
        logical.relative_to(self.workspace)
        return self.root / logical.relative_to("/")

    def mount(self):
        if not self._mounted:
            command([self.yolo, "mount"], cwd=self.control)
            self._mounted = True
            self._ctl = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)

    def unmount(self):
        if self._ctl is not None:
            os.close(self._ctl)
            self._ctl = None
        if self._mounted:
            command([self.yolo, "unmount"], cwd=self.control)
            self._mounted = False

    def remount(self):
        self.unmount()
        self.mount()

    def snapshot(self, name=None):
        if self._closed or not self._mounted:
            raise RuntimeError("session is not mounted")
        name = name or f"agenttx-{time.time_ns()}"
        encoded = name.encode()
        if len(encoded) >= 4096 or b"\n" in encoded or b"\0" in encoded:
            raise ValueError("invalid snapshot name")
        text = ctypes.create_string_buffer(encoded)
        payload = bytearray(struct.pack("=QQHB5x", 0, ctypes.addressof(text),
                                        len(encoded), 0))
        fcntl.ioctl(self._ctl, (3 << 30) | (24 << 16) | (ord("A") << 8) | 40,
                    payload, True)
        point = YoloSnapshot(struct.unpack_from("=Q", payload)[0], name)
        if point.generation == 0:
            raise RuntimeError("native snapshot did not create a generation")
        self.snapshots.append(point)
        return point

    def travel(self, snapshot):
        generation = snapshot.generation if isinstance(snapshot, YoloSnapshot) else int(snapshot)
        command([self.yolo, "travel", generation], cwd=self.control)

    def staged_paths(self):
        """Paths touched in the native artifact, including historical branches."""
        journal = self.artifact / "journal"
        if not journal.exists():
            return set()
        paths = set()
        for record in journal.read_bytes().splitlines():
            fields = record.split(b"\0")
            if fields[0] in (b"S", b"D") and len(fields) > 1:
                paths.add(os.fsdecode(fields[1]))
            elif fields[0] == b"R" and len(fields) > 2:
                paths.update(os.fsdecode(p) for p in fields[1:3])
        return paths

    def backing_map(self):
        """Resolve native S/D/R/P/T records to sparse current backing entries."""
        journal = self.artifact / "journal"
        tree, markers = {}, [{}]
        if not journal.exists():
            return tree
        def pre(value):
            if value == b'a':
                return None
            if value.startswith(b's:'):
                return ('staged', int(value[2:]))
            if value.startswith(b'b:'):
                return ('base', os.fsdecode(value[2:]))
            raise ValueError(f'invalid native backing: {value!r}')
        def clear(path):
            for key in list(tree):
                if key == path or key.startswith(path + '/'):
                    del tree[key]
        for line in journal.read_bytes().splitlines():
            f = line.split(b'\0')
            if f[0] == b'S':
                path = os.fsdecode(f[1])
                clear(path)
                tree[path] = ('staged', int(f[2]))
            elif f[0] == b'D':
                path = os.fsdecode(f[1])
                clear(path)
                tree[path] = None
            elif f[0] == b'R':
                dst, src = os.fsdecode(f[1]), os.fsdecode(f[2])
                moved = {dst + key[len(src):]: value for key, value in tree.items()
                         if key.startswith(src + '/')}
                clear(dst)
                clear(src)
                tree[src] = None
                tree[dst] = pre(f[3])
                tree.update(moved)
            elif f[0] == b'P':
                markers.append(tree.copy())
            elif f[0] == b'T':
                tree = markers[int(f[1])].copy()
                markers.append(tree.copy())
        return tree

    def backing(self, logical, mapping=None, *, staged_only=False):
        """Backing object for metadata access; writes require an exact staged entry."""
        logical = str(Path(logical))
        Path(logical).relative_to(self.workspace)
        mapping = self.backing_map() if mapping is None else mapping
        candidate = logical
        while candidate != '/':
            if candidate in mapping:
                value = mapping[candidate]
                if value is None:
                    return None
                kind, target = value
                suffix = logical[len(candidate):].lstrip('/')
                if kind == 'staged':
                    if staged_only and suffix:
                        raise ValueError('metadata write requires an exact staged inode')
                    result = self.artifact/'inodes'/str(target//100)/str(target)
                else:
                    if staged_only:
                        raise ValueError('refusing metadata write to base')
                    result = Path(target)
                return result/suffix if suffix else result
            candidate = str(Path(candidate).parent)
        if staged_only:
            raise ValueError('refusing metadata write to an unstaged object')
        return Path(logical)

    def commit(self):
        unexpected = []
        workspace = str(self.workspace)
        prefix = workspace + '/'
        for name in self.staged_paths():
            # Journal paths are absolute dentry paths. Normalize once and
            # compare at a component boundary rather than constructing Path
            # objects and walking their parents for every staged file.
            normalized = os.path.normpath(name)
            if normalized != workspace and not normalized.startswith(prefix):
                unexpected.append(name)
        if unexpected:
            raise ValueError(f"refusing to publish writes outside workspace: {unexpected[:8]}")
        command([self.yolo, "commit"], cwd=self.control)

    def close(self, destroy=False):
        if self._closed:
            return
        self.unmount()
        self._closed = True
        if destroy:
            shutil.rmtree(self.state_dir)

