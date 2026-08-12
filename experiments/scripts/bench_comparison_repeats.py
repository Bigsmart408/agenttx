#!/usr/bin/env python3
"""Repeated comparison-baseline experiment with tail statistics.

This keeps the original three-repeat comparison matrix intact and writes a
separate artifact.  Every overhead sample starts from a fresh workspace; the
recovery check is also repeated so the semantic result is reported as a rate.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import subprocess
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.scripts.bench_comparison_matrix import (  # noqa: E402
    TRAJECTORY,
    cleanup,
    overhead_modes,
    run_recovery,
)

OUT = ROOT / "experiments" / "results"
MODES = [
    "bare",
    "per_call_try",
    "session_try",
    "shared_try",
    "shared_checkpoint",
    "bubblewrap",
    "agenttx_without_read_tracing",
    "agenttx_full",
]


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def run_overhead_repeats(repeats: int, n: int) -> list[dict]:
    commands = [f"echo {i} >> out.txt" for i in range(n)]
    rows: list[dict] = []
    for mode, fn in overhead_modes(n).items():
        samples: list[float] = []
        supported = True
        note = ""
        for repeat in range(repeats):
            scratch = Path(tempfile.mkdtemp(prefix=f"agenttx-repeat-{mode}-", dir="/tmp"))
            workspace = scratch / "ws"
            workspace.mkdir()
            try:
                samples.append(float(fn(workspace, commands)))
            except (FileNotFoundError, RuntimeError, subprocess.SubprocessError) as exc:
                if mode == "bubblewrap":
                    supported = False
                    note = f"unavailable: {str(exc).strip()[:180]}"
                    break
                raise
            finally:
                cleanup(scratch)
            print(f"overhead {mode} repeat={repeat + 1}/{repeats}", flush=True)
        if not samples:
            rows.append(
                {
                    "mode": mode,
                    "supported": supported,
                    "samples": 0,
                    "repeats_requested": repeats,
                    "n_calls": n,
                    "wall_s_mean": None,
                    "wall_s_stdev": None,
                    "wall_s_p50": None,
                    "wall_s_p95": None,
                    "wall_s_p99": None,
                    "per_step_ms_mean": None,
                    "per_step_ms_p50": None,
                    "per_step_ms_p95": None,
                    "per_step_ms_p99": None,
                    "note": note,
                }
            )
            continue
        mean = statistics.mean(samples)
        stdev = statistics.stdev(samples) if len(samples) > 1 else 0.0
        p50 = percentile(samples, 0.50)
        p95 = percentile(samples, 0.95)
        p99 = percentile(samples, 0.99)
        rows.append(
            {
                "mode": mode,
                "supported": supported,
                "samples": len(samples),
                "repeats_requested": repeats,
                "n_calls": n,
                "wall_s_mean": round(mean, 6),
                "wall_s_stdev": round(stdev, 6),
                "wall_s_p50": round(p50, 6),
                "wall_s_p95": round(p95, 6),
                "wall_s_p99": round(p99, 6),
                "per_step_ms_mean": round(mean / n * 1000.0, 3),
                "per_step_ms_p50": round(p50 / n * 1000.0, 3),
                "per_step_ms_p95": round(p95 / n * 1000.0, 3),
                "per_step_ms_p99": round(p99 / n * 1000.0, 3),
                "note": note,
            }
        )
    return rows


def run_recovery_repeats(repeats: int) -> list[dict]:
    rows: list[dict] = []
    for mode in MODES:
        samples: list[dict] = []
        for repeat in range(repeats):
            result = run_recovery(mode)
            samples.append(result)
            print(f"recovery {mode} repeat={repeat + 1}/{repeats}", flush=True)
        supported = [bool(row.get("supported", True)) for row in samples]
        correct = [bool(row.get("causal_retention_correct")) for row in samples]
        rows.append(
            {
                "mode": mode,
                "samples": len(samples),
                "supported_rate": round(sum(supported) / len(samples), 6),
                "causal_correct_count": sum(correct),
                "causal_correct_rate": round(sum(correct) / len(samples), 6),
                "host_clean_rate": round(
                    sum(bool(row.get("host_clean_before_recovery")) for row in samples)
                    / len(samples),
                    6,
                ),
                "notes": sorted({str(row.get("note", "")) for row in samples}),
            }
        )
    return rows


def write_outputs(overhead: list[dict], recovery: list[dict], repeats: int, n: int) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    payload = {
        "suite": "comparison_repeats",
        "repeats": repeats,
        "n_calls": n,
        "overhead": overhead,
        "recovery": recovery,
    }
    (OUT / "comparison_repeats.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    csv_rows: list[dict] = []
    for row in overhead:
        csv_rows.append({"suite": "overhead", **row})
    for row in recovery:
        csv_rows.append({"suite": "recovery", **row})
    fields = sorted({key for row in csv_rows for key in row})
    with (OUT / "comparison_repeats.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(csv_rows)

    lines = [
        "# Repeated comparison baselines",
        "",
        f"Fixed trajectory: {n} writes; independent fresh-workspace samples per mode: {repeats}.",
        "",
        "## Runtime distribution",
        "",
        "| mode | samples | mean ms/step | p50 ms/step | p95 ms/step | p99 ms/step | stdev s |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in overhead:
        lines.append(
            f"| {row['mode']} | {row['samples']} | {row['per_step_ms_mean']} | "
            f"{row['per_step_ms_p50']} | {row['per_step_ms_p95']} | {row['per_step_ms_p99']} | {row['wall_s_stdev']} |"
        )
    lines += [
        "",
        "## Recovery semantics",
        "",
        "| mode | samples | supported rate | causal-correct count | causal-correct rate | host-clean rate |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in recovery:
        lines.append(
            f"| {row['mode']} | {row['samples']} | {row['supported_rate']} | "
            f"{row['causal_correct_count']} | {row['causal_correct_rate']} | {row['host_clean_rate']} |"
        )
    lines += [
        "",
        "The repeated runtime rows quantify VM variance; the recovery rows test whether the semantic result is stable across fresh workspaces.",
        "Bubblewrap is an isolation/abort lower bound, not a causal-recovery implementation.",
    ]
    (OUT / "comparison_repeats.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT / 'comparison_repeats.csv'}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--n", type=int, default=10)
    args = parser.parse_args()
    if args.repeats <= 0 or args.n <= 0:
        parser.error("repeats and n must be positive")
    overhead = run_overhead_repeats(args.repeats, args.n)
    recovery = run_recovery_repeats(args.repeats)
    write_outputs(overhead, recovery, args.repeats, args.n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
