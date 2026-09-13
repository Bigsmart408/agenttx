#!/usr/bin/env python3
"""M4 counterfactual recovery boundaries over native SWE-agent traces."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Sequence

import tiktoken


POLICIES = (
    "turn_checkpoint_proxy",
    "temporal_step",
    "whole_branch",
    "agenttx_causal",
    "ideal_oracle",
)


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


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
    groups = []
    remaining = set(nodes)
    while remaining:
        group = {remaining.pop()}
        frontier = list(group)
        while frontier:
            found = neighbors[frontier.pop()] & remaining
            remaining -= found
            group |= found
            frontier.extend(found)
        groups.append(group)
    return groups


def call_weights(calls: Sequence[dict], encoding) -> dict[int, int]:
    output = {}
    for call in calls:
        step_id = int(call["step_id"])
        payload = json.dumps(
            {
                "name": call.get("tool_name") or "",
                "arguments": call.get("arguments") or "",
                "artifact_payload": call.get("result") or "",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        output[step_id] = len(encoding.encode(payload))
    return output


def analyze_trace(path: Path, encoding) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    task = str(data["task"])
    ledger = {int(step["step_id"]): step for step in data["ledger"]["steps"]}
    output = []
    for turn in data["conversation"]["turns"]:
        calls = [call for call in turn.get("calls") or [] if call.get("step_id") is not None]
        nodes = {int(call["step_id"]) for call in calls}
        if len(nodes) < 2:
            continue
        parents = {
            node: set(int(parent) for parent in ledger[node].get("parents") or [])
            for node in nodes
        }
        for call in calls:
            parents[int(call["step_id"])].update(int(parent) for parent in call.get("parents") or [])
        mutating = {
            node
            for node in nodes
            if any(effect.get("kind") in {"W", "D"} for effect in ledger[node].get("effects") or [])
        }
        stateful_components = sum(bool(component & mutating) for component in components(nodes, parents))
        if stateful_components < 2:
            continue
        weights = call_weights(calls, encoding)
        first = min(nodes)
        for root in sorted(mutating):
            invalid = closure(nodes, parents, root)
            valid = nodes - invalid
            policy_targets = {
                "turn_checkpoint_proxy": set(nodes),
                "temporal_step": {node for node in nodes if node >= root},
                "whole_branch": set(nodes),
                "agenttx_causal": closure(nodes, parents, root),
                "ideal_oracle": set(invalid),
            }
            manifest = json.dumps(
                {
                    "task": task,
                    "turn": turn["turn_id"],
                    "root": root,
                    "nodes": sorted(nodes),
                    "parents": {str(key): sorted(value) for key, value in parents.items()},
                },
                sort_keys=True,
            )
            for policy, targets in policy_targets.items():
                useful = targets & valid
                output.append(
                    {
                        "task": task,
                        "trace_id": path.stem,
                        "turn_id": int(turn["turn_id"]),
                        "fault_step_id": root,
                        "policy": policy,
                        "recovery_context_variant": "compact" if policy == "agenttx_causal" else "not_applicable",
                        "manifest": manifest,
                        "loop_first_step": first,
                        "calls_per_turn": len(nodes),
                        "closure_size": len(invalid),
                        "valid_size": len(valid),
                        "rollback_set": json.dumps(sorted(targets)),
                        "valid_retention": 1.0 - len(useful) / max(len(valid), 1),
                        "invalid_removal": len(targets & invalid) / max(len(invalid), 1),
                        "useful_steps_wasted": len(useful),
                        "useful_tokens_wasted": sum(weights[node] for node in useful),
                        "invalid_steps_retained": len(invalid - targets),
                        "agenttx_to_ideal_gap_steps": len(useful) if policy == "agenttx_causal" else "",
                        "final_correct_without_replay": not (invalid - targets) and not useful,
                        "execution_kind": "recorded_graph_counterfactual",
                    }
                )
    return output


def write_csv(path: Path, rows: Sequence[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    encoding = tiktoken.get_encoding("o200k_base")
    rows = []
    for path in sorted(args.trace_dir.glob("*.json")):
        rows.extend(analyze_trace(path, encoding))
    if not rows:
        raise RuntimeError("no M4-eligible turns found")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "motivation_real_counterfactual_raw.csv", rows)

    per_turn = defaultdict(list)
    for row in rows:
        per_turn[(row["trace_id"], row["turn_id"], row["policy"])].append(row)
    turn_rows = []
    for (trace_id, turn_id, policy), group in sorted(per_turn.items()):
        turn_rows.append(
            {
                "trace_id": trace_id,
                "turn_id": turn_id,
                "policy": policy,
                "fault_roots": len(group),
                "useful_tokens_wasted_mean": statistics.mean(float(row["useful_tokens_wasted"]) for row in group),
                "valid_retention_mean": statistics.mean(float(row["valid_retention"]) for row in group),
                "invalid_removal_mean": statistics.mean(float(row["invalid_removal"]) for row in group),
            }
        )
    write_csv(args.output_dir / "motivation_real_counterfactual_turns.csv", turn_rows)

    summary = []
    for policy in POLICIES:
        group = [row for row in turn_rows if row["policy"] == policy]
        token_values = [float(row["useful_tokens_wasted_mean"]) for row in group]
        summary.append(
            {
                "policy": policy,
                "turns": len(group),
                "useful_tokens_wasted_median": percentile(token_values, 0.50),
                "useful_tokens_wasted_p95": percentile(token_values, 0.95),
                "useful_tokens_wasted_p99": percentile(token_values, 0.99),
                "valid_retention_mean": statistics.mean(float(row["valid_retention_mean"]) for row in group),
                "invalid_removal_mean": statistics.mean(float(row["invalid_removal_mean"]) for row in group),
            }
        )
    write_csv(args.output_dir / "motivation_real_counterfactual_summary.csv", summary)
    (args.output_dir / "M4_REPORT.md").write_text(
        "\n".join(
            [
                "# M4 real-trace counterfactual recovery",
                "",
                "This is a recorded-graph recovery-boundary analysis, not an end-to-end continuation or replay-token result.",
                "All `agenttx_causal` rows use the required compact recovery context.",
                f"Eligible real turns: {len({(row['trace_id'], row['turn_id']) for row in rows})}; fault-policy rows: {len(rows)}.",
                "",
                "| policy | turns | wasted token weight median | P95 | P99 | retention mean | invalid removal mean |",
                "|---|---:|---:|---:|---:|---:|---:|",
                *[
                    f"| {row['policy']} | {row['turns']} | {row['useful_tokens_wasted_median']:.2f} | "
                    f"{row['useful_tokens_wasted_p95']:.2f} | {row['useful_tokens_wasted_p99']:.2f} | "
                    f"{row['valid_retention_mean']:.4f} | {row['invalid_removal_mean']:.4f} |"
                    for row in summary
                ],
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
