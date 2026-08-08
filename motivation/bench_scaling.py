#!/usr/bin/env python3
"""Measure current AgentTX modes over several trajectory lengths.

This is intentionally separate from the single-length comparison so the
paper-facing line plots have one consistent workload family and an explicit
length axis.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Sequence

from motivation.bench_optimization_comparison import DEFAULT_MODES, run_comparison


ROOT = Path(__file__).resolve().parents[1]


def write_outputs(rows: Sequence[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with (output_dir / "motivation_scaling.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    (output_dir / "motivation_scaling.json").write_text(
        json.dumps(list(rows), indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# AgentTX motivation scaling",
        "",
        "Current implementations over the deterministic long coding-agent workload.",
        "",
        "| length | mode | wall mean (s) | ms/step | stdev (ms/step) | failures | host polluted |",
        "|---:|---|---:|---:|---:|---:|:---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['length']} | {row['mode']} | {row['wall_mean_s']} | "
            f"{row['per_step_mean_ms']} | {row['per_step_stdev_ms']} | "
            f"{row['failures_mean']} | {row['host_polluted']} |"
        )
    (output_dir / "motivation_scaling.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lengths", nargs="+", type=int, default=[32, 64, 96])
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=list(DEFAULT_MODES),
        default=["bare", "agenttx_without_read_tracing", "agenttx_full"],
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "experiments" / "results")
    args = parser.parse_args()
    if any(length <= 0 for length in args.lengths) or args.repeats <= 0:
        parser.error("lengths and repeats must be positive")

    rows = []
    for length in args.lengths:
        for row in run_comparison(length, args.repeats, args.modes):
            # Keep the raw runner output and add a stable error-bar field used
            # by the FAST-style line plot.
            row = dict(row)
            row["per_step_stdev_ms"] = row.pop("per_step_stdev_ms", 0.0)
            rows.append(row)
    write_outputs(rows, args.output_dir)
    print(f"wrote {args.output_dir / 'motivation_scaling.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
