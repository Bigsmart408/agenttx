#!/usr/bin/env python3
"""Run the controlled task-length and fault-position motivation sweeps."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from experiments.scripts.bench_intra_turn_recovery import (  # noqa: E402
    POLICIES,
    analytical_rows,
    git_state,
)
from experiments.workloads.intra_turn_recovery_dag import (  # noqa: E402
    build_multiturn_controlled_waste_plan,
)


SCHEMA = "agenttx.motivation.controlled-waste/v4"


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def summarize(rows: list[dict]) -> list[dict]:
    grouped: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[(int(row["turn_count"]), str(row["policy"]))].append(row)
    output = []
    for (turn_count, policy), group in sorted(grouped.items()):
        output.append(
            {
                "turn_count": turn_count,
                "turn_width": int(group[0]["turn_width"]),
                "total_operations": int(group[0]["loop_width"]),
                "policy": policy,
                "cases": len(group),
                "closure_size": int(group[0]["closure_size"]),
                "useful_steps_wasted_mean": sum(
                    float(row["useful_steps_wasted"]) for row in group
                ) / len(group),
                "useful_tokens_wasted_mean": sum(
                    float(row["useful_tokens_wasted"]) for row in group
                ) / len(group),
                "invalid_removal_mean": sum(
                    float(row["invalid_removal"]) for row in group
                ) / len(group),
                "rollback_safe_rate": sum(
                    row["rollback_safe"] is True for row in group
                ) / len(group),
            }
        )
    return output


def summarize_positions(rows: list[dict]) -> list[dict]:
    grouped: dict[tuple[float, str], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[(float(row["requested_fault_position"]), str(row["policy"]))].append(row)
    output = []
    for (position, policy), group in sorted(grouped.items()):
        output.append(
            {
                "fault_position": position,
                "policy": policy,
                "cases": len(group),
                "loop_width": int(group[0]["loop_width"]),
                "closure_size": int(group[0]["closure_size"]),
                "useful_steps_wasted_mean": sum(
                    float(row["useful_steps_wasted"]) for row in group
                ) / len(group),
                "invalid_removal_mean": sum(
                    float(row["invalid_removal"]) for row in group
                ) / len(group),
            }
        )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--turn-counts",
        nargs="+",
        type=int,
        default=list(range(1, 11)),
    )
    parser.add_argument("--turn-width", type=int, default=16)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument(
        "--fault-positions",
        nargs="+",
        type=float,
        default=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9],
    )
    parser.add_argument("--position-sweep-turn-count", type=int, default=10)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.repeats <= 0:
        parser.error("repeats must be positive")
    if any(not 1 <= value <= 10 for value in args.turn_counts):
        parser.error("turn counts must be in [1, 10]")
    if args.turn_width < 16:
        parser.error("turn width must be at least 16")

    rows: list[dict] = []
    for turn_count in args.turn_counts:
        for repeat in range(args.repeats):
            plan = build_multiturn_controlled_waste_plan(
                turn_count,
                turn_width=args.turn_width,
                seed=repeat,
            )
            plan_rows = analytical_rows(plan, repeat)
            for row in plan_rows:
                row["phase"] = "controlled"
            rows.extend(plan_rows)

    expected = len(args.turn_counts) * args.repeats * len(POLICIES)
    if len(rows) != expected:
        raise AssertionError((len(rows), expected))
    for row in rows:
        if int(row["closure_size"]) != 4:
            raise AssertionError("controlled fault closure changed size")

    position_rows: list[dict] = []
    for position in args.fault_positions:
        for repeat in range(args.repeats):
            plan = build_multiturn_controlled_waste_plan(
                args.position_sweep_turn_count,
                turn_width=args.turn_width,
                seed=repeat,
                fault_position=position,
            )
            plan_rows = analytical_rows(plan, repeat)
            for row in plan_rows:
                row["phase"] = "controlled-position"
            position_rows.extend(plan_rows)
    expected_positions = len(args.fault_positions) * args.repeats * len(POLICIES)
    if len(position_rows) != expected_positions or any(
        int(row["closure_size"]) != 4 for row in position_rows
    ):
        raise AssertionError("controlled position sweep is incomplete")

    summaries = summarize(rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "controlled_recovery_raw.csv", rows)
    write_csv(args.output_dir / "controlled_recovery_summary.csv", summaries)
    write_csv(args.output_dir / "controlled_position_raw.csv", position_rows)
    write_csv(
        args.output_dir / "controlled_position_summary.csv",
        summarize_positions(position_rows),
    )
    (args.output_dir / "controlled_recovery.json").write_text(
        json.dumps({"raw": rows, "summary": summaries}, indent=2) + "\n",
        encoding="utf-8",
    )
    config = {
        "schema": SCHEMA,
        "turn_counts": args.turn_counts,
        "turn_width": args.turn_width,
        "repeats": args.repeats,
        "policies": list(POLICIES),
        "fault_position": 0.25,
        "fixed_closure_size": 4,
        "planned_cells": len(args.turn_counts) * args.repeats,
        "fault_positions": args.fault_positions,
        "position_sweep_turn_count": args.position_sweep_turn_count,
        "planned_position_cells": len(args.fault_positions) * args.repeats,
        "execution_kind": "ledger_analytical",
        "git": git_state(),
    }
    (args.output_dir / "config.json").write_text(
        json.dumps(config, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(rows)} rows to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
