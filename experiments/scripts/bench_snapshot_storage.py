#!/usr/bin/env python3
"""Measure physical storage saved by content-addressed snapshots."""
from __future__ import annotations

import csv
import os
import shutil
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / "src"))

from agenttx.layers import LayerStore


def _cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _regular_bytes(root: Path, unique: bool) -> int:
    seen = set()
    total = 0
    for entry in root.rglob("*"):
        try:
            stat_result = entry.lstat()
        except FileNotFoundError:
            continue
        if not entry.is_file() or entry.is_symlink():
            continue
        identity = (stat_result.st_dev, stat_result.st_ino)
        if unique and identity in seen:
            continue
        seen.add(identity)
        total += stat_result.st_size
    return total


def main() -> int:
    scratch = Path(tempfile.mkdtemp(prefix="agenttx-snapshot-bench-", dir="/tmp"))
    try:
        upper = scratch / "upper"
        upper.mkdir()
        payload = b"x" * (64 * 1024)
        for index in range(128):
            (upper / f"file-{index:04d}.bin").write_bytes(payload)
        layers = LayerStore(scratch / "layers")
        t0 = time.perf_counter()
        snapshots = 12
        for step_id in range(snapshots):
            layers.snapshot_before(step_id, upper)
            target = upper / f"file-{step_id:04d}.bin"
            target.write_bytes(bytes([65 + step_id]) * len(payload))
        elapsed = time.perf_counter() - t0
        before_root = scratch / "layers"
        logical = sum(
            _regular_bytes(before_root / f"before_{step_id:04d}", unique=False)
            for step_id in range(snapshots)
        )
        physical = _regular_bytes(before_root, unique=True)
        blob_bytes = _regular_bytes(before_root / "blobs", unique=True)
        row = {
            "files": 128,
            "file_bytes": len(payload),
            "snapshots": snapshots,
            "logical_snapshot_bytes": logical,
            "physical_unique_bytes": physical,
            "blob_bytes": blob_bytes,
            "dedup_ratio": physical / logical,
            "elapsed_s": elapsed,
        }
        out = ROOT / "experiments" / "results"
        out.mkdir(parents=True, exist_ok=True)
        with (out / "snapshot_storage.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
        (out / "snapshot_storage.md").write_text(
            "# Content-addressed snapshot storage\n\n"
            "| files | file_bytes | snapshots | logical bytes | physical unique bytes | blob bytes | ratio | elapsed_s |\n"
            "|---:|---:|---:|---:|---:|---:|---:|---:|\n"
            f"| {row['files']} | {row['file_bytes']} | {row['snapshots']} | "
            f"{row['logical_snapshot_bytes']} | {row['physical_unique_bytes']} | "
            f"{row['blob_bytes']} | {row['dedup_ratio']:.3f} | {row['elapsed_s']:.3f} |\n",
            encoding="utf-8",
        )
        print(row)
        return 0
    finally:
        _cleanup(scratch)


if __name__ == "__main__":
    raise SystemExit(main())
