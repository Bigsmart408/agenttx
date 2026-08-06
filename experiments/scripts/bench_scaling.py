#!/usr/bin/env python3
"""Scaling curve: bare vs per-call try vs shared AgentTX overlay."""
from __future__ import annotations

import csv
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agenttx.runtime import AgentTX


def try_bin() -> Path:
    return ROOT / "scripts" / "try-wrapper.sh"


def _cleanup(scratch: Path) -> None:
    subprocess.run(["bash", "-lc", f"chmod -R u+rwX '{scratch}' 2>/dev/null || true"], check=False)
    shutil.rmtree(scratch, ignore_errors=True)


def run_bare(n: int) -> float:
    scratch = Path(tempfile.mkdtemp(prefix="agenttx-scale-bare-", dir="/tmp"))
    ws = scratch / "ws"
    ws.mkdir()
    t0 = time.perf_counter()
    try:
        for i in range(n):
            subprocess.run(["bash", "-c", f"echo {i} >> out.txt"], cwd=str(ws), check=True)
        return time.perf_counter() - t0
    finally:
        _cleanup(scratch)


def run_per_call(n: int) -> float:
    scratch = Path(tempfile.mkdtemp(prefix="agenttx-scale-pc-", dir="/tmp"))
    ws = scratch / "ws"
    ws.mkdir()
    tb = try_bin()
    t0 = time.perf_counter()
    try:
        for i in range(n):
            subprocess.run(
                [str(tb), "-n", "--", "bash", "-c", f"echo {i} >> out.txt"],
                cwd=str(ws),
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        return time.perf_counter() - t0
    finally:
        _cleanup(scratch)


def run_shared(n: int) -> float:
    scratch = Path(tempfile.mkdtemp(prefix="agenttx-scale-sh-", dir="/tmp"))
    ws = scratch / "ws"
    ws.mkdir()
    t0 = time.perf_counter()
    try:
        tx = AgentTX.begin(workdir=ws, session_dir=scratch / "sess")
        for i in range(n):
            tx.run_tool("w", ["bash", "-c", f"echo {i} >> out.txt"])
        tx.close(destroy=True)
        return time.perf_counter() - t0
    finally:
        _cleanup(scratch)


def main() -> int:
    ns = [5, 10, 20, 40]
    repeats = 2
    out = ROOT / "experiments" / "results"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for n in ns:
        for mode, fn in (("bare", run_bare), ("per_call_try", run_per_call), ("shared_agenttx", run_shared)):
            walls = []
            for _ in range(repeats):
                walls.append(fn(n))
            mean = sum(walls) / len(walls)
            rows.append(
                {
                    "n": n,
                    "mode": mode,
                    "repeats": repeats,
                    "wall_s_mean": mean,
                    "per_step_ms": (mean / n) * 1000.0,
                }
            )
            print(f"n={n} mode={mode} wall={mean:.3f}s per_step={mean/n*1000:.1f}ms", flush=True)
    csv_path = out / "scaling_curve.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["n", "mode", "repeats", "wall_s_mean", "per_step_ms"])
        w.writeheader()
        w.writerows(rows)
    md = out / "scaling_curve.md"
    lines = [
        "# Scaling curve (bare / per-call try / shared AgentTX)",
        "",
        "| n | mode | wall_s_mean | per_step_ms |",
        "|---:|---|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['n']} | {r['mode']} | {r['wall_s_mean']:.3f} | {r['per_step_ms']:.1f} |"
        )
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(md.read_text(encoding="utf-8"))
    print(f"wrote {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
