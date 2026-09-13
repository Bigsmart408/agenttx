#!/usr/bin/env python3
"""Run the M1/M2 single-loop recovery-granularity experiments."""

from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Sequence

import tiktoken


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from agenttx.ledger import Effect, EffectKind, Ledger  # noqa: E402
from agenttx.runtime import AgentTX  # noqa: E402
from experiments.workloads.intra_turn_recovery_dag import (  # noqa: E402
    INTERLEAVINGS,
    SHAPES,
    IntraTurnPlan,
    build_extreme_plan,
    build_intra_turn_plan,
    causal_closure,
)


POLICIES = (
    "no_recovery",
    "root_only",
    "turn_checkpoint_proxy",
    "temporal_step",
    "whole_branch",
    "agenttx_causal",
    "ideal_oracle",
)
CORRECT_POLICIES = {
    "turn_checkpoint_proxy",
    "temporal_step",
    "whole_branch",
    "agenttx_causal",
    "ideal_oracle",
}


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def bootstrap_ci(values: Sequence[float], seed: int = 20260912) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    if len(values) == 1:
        return (float(values[0]), float(values[0]))
    rng = random.Random(seed)
    means = [
        statistics.mean(rng.choice(values) for _ in values)
        for _ in range(2000)
    ]
    return percentile(means, 0.025), percentile(means, 0.975)


def cleanup(path: Path) -> None:
    subprocess.run(
        ["chmod", "-R", "u+rwX", str(path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    shutil.rmtree(path, ignore_errors=True)


def call_token_weights(plan: IntraTurnPlan) -> dict[int, int]:
    encoding = tiktoken.get_encoding("o200k_base")
    return {
        step.index: len(encoding.encode(step.token_payload(plan.steps)))
        for step in plan.steps
    }


def conversation_messages(plan: IntraTurnPlan, records: Sequence[object]) -> list[dict]:
    calls = []
    messages: list[dict] = []
    for step, record in zip(plan.steps, records):
        call_id = f"motivation-{step.index}"
        calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": step.name,
                    "arguments": json.dumps({"cmd": step.command(plan.steps)}, sort_keys=True),
                },
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": json.dumps(
                    {
                        "step_id": record.step_id,
                        "parents": list(record.parents),
                        "stdout": record.stdout,
                    },
                    sort_keys=True,
                ),
            }
        )
    assistant = {"role": "assistant", "content": "materialize loop", "tool_calls": calls}
    return [assistant, *messages]


def materialize(plan: IntraTurnPlan, workspace: Path, session: Path) -> AgentTX:
    tx = AgentTX.begin(workdir=workspace, session_dir=session, trace_reads=False)
    tx.conversation.seed("single-loop recovery experiment", plan.trace_id)
    records = []
    for step in plan.steps:
        reads = [str((workspace / plan.steps[parent].relative_path).resolve()) for parent in step.parent_indices]
        record = tx.run_tool(step.name, step.command(plan.steps), extra_reads=reads)
        if record.returncode != 0:
            raise RuntimeError(record.stderr or f"failed to execute {step.name}")
        records.append(record)
    tx.conversation.append_turn(
        conversation_messages(plan, records),
        step_ids=[record.step_id for record in records],
        tool_names=[step.name for step in plan.steps],
        kind="effect",
    )
    tx._persist()
    return tx


def rollback_exact(tx: AgentTX, targets: Iterable[int], mode: str) -> list[int]:
    selected = sorted(set(int(target) for target in targets))
    if not selected:
        return []
    assert tx.pool is not None
    path_groups = []
    for target in selected:
        paths = sorted(
            effect.path
            for effect in tx.ledger.steps[target].effects
            if effect.kind in {EffectKind.WRITE, EffectKind.DELETE}
        )
        if paths:
            path_groups.append((target, paths))
    tx.pool.rollback_causal_groups(selected, path_groups)
    tx.ledger.mark_rolled_back(selected)
    tx._rewind_conversation(selected, mode=mode)
    tx._persist()
    return selected


def apply_policy(tx: AgentTX, plan: IntraTurnPlan, policy: str) -> list[int]:
    if policy == "no_recovery":
        return []
    if policy == "root_only":
        return rollback_exact(tx, [plan.fault_step_id], policy)
    if policy in {"turn_checkpoint_proxy", "whole_branch"}:
        return tx.rollback(0)
    if policy == "temporal_step":
        return tx.rollback(plan.fault_step_id)
    if policy == "agenttx_causal":
        return tx.rollback_causal_many([plan.fault_step_id])
    if policy == "ideal_oracle":
        return rollback_exact(tx, plan.oracle_closure, policy)
    raise ValueError(policy)


