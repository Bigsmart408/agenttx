#!/usr/bin/env python3
"""Collect tail-latency curves for several AgentTX trajectory lengths."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Sequence

from experiments.scripts.bench_robustness import run_tail_latency


ROOT = Path(__file__).resolve().parents[1]


def write_outputs(rows: Sequence[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with (output_dir / "motivation_tail_scaling.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    (output_dir / "motivation_tail_scaling.json").write_text(
        json.dumps(list(rows), indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# AgentTX motivation tail scaling",
        "",
        "p50/p95 tail measurements over several deterministic workload lengths.",
        "",
        "| length | mode | step p50 (ms) | step p95 (ms) | run p50 (ms) | run p95 (ms) | failure rate |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['length']} | {row['mode']} | {row['step_p50_ms']} | "
            f"{row['step_p95_ms']} | {row['run_p50_ms']} | "
            f"{row['run_p95_ms']} | {row['failure_rate']} |"
        )
    (output_dir / "motivation_tail_scaling.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lengths", nargs="+", type=int, default=[32, 64, 96])
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=["agenttx_without_read_tracing", "agenttx_full"],
        default=["agenttx_without_read_tracing", "agenttx_full"],
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "experiments" / "results")
    args = parser.parse_args()
    if any(length <= 0 for length in args.lengths) or args.repeats <= 0:
        parser.error("lengths and repeats must be positive")

    rows = []
    for length in args.lengths:
        for mode in args.modes:
            rows.append(run_tail_latency(mode, length, args.repeats))
    write_outputs(rows, args.output_dir)
    print(f"wrote {args.output_dir / 'motivation_tail_scaling.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
