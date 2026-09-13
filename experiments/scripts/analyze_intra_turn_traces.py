#!/usr/bin/env python3
"""Analyze native SWE-agent traces for M0 intra-turn recoverable work."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Sequence

import matplotlib.pyplot as plt
import tiktoken


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def write_csv(path: Path, rows: Sequence[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def closure(nodes: set[int], parents: dict[int, set[int]], root: int) -> set[int]:
    result = {root}
    changed = True
    while changed:
        changed = False
        for node in nodes - result:
            if parents.get(node, set()) & result:
                result.add(node)
                changed = True
    return result


def components(nodes: set[int], parents: dict[int, set[int]]) -> list[set[int]]:
    neighbors = {node: set() for node in nodes}
    for child in nodes:
        for parent in parents.get(child, set()) & nodes:
            neighbors[child].add(parent)
            neighbors[parent].add(child)
    output = []
    remaining = set(nodes)
    while remaining:
        current = {remaining.pop()}
        frontier = list(current)
        while frontier:
            node = frontier.pop()
            new = neighbors[node] & remaining
            remaining -= new
            current |= new
            frontier.extend(new)
        output.append(current)
    return output


def summarize(values: Sequence[float], prefix: str) -> dict:
    return {
        f"{prefix}_mean": statistics.mean(values) if values else 0.0,
        f"{prefix}_p50": percentile(values, 0.50),
        f"{prefix}_p95": percentile(values, 0.95),
        f"{prefix}_p99": percentile(values, 0.99),
    }


def analyze_trace(path: Path, encoding) -> tuple[dict, list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    task = str(data["task"])
    ledger_steps = {int(step["step_id"]): step for step in data["ledger"]["steps"]}
    rows = []
    for turn in data["conversation"]["turns"]:
        calls = [call for call in turn.get("calls") or [] if call.get("step_id") is not None]
        step_ids = {int(call["step_id"]) for call in calls}
        if not step_ids:
            continue
        parent_map = {
            step_id: set(int(parent) for parent in ledger_steps[step_id].get("parents") or [])
            for step_id in step_ids
        }
        for call in calls:
            parent_map[int(call["step_id"])].update(
                int(parent) for parent in call.get("parents") or []
            )
        graph_components = components(step_ids, parent_map)
        weights = {
            int(call["step_id"]): len(
                encoding.encode(
                    json.dumps(
                        {
                            "name": call.get("tool_name") or "",
                            "arguments": call.get("arguments") or "",
                            "artifact_payload": call.get("result") or "",
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
            )
            for call in calls
        }
        mutating = [
            step_id
            for step_id in sorted(step_ids)
            if any(effect.get("kind") in {"W", "D"} for effect in ledger_steps[step_id].get("effects") or [])
        ]
        selected = None
        invalid: set[int] = set()
        valid = set(step_ids)
        if mutating:
            seed_text = f"{task}:{turn['turn_id']}:20260912"
            rng = random.Random(int(hashlib.sha256(seed_text.encode()).hexdigest()[:16], 16))
            selected = rng.choice(mutating)
            invalid = closure(step_ids, parent_map, selected)
            valid -= invalid
        write_steps = sum(
            1
            for step_id in step_ids
            if any(effect.get("kind") in {"W", "D"} for effect in ledger_steps[step_id].get("effects") or [])
        )
        effect_count = sum(len(ledger_steps[step_id].get("effects") or []) for step_id in step_ids)
        stateful_components = sum(
            1 for component in graph_components if set(mutating) & component
        )
        rows.append(
            {
                "task": task,
                "trace_id": path.stem,
                "turn_id": int(turn["turn_id"]),
                "calls_per_turn": len(step_ids),
                "write_delete_steps": write_steps,
                "effect_count": effect_count,
                "causal_components": len(graph_components),
                "stateful_components": stateful_components,
                "selected_fault_step": "" if selected is None else selected,
                "closure_size": len(invalid),
                "valid_size": len(valid),
                "turn_checkpoint_extra_steps": len(valid),
                "turn_checkpoint_extra_tokens": sum(weights.get(step, 0) for step in valid),
                "call_token_weight": sum(weights.values()),
                "multi_call": len(step_ids) >= 2,
                "multiple_independent_stateful_components": stateful_components >= 2,
            }
        )
    return data, rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--min-success-cases", type=int, default=20)
    args = parser.parse_args()
    encoding = tiktoken.get_encoding("o200k_base")
    traces = []
    rows = []
    for path in sorted(args.trace_dir.glob("*.json")):
        data, trace_rows = analyze_trace(path, encoding)
        if data.get("source_success") and data.get("replay_completed"):
            traces.append(data)
            rows.extend(trace_rows)
    if len(traces) < args.min_success_cases:
        raise RuntimeError(
            f"M0 gate requires {args.min_success_cases} successful native traces; found {len(traces)}"
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "motivation_turn_audit_raw.csv", rows)
    summary_rows = []
    for scope, group in [("global", rows), *sorted((task, list(group)) for task, group in _group(rows).items())]:
        item = {"scope": scope, "cases": len({row["task"] for row in group}), "turns": len(group)}
        for metric in (
            "calls_per_turn",
            "write_delete_steps",
            "effect_count",
            "causal_components",
            "turn_checkpoint_extra_steps",
            "turn_checkpoint_extra_tokens",
        ):
            item.update(summarize([float(row[metric]) for row in group], metric))
        item["multi_call_ratio"] = statistics.mean(bool(row["multi_call"]) for row in group) if group else 0.0
        item["multiple_independent_stateful_components_ratio"] = (
            statistics.mean(bool(row["multiple_independent_stateful_components"]) for row in group)
            if group
            else 0.0
        )
        summary_rows.append(item)
    write_csv(args.output_dir / "motivation_turn_audit_summary.csv", summary_rows)

    values = sorted(float(row["turn_checkpoint_extra_tokens"]) for row in rows)
    cdf = [
        {"turn_checkpoint_extra_tokens": value, "cdf": (index + 1) / len(values)}
        for index, value in enumerate(values)
    ]
    write_csv(args.output_dir / "motivation_turn_audit_cdf.csv", cdf)
    figure, axis = plt.subplots(figsize=(6.4, 4.0))
    axis.step([row["turn_checkpoint_extra_tokens"] for row in cdf], [row["cdf"] for row in cdf], where="post")
    axis.set_xlabel("Avoidable valid token weight discarded by turn checkpoint")
    axis.set_ylabel("CDF")
    axis.grid(alpha=0.25)
    figure.tight_layout()
    figure.savefig(args.output_dir / "motivation_turn_audit_cdf.png", dpi=180)
    plt.close(figure)

    global_row = summary_rows[0]
    (args.output_dir / "M0_REPORT.md").write_text(
        "\n".join(
            [
                "# M0 native SWE-agent turn audit",
                "",
                "This phase does not execute a causal recovery policy, so recovery context is not applicable.",
                f"Successful source/replay cases: {len(traces)}; recorded turns: {len(rows)}.",
                "Source successes come from the public SWE-agent 1.0 + Claude 3.7 Sonnet SWE-Bench run; actions were replayed through the current SWEAgentNativeAdapter without model calls.",
                "",
                "| metric | value |",
                "|---|---:|",
                f"| multi-call turns | {global_row['multi_call_ratio']:.4f} |",
                f"| turns with >=2 independent stateful components | {global_row['multiple_independent_stateful_components_ratio']:.4f} |",
                f"| calls/turn P50 | {global_row['calls_per_turn_p50']:.2f} |",
                f"| calls/turn P95 | {global_row['calls_per_turn_p95']:.2f} |",
                f"| calls/turn P99 | {global_row['calls_per_turn_p99']:.2f} |",
                f"| avoidable checkpoint token weight P50 | {global_row['turn_checkpoint_extra_tokens_p50']:.2f} |",
                f"| avoidable checkpoint token weight P95 | {global_row['turn_checkpoint_extra_tokens_p95']:.2f} |",
                f"| avoidable checkpoint token weight P99 | {global_row['turn_checkpoint_extra_tokens_p99']:.2f} |",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


def _group(rows: Iterable[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row["task"])].append(row)
    return grouped


if __name__ == "__main__":
    raise SystemExit(main())
