#!/usr/bin/env python3
"""Measure incremental per-step overhead from automatic dependency tracing."""

from __future__ import annotations

import argparse
import csv
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agenttx.runtime import AgentTX


def _cleanup(path: Path) -> None:
    subprocess.run(
        ["chmod", "-R", "u+rwX", str(path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    shutil.rmtree(path, ignore_errors=True)


def measure(n_steps: int, trace_reads: bool) -> float:
    scratch = Path(tempfile.mkdtemp(prefix="agenttx-trace-bench-", dir="/tmp"))
    workspace = scratch / "ws"
    workspace.mkdir()
    tx = None
    try:
        tx = AgentTX.begin(
            workdir=workspace,
            session_dir=scratch / "session",
            trace_reads=trace_reads,
        )
        started = time.perf_counter()
        for index in range(n_steps):
            tx.run_tool(f"noop-{index}", ["bash", "-c", ":"])
        return (time.perf_counter() - started) / n_steps
    finally:
        if tx is not None and tx.pool is not None:
            tx.close(destroy=True)
        _cleanup(scratch)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", "--steps", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.steps <= 0 or args.repeats <= 0:
        parser.error("steps and repeats must be positive")

    rows = []
    for trace_reads in (False, True):
        samples = [measure(args.steps, trace_reads) for _ in range(args.repeats)]
        rows.append(
            {
                "mode": "trace_on" if trace_reads else "trace_off",
                "trace_reads": trace_reads,
                "steps": args.steps,
                "repeats": args.repeats,
                "per_step_ms_mean": statistics.mean(samples) * 1000.0,
                "per_step_ms_stdev": (
                    statistics.stdev(samples) * 1000.0
                    if len(samples) > 1
                    else 0.0
                ),
            }
        )

    results = ROOT / "experiments" / "results"
    results.mkdir(parents=True, exist_ok=True)
    csv_path = results / "trace_overhead.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    baseline = rows[0]["per_step_ms_mean"]
    traced = rows[1]["per_step_ms_mean"]
    delta = traced - baseline
    percent = (delta / baseline) * 100.0
    md_path = results / "trace_overhead.md"
    lines = [
        "# Automatic dependency-tracing overhead",
        "",
        f"No-op tool calls; {args.steps} steps per run, {args.repeats} repeats.",
        "",
        "| mode | per_step_ms_mean | per_step_ms_stdev |",
        "|---|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['mode']} | {row['per_step_ms_mean']:.2f} | "
            f"{row['per_step_ms_stdev']:.2f} |"
        )
    lines.extend(
        [
            "",
            f"Incremental tracing cost: {delta:.2f} ms/step ({percent:.1f}%).",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(md_path.read_text(encoding="utf-8"))
    print(f"wrote {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
