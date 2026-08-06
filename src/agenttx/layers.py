"""Per-step upperdir snapshots for surgical cascade rollback."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List


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
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        if upperdir.exists():
            shutil.copytree(upperdir, dest, symlinks=True, ignore_dangling_symlinks=True)
        else:
            dest.mkdir(parents=True, exist_ok=True)
        return dest

    def restore_before(self, step_id: int, upperdir: Path) -> None:
        src = self.root / f"before_{step_id:04d}"
        if upperdir.exists():
            shutil.rmtree(upperdir, ignore_errors=True)
        if src.exists():
            shutil.copytree(src, upperdir, symlinks=True, ignore_dangling_symlinks=True)
        else:
            upperdir.mkdir(parents=True, exist_ok=True)

    def drop_from(self, step_ids: List[int]) -> None:
        for step_id in step_ids:
            shutil.rmtree(self.root / f"before_{step_id:04d}", ignore_errors=True)