def present_ids(tx: AgentTX, plan: IntraTurnPlan) -> set[int]:
    present = set()
    for step in plan.steps:
        entry = tx._overlay_entry((tx.workspace / step.relative_path).resolve())
        if entry is not None and entry.read_text(encoding="utf-8").startswith("artifact-"):
            present.add(step.index)
    return present


def host_present_ids(workspace: Path, plan: IntraTurnPlan) -> set[int]:
    return {
        step.index
        for step in plan.steps
        if (workspace / step.relative_path).read_text(encoding="utf-8").startswith("artifact-")
    }


def reset_host(workspace: Path, plan: IntraTurnPlan) -> None:
    for step in plan.steps:
        (workspace / step.relative_path).write_text("BASE\n", encoding="utf-8")


def run_policy(
    plan: IntraTurnPlan,
    policy: str,
    workspace: Path,
    session: Path,
    weights: dict[int, int],
    phase: str,
    repeat: int,
) -> dict:
    tx = AgentTX.load(session)
    try:
        started = time.perf_counter()
        actual_targets = apply_policy(tx, plan, policy)
        rollback_ms = (time.perf_counter() - started) * 1000.0
        merged = present_ids(tx, plan)
        invalid = set(plan.oracle_closure)
        valid = set(plan.valid_ids)
        targets = set(actual_targets)
        expected_active = set(range(len(plan.steps))) - targets
        conversation_active = set(tx.conversation.active_step_ids())
        conversation_correct = conversation_active == expected_active
        merged_correct = merged == expected_active

        active = [
            step.step_id
            for step in tx.ledger.steps
            if step.status != "rolled_back" and step.step_id > tx.ledger.committed_frontier
        ]
        if active:
            tx.commit(max(active))
        host = host_present_ids(workspace, plan)
        host_correct = host == expected_active
        verifier_correct = not (host & invalid) and valid.issubset(host)
        useful_wasted = targets & valid
        invalid_retained = invalid - targets
        final_correct = (
            not invalid_retained
            and not useful_wasted
            and merged_correct
            and conversation_correct
            and host_correct
            and verifier_correct
        )
        if phase == "m2" and policy == "agenttx_causal":
            assert actual_targets == [1, 32, 64, 127], actual_targets
        return {
            "execution_kind": "physical_overlay",
            "phase": phase,
            "trace_id": plan.trace_id,
            "turn_id": 0,
            "fault_step_id": plan.fault_step_id,
            "seed": plan.seed,
            "repeat": repeat,
            "manifest_hash": plan.manifest_hash,
            "policy": policy,
            "recovery_context_variant": "compact" if policy == "agenttx_causal" else "not_applicable",
            "loop_width": len(plan.steps),
            "requested_fault_position": plan.requested_fault_position,
            "actual_fault_position": plan.fault_step_id / max(len(plan.steps) - 1, 1),
            "requested_closure_ratio": plan.requested_closure_ratio,
            "actual_closure_ratio": len(invalid) / len(plan.steps),
            "shape": plan.shape,
            "interleaving": plan.interleaving,
            "closure_size": len(invalid),
            "valid_size": len(valid),
            "rollback_set": json.dumps(actual_targets),
            "valid_retention": 1.0 - len(useful_wasted) / max(len(valid), 1),
            "invalid_removal": len(targets & invalid) / max(len(invalid), 1),
            "useful_steps_wasted": len(useful_wasted),
            "useful_tokens_wasted": sum(weights[index] for index in useful_wasted),
            "invalid_steps_retained": len(invalid_retained),
            "attributed_lost_tokens": sum(weights[index] for index in useful_wasted),
            "replay_prompt_tokens": "",
            "replay_completion_tokens": "",
            "replay_total_tokens": "",
            "rollback_ms": round(rollback_ms, 6),
            "recovery_wall_ms": round(rollback_ms, 6),
            "merged_correct": merged_correct,
            "conversation_correct": conversation_correct,
            "host_correct": host_correct,
            "verifier_passed": verifier_correct,
            "final_correct": final_correct,
        }
    finally:
        tx.close(destroy=True)


