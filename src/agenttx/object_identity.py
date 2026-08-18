"""Host-side identity discovery for selective commit.

OverlayFS can expose one lower inode through several directory entries.  A
path-only commit must therefore expand a selected regular-file effect to the
complete hard-link group before materializing it.  This module deliberately
keeps the scope small: it discovers existing host hard-link groups and fails
closed when an alias is outside the workspace or cannot be enumerated.  New
links created only inside the speculative overlay still require the future
object-id ledger described in ``docs/step29-hardlink-preserving-transactions.md``.
"""

from __future__ import annotations

import os
import stat
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


class HardlinkTopologyError(ValueError):
    """The complete hard-link group cannot be proven from the host view."""


@dataclass(frozen=True)
class HardlinkGroup:
    """A complete set of workspace paths sharing one regular-file inode."""

    device: int
    inode: int
    paths: Tuple[str, ...]


@dataclass
class ObjectRecord:
    """Persisted identity for one regular-file object in a session."""

    object_id: str
    device: int
    inode: int
    ctime_ns: int
    aliases: Tuple[str, ...]
    observed_nlink: int
    complete: bool
    generation: int = 0

    @property
    def base_token(self) -> Tuple[int, int, int]:
        return self.device, self.inode, self.ctime_ns

    def to_dict(self) -> dict:
        return {
            "object_id": self.object_id,
            "device": self.device,
            "inode": self.inode,
            "ctime_ns": self.ctime_ns,
            "aliases": list(self.aliases),
            "observed_nlink": self.observed_nlink,
            "complete": self.complete,
            "generation": self.generation,
        }

    @staticmethod
    def from_dict(payload: dict) -> "ObjectRecord":
        return ObjectRecord(
            object_id=str(payload["object_id"]),
            device=int(payload["device"]),
            inode=int(payload["inode"]),
            ctime_ns=int(payload.get("ctime_ns", 0)),
            aliases=tuple(sorted(str(path) for path in payload.get("aliases", []))),
            observed_nlink=int(payload.get("observed_nlink", 0)),
            complete=bool(payload.get("complete", False)),
            generation=int(payload.get("generation", 0)),
        )


@dataclass
class HardlinkCatalog:
    """Session-persisted object map used by ledger dependency construction.

    The catalog is intentionally conservative.  Device/inode/ctime identify
    an object only within the current host generation; the random ``object_id``
    is the durable session key.  A group with an unobserved external alias is
    retained as ``complete=False`` so commit policy can reject it later.
    """

    records: Dict[str, ObjectRecord] = field(default_factory=dict)
    generation: int = 0

    def __post_init__(self) -> None:
        return None

    @classmethod
    def from_dict(cls, payload: Optional[dict]) -> "HardlinkCatalog":
        payload = payload or {}
        records = {
            record["object_id"]: ObjectRecord.from_dict(record)
            for record in payload.get("records", [])
        }
        return cls(records=records, generation=int(payload.get("generation", 0)))

    def to_dict(self) -> dict:
        return {
            "generation": self.generation,
            "records": [record.to_dict() for record in self.records.values()],
        }

    def _previous_by_token(self) -> Dict[Tuple[int, int, int], ObjectRecord]:
        return {record.base_token: record for record in self.records.values()}

    def _previous_by_alias(self) -> Dict[str, ObjectRecord]:
        return {
            alias: record
            for record in self.records.values()
            for alias in record.aliases
        }

    def refresh(self, workspace: Path) -> None:
        """Rescan complete and partial regular-file groups in ``workspace``."""

        workspace = Path(workspace).resolve()
        grouped: Dict[Tuple[int, int], List[Tuple[Path, os.stat_result]]] = {}
        for root, directories, filenames in os.walk(
            workspace, topdown=True, followlinks=False
        ):
            directories[:] = [name for name in directories if name not in {".git"}]
            for name in filenames:
                path = Path(root) / name
                try:
                    result = path.lstat()
                except (FileNotFoundError, PermissionError, OSError):
                    continue
                if stat.S_ISREG(result.st_mode) and result.st_nlink > 1:
                    grouped.setdefault((result.st_dev, result.st_ino), []).append(
                        (path, result)
                    )

        previous = self._previous_by_token()
        previous_aliases = self._previous_by_alias()
        refreshed: Dict[str, ObjectRecord] = {}
        for (device, inode), entries in grouped.items():
            aliases = tuple(sorted(str(path) for path, _ in entries))
            representative = entries[0][1]
            token = (device, inode, representative.st_ctime_ns)
            old = previous.get(token)
            if old is None:
                # In-place publication changes ctime while preserving the
                # inode and alias set.  Alias overlap keeps the session object
                # id stable across that generation boundary.
                old = next(
                    (previous_aliases[alias] for alias in aliases if alias in previous_aliases),
                    None,
                )
            object_id = old.object_id if old is not None else str(uuid.uuid4())
            refreshed[object_id] = ObjectRecord(
                object_id=object_id,
                device=device,
                inode=inode,
                ctime_ns=representative.st_ctime_ns,
                aliases=aliases,
                observed_nlink=representative.st_nlink,
                complete=len(aliases) == representative.st_nlink,
                generation=self.generation,
            )
        self.records = refreshed

    def object_for_path(self, path: str) -> Optional[ObjectRecord]:
        for record in self.records.values():
            if path in record.aliases:
                return record
        return None

    def annotate(self, effects, generation: Optional[int] = None):
        """Attach object identity to effects that name a known alias."""

        from dataclasses import replace

        if generation is None:
            generation = self.generation
        annotated = []
        for effect in effects:
            record = self.object_for_path(effect.path)
            if record is None:
                annotated.append(effect)
                continue
            topology_op = "unlink" if effect.kind.value == "D" else "none"
            annotated.append(
                replace(
                    effect,
                    object_id=record.object_id,
                    object_version=record.generation,
                    topology_op=topology_op,
                )
            )
        return annotated


