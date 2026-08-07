#!/usr/bin/env python3
"""Re-run the current AgentTX/baseline comparison used by the motivation section.

This script deliberately reuses the long-workload runner instead of duplicating
tool translation or correctness checks.  It measures current implementations;
the historical before/after chain is summarized by ``summarize_optimization_history.py``.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import tempfile
from pathlib import Path
from typing import List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from experiments.scripts.bench_long_trajectory import (  # noqa: E402
    cleanup,
    run_overhead,
    seed_long_repo,
    tree_snapshot,
)
from experiments.workloads.long_coding_traj import build_long_coding_trajectory  # noqa: E402
from experiments.scripts.bench_robustness import percentile  # noqa: E402


DEFAULT_MODES = (
    "bare",
    "per_call_try",
    "shared_try",
    "shared_checkpoint",
    "agenttx_without_read_tracing",
    "agenttx_full",
)


def run_comparison(length: int, repeats: int, modes: Sequence[str]) -> List[dict]:
    steps = build_long_coding_trajectory(length)
    rows: List[dict] = []
    for mode in modes:
        samples: List[dict] = []
        for repeat in range(repeats):
            scratch = Path(tempfile.mkdtemp(prefix=f"agenttx-motivation-{mode}-", dir="/tmp"))
            workdir = scratch / "ws"
            workdir.mkdir()
            seed_long_repo(workdir)
            before = tree_snapshot(workdir)
            try:
                sample = run_overhead(mode, workdir, steps, before, scratch)
                samples.append(sample)
                print(
                    f"mode={mode:32s} repeat={repeat} "
                    f"wall={sample['wall_s']:.3f}s steps={sample['n_steps']} "
                    f"failures={sample['failures']}",
                    flush=True,
                )
            finally:
                cleanup(scratch)
        walls = [float(sample["wall_s"]) for sample in samples]
        per_step = [float(sample["per_step_ms"]) for sample in samples]
        rows.append(
            {
                "suite": "motivation_runtime_comparison",
                "mode": mode,
                "length": length,
                "repeats": repeats,
                "wall_mean_s": round(statistics.mean(walls), 6),
                "wall_stdev_s": round(statistics.stdev(walls), 6) if len(walls) > 1 else 0.0,
                "wall_p50_s": round(percentile(walls, 0.50), 6),
                "wall_p95_s": round(percentile(walls, 0.95), 6),
                "per_step_mean_ms": round(statistics.mean(per_step), 3),
                "failures_mean": round(statistics.mean(float(sample["failures"]) for sample in samples), 3),
                "host_polluted": samples[-1].get("host_polluted", "") if samples else "",
            }
        )
    return rows


def write_outputs(rows: Sequence[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys()) if rows else []
    with (output_dir / "motivation_runtime_comparison.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    (output_dir / "motivation_runtime_comparison.json").write_text(
        json.dumps(list(rows), indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# Motivation runtime comparison",
        "",
        "Current implementations on the deterministic long Coding Agent workload.",
        "Historical optimization iterations are reported separately in `motivation_optimization_history.md`.",
        "",
        "| mode | wall mean (s) | wall p50 (s) | wall p95 (s) | ms/step | failures | host polluted |",
        "|---|---:|---:|---:|---:|---:|:---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['mode']} | {row['wall_mean_s']} | {row['wall_p50_s']} | "
            f"{row['wall_p95_s']} | {row['per_step_mean_ms']} | "
            f"{row['failures_mean']} | {row['host_polluted']} |"
        )
    (output_dir / "motivation_runtime_comparison.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print(f"wrote {output_dir / 'motivation_runtime_comparison.csv'}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--length", type=int, default=64)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--modes", nargs="+", choices=list(DEFAULT_MODES), default=list(DEFAULT_MODES))
    parser.add_argument("--output-dir", type=Path, default=ROOT / "experiments" / "results")
    args = parser.parse_args()
    if args.length <= 0 or args.repeats <= 0:
        parser.error("length and repeats must be positive")
    rows = run_comparison(args.length, args.repeats, args.modes)
    write_outputs(rows, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
