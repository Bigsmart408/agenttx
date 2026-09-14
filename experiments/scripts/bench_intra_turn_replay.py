#!/usr/bin/env python3
"""M3 real-model replay for representative intra-turn recovery traces."""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import random
import shutil
import statistics
import sys
import tempfile
from pathlib import Path
from typing import Protocol

import tiktoken

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from agenttx.agents.external import CodexHarness  # noqa: E402
from experiments.workloads.intra_turn_recovery_dag import (  # noqa: E402
    IntraTurnPlan,
    build_extreme_plan,
    build_intra_turn_plan,
)


class WorkloadHarness(Protocol):
    model: str

    def run(self, task: str, workdir: Path, env: dict[str, str] | None = None): ...


POLICIES = (
    "clean_recovery",
    "crash_no_rollback",
    "turn_checkpoint_proxy",
    "temporal_step",
    "whole_branch",
    "agenttx_causal",
    "ideal_oracle",
)
COMPARISON_POLICIES = (
    "crash_no_rollback",
    "turn_checkpoint_proxy",
    "temporal_step",
    "whole_branch",
)


def percentile(values: list[int] | list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def bootstrap_mean_ci(values: list[int], seed: int = 20260912) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    if len(values) == 1:
        return float(values[0]), float(values[0])
    rng = random.Random(seed)
    means = [statistics.mean(rng.choice(values) for _ in values) for _ in range(2000)]
    return percentile(means, 0.025), percentile(means, 0.975)


def representative_plans() -> list[IntraTurnPlan]:
    plans = [
        build_intra_turn_plan(
            width,
            fault_position=fault,
            closure_ratio=ratio,
            shape="layered",
            interleaving="alternating",
            seed=0,
        )
        for width in (16, 64, 128)
        for fault in (0.05, 0.50)
        for ratio in (0.03, 0.25)
    ]
    return [*plans, build_extreme_plan()]


def rollback_targets(plan: IntraTurnPlan, policy: str) -> set[int]:
    if policy in {"clean_recovery", "crash_no_rollback"}:
        return set()
    if policy in {"turn_checkpoint_proxy", "whole_branch"}:
        return set(range(len(plan.steps)))
    if policy == "temporal_step":
        return set(range(plan.fault_step_id, len(plan.steps)))
    if policy in {"agenttx_causal", "ideal_oracle"}:
        return set(plan.oracle_closure)
    raise ValueError(policy)


def desired(step_id: int) -> str:
    return f"artifact-{step_id:03d}-valid\n"


def prepare(plan: IntraTurnPlan, policy: str, workspace: Path) -> list[int]:
    workspace.mkdir(parents=True, exist_ok=True)
    invalid = set() if policy == "clean_recovery" else set(plan.oracle_closure)
    missing = sorted(rollback_targets(plan, policy) | invalid)
    for step in plan.steps:
        if policy == "crash_no_rollback" and step.index in invalid:
            (workspace / step.relative_path).write_text(
                f"artifact-{step.index:03d}-FAULTY\n", encoding="utf-8"
            )
        elif step.index not in missing:
            (workspace / step.relative_path).write_text(
                desired(step.index), encoding="utf-8"
            )
    return [] if policy == "crash_no_rollback" else missing


def call_token_weights(plan: IntraTurnPlan) -> dict[int, int]:
    encoding = tiktoken.get_encoding("o200k_base")
    return {
        step.index: len(encoding.encode(step.token_payload(plan.steps)))
        for step in plan.steps
    }


def make_manifest(
    plan: IntraTurnPlan,
    missing: list[int],
    *,
    fault_injected: bool = True,
    diagnosis_required: bool = False,
) -> dict:
    payload = {
        "schema": "agenttx.motivation.replay/v1",
        # Do not expose the internal trace id: it encodes fault position and
        # closure ratio in controlled M3 plans.
        "case_id": hashlib.sha256(plan.trace_id.encode("utf-8")).hexdigest()[:16],
        "fault_step_id": (
            plan.fault_step_id
            if fault_injected and not diagnosis_required
            else None
        ),
        "diagnosis_required": diagnosis_required,
        "missing": [
            {"path": plan.steps[index].relative_path, "content": desired(index)}
            for index in missing
        ],
        "repair": [],
        "retained_count": len(plan.steps) - len(missing),
    }
    payload["manifest_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return payload


def make_prompt(payload: dict) -> str:
    if payload.get("diagnosis_required"):
        return (
            "A crash was detected in this deterministic artifact workspace. "
            "No fault location, repair targets, or expected contents are provided. "
            "Inspect the workspace, diagnose every corrupted artifact from workspace evidence, "
            "infer the intended format from intact sibling artifacts, and repair all corruption. "
            "Do not modify intact artifacts. Use tools as needed, then stop.\n\n"
            + json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )
    return (
        "Recover this deterministic workspace from the authoritative JSON manifest. "
        "Create every `missing` path and replace every `repair` path with exactly its listed content. "
        "Do not read, inspect, "
        "or modify any retained file. Use tools as needed, then stop.\n\n"
        + json.dumps(payload, sort_keys=True, separators=(",", ":"))
    )


def verify(plan: IntraTurnPlan, workspace: Path) -> tuple[bool, list[str]]:
    errors = []
    for step in plan.steps:
        path = workspace / step.relative_path
        if not path.is_file():
            errors.append(f"missing:{step.relative_path}")
        elif path.read_text(encoding="utf-8") != desired(step.index):
            errors.append(f"content:{step.relative_path}")
    return not errors, errors


def completed_keys(path: Path) -> set[tuple[str, str, int]]:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            (row["trace_id"], row["policy"], int(row["repeat"]))
            for row in csv.DictReader(handle)
            if row.get("completed") == "True"
        }


def append_row(path: Path, row: dict) -> None:
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        exists = path.exists() and path.stat().st_size > 0
        with path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row), lineterminator="\n")
            if not exists:
                writer.writeheader()
            writer.writerow(row)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def run_cell(
    plan: IntraTurnPlan,
    policy: str,
    repeat: int,
    harness: WorkloadHarness,
) -> dict:
    scratch = Path(tempfile.mkdtemp(prefix="agenttx-m3-", dir="/tmp"))
    workspace = scratch / "workspace"
    try:
        missing = prepare(plan, policy, workspace)
        fault_injected = policy != "clean_recovery"
        diagnosis_required = policy == "crash_no_rollback"
        payload = make_manifest(
            plan,
            missing,
            fault_injected=fault_injected,
            diagnosis_required=diagnosis_required,
        )
        cell_id = f"{plan.trace_id}__{policy}__r{repeat}"
        result = harness.run(
            make_prompt(payload),
            workspace,
            env={
                "AGENTTX_CELL_ID": cell_id,
                "AGENTTX_DIAGNOSIS_REQUIRED": "1" if diagnosis_required else "0",
            },
        )
        passed, errors = verify(plan, workspace)
        useful = rollback_targets(plan, policy) & set(plan.valid_ids)
        weights = call_token_weights(plan)
        row = {
            "trace_id": plan.trace_id,
            "fault_injected": fault_injected,
            "fault_step_id": plan.fault_step_id if fault_injected else "",
            "seed": plan.seed,
            "manifest_hash": plan.manifest_hash,
            "replay_manifest_hash": payload["manifest_hash"],
            "policy": policy,
            "recovery_context_variant": "compact",
            "repeat": repeat,
            "loop_width": len(plan.steps),
            "fault_position": plan.requested_fault_position if fault_injected else "",
            "closure_ratio": plan.requested_closure_ratio if fault_injected else "",
            "shape": plan.shape,
            "interleaving": plan.interleaving,
            "missing_artifacts": len(missing),
            "repair_artifacts": 0,
            "diagnosis_required": diagnosis_required,
            "fault_location_disclosed": bool(
                fault_injected and not diagnosis_required
            ),
            "repair_hint_provided": False,
            "attributed_lost_steps": len(useful),
            "attributed_lost_tokens": sum(weights[index] for index in useful),
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "replay_total_tokens": result.total_tokens,
            "usage_source": result.usage_source,
            "tool_calls": result.tool_calls,
            "harness_returncode": result.returncode,
            "verifier_passed": passed,
            "verifier_errors": json.dumps(errors),
            "completed": bool(result.returncode == 0 and passed and result.total_tokens > 0),
            "wall_s": round(result.duration_s, 6),
            "stderr": result.stderr[-2000:],
        }
        row.update(getattr(result, "metadata", {}))
        return row
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def summarize(raw_path: Path, output_dir: Path) -> None:
    lock_path = raw_path.with_suffix(raw_path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
        with raw_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    latest: dict[tuple[str, str, int], dict] = {}
    attempts: dict[str, int] = {}
    for row in rows:
        policy = row["policy"]
        attempts[policy] = attempts.get(policy, 0) + 1
        latest[(row["trace_id"], policy, int(row["repeat"]))] = row
    groups: dict[str, list[dict]] = {}
    for row in latest.values():
        groups.setdefault(row["policy"], []).append(row)
    summary = []
    for policy, group in sorted(groups.items()):
        good = [row for row in group if row["completed"] == "True"]
        tokens = [int(row["replay_total_tokens"]) for row in good]
        prompts = [int(row["prompt_tokens"]) for row in good]
        completions = [int(row["completion_tokens"]) for row in good]
        overheads = []
        if policy != "clean_recovery":
            for row in good:
                clean = latest.get(
                    (row["trace_id"], "clean_recovery", int(row["repeat"]))
                )
                if clean and clean["completed"] == "True":
                    overheads.append(
                        int(row["replay_total_tokens"])
                        - int(clean["replay_total_tokens"])
                    )
        summary.append(
            {
                "policy": policy,
                "cells": len(group),
                "attempts": attempts[policy],
                "completed": len(good),
                "success_rate": len(good) / len(group),
                "replay_tokens_mean": statistics.mean(tokens) if tokens else 0,
                "replay_tokens_p50": statistics.median(tokens) if tokens else 0,
                "replay_tokens_p95": percentile(tokens, 0.95) if tokens else 0,
                "replay_tokens_p99": percentile(tokens, 0.99) if tokens else 0,
                "prompt_tokens_mean": statistics.mean(prompts) if prompts else 0,
                "completion_tokens_mean": statistics.mean(completions) if completions else 0,
                "recovery_overhead_mean": statistics.mean(overheads) if overheads else 0,
                "recovery_overhead_p50": statistics.median(overheads) if overheads else 0,
                "recovery_overhead_p95": percentile(overheads, 0.95) if overheads else 0,
                "recovery_overhead_p99": percentile(overheads, 0.99) if overheads else 0,
            }
        )
    if summary:
        with (output_dir / "motivation_replay_summary.csv").open(
            "w", newline="", encoding="utf-8"
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=list(summary[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(summary)

    paired = []
    for reference in ("agenttx_causal", "ideal_oracle"):
        for baseline in COMPARISON_POLICIES:
            differences = []
            baseline_overheads = []
            reference_overheads = []
            wins = 0
            for trace_id, _, repeat in {
                key for key in latest if key[1] == reference
            }:
                ref = latest.get((trace_id, reference, repeat))
                base = latest.get((trace_id, baseline, repeat))
                clean = latest.get((trace_id, "clean_recovery", repeat))
                if (
                    not ref
                    or not base
                    or not clean
                    or ref["completed"] != "True"
                    or base["completed"] != "True"
                    or clean["completed"] != "True"
                ):
                    continue
                ref_tokens = int(ref["replay_total_tokens"])
                base_tokens = int(base["replay_total_tokens"])
                clean_tokens = int(clean["replay_total_tokens"])
                difference = base_tokens - ref_tokens
                differences.append(difference)
                baseline_overheads.append(base_tokens - clean_tokens)
                reference_overheads.append(ref_tokens - clean_tokens)
                wins += difference > 0
            low, high = bootstrap_mean_ci(differences)
            if not differences:
                continue
            paired.append(
                {
                    "reference": reference,
                    "baseline": baseline,
                    "paired_cells": len(differences),
                    "saved_tokens_mean": statistics.mean(differences),
                    "saved_tokens_p50": statistics.median(differences),
                    "saved_tokens_mean_ci95_low": low,
                    "saved_tokens_mean_ci95_high": high,
                    "baseline_recovery_overhead_mean": statistics.mean(baseline_overheads),
                    "reference_recovery_overhead_mean": statistics.mean(reference_overheads),
                    "saved_recovery_pct": (
                        statistics.mean(differences)
                        / statistics.mean(baseline_overheads)
                        if statistics.mean(baseline_overheads) > 0
                        else ""
                    ),
                    "win_rate": wins / len(differences),
                }
            )
    if paired:
        paired_path = output_dir / "motivation_replay_paired.csv"
        with paired_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(paired[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(paired)

    report = [
        "# M3 real-model replay cost",
        "",
        "Rows are deduplicated by `(trace_id, policy, repeat)` using the latest attempt. "
        "Failed attempts remain in the raw CSV for audit.",
        "All replays use the fixed compact-manifest projection; `clean_recovery` has no fault and an empty missing manifest, and `agenttx_causal` is explicitly pinned to compact context.",
        "",
        "| policy | cells | attempts | success | mean total | P50 | P95 | P99 | mean overhead vs clean |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary:
        report.append(
            f"| {row['policy']} | {row['cells']} | {row['attempts']} | {row['success_rate']:.4f} | "
            f"{row['replay_tokens_mean']:.1f} | {row['replay_tokens_p50']:.1f} | "
            f"{row['replay_tokens_p95']:.1f} | {row['replay_tokens_p99']:.1f} | "
            f"{row['recovery_overhead_mean']:.1f} |"
        )
    report.extend(
        [
            "",
            "Recovery overhead is paired policy token minus clean token. Positive savings mean that the reference removed part of that crash-induced overhead.",
            "",
            "| reference | baseline | pairs | baseline overhead | reference overhead | mean saved | mean 95% CI | saved overhead | win rate |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in paired:
        saved_pct = (
            f"{row['saved_recovery_pct']:.2%}"
            if row["saved_recovery_pct"] != ""
            else "N/A"
        )
        report.append(
            f"| {row['reference']} | {row['baseline']} | {row['paired_cells']} | "
            f"{row['baseline_recovery_overhead_mean']:.1f} | "
            f"{row['reference_recovery_overhead_mean']:.1f} | "
            f"{row['saved_tokens_mean']:.1f} | "
            f"[{row['saved_tokens_mean_ci95_low']:.1f}, {row['saved_tokens_mean_ci95_high']:.1f}] | "
            f"{saved_pct} | {row['win_rate']:.2%} |"
        )
    (output_dir / "M3_REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--harness", choices=("codex", "sweagent"), default="codex")
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--policies", nargs="+", choices=POLICIES, default=list(POLICIES))
    parser.add_argument(
        "--max-plans",
        type=int,
        default=None,
        help="Limit the deterministic plan list for smoke gates.",
    )
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--num-shards", type=int, default=1)
    parser.add_argument("--model", default=None)
    parser.add_argument("--reasoning-effort", default=None)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument(
        "--sweagent-config",
        type=Path,
        default=Path("/home/bfq/SWE-agent/config/bash_only.yaml"),
    )
    parser.add_argument(
        "--swe-agent-root",
        type=Path,
        default=Path("/home/bfq/SWE-agent"),
    )
    parser.add_argument(
        "--codex-config",
        type=Path,
        default=Path("/home/bfq/.codex/config.toml"),
    )
    parser.add_argument(
        "--codex-auth",
        type=Path,
        default=Path("/home/bfq/.codex/auth.json"),
    )
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--max-model-calls", type=int, default=30)
    args = parser.parse_args()
    if not 0 <= args.shard_index < args.num_shards:
        parser.error("shard index must be within num shards")
    if args.max_plans is not None and args.max_plans < 1:
        parser.error("max plans must be positive")
    if args.max_steps < 1 or args.max_model_calls < 1:
        parser.error("step and model-call limits must be positive")

    if args.harness == "codex":
        model = args.model or "gpt-5.6-luna"
        harness: WorkloadHarness = CodexHarness(model=model, timeout_s=args.timeout)
        harness_config = {"harness_backend": "codex"}
    else:
        from experiments.scripts.sweagent_native_workload import (
            SWEAgentNativeWorkloadHarness,
            load_codex_gpt_profile,
        )

        profile = load_codex_gpt_profile(
            args.codex_config,
            args.codex_auth,
            model_override=args.model,
            reasoning_effort_override=args.reasoning_effort,
        )
        model = profile.model
        sweagent_harness = SWEAgentNativeWorkloadHarness(
            output_dir=args.output_dir,
            profile=profile,
            config_path=args.sweagent_config,
            swe_agent_root=args.swe_agent_root,
            max_steps=args.max_steps,
            max_model_calls=args.max_model_calls,
        )
        harness = sweagent_harness
        harness_config = {
            **sweagent_harness.public_config,
            "sweagent_config": str(args.sweagent_config.resolve()),
            "swe_agent_root": str(args.swe_agent_root.resolve()),
            "max_steps": args.max_steps,
            "max_model_calls": args.max_model_calls,
        }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    config_path = args.output_dir / "motivation_replay_config.json"
    config_path.write_text(
        json.dumps(
            {
                "schema": "agenttx.motivation.replay_config/v2",
                "model": model,
                "repeats": args.repeats,
                "policies": args.policies,
                "max_plans": args.max_plans,
                "prompt_projection": "compact_manifest",
                "causal_recovery_context": "compact",
                **harness_config,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    raw_path = args.output_dir / "motivation_replay_raw.csv"
    existing = completed_keys(raw_path)
    plans = representative_plans()
    if args.max_plans is not None:
        plans = plans[: args.max_plans]
    all_cells = [
        (plan, policy, repeat)
        for plan in plans
        for policy in args.policies
        for repeat in range(args.repeats)
    ]
    cells = [
        cell
        for index, cell in enumerate(all_cells)
        if index % args.num_shards == args.shard_index
    ]
    for index, (plan, policy, repeat) in enumerate(cells, 1):
        key = (plan.trace_id, policy, repeat)
        if key in existing:
            continue
        print(f"m3 {index}/{len(cells)} {plan.trace_id} {policy} r{repeat}", flush=True)
        row = run_cell(plan, policy, repeat, harness)
        append_row(raw_path, row)
        summarize(raw_path, args.output_dir)
        print(
            f"done={row['completed']} tokens={row['replay_total_tokens']} "
            f"verifier={row['verifier_passed']}",
            flush=True,
        )
        if (
            args.harness == "sweagent"
            and int(row.get("sweagent_model_calls", 0)) == 0
            and int(row["replay_total_tokens"]) == 0
        ):
            print(
                "fatal: SWEAgent made no model call and reported no token usage; "
                "check the GPT profile before resuming",
                flush=True,
            )
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