def analytical_rows(plan: IntraTurnPlan, repeat: int) -> list[dict]:
    ledger = Ledger()
    for step in plan.steps:
        effects = [Effect(f"/motivation/{step.relative_path}", EffectKind.WRITE)]
        effects.extend(
            Effect(
                f"/motivation/{plan.steps[parent].relative_path}",
                EffectKind.READ,
            )
            for parent in step.parent_indices
        )
        ledger.add_step(step.name, effects)
    actual_causal = ledger.causal_dependents_many([plan.fault_step_id])
    expected_causal = list(causal_closure(plan.steps, [plan.fault_step_id]))
    if actual_causal != expected_causal:
        raise AssertionError((actual_causal, expected_causal, plan.trace_id))
    weights = call_token_weights(plan)
    invalid = set(plan.oracle_closure)
    valid = set(plan.valid_ids)
    policy_targets = {
        "no_recovery": [],
        "root_only": [plan.fault_step_id],
        "turn_checkpoint_proxy": list(range(len(plan.steps))),
        "temporal_step": list(range(plan.fault_step_id, len(plan.steps))),
        "whole_branch": list(range(len(plan.steps))),
        "agenttx_causal": actual_causal,
        "ideal_oracle": list(plan.oracle_closure),
    }
    rows = []
    for policy in POLICIES:
        started = time.perf_counter()
        targets = set(policy_targets[policy])
        rollback_ms = (time.perf_counter() - started) * 1000.0
        useful_wasted = targets & valid
        invalid_retained = invalid - targets
        rows.append(
            {
                "execution_kind": "ledger_analytical",
                "phase": "m1",
                "trace_id": plan.trace_id,
                "turn_id": 0,
                "fault_step_id": plan.fault_step_id,
                "seed": plan.seed,
                "repeat": repeat,
                "manifest_hash": plan.manifest_hash,
                "policy": policy,
                "recovery_context_variant": "compact" if policy == "agenttx_causal" else "not_applicable",
                "loop_width": len(plan.steps),
                "requested_fault_position": plan.requested_fault_position,
                "actual_fault_position": plan.fault_step_id / max(len(plan.steps) - 1, 1),
                "requested_closure_ratio": plan.requested_closure_ratio,
                "actual_closure_ratio": len(invalid) / len(plan.steps),
                "shape": plan.shape,
                "interleaving": plan.interleaving,
                "closure_size": len(invalid),
                "valid_size": len(valid),
                "rollback_set": json.dumps(sorted(targets)),
                "valid_retention": 1.0 - len(useful_wasted) / max(len(valid), 1),
                "invalid_removal": len(targets & invalid) / max(len(invalid), 1),
                "useful_steps_wasted": len(useful_wasted),
                "useful_tokens_wasted": sum(weights[index] for index in useful_wasted),
                "invalid_steps_retained": len(invalid_retained),
                "attributed_lost_tokens": sum(weights[index] for index in useful_wasted),
                "replay_prompt_tokens": "",
                "replay_completion_tokens": "",
                "replay_total_tokens": "",
                "rollback_ms": round(rollback_ms, 6),
                "recovery_wall_ms": round(rollback_ms, 6),
                "merged_correct": "not_run",
                "conversation_correct": "not_run",
                "host_correct": "not_run",
                "verifier_passed": "not_run",
                "final_correct": not invalid_retained and not useful_wasted,
            }
        )
    return rows


def run_plan(plan: IntraTurnPlan, phase: str, repeat: int) -> list[dict]:
    scratch = Path(tempfile.mkdtemp(prefix=f"agenttx-{phase}-", dir="/tmp"))
    workspace = scratch / "workspace"
    workspace.mkdir()
    reset_host(workspace, plan)
    base_session = scratch / "base"
    fork_dirs: dict[str, Path] = {}
    base = materialize(plan, workspace, base_session)
    try:
        for policy in POLICIES:
            branch_dir = scratch / f"branch-{policy}"
            branch = base.fork(branch_dir)
            branch.close(destroy=False)
            fork_dirs[policy] = branch_dir
    finally:
        base.close(destroy=True)

    weights = call_token_weights(plan)
    rows = []
    try:
        for policy in POLICIES:
            row = run_policy(
                plan, policy, workspace, fork_dirs[policy], weights, phase, repeat
            )
            rows.append(row)
            reset_host(workspace, plan)
            print(
                f"{phase} {plan.trace_id} policy={policy} "
                f"retain={row['valid_retention']:.4f} "
                f"remove={row['invalid_removal']:.4f} correct={row['final_correct']}",
                flush=True,
            )
        hashes = {row["manifest_hash"] for row in rows}
        if len(hashes) != 1:
            raise AssertionError("policies did not share one materialized trace")
        return rows
    finally:
        cleanup(scratch)


def summarize(rows: Sequence[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["phase"], row["policy"])].append(row)
    metrics = (
        "valid_retention",
        "invalid_removal",
        "useful_steps_wasted",
        "useful_tokens_wasted",
        "invalid_steps_retained",
        "rollback_ms",
    )
    output = []
    for (phase, policy), group in sorted(groups.items()):
        item = {"phase": phase, "policy": policy, "n": len(group)}
        for metric in metrics:
            values = [float(row[metric]) for row in group]
            low, high = bootstrap_ci(values)
            item.update(
                {
                    f"{metric}_mean": statistics.mean(values),
                    f"{metric}_p50": percentile(values, 0.50),
                    f"{metric}_p95": percentile(values, 0.95),
                    f"{metric}_p99": percentile(values, 0.99),
                    f"{metric}_ci95_low": low,
                    f"{metric}_ci95_high": high,
                }
            )
        item["final_correct_rate"] = statistics.mean(
            1.0 if row["final_correct"] else 0.0 for row in group
        )
        output.append(item)
    return output


