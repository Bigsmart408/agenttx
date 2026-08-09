#!/usr/bin/env python3
"""Quantify useful-work retention for causal vs temporal recovery policies."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import statistics
import subprocess
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "experiments" / "results"
import sys

sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from agenttx.runtime import AgentTX  # noqa: E402
from experiments.workloads.causal_retention_dag import (  # noqa: E402
    SHAPES,
    RetentionPlan,
    build_causal_retention_plan,
)


MODES = (
    "causal",
    "temporal",
    "whole_session",
    "causal_without_dependencies",
    "causal_traced",
)


def cleanup(path: Path) -> None:
    subprocess.run(
        ["bash", "-lc", f"chmod -R u+rwX '{path}' 2>/dev/null || true"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    shutil.rmtree(path, ignore_errors=True)


def percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _present_ids(plan: RetentionPlan, workspace: Path) -> set[int]:
    return {
        index
        for index, step in enumerate(plan.steps)
        if (workspace / step.relative_path).exists()
    }


def score_retention(
    plan: RetentionPlan,
    actual_targets: Iterable[int],
    present_ids: Iterable[int],
) -> dict:
    expected = set(plan.expected_rollback_ids)
    independent = set(plan.independent_ids)
    actual = set(actual_targets)
    present = set(present_ids)
    true_targets = actual & expected
    false_targets = actual & independent
    missed_targets = expected - actual
    retained_independent = independent & present
    retained_invalid = expected & present
    precision = len(true_targets) / max(len(actual), 1)
    recall = len(true_targets) / max(len(expected), 1)
    return {
        "rollback_precision": precision,
        "rollback_recall": recall,
        "independent_retention": len(retained_independent) / max(len(independent), 1),
        "target_removed": 1.0 - len(retained_invalid) / max(len(expected), 1),
        "useful_work_lost": len(independent - present),
        "invalid_work_retained": len(retained_invalid),
        "false_rollback_count": len(false_targets),
        "missed_target_count": len(missed_targets),
        "final_correct": not retained_invalid and independent.issubset(present),
    }


def run_case(plan: RetentionPlan, mode: str, repeat: int, config: dict) -> dict:
    if mode not in MODES:
        raise ValueError(f"unsupported mode: {mode}")
    scratch = Path(tempfile.mkdtemp(prefix=f"agenttx-causal-{mode}-", dir="/tmp"))
    workspace = scratch / "ws"
    workspace.mkdir()
    trace_reads = mode == "causal_traced"
    tx = AgentTX.begin(
        workdir=workspace,
        session_dir=scratch / "session",
        trace_reads=trace_reads,
    )
    execution_start = time.perf_counter()
    records = []
    try:
        for step in plan.steps:
            extra_reads = None
            if mode not in {"causal_without_dependencies", "causal_traced"}:
                extra_reads = [str((workspace / path).resolve()) for path in step.parents]
            record = tx.run_tool(
                step.name,
                step.command(),
                extra_reads=extra_reads,
            )
            if record.returncode != 0:
                raise RuntimeError(record.stderr or f"step failed: {step.name}")
            records.append(record)
        execution_ms = (time.perf_counter() - execution_start) * 1000.0
        host_clean_before_commit = not any(workspace.rglob("*.txt"))

        rollback_start = time.perf_counter()
        if mode in {"causal", "causal_without_dependencies", "causal_traced"}:
            actual_targets = tx.rollback_causal(plan.fault_step_id)
        elif mode == "temporal":
            actual_targets = tx.rollback(plan.fault_step_id)
        else:
            actual_targets = tx.rollback(0)
        rollback_ms = (time.perf_counter() - rollback_start) * 1000.0

        merged_present = {
            index
            for index, step in enumerate(plan.steps)
            if tx.path_exists(workspace / step.relative_path)
        }
        active = [
            step.step_id
            for step in tx.ledger.steps
            if step.status != "rolled_back"
            and step.step_id > tx.ledger.committed_frontier
        ]
        commit_start = time.perf_counter()
        if active:
            tx.commit(max(active))
        commit_ms = (time.perf_counter() - commit_start) * 1000.0
        final_present = _present_ids(plan, workspace)
        score = score_retention(plan, actual_targets, final_present)
        expected = set(plan.expected_rollback_ids)
        merged_correct = not (expected & merged_present) and set(plan.independent_ids).issubset(
            merged_present
        )
        return {
            **config,
            "mode": mode,
            "repeat": repeat,
            "total_steps": plan.total_steps,
            "shape": plan.shape,
            "fault_step": plan.fault_step_id,
            "fault_fraction": round(plan.actual_fault_fraction, 6),
            "independent_fraction": round(plan.actual_independent_fraction, 6),
            "expected_target_count": len(plan.expected_rollback_ids),
            "independent_count": len(plan.independent_ids),
            "actual_target_count": len(actual_targets),
            "ledger_edges": sum(len(step.parents) for step in tx.ledger.steps),
            "execution_ms": round(execution_ms, 3),
            "rollback_ms": round(rollback_ms, 3),
            "commit_ms": round(commit_ms, 3),
            "host_clean_before_commit": host_clean_before_commit,
            "merged_correct_after_rollback": merged_correct,
            **score,
        }
    finally:
        tx.close(destroy=True)
        cleanup(scratch)


def build_configs(args: argparse.Namespace) -> List[dict]:
    configs: List[dict] = []
    config_id = 0

    def add(sweep: str, x_value: object, total: int, shape: str, fault: float, independent: float) -> None:
        nonlocal config_id
        configs.append(
            {
                "config_id": config_id,
                "sweep": sweep,
                "x_value": x_value,
                "requested_total_steps": total,
                "requested_shape": shape,
                "requested_fault_fraction": fault,
                "requested_independent_fraction": independent,
            }
        )
        config_id += 1

    selected = set(args.sweeps)
    if "size" in selected:
        for size in args.sizes:
            add("size", size, size, "layered", args.default_fault_fraction, args.default_independent_fraction)
    if "shape" in selected:
        for shape in args.shapes:
            add("shape", shape, args.fixed_size, shape, args.default_fault_fraction, args.default_independent_fraction)
    if "fault_position" in selected:
        for fault in args.fault_fractions:
            add("fault_position", fault, args.fixed_size, "layered", fault, args.default_independent_fraction)
    if "independence" in selected:
        for independent in args.independent_fractions:
            add("independence", independent, args.fixed_size, "layered", args.default_fault_fraction, independent)
    return configs


def summarize(raw_rows: Sequence[dict]) -> List[dict]:
    groups: Dict[tuple, List[dict]] = defaultdict(list)
    for row in raw_rows:
        groups[(row["config_id"], row["mode"])].append(row)
    summary: List[dict] = []
    metrics = (
        "rollback_precision",
        "rollback_recall",
        "independent_retention",
        "target_removed",
        "useful_work_lost",
        "invalid_work_retained",
        "execution_ms",
        "rollback_ms",
        "commit_ms",
    )
    for _, rows in sorted(groups.items()):
        first = rows[0]
        item = {
            key: first[key]
            for key in (
                "config_id",
                "sweep",
                "x_value",
                "mode",
                "total_steps",
                "shape",
                "fault_step",
                "fault_fraction",
                "independent_fraction",
                "expected_target_count",
                "independent_count",
                "ledger_edges",
            )
        }
        item["repeats"] = len(rows)
        for metric in metrics:
            values = [float(row[metric]) for row in rows]
            item[f"{metric}_mean"] = round(statistics.mean(values), 6)
            item[f"{metric}_stdev"] = round(statistics.stdev(values), 6) if len(values) > 1 else 0.0
            if metric == "rollback_ms":
                item["rollback_ms_p95"] = round(percentile(values, 0.95), 6)
        item["final_correct_rate"] = round(
            statistics.mean(1.0 if row["final_correct"] else 0.0 for row in rows), 6
        )
        item["host_clean_rate"] = round(
            statistics.mean(1.0 if row["host_clean_before_commit"] else 0.0 for row in rows), 6
        )
        summary.append(item)
    return summary


def write_outputs(raw_rows: Sequence[dict], summary_rows: Sequence[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_fields = list(summary_rows[0]) if summary_rows else []
    with (output_dir / "causal_retention.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=summary_fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(summary_rows)
    raw_fields = list(raw_rows[0]) if raw_rows else []
    with (output_dir / "causal_retention_raw.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=raw_fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(raw_rows)
    (output_dir / "causal_retention.json").write_text(
        json.dumps({"summary": list(summary_rows), "raw": list(raw_rows)}, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Quantitative causal-retention evaluation",
        "",
        "Controlled effect-DAG workload. `causal` and temporal baselines receive the same declared read effects; `causal_without_dependencies` is the dependency-capture ablation.",
        "",
        "| sweep | x | mode | steps | targets | independent | precision | recall | useful retained | invalid removed | rollback p95 (ms) | correct rate |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary_rows:
        lines.append(
            f"| {row['sweep']} | {row['x_value']} | {row['mode']} | {row['total_steps']} | "
            f"{row['expected_target_count']} | {row['independent_count']} | "
            f"{row['rollback_precision_mean']:.3f} | {row['rollback_recall_mean']:.3f} | "
            f"{row['independent_retention_mean']:.3f} | {row['target_removed_mean']:.3f} | "
            f"{row['rollback_ms_p95']:.3f} | {row['final_correct_rate']:.3f} |"
        )
    (output_dir / "causal_retention.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sweeps",
        nargs="+",
        choices=["size", "shape", "fault_position", "independence"],
        default=["size", "shape", "fault_position", "independence"],
    )
    parser.add_argument("--sizes", nargs="+", type=int, default=[16, 32, 64])
    parser.add_argument("--fixed-size", type=int, default=32)
    parser.add_argument("--shapes", nargs="+", choices=list(SHAPES), default=list(SHAPES))
    parser.add_argument("--fault-fractions", nargs="+", type=float, default=[0.1, 0.5, 0.75])
    parser.add_argument("--independent-fractions", nargs="+", type=float, default=[0.25, 0.5, 0.75])
    parser.add_argument("--default-fault-fraction", type=float, default=0.25)
    parser.add_argument("--default-independent-fraction", type=float, default=0.5)
    parser.add_argument("--modes", nargs="+", choices=list(MODES), default=list(MODES[:-1]))
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--output-dir", type=Path, default=OUT)
    args = parser.parse_args()
    if args.repeats <= 0:
        parser.error("repeats must be positive")

    raw_rows: List[dict] = []
    for config in build_configs(args):
        plan = build_causal_retention_plan(
            int(config["requested_total_steps"]),
            shape=str(config["requested_shape"]),
            fault_fraction=float(config["requested_fault_fraction"]),
            independent_fraction=float(config["requested_independent_fraction"]),
        )
        for mode in args.modes:
            for repeat in range(args.repeats):
                row = run_case(plan, mode, repeat, config)
                raw_rows.append(row)
                print(
                    f"sweep={config['sweep']:14s} x={str(config['x_value']):8s} "
                    f"mode={mode:29s} repeat={repeat} "
                    f"retain={row['independent_retention']:.3f} "
                    f"remove={row['target_removed']:.3f} "
                    f"rollback={row['rollback_ms']:.1f}ms",
                    flush=True,
                )
    summary_rows = summarize(raw_rows)
    write_outputs(raw_rows, summary_rows, args.output_dir)
    print(f"wrote {args.output_dir / 'causal_retention.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
