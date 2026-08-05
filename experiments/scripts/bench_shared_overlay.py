#!/usr/bin/env python3
"""Step-2 bench: per-call try vs shared -N overlay (AgentTX pool)."""

from __future__ import annotations

import csv
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agenttx.runtime import AgentTXRuntime


def try_bin() -> Path:
    return ROOT / "scripts" / "try-wrapper.sh"


def bench_per_call(ws: Path, n: int) -> float:
    total = 0.0
    tb = try_bin()
    for i in range(n):
        t0 = time.perf_counter()
        subprocess.run(
            [str(tb), "-n", "--", "bash", "-c", f"echo {i} >> out.txt"],
            cwd=str(ws),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        total += time.perf_counter() - t0
    return total


def bench_shared(ws: Path, n: int):
    t0 = time.perf_counter()
    with AgentTXRuntime(workspace=ws) as rt:
        for i in range(n):
            rec = rt.run_tool("shell", ["bash", "-c", f"echo {i} >> out.txt"])
            if rec.returncode != 0:
                raise RuntimeError(f"tool failed rc={rec.returncode}")
        ledger = rt.ledger.to_dict()
    return time.perf_counter() - t0, ledger


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    repeats = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    out_dir = ROOT / "experiments" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="agenttx-shared-bench-", dir="/tmp"))
    rows = []
    try:
        for mode in ("per_call_try", "shared_overlay"):
            samples = []
            last_ledger = None
            for r in range(repeats):
                ws = scratch / f"{mode}-{r}"
                ws.mkdir(parents=True)
                (ws / "seed.txt").write_text("seed\n", encoding="utf-8")
                if mode == "per_call_try":
                    samples.append(bench_per_call(ws, n))
                else:
                    total, last_ledger = bench_shared(ws, n)
                    samples.append(total)
            mean = statistics.mean(samples)
            stdev = statistics.stdev(samples) if len(samples) > 1 else 0.0
            rows.append(
                {
                    "mode": mode,
                    "n_calls": n,
                    "repeats": repeats,
                    "total_s_mean": f"{mean:.6f}",
                    "total_s_stdev": f"{stdev:.6f}",
                    "per_call_s_mean": f"{mean / n:.6f}",
                }
            )
            print(f"{mode:16s} total={mean:.4f}s +/-{stdev:.4f}  per_call={mean/n*1000:.2f}ms")
            if last_ledger is not None:
                dump = out_dir / f"shared_overlay_ledger_n{n}.json"
                dump.write_text(json.dumps(last_ledger, indent=2) + "\n", encoding="utf-8")
                print(f"wrote {dump}")
    finally:
        subprocess.run(
            ["bash", "-lc", f"chmod -R u+rwX '{scratch}' 2>/dev/null || true"],
            check=False,
        )
        shutil.rmtree(scratch, ignore_errors=True)

    csv_path = out_dir / f"shared_overlay_n{n}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())