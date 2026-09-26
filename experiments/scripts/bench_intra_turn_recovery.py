#!/usr/bin/env python3
"""Run the M1/M2 multi-turn recovery-granularity experiments."""

from __future__ import annotations

import argparse
import csv
import hashlib
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

from agenttx.conversation import render_ledger_recovery_notice  # noqa: E402
from agenttx.ledger import Effect, EffectKind, Ledger  # noqa: E402
from agenttx.runtime import AgentTX  # noqa: E402
from experiments.motivation_strategies import (  # noqa: E402
    CORRECT_RECOVERY_POLICIES,
    MICROBENCHMARK_POLICIES,
)
from experiments.workloads.intra_turn_recovery_dag import (  # noqa: E402
    INTERLEAVINGS,
    SHAPES,
    IntraTurnPlan,
    build_extreme_plan,
    build_intra_turn_plan,
    build_multi_turn_plan,
)


POLICIES = MICROBENCHMARK_POLICIES
CORRECT_POLICIES = set(CORRECT_RECOVERY_POLICIES)
MICROBENCHMARK_SCHEMA = "agenttx.motivation.microbenchmark/multiturn-v3"
DEFAULT_DEPENDENCY_VALUES = tuple(index / 20 for index in range(21))


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


def plan_turn_width(plan: IntraTurnPlan) -> int:
    return max(
        sum(step.turn_index == turn_index for step in plan.steps)
        for turn_index in plan.turn_ids
    )


def conversation_messages(
    plan: IntraTurnPlan,
    records: Sequence[object],
    turn_index: int,
) -> list[dict]:
    calls = []
    messages: list[dict] = []
    for step, record in zip(plan.steps, records):
        if step.turn_index != turn_index:
            continue
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
    assistant = {
        "role": "assistant",
        "content": f"materialize turn {turn_index}",
        "tool_calls": calls,
    }
    return [assistant, *messages]


def materialize(plan: IntraTurnPlan, workspace: Path, session: Path) -> AgentTX:
    tx = AgentTX.begin(workdir=workspace, session_dir=session, trace_reads=False)
    tx.conversation.seed("multi-turn recovery experiment", plan.trace_id)
    records = []
    for step in plan.steps:
        reads = [str((workspace / plan.steps[parent].relative_path).resolve()) for parent in step.parent_indices]
        record = tx.run_tool(step.name, step.command(plan.steps), extra_reads=reads)
        if record.returncode != 0:
            raise RuntimeError(record.stderr or f"failed to execute {step.name}")
        records.append(record)
    for turn_index in plan.turn_ids:
        turn_records = [
            record
            for step, record in zip(plan.steps, records)
            if step.turn_index == turn_index
        ]
        turn_steps = [step for step in plan.steps if step.turn_index == turn_index]
        tx.conversation.append_turn(
            conversation_messages(plan, records, turn_index),
            step_ids=[record.step_id for record in turn_records],
            tool_names=[step.name for step in turn_steps],
            kind="effect",
        )
    tx._persist()
    return tx


def rewind_conversation_exact(tx: AgentTX, targets: Iterable[int], mode: str) -> None:
    """Invalidate only the requested spans for exact-set benchmark policies."""

    selected = {int(target) for target in targets}
    for turn in tx.conversation.turns:
        if turn.status != "active":
            continue
        touched = False
        for span in turn.calls:
            if span.status == "active" and span.step_id in selected:
                span.status = "invalidated"
                touched = True
        if touched:
            turn.refresh_status()
    tx.conversation.invalidate_stale_control()
    notice = render_ledger_recovery_notice(tx.ledger, sorted(selected), mode=mode)
    tx.conversation.append_recovery(notice, mode=mode)
    tx.conversation.generation += 1


def rollback_exact(
    tx: AgentTX,
    targets: Iterable[int],
    mode: str,
) -> list[int]:
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
    rewind_conversation_exact(tx, selected, mode=mode)
    tx._persist()
    return selected


def policy_targets(plan: IntraTurnPlan, policy: str) -> set[int]:
    if policy == "no_recovery":
        return set()
    if policy == "root_only":
        return set(plan.fault_roots)
    if policy == "turn_checkpoint_proxy":
        affected_turns = {
            plan.steps[step_id].turn_index for step_id in plan.oracle_closure
        }
        return {
            step.index for step in plan.steps if step.turn_index in affected_turns
        }
    if policy == "temporal_step":
        return set(range(min(plan.fault_roots), len(plan.steps)))
    if policy == "whole_branch":
        return set(range(len(plan.steps)))
    if policy == "ideal_oracle":
        return set(plan.oracle_closure)
    raise ValueError(policy)


def apply_policy(tx: AgentTX, plan: IntraTurnPlan, policy: str) -> list[int]:
    targets = policy_targets(plan, policy)
    if not targets:
        return []
    if policy == "temporal_step":
        return tx.rollback(min(plan.fault_roots))
    if policy == "whole_branch":
        return tx.rollback(0)
    return rollback_exact(tx, targets, policy)


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
        rollback_safe = not invalid_retained
        exact_state_before_replay = (
            rollback_safe
            and not useful_wasted
            and merged_correct
            and conversation_correct
            and host_correct
            and verifier_correct
        )
        return {
            "execution_kind": "physical_overlay",
            "phase": phase,
            "trace_id": plan.trace_id,
            "turn_id": plan.steps[plan.fault_step_id].turn_index,
            "turn_count": plan.turn_count,
            "turn_width": plan_turn_width(plan),
            "fault_step_id": plan.fault_step_id,
            "seed": plan.seed,
            "repeat": repeat,
            "manifest_hash": plan.manifest_hash,
            "policy": policy,
            "recovery_context_variant": "not_applicable",
            "loop_width": len(plan.steps),
            "requested_fault_position": plan.requested_fault_position,
            "actual_fault_position": plan.fault_step_id / max(len(plan.steps) - 1, 1),
            "requested_closure_ratio": plan.requested_closure_ratio,
            "actual_closure_ratio": len(invalid) / len(plan.steps),
            "requested_dependency_value": plan.requested_dependency_value,
            "actual_dependency_value": plan.actual_dependency_value,
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
            "rollback_safe": rollback_safe,
            "exact_state_before_replay": exact_state_before_replay,
            # Compatibility alias for older analysis scripts.
            "final_correct": exact_state_before_replay,
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
    weights = call_token_weights(plan)
    invalid = set(plan.oracle_closure)
    valid = set(plan.valid_ids)
    rows = []
    for policy in POLICIES:
        started = time.perf_counter()
        targets = policy_targets(plan, policy)
        rollback_ms = (time.perf_counter() - started) * 1000.0
        useful_wasted = targets & valid
        invalid_retained = invalid - targets
        rollback_safe = not invalid_retained
        exact_state_before_replay = rollback_safe and not useful_wasted
        rows.append(
            {
                "execution_kind": "ledger_analytical",
                "phase": "m1",
                "trace_id": plan.trace_id,
                "turn_id": plan.steps[plan.fault_step_id].turn_index,
                "turn_count": plan.turn_count,
                "turn_width": plan_turn_width(plan),
                "fault_step_id": plan.fault_step_id,
                "seed": plan.seed,
                "repeat": repeat,
                "manifest_hash": plan.manifest_hash,
                "policy": policy,
                "recovery_context_variant": "not_applicable",
                "loop_width": len(plan.steps),
                "requested_fault_position": plan.requested_fault_position,
                "actual_fault_position": plan.fault_step_id / max(len(plan.steps) - 1, 1),
                "requested_closure_ratio": plan.requested_closure_ratio,
                "actual_closure_ratio": len(invalid) / len(plan.steps),
                "requested_dependency_value": plan.requested_dependency_value,
                "actual_dependency_value": plan.actual_dependency_value,
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
                "rollback_safe": rollback_safe,
                "exact_state_before_replay": exact_state_before_replay,
                # Compatibility alias for older analysis scripts.
                "final_correct": exact_state_before_replay,
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
                f"remove={row['invalid_removal']:.4f} "
                f"safe={row['rollback_safe']} exact={row['exact_state_before_replay']}",
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
        groups[
            (
                row["phase"],
                row["policy"],
                row.get("turn_count", 1),
                row.get("turn_width", row["loop_width"]),
                row["loop_width"],
                row["requested_fault_position"],
                row.get("requested_dependency_value"),
                row["shape"],
                row["interleaving"],
            )
        ].append(row)
    metrics = (
        "valid_retention",
        "invalid_removal",
        "useful_steps_wasted",
        "useful_tokens_wasted",
        "invalid_steps_retained",
        "rollback_ms",
    )
    output = []
    for key, group in sorted(groups.items(), key=lambda item: tuple(str(v) for v in item[0])):
        (
            phase,
            policy,
            turn_count,
            turn_width,
            width,
            fault,
            dependency,
            shape,
            interleaving,
        ) = key
        item = {
            "phase": phase,
            "policy": policy,
            "turn_count": turn_count,
            "turn_width": turn_width,
            "loop_width": width,
            "requested_fault_position": fault,
            "requested_dependency_value": dependency,
            "actual_dependency_value_mean": statistics.mean(
                float(row.get("actual_dependency_value") or 0.0) for row in group
            ),
            "shape": shape,
            "interleaving": interleaving,
            "n": len(group),
        }
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
        item["rollback_safe_rate"] = statistics.mean(
            1.0 if row.get("rollback_safe", not row["invalid_steps_retained"]) else 0.0
            for row in group
        )
        item["exact_state_before_replay_rate"] = statistics.mean(
            1.0
            if row.get("exact_state_before_replay", row["final_correct"])
            else 0.0
            for row in group
        )
        item["final_correct_rate"] = item["exact_state_before_replay_rate"]
        output.append(item)
    return output


def write_csv(path: Path, rows: Sequence[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def result_audit(directory: Path) -> tuple[int, int, int, int]:
    config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    payload = json.loads(
        (directory / "motivation_recovery.json").read_text(encoding="utf-8")
    )
    raw = list(payload.get("raw") or [])
    cells: dict[str, set[str]] = defaultdict(set)
    manifests: dict[str, set[str]] = defaultdict(set)
    for row in raw:
        if row.get("phase") != "m1":
            continue
        trace_id = str(row["trace_id"])
        cells[trace_id].add(str(row["policy"]))
        manifests[trace_id].add(str(row["manifest_hash"]))
    complete = sum(
        policies == set(POLICIES) and len(manifests[trace_id]) == 1
        for trace_id, policies in cells.items()
    )
    return int(config["planned_m1_traces"]), complete, len(raw), len(cells) - complete


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
    config_path = output_dir / "config.json"
    config = (
        json.loads(config_path.read_text(encoding="utf-8"))
        if config_path.exists()
        else {}
    )
    m1 = [row for row in rows if row["phase"] == "m1"]
    cell_policies: dict[str, set[str]] = defaultdict(set)
    cell_manifests: dict[str, set[str]] = defaultdict(set)
    for row in m1:
        trace_id = str(row["trace_id"])
        cell_policies[trace_id].add(str(row["policy"]))
        cell_manifests[trace_id].add(str(row["manifest_hash"]))
    inconsistent_cells = sum(
        len(manifests) != 1 for manifests in cell_manifests.values()
    )
    paired_cells = sum(
        policies == set(POLICIES) and len(cell_manifests[trace_id]) == 1
        for trace_id, policies in cell_policies.items()
    )
    planned_cells = int(config.get("planned_m1_traces") or len(cell_policies))
    complete_rows = paired_cells * len(POLICIES)
    excluded_rows = max(planned_cells * len(POLICIES) - complete_rows, 0)
    git = config.get("git") or {}
    lines = [
        "# Multi-turn recovery motivation",
        "",
        "`turn_checkpoint_proxy` is a turn-boundary proxy, not a Crab artifact.",
        "M1/M2 token weights are tokenizer attribution, not billed replay usage.",
        "Motivation reports the ideal oracle and non-causal baselines; the implemented causal policy is intentionally excluded.",
        "Analytical rows use the deterministic generator oracle and paired manifests.",
        "`rollback safe` means the complete invalid closure was removed. `exact pre-replay` additionally requires that no valid work was discarded; coarse rollback is expected to be safe but non-exact until replay completes.",
        "",
        "## Run audit",
        "",
        f"- Planned paired cells: {planned_cells}",
        f"- Completed paired cells: {paired_cells}",
        f"- Completed policy rows: {complete_rows}",
        f"- Excluded or missing policy rows: {excluded_rows}",
        f"- Cells with inconsistent policy manifests: {inconsistent_cells}",
        f"- Git commit: `{git.get('commit', 'unknown')}`",
        f"- Dirty worktree: `{git.get('dirty', 'unknown')}`",
        f"- Worktree status SHA-256: `{git.get('worktree_status_sha256', 'unknown')}`",
        "",
        "## Results",
        "",
        "| phase | policy | dependency | n | retention mean | invalid removal mean | useful steps wasted mean | rollback safe | exact pre-replay |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in summaries:
        lines.append(
            f"| {item['phase']} | {item['policy']} | "
            f"{item['requested_dependency_value']} | {item['n']} | "
            f"{item['valid_retention_mean']:.4f} | {item['invalid_removal_mean']:.4f} | "
            f"{item['useful_steps_wasted_mean']:.2f} | "
            f"{item['rollback_safe_rate']:.4f} | "
            f"{item['exact_state_before_replay_rate']:.4f} |"
        )
    if output_dir.name == "microbenchmark":
        auxiliary = (
            ("Turn-count sensitivity", output_dir / "sensitivity" / "turn_count"),
            (
                "Fault-position sensitivity",
                output_dir / "sensitivity" / "fault_position",
            ),
            ("Shape sensitivity", output_dir / "sensitivity" / "shape"),
            (
                "Interleaving sensitivity",
                output_dir / "sensitivity" / "interleaving",
            ),
            ("Boundary physical gate", output_dir / "physical_boundary_gate"),
        )
        lines.extend(
            [
                "",
                "## Auxiliary sweep audit",
                "",
                "| run | planned cells | completed cells | policy rows | incomplete cells |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for label, directory in auxiliary:
            if not (directory / "motivation_recovery.json").exists():
                lines.append(f"| {label} | not run | not run | not run | not run |")
                continue
            planned, complete, row_count, incomplete = result_audit(directory)
            lines.append(
                f"| {label} | {planned} | {complete} | {row_count} | {incomplete} |"
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


def git_state() -> dict:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    ).stdout
    return {
        "commit": commit,
        "dirty": bool(status),
        "worktree_status_sha256": hashlib.sha256(status.encode("utf-8")).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["m1", "m2", "all"], default="all")
    parser.add_argument(
        "--turn-counts",
        nargs="+",
        type=int,
        default=[5],
    )
    parser.add_argument(
        "--widths",
        nargs="+",
        type=int,
        default=[16],
        help="operations per turn",
    )
    parser.add_argument("--fault-positions", nargs="+", type=float, default=[0.05, 0.25, 0.5, 0.75])
    density = parser.add_mutually_exclusive_group()
    density.add_argument(
        "--dependency-values",
        nargs="+",
        type=float,
        default=list(DEFAULT_DEPENDENCY_VALUES),
    )
    density.add_argument("--closure-ratios", nargs="+", type=float)
    parser.add_argument("--shapes", nargs="+", choices=SHAPES, default=list(SHAPES))
    parser.add_argument("--interleavings", nargs="+", choices=INTERLEAVINGS, default=list(INTERLEAVINGS))
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument(
        "--m1-execution",
        choices=["analytical", "physical"],
        default="analytical",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="regenerate summaries and REPORT.md from existing raw rows",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.repeats <= 0:
        parser.error("repeats must be positive")
    if any(not 1 <= value <= 10 for value in args.turn_counts):
        parser.error("turn counts must be in [1, 10]")
    dependency_mode = args.closure_ratios is None
    density_values = (
        args.dependency_values if dependency_mode else args.closure_ratios
    )
    if dependency_mode and any(not 0.0 <= value <= 1.0 for value in density_values):
        parser.error("dependency values must be in [0, 1]")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "schema": MICROBENCHMARK_SCHEMA,
        "phase": args.phase,
        "density_axis": "dependency_value" if dependency_mode else "legacy_closure_ratio",
        "dependency_values": args.dependency_values if dependency_mode else None,
        "closure_ratios": args.closure_ratios,
        "turn_counts": args.turn_counts,
        "turn_widths": args.widths,
        "widths": args.widths,
        "fault_positions": args.fault_positions,
        "shapes": args.shapes,
        "interleavings": args.interleavings,
        "repeats": args.repeats,
        "m1_execution": args.m1_execution,
        "policies": list(POLICIES),
        "planned_m1_traces": (
            0
            if args.phase == "m2"
            else (
                len(args.widths)
                * len(args.turn_counts)
                * len(args.fault_positions)
                * len(density_values)
                * len(args.shapes)
                * len(args.interleavings)
                * args.repeats
            )
        ),
        "git": git_state(),
    }
    if args.phase in {"m2", "all"}:
        config["m2_extreme"] = {
            "planned_traces": 1,
            "loop_width": 128,
            "fault_step_id": 1,
            "closure_size": 4,
            "valid_size": 124,
        }
    config_path = args.output_dir / "config.json"
    if config_path.exists():
        previous = json.loads(config_path.read_text(encoding="utf-8"))
        comparable = {key: value for key, value in config.items() if key != "git"}
        previous_comparable = {
            key: value for key, value in previous.items() if key != "git"
        }
        if previous_comparable != comparable:
            raise RuntimeError(
                f"output directory has an incompatible configuration: {config_path}"
            )
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    rows: list[dict] = []
    existing = args.output_dir / "motivation_recovery.json"
    if existing.exists():
        rows = list(json.loads(existing.read_text(encoding="utf-8")).get("raw") or [])
        for row in rows:
            row.setdefault("execution_kind", "physical_overlay")
    if args.report_only:
        if not rows:
            parser.error(f"no existing rows found in {args.output_dir}")
        write_outputs(args.output_dir, rows)
        return 0
    selected_phases = {"m1", "m2"} if args.phase == "all" else {args.phase}
    rows = [row for row in rows if row.get("phase") not in selected_phases]
    if args.phase in {"m2", "all"}:
        rows.extend(run_plan(build_extreme_plan(), "m2", 0))
        write_outputs(args.output_dir, rows)
    if args.phase in {"m1", "all"}:
        config_count = 0
        for turn_count in args.turn_counts:
            for width in args.widths:
                for fault in args.fault_positions:
                    for density_value in density_values:
                        for shape in args.shapes:
                            for interleaving in args.interleavings:
                                for repeat in range(args.repeats):
                                    plan = build_multi_turn_plan(
                                        turn_count,
                                        width,
                                    fault_position=fault,
                                    closure_ratio=(
                                        None if dependency_mode else density_value
                                    ),
                                    dependency_value=(
                                        density_value if dependency_mode else None
                                    ),
                                    shape=shape,
                                    interleaving=interleaving,
                                        seed=repeat,
                                    )
                                    if args.m1_execution == "physical":
                                        rows.extend(run_plan(plan, "m1", repeat))
                                    else:
                                        rows.extend(analytical_rows(plan, repeat))
                                    config_count += 1
                                    if config_count % 500 == 0:
                                        write_outputs(args.output_dir, rows)
                                        print(f"m1 checkpoint configs={config_count} rows={len(rows)}", flush=True)
        if config_count % 500:
            write_outputs(args.output_dir, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