def _candidate_stat(path: Path) -> Optional[os.stat_result]:
    try:
        result = path.lstat()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    if not stat.S_ISREG(result.st_mode) or result.st_nlink <= 1:
        return None
    return result


def _scan_inode(workspace: Path, device: int, inode: int) -> List[str]:
    """Return every regular workspace entry for ``(device, inode)``.

    ``os.walk`` does not follow symlinked directories, so an attacker cannot
    turn identity discovery into an unbounded walk through the host.  Files
    that disappear during the scan are ignored and subsequently trigger the
    fail-closed link-count check.
    """

    aliases: List[str] = []
    for root, directories, filenames in os.walk(
        workspace, topdown=True, followlinks=False
    ):
        directories[:] = [name for name in directories if name not in {".git"}]
        for name in filenames:
            path = Path(root) / name
            try:
                result = path.lstat()
            except (FileNotFoundError, PermissionError, OSError):
                continue
            if (
                stat.S_ISREG(result.st_mode)
                and result.st_dev == device
                and result.st_ino == inode
            ):
                aliases.append(str(path))
    return sorted(set(aliases))


def discover_hardlink_group(path: Path, workspace: Path) -> Optional[HardlinkGroup]:
    """Discover the complete host-side group containing ``path``.

    ``None`` means that the current host entry is not a multi-link regular
    file.  If ``st_nlink`` disagrees with the enumerated aliases, the caller
    must not perform a path-only commit because an unobserved alias could be
    split.
    """

    workspace = Path(workspace).resolve()
    # Keep the final directory entry lexical: following a symlink here would
    # mistake a symlink to a hard-linked file for another hard-link alias.
    path = Path(os.path.abspath(path))
    try:
        path.relative_to(workspace)
    except ValueError:
        raise HardlinkTopologyError(f"hard-link candidate is outside workspace: {path}")

    result = _candidate_stat(path)
    if result is None:
        return None
    aliases = _scan_inode(workspace, result.st_dev, result.st_ino)
    if len(aliases) != result.st_nlink:
        raise HardlinkTopologyError(
            "cannot prove complete hard-link group for "
            f"{path}: inode link count is {result.st_nlink}, "
            f"workspace scan found {len(aliases)} aliases"
        )
    return HardlinkGroup(result.st_dev, result.st_ino, tuple(aliases))


def expand_hardlink_paths(
    paths: Iterable[str], workspace: Path
) -> Tuple[List[str], List[HardlinkGroup]]:
    """Expand selected paths to complete existing hard-link groups."""

    workspace = Path(workspace).resolve()
    expanded = {str(Path(os.path.abspath(raw))) for raw in paths}
    groups: Dict[Tuple[int, int], HardlinkGroup] = {}
    for raw in sorted(expanded):
        group = discover_hardlink_group(Path(raw), workspace)
        if group is None:
            continue
        groups[(group.device, group.inode)] = group
        expanded.update(group.paths)
    return sorted(expanded), [groups[key] for key in sorted(groups)]
