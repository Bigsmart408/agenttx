#!/usr/bin/env python3
"""Step-1 baseline: measure try setup overhead for N tool-like calls.

Baselines:
  bare          - run command directly
  per_call_try  - wrap each call with `try -n -- CMD`
  session_try   - one try shell running N commands inside

Writes a CSV summary under experiments/results/ (create then keep only CSV;
scratch dirs under /tmp are deleted).
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def try_bin(root: Path) -> Path:
    wrapper = root / "scripts" / "try-wrapper.sh"
    if wrapper.exists():
        return wrapper
    raise SystemExit("missing scripts/try-wrapper.sh")


def run_timed(cmd, cwd: Path, env=None) -> float:
    t0 = time.perf_counter()
    subprocess.run(cmd, cwd=str(cwd), env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return time.perf_counter() - t0


def make_workspace(base: Path, name: str) -> Path:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "seed.txt").write_text("seed\n", encoding="utf-8")
    return d


def bench_bare(ws: Path, n: int) -> float:
    total = 0.0
    for i in range(n):
        total += run_timed(["bash", "-c", f"echo {i} >> out.txt"], cwd=ws)
    return total


def bench_per_call_try(ws: Path, try_path: Path, n: int) -> float:
    total = 0.0
    for i in range(n):
        cmd = [str(try_path), "-n", "--", "bash", "-c", f"echo {i} >> out.txt"]
        total += run_timed(cmd, cwd=ws)
    return total


def bench_session_try(ws: Path, try_path: Path, n: int) -> float:
    # One try invocation; N commands inside a single shell.
    inner = " ; ".join([f"echo {i} >> out.txt" for i in range(n)])
    cmd = [str(try_path), "-n", "--", "bash", "-c", inner]
    return run_timed(cmd, cwd=ws)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=20, help="number of tool-like calls")
    ap.add_argument("--repeats", type=int, default=3)
    args = ap.parse_args()

    root = repo_root()
    try_path = try_bin(root)
    results_dir = root / "experiments" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    scratch = Path(tempfile.mkdtemp(prefix="agenttx-bench-", dir="/tmp"))
    rows = []
    try:
        for mode, fn in [
            ("bare", lambda ws: bench_bare(ws, args.n)),
            ("per_call_try", lambda ws: bench_per_call_try(ws, try_path, args.n)),
            ("session_try", lambda ws: bench_session_try(ws, try_path, args.n)),
        ]:
            samples = []
            for r in range(args.repeats):
                ws = make_workspace(scratch, f"{mode}-{r}")
                try:
                    samples.append(fn(ws))
                except subprocess.CalledProcessError as e:
                    print(f"FAIL {mode} repeat={r}: {e}", file=sys.stderr)
                    raise
            mean = statistics.mean(samples)
            stdev = statistics.stdev(samples) if len(samples) > 1 else 0.0
            per_call = mean / args.n
            rows.append(
                {
                    "mode": mode,
                    "n_calls": args.n,
                    "repeats": args.repeats,
                    "total_s_mean": f"{mean:.6f}",
                    "total_s_stdev": f"{stdev:.6f}",
                    "per_call_s_mean": f"{per_call:.6f}",
                }
            )
            print(f"{mode:14s} total={mean:.4f}s ±{stdev:.4f}  per_call={per_call*1000:.2f}ms  (n={args.n})")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    out = results_dir / f"try_overhead_n{args.n}.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
