"""Immutable object deltas, independent of native snapshot frequency."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import stat

from .storage import atomic_json


def remove(path):
    if not os.path.lexists(path):
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def overlaps(left, right):
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


class ObjectHistory:
    def __init__(self, session):
        import json
        self.session = session
        self.root = session.state_dir / "history"
        self.blobs = self.root / "blobs"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.file = self.root / "index.json"
        self._fingerprints = {}
        if self.file.exists():
            self.data = json.loads(self.file.read_text())
            self.current = self.resolve(len(self.data["events"]))
        else:
            self.current = self.scan()
            self.data = {"version": 1, "initial": self.current.copy(), "events": [], "points": {}}
            self.save()

    def save(self):
        atomic_json(self.file, self.data)

    def scan(self):
        result = {}
        root = self.session.view
        backings = self.session.backing_map()

        def walk(parent):
            with os.scandir(parent) as entries:
                children = sorted(entries, key=lambda e: e.name)
            for child in children:
                path = Path(child.path)
                name = str(path.relative_to(root))
                info = child.stat(follow_symlinks=False)
                mode = info.st_mode
                record = dict(mode=stat.S_IMODE(mode), uid=info.st_uid, gid=info.st_gid)
                if stat.S_ISREG(mode):
                    record["kind"] = "file"
                    record["mtime_ns"] = info.st_mtime_ns
                    fingerprint = (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                    cached = self._fingerprints.get(name)
                    if cached and cached[0] == fingerprint:
                        record["blob"] = cached[1]
                    else:
                        content = path.read_bytes()
                        digest = hashlib.sha256(content).hexdigest()
                        target = self.blobs / digest
                        if not target.exists():
                            temporary = target.with_suffix(".tmp")
                            with temporary.open("wb") as stream:
                                stream.write(content)
                                stream.flush()
                                os.fsync(stream.fileno())
                            temporary.replace(target)
                        record["blob"] = digest
                        self._fingerprints[name] = (fingerprint, digest)
                elif stat.S_ISDIR(mode):
                    record["kind"] = "dir"
                elif stat.S_ISLNK(mode):
                    record.update(kind="symlink", target=os.readlink(path))
                    record["mtime_ns"] = info.st_mtime_ns
                elif stat.S_ISSOCK(mode):
                    record["kind"] = "socket"
                else:
                    raise ValueError(f"unsupported workspace object: {name}")
                # Preserve user-visible xattrs independently of the base inode.
                # Native YoloFS exposes listxattr but not every getxattr handler;
                # read the same backing inode for the immutable metadata copy.
                metadata_path = self.session.backing(self.session.workspace/name, backings)
                try:
                    record["xattrs"] = {key: os.getxattr(metadata_path, key, follow_symlinks=False).hex()
                                        for key in os.listxattr(metadata_path, follow_symlinks=False)}
                except OSError as error:
                    if error.errno not in (95,):
                        raise
                    record["xattrs"] = {}
                result[name] = record
                if record["kind"] == "dir":
                    walk(path)
        walk(root)
        return result

    def point(self, step):
        self.data["points"][str(step)] = len(self.data["events"])
        self.save()

    def record(self, *, kind, step=None):
        after = self.scan()
        delta = {name: after.get(name) for name in self.current.keys() | after.keys()
                 if self.current.get(name) != after.get(name)}
        self.data["events"].append(dict(kind=kind, step=step, delta=delta))
        self.current = after
        self.save()
        return delta

    def resolve(self, index):
        state = self.data["initial"].copy()
        for event in self.data["events"][:index]:
            for name, value in event["delta"].items():
                if value is None:
                    state.pop(name, None)
                else:
                    state[name] = value
        return state

    def before(self, step):
        return self.resolve(self.data["points"][str(step)])

    def restore(self, groups):
        desired = {}
        roots = []
        for step, paths in groups:
            state = self.before(step)
            for absolute in paths:
                name = str(Path(absolute).relative_to(self.session.workspace))
                if any(name == old or name.startswith(old + "/") for old in roots):
                    continue
                roots.append(name)
                desired.update({key: value for key, value in state.items()
                                if key == name or key.startswith(name + "/")})
        roots = [name for name in roots if not any(name != old and name.startswith(old + "/")
                                                 for old in roots)]
        for name, value in desired.items():
            if value["kind"] not in ("file", "dir", "symlink"):
                raise ValueError(f"cannot reconstruct {value['kind']} at {name}")
        for name in sorted(roots, key=lambda s: -s.count("/")):
            remove(self.session.view / name)
        for name, value in sorted(desired.items(), key=lambda item: (item[0].count("/"), item[0])):
            target = self.session.view / name
            target.parent.mkdir(parents=True, exist_ok=True)
            kind = value["kind"]
            if kind == "dir":
                target.mkdir(exist_ok=True)
            elif kind == "file":
                shutil.copyfile(self.blobs / value["blob"], target)
            elif kind == "symlink":
                target.symlink_to(value["target"])
            else:
                raise ValueError(f"cannot reconstruct {kind} at {name}")
        backings = self.session.backing_map()
        for name, value in sorted(desired.items(), key=lambda item: -item[0].count("/")):
            # Each restored object was just created through the native mount,
            # so its S record already identifies the inode commit/remount use.
            # Apply metadata on that staged inode to retain nanoseconds and
            # xattrs that the native stackable superblock otherwise truncates.
            target = self.session.backing(self.session.workspace/name, backings, staged_only=True)
            os.chown(target, value["uid"], value["gid"], follow_symlinks=False)
            if value["kind"] != "symlink":
                target.chmod(value["mode"])
            for key, content in value["xattrs"].items():
                os.setxattr(target, key, bytes.fromhex(content), follow_symlinks=False)
            if "mtime_ns" in value:
                current = target.lstat()
                os.utime(target, ns=(current.st_atime_ns, value["mtime_ns"]), follow_symlinks=False)
        self._fingerprints.clear()
        return self.record(kind="recovery")