def write_csv(path: Path, rows: Sequence[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_outputs(output_dir: Path, rows: Sequence[dict]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries = summarize(rows)
    write_csv(output_dir / "motivation_recovery_raw.csv", rows)
    write_csv(output_dir / "motivation_recovery_summary.csv", summaries)
    (output_dir / "motivation_recovery.json").write_text(
        json.dumps({"raw": list(rows), "summary": summaries}, indent=2) + "\n",
        encoding="utf-8",
    )
    m2 = [row for row in rows if row["phase"] == "m2"]
    lines = [
        "# Intra-turn recovery motivation",
        "",
        "`turn_checkpoint_proxy` is a turn-boundary proxy, not a Crab artifact.",
        "M1/M2 token weights are tokenizer attribution, not billed replay usage.",
        "All `agenttx_causal` rows use the required compact recovery context.",
        "M1 ledger-analytical rows use the current Ledger dependency implementation; physical correctness is validated separately by M1 gate cells and M2.",
        "",
        "| phase | policy | n | retention mean | invalid removal mean | useful steps wasted mean | correct rate |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for item in summaries:
        lines.append(
            f"| {item['phase']} | {item['policy']} | {item['n']} | "
            f"{item['valid_retention_mean']:.4f} | {item['invalid_removal_mean']:.4f} | "
            f"{item['useful_steps_wasted_mean']:.2f} | {item['final_correct_rate']:.4f} |"
        )
    if m2:
        lines.extend(
            [
                "",
                "## M2 extreme case",
                "",
                "| policy | rollback set | useful wasted | retention | invalid removal | correct |",
                "|---|---|---:|---:|---:|---:|",
            ]
        )
        for row in m2:
            lines.append(
                f"| {row['policy']} | `{row['rollback_set']}` | "
                f"{row['useful_steps_wasted']} | {row['valid_retention']:.4f} | "
                f"{row['invalid_removal']:.4f} | {row['final_correct']} |"
            )
    (output_dir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["m1", "m2", "all"], default="all")
    parser.add_argument("--widths", nargs="+", type=int, default=[8, 16, 32, 64, 128])
    parser.add_argument("--fault-positions", nargs="+", type=float, default=[0.05, 0.25, 0.5, 0.75])
    parser.add_argument("--closure-ratios", nargs="+", type=float, default=[0.03, 0.10, 0.25, 0.50])
    parser.add_argument("--shapes", nargs="+", choices=SHAPES, default=list(SHAPES))
    parser.add_argument("--interleavings", nargs="+", choices=INTERLEAVINGS, default=list(INTERLEAVINGS))
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument(
        "--m1-execution",
        choices=["analytical", "physical"],
        default="analytical",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.repeats <= 0:
        parser.error("repeats must be positive")

    rows: list[dict] = []
    existing = args.output_dir / "motivation_recovery.json"
    if existing.exists():
        rows = list(json.loads(existing.read_text(encoding="utf-8")).get("raw") or [])
        for row in rows:
            row.setdefault("execution_kind", "physical_overlay")
    selected_phases = {"m1", "m2"} if args.phase == "all" else {args.phase}
    rows = [row for row in rows if row.get("phase") not in selected_phases]
    if args.phase in {"m2", "all"}:
        rows.extend(run_plan(build_extreme_plan(), "m2", 0))
        write_outputs(args.output_dir, rows)
    if args.phase in {"m1", "all"}:
        config_count = 0
        for width in args.widths:
            for fault in args.fault_positions:
                for closure in args.closure_ratios:
                    for shape in args.shapes:
                        for interleaving in args.interleavings:
                            for repeat in range(args.repeats):
                                plan = build_intra_turn_plan(
                                    width,
                                    fault_position=fault,
                                    closure_ratio=closure,
                                    shape=shape,
                                    interleaving=interleaving,
                                    seed=repeat,
                                )
                                if args.m1_execution == "physical":
                                    rows.extend(run_plan(plan, "m1", repeat))
                                else:
                                    rows.extend(analytical_rows(plan, repeat))
                                config_count += 1
                                if config_count % 100 == 0:
                                    write_outputs(args.output_dir, rows)
                                    print(f"m1 checkpoint configs={config_count} rows={len(rows)}", flush=True)
        write_outputs(args.output_dir, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
