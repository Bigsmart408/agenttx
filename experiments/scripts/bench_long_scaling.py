#!/usr/bin/env python3
"""Scaling and variance experiment for the deterministic long workload."""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import tempfile
from pathlib import Path
from typing import List, Sequence

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from experiments.scripts.bench_long_trajectory import (  # noqa: E402
    cleanup,
    run_overhead,
    seed_long_repo,
    tree_snapshot,
)
from experiments.workloads.long_coding_traj import build_long_coding_trajectory  # noqa: E402

DEFAULT_LENGTHS = (54, 64, 96)
DEFAULT_MODES = ("bare", "agenttx_without_read_tracing", "agenttx_full")


def _mean(values: Sequence[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _stdev(values: Sequence[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0


def run_scaling(lengths: Sequence[int], modes: Sequence[str], repeats: int) -> List[dict]:
    rows: List[dict] = []
    for length in lengths:
        steps = build_long_coding_trajectory(length)
        for mode in modes:
            samples: List[dict] = []
            for repeat in range(repeats):
                scratch = Path(tempfile.mkdtemp(prefix=f"agenttx-long-scale-{mode}-", dir="/tmp"))
                ws = scratch / "ws"
                ws.mkdir()
                seed_long_repo(ws)
                before = tree_snapshot(ws)
                try:
                    sample = run_overhead(mode, ws, steps, before, scratch)
                    samples.append(sample)
                    print(
                        f"length={length:3d} mode={mode:32s} r{repeat}: "
                        f"wall={sample['wall_s']:.3f}s steps={sample['n_steps']} "
                        f"fail={sample['failures']}",
                        flush=True,
                    )
                finally:
                    cleanup(scratch)
            walls = [float(sample["wall_s"]) for sample in samples]
            per_step = [float(sample["per_step_ms"]) for sample in samples]
            failures = [float(sample["failures"]) for sample in samples]
            row = {
                "length": length,
                "mode": mode,
                "repeats": repeats,
                "wall_s_mean": round(_mean(walls), 6),
                "wall_s_stdev": round(_stdev(walls), 6),
                "per_step_ms_mean": round(_mean(per_step), 3),
                "per_step_ms_stdev": round(_stdev(per_step), 3),
                "failures_mean": round(_mean(failures), 3),
                "host_polluted": samples[-1].get("host_polluted", ""),
                "ledger_effects_mean": round(_mean([float(s.get("ledger_effects", "")) for s in samples if s.get("ledger_effects", "") != ""]), 3),
                "read_effects_mean": round(_mean([float(s.get("read_effects", "")) for s in samples if s.get("read_effects", "") != ""]), 3),
            }
            rows.append(row)
    return rows


def write_outputs(rows: Sequence[dict], lengths: Sequence[int], modes: Sequence[str], repeats: int) -> None:
    out = ROOT / "experiments" / "results"
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / "long_workload_scaling.csv"
    fields = list(rows[0].keys())
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    (out / "long_workload_scaling.json").write_text(json.dumps(list(rows), indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Long workload scaling and variance",
        "",
        f"Lengths: {', '.join(map(str, lengths))}; modes: {', '.join(modes)}; repeats per point: {repeats}.",
        "The same deterministic workload prefix is used at every length; the fault and repair remain fixed.",
        "",
        "| length | mode | wall mean (s) | wall stdev (s) | ms/step mean | ms/step stdev | failures | host polluted | ledger effects | read effects |",
        "|---:|---|---:|---:|---:|---:|---:|:---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['length']} | {row['mode']} | {row['wall_s_mean']} | {row['wall_s_stdev']} | "
            f"{row['per_step_ms_mean']} | {row['per_step_ms_stdev']} | {row['failures_mean']} | "
            f"{row['host_polluted']} | {row['ledger_effects_mean']} | {row['read_effects_mean']} |"
        )
    lines += [
        "",
        "Interpretation: the bare row is the execution lower bound; AgentTX rows include overlay, ledger, and (for full mode) strace tracing.",
        "The workload is deterministic and VM-local; these measurements are not a universal throughput claim.",
    ]
    (out / "long_workload_scaling.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {csv_path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lengths", nargs="+", type=int, default=list(DEFAULT_LENGTHS))
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--modes", nargs="+", default=list(DEFAULT_MODES), choices=list(DEFAULT_MODES))
    args = parser.parse_args()
    if args.repeats <= 0:
        parser.error("--repeats must be positive")
    rows = run_scaling(args.lengths, args.modes, args.repeats)
    write_outputs(rows, args.lengths, args.modes, args.repeats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())