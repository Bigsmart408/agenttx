#!/usr/bin/env python3
"""Turn recorded optimization iterations into a paper-ready motivation bundle."""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _stage_metric(note: str) -> Optional[tuple[float, float]]:
    match = re.search(r"snapshot stage\s+([0-9.]+)\s*->\s*([0-9.]+)\s*s", note)
    if not match:
        return None
    return float(match.group(1)), float(match.group(2))


def summarize_history(records: Sequence[dict]) -> List[dict]:
    rows: List[dict] = []
    for record in records:
        before = record.get("before_full_ms_per_step", "")
        after = record.get("after_full_ms_per_step", "")
        metric = "full_ms_per_step"
        stage = _stage_metric(str(record.get("note", "")))
        if stage is not None:
            before, after = stage
            metric = "snapshot_stage_s"
        improvement = ""
        if before != "" and after != "" and float(before) != 0:
            improvement = round((float(before) - float(after)) / float(before) * 100.0, 3)
        rows.append(
            {
                "iteration": record.get("iteration"),
                "snapshot": record.get("snapshot"),
                "optimization": record.get("optimization"),
                "metric": metric,
                "before": before,
                "after": after,
                "improvement_pct": improvement,
                "correct": record.get("correct"),
                "note": record.get("note", ""),
            }
        )
    return rows


def build_bundle(history: Sequence[dict], robustness: Optional[dict], real_agent: Optional[dict]) -> dict:
    return {
        "problem": {
            "baseline": "Each opaque tool call paid for tracing, shell/script setup, snapshot traversal, and try namespace setup.",
            "motivation": "Long trajectories multiply per-call costs; optimization must reduce overhead without weakening causal recovery.",
        },
        "optimization_history": summarize_history(history),
        "deterministic_robustness": robustness,
        "real_agent": real_agent,
    }


def write_bundle(bundle: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    history = bundle["optimization_history"]
    with (output_dir / "motivation_optimization_history.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        fields = ["iteration", "snapshot", "optimization", "metric", "before", "after", "improvement_pct", "correct", "note"]
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(history)
    (output_dir / "motivation_optimization_history.json").write_text(
        json.dumps(bundle, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# AgentTX motivation: optimization chain",
        "",
        "## Problem exposed by the baseline",
        "",
        bundle["problem"]["baseline"],
        bundle["problem"]["motivation"],
        "",
        "The historical rows below are directional before/after measurements from the same VM. Iteration 06 is a snapshot-stage metric and intentionally does not claim an end-to-end speedup.",
        "",
        "| iter | optimization | metric | before | after | improvement | correct |",
        "|---:|---|---|---:|---:|---:|:---:|",
    ]
    for row in history:
        improvement = f"{row['improvement_pct']}%" if row["improvement_pct"] != "" else ""
        lines.append(
            f"| {row['iteration']} | {row['optimization']} | {row['metric']} | "
            f"{row['before']} | {row['after']} | {improvement} | {row['correct']} |"
        )
    lines += [
        "",
        "## Interpretation",
        "",
        "1. Trusted harness effects remove avoidable tracing work while preserving explicit READ/NEGATIVE dependencies.",
        "2. Reusing the command script and deferring blob GC remove repeated temporary-file and maintenance work.",
        "3. Direct script execution removes an extra shell parse.",
        "4. The persistent try worker removes per-call namespace/overlay setup and is the largest recorded endpoint reduction.",
        "5. Incremental snapshots reduce snapshot-stage traversal by replaying only changed upperdir paths; boundary operations retain a full-copy fallback.",
        "6. Worker crash injection, reloadable long sessions, concurrent agents, and real-agent repeats validate that the speed path remains recoverable and isolated.",
        "",
    ]
    robustness = bundle.get("deterministic_robustness") or {}
    if robustness:
        lines += [
            "## Runtime tail and real-agent evidence",
            "",
            "Deterministic p50/p95 and real-agent p50/p95 are recorded in the robustness bundles; network/model latency is kept separate from runtime-only measurements.",
            "",
        ]
    real_agent = bundle.get("real_agent") or {}
    if real_agent:
        lines += [
            f"Latest real-agent model: `{real_agent.get('model', '')}`, wall p50/p95 {real_agent.get('wall_p50_s', '')}/{real_agent.get('wall_p95_s', '')} s, success rate {real_agent.get('success_rate', '')}.",
            "",
        ]
    (output_dir / "motivation_optimization_history.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    print(f"wrote {output_dir / 'motivation_optimization_history.csv'}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", type=Path, default=ROOT / "experiments" / "results" / "optimization_iterations.json")
    parser.add_argument("--robustness", type=Path, default=ROOT / "experiments" / "results" / "robustness.json")
    parser.add_argument("--real-agent", type=Path, default=ROOT / "experiments" / "results" / "real_agent_robustness.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "experiments" / "results")
    args = parser.parse_args()
    history = _load(args.history)
    robustness = _load(args.robustness) if args.robustness.exists() else None
    real_agent = _load(args.real_agent) if args.real_agent.exists() else None
    write_bundle(build_bundle(history, robustness, real_agent), args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
