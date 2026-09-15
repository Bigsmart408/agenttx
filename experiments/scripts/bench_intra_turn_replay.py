#!/usr/bin/env python3
"""M3 real-model replay for representative intra-turn recovery traces."""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import random
import re
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
    causal_closure,
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

FAULT_NOTICE = (
    "A crash was detected. Its originating step and affected artifact paths are unknown."
)
ARTIFACT_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])step_\d{3}\.txt(?![A-Za-z0-9_])")
SHELL_WRITE_RE = re.compile(
    r"(?:>>?|\btee\b|\btouch\b|\btruncate\b|\bcp\b|\bmv\b|\binstall\b|"
    r"\bsed\s+[^\n;]*?-i\b|\bperl\s+[^\n;]*?-i\b)"
)


def config_compat_value(payload: dict, key: str):
    """Read equivalent config fields across Codex and SWEAgent layouts."""
    if key == "reasoning_effort":
        profile = payload.get("codex_gpt_profile") or {}
        return payload.get(key, profile.get(key))
    return payload.get(key)


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


def injected_stale_payload(step_id: int, step_count: int) -> str:
    """Return a plausible prior artifact without embedding an evaluator label."""
    return desired((step_id - 1) % step_count)


def localize_artifact_faults(plan: IntraTurnPlan, workspace: Path) -> set[int]:
    """Find contract violations without consulting injected fault truth."""
    localized = set()
    for step in plan.steps:
        path = workspace / step.relative_path
        try:
            content = path.read_text(encoding="utf-8")
        except (FileNotFoundError, UnicodeError):
            localized.add(step.index)
            continue
        if content != desired(step.index):
            localized.add(step.index)
    return localized


def recovery_targets_from_localization(
    plan: IntraTurnPlan,
    policy: str,
    localized: set[int],
) -> set[int]:
    """Apply each recovery granularity to the same discovered fault set."""
    if policy in {"clean_recovery", "crash_no_rollback"}:
        return set()
    if policy in {"turn_checkpoint_proxy", "whole_branch"}:
        return set(range(len(plan.steps)))
    if policy == "temporal_step":
        return set(range(min(localized), len(plan.steps))) if localized else set()
    if policy == "agenttx_causal":
        roots = {
            step_id
            for step_id in localized
            if not localized.intersection(plan.steps[step_id].parent_indices)
        }
        return set(causal_closure(plan.steps, roots))
    if policy == "ideal_oracle":
        return set(localized)
    raise ValueError(policy)


def prepare_recovery(
    plan: IntraTurnPlan,
    policy: str,
    workspace: Path,
) -> tuple[list[int], set[int], set[int]]:
    """Create one crash state, localize it, then apply the selected policy."""
    workspace.mkdir(parents=True, exist_ok=True)
    fault_injected = policy != "clean_recovery"
    injected_faults = set(plan.oracle_closure) if fault_injected else set()
    for step in plan.steps:
        path = workspace / step.relative_path
        if step.index in injected_faults:
            path.write_text(
                injected_stale_payload(step.index, len(plan.steps)),
                encoding="utf-8",
            )
        else:
            path.write_text(desired(step.index), encoding="utf-8")

    localized = localize_artifact_faults(plan, workspace)
    targets = recovery_targets_from_localization(plan, policy, localized)
    for step_id in targets:
        (workspace / plan.steps[step_id].relative_path).unlink(missing_ok=True)
    return sorted(targets), localized, targets


def prepare(plan: IntraTurnPlan, policy: str, workspace: Path) -> list[int]:
    missing, _, _ = prepare_recovery(plan, policy, workspace)
    return missing


def call_token_weights(plan: IntraTurnPlan) -> dict[int, int]:
    encoding = tiktoken.get_encoding("o200k_base")
    return {
        step.index: len(encoding.encode(step.token_payload(plan.steps)))
        for step in plan.steps
    }


def make_manifest(
    plan: IntraTurnPlan,
    _missing: list[int],
    *,
    fault_injected: bool = True,
    diagnosis_required: bool = False,
) -> dict:
    payload = {
        "schema": "agenttx.motivation.replay/v3",
        # Do not expose the internal trace id: it encodes fault position and
        # closure ratio in controlled M3 plans.
        "case_id": hashlib.sha256(plan.trace_id.encode("utf-8")).hexdigest()[:16],
        # Fault truth remains private to the injector/evaluator. Every policy
        # must discover missing or corrupted artifacts from the workspace.
        "fault_step_id": None,
        "fault_detected": fault_injected,
        "diagnosis_required": diagnosis_required,
        "artifact_contract": {
            "count": len(plan.steps),
            "index_start": 0,
            "index_end_exclusive": len(plan.steps),
            "path_template": "step_{index:03d}.txt",
            "content_template": "artifact-{index:03d}-valid\\n",
            "replay_unit": "one artifact per write-producing tool invocation",
        },
        "missing": [],
        "repair": [],
    }
    payload["manifest_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return payload


def render_unrewound_history(plan: IntraTurnPlan) -> str:
    """Project the still-active pre-crash tool history for no-rollback runs.

    External black-box harnesses cannot resume an injected native conversation.
    The projection preserves the tool-success observations and the agent's
    resulting belief that it can proceed. The workspace may contradict that
    belief after a crash, but the projection never labels the affected calls.
    """
    turns = []
    for step in plan.steps:
        turns.append(
            {
                "assistant_tool_call": {
                    "name": step.name,
                    "arguments": {"path": step.relative_path},
                },
                "tool_result": {
                    "exit_code": 0,
                    "stdout": "",
                    "stderr": "",
                    "status": "completed",
                },
                "assistant_message": (
                    f"{step.name} completed successfully; proceeding with the "
                    "remaining original work."
                ),
            }
        )
    return json.dumps(
        {
            "schema": "agenttx.active_tool_history/v2",
            "status": "active_unrewound",
            "turns": turns,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def make_prompt(
    payload: dict,
    *,
    policy: str,
    active_history: str = "",
) -> str:
    replay_rule = (
        "Each artifact represents one independent original tool call. Create or modify at "
        "most one step_*.txt artifact per write-producing shell or edit invocation. Do not "
        "use loops, brace or glob expansion, xargs, generated scripts, or a multi-file patch "
        "to repair multiple artifacts at once. Read-only inspection may cover multiple "
        "artifacts. "
    )
    contract = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    original_task = (
        "Original task: make the current workspace satisfy the deterministic artifact "
        "contract below, without modifying correct artifacts. "
    )
    if not payload.get("diagnosis_required"):
        return original_task + replay_rule + "Use tools as needed, then stop.\n\n" + contract
    if policy == "crash_no_rollback":
        if not active_history:
            raise ValueError("crash_no_rollback requires the active unrewound history")
        return (
            original_task
            + FAULT_NOTICE
            + " No rollback or recovery handoff was applied. Continue the original task "
            "with the current workspace and active history; no repair plan is provided. "
            + replay_rule
            + "Use tools as needed, then stop.\n\n"
            + contract
            + "\n\nActive pre-crash tool history:\n"
            + active_history
        )
    return (
        original_task
        + FAULT_NOTICE
        + " The configured rollback policy has already been applied and its invalidated "
        "history was removed. Continue the original task from the current compact state; "
        "no repair plan is provided. "
        + replay_rule
        + "Use tools as needed, then stop.\n\n"
        + contract
    )


def audit_replay_write_events(
    events: list[dict], required_paths: set[str]
) -> dict:
    """Enforce the v8 one-artifact-per-completed-write-event protocol."""
    covered: set[str] = set()
    write_events = 0
    completed_tool_events = 0
    violations: list[dict] = []
    for event in events:
        if str(event.get("type", "")).lower() != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", "")).lower()
        touched: set[str] = set()
        if item_type == "command_execution":
            completed_tool_events += 1
            command = str(item.get("command") or "")
            if SHELL_WRITE_RE.search(command):
                touched = set(ARTIFACT_PATH_RE.findall(command)) & required_paths
        elif item_type == "file_change":
            completed_tool_events += 1
            changes = item.get("changes") or []
            if isinstance(changes, dict):
                changes = [changes]
            if isinstance(changes, list):
                for change in changes:
                    if not isinstance(change, dict):
                        continue
                    candidate = str(change.get("path") or change.get("file") or "")
                    touched.update(ARTIFACT_PATH_RE.findall(candidate))
                touched &= required_paths
        elif item_type in {"mcp_tool_call", "dynamic_tool_call"}:
            completed_tool_events += 1
        if not touched:
            continue
        write_events += 1
        if len(touched) > 1:
            violations.append(
                {"item_id": item.get("id"), "paths": sorted(touched)}
            )
        covered.update(touched)
    missing = required_paths - covered
    available = bool(events) or not required_paths
    return {
        "available": available,
        "followed": bool(available and not missing and not violations),
        "write_events": write_events,
        "completed_tool_events": completed_tool_events,
        "covered_paths": sorted(covered),
        "missing_paths": sorted(missing),
        "violations": violations,
    }


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
        missing, localized, targets = prepare_recovery(plan, policy, workspace)
        fault_injected = policy != "clean_recovery"
        diagnosis_required = fault_injected
        payload = make_manifest(
            plan,
            missing,
            fault_injected=fault_injected,
            diagnosis_required=diagnosis_required,
        )
        cell_id = f"{plan.trace_id}__{policy}__r{repeat}"
        active_history = (
            render_unrewound_history(plan) if policy == "crash_no_rollback" else ""
        )
        prompt = make_prompt(
            payload,
            policy=policy,
            active_history=active_history,
        )
        result = harness.run(
            prompt,
            workspace,
            env={
                "AGENTTX_CELL_ID": cell_id,
                "AGENTTX_DIAGNOSIS_REQUIRED": "1" if diagnosis_required else "0",
            },
        )
        passed, errors = verify(plan, workspace)
        useful = targets & set(plan.valid_ids)
        weights = call_token_weights(plan)
        replay_steps = targets if targets else (localized if fault_injected else set())
        replay_paths = {plan.steps[index].relative_path for index in replay_steps}
        replay_units = len(replay_paths)
        write_audit = audit_replay_write_events(
            list(getattr(result, "events", []) or []), replay_paths
        )
        replay_protocol_followed = write_audit["followed"]
        row = {
            "trace_id": plan.trace_id,
            "fault_injected": fault_injected,
            "fault_step_id": plan.fault_step_id if fault_injected else "",
            "seed": plan.seed,
            "manifest_hash": plan.manifest_hash,
            "replay_manifest_hash": payload["manifest_hash"],
            "policy": policy,
            "recovery_context_variant": (
                "full_unrewound" if policy == "crash_no_rollback" else "compact"
            ),
            "active_history_chars": len(active_history),
            "active_history_tokens": len(
                tiktoken.get_encoding("o200k_base").encode(active_history)
            ),
            "fault_notice_hash": hashlib.sha256(FAULT_NOTICE.encode("utf-8")).hexdigest(),
            "agent_prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "repeat": repeat,
            "loop_width": len(plan.steps),
            "fault_position": plan.requested_fault_position if fault_injected else "",
            "closure_ratio": plan.requested_closure_ratio if fault_injected else "",
            "shape": plan.shape,
            "interleaving": plan.interleaving,
            "missing_artifacts": len(missing),
            "repair_artifacts": 0,
            "diagnosis_required": diagnosis_required,
            "fault_location_disclosed": False,
            "repair_hint_provided": False,
            "fault_payload_encoding": "opaque_stale_artifact_v1",
            "active_history_projection": (
                "accepted_tool_success_v2"
                if policy == "crash_no_rollback"
                else "compact_invalid_history_removed"
            ),
            "localization_source": "artifact_contract_scan",
            "localized_fault_steps": json.dumps(sorted(localized)),
            "localization_matches_injected_faults": localized
            == (set(plan.oracle_closure) if fault_injected else set()),
            "recovery_targets": json.dumps(sorted(targets)),
            "replay_units_required": replay_units,
            "replay_protocol_followed": replay_protocol_followed,
            "replay_write_audit_available": write_audit["available"],
            "replay_write_events": write_audit["write_events"],
            "replay_write_covered_paths": json.dumps(write_audit["covered_paths"]),
            "replay_write_missing_paths": json.dumps(write_audit["missing_paths"]),
            "replay_write_violations": json.dumps(write_audit["violations"]),
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
            "completed": bool(
                result.returncode == 0
                and passed
                and result.total_tokens > 0
                and replay_protocol_followed
            ),
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
    attempt_groups: dict[str, list[dict]] = {}
    for row in rows:
        attempt_groups.setdefault(row["policy"], []).append(row)
    summary = []
    for policy, group in sorted(groups.items()):
        good = [row for row in group if row["completed"] == "True"]
        all_attempts = attempt_groups[policy]
        observed_tokens = [int(row["replay_total_tokens"]) for row in all_attempts]
        tokens = [int(row["replay_total_tokens"]) for row in good]
        prompts = [int(row["prompt_tokens"]) for row in good]
        completions = [int(row["completion_tokens"]) for row in good]
        walls = [float(row["wall_s"]) for row in good]
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
                "failed": len(group) - len(good),
                "failed_attempts": sum(
                    row["completed"] != "True" for row in all_attempts
                ),
                "success_rate": len(good) / len(group),
                "observed_tokens_sum": sum(observed_tokens),
                "observed_tokens_mean_all_attempts": statistics.mean(observed_tokens),
                "observed_tokens_per_success": (
                    sum(observed_tokens) / len(good) if good else ""
                ),
                "replay_protocol_rate": sum(
                    row.get("replay_protocol_followed") == "True" for row in group
                )
                / len(group),
                "replay_tokens_mean": statistics.mean(tokens) if tokens else 0,
                "replay_tokens_p50": statistics.median(tokens) if tokens else 0,
                "replay_tokens_p95": percentile(tokens, 0.95) if tokens else 0,
                "replay_tokens_p99": percentile(tokens, 0.99) if tokens else 0,
                "prompt_tokens_mean": statistics.mean(prompts) if prompts else 0,
                "completion_tokens_mean": statistics.mean(completions) if completions else 0,
                "wall_s_mean": statistics.mean(walls) if walls else 0,
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
            for trace_id, _, repeat in sorted(
                key for key in latest if key[1] == reference
            ):
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

    versus_no_rollback = []
    for policy in sorted(groups):
        if policy in {"clean_recovery", "crash_no_rollback"}:
            continue
        differences = []
        wins = 0
        for trace_id, _, repeat in sorted(
            key for key in latest if key[1] == policy
        ):
            candidate = latest.get((trace_id, policy, repeat))
            baseline = latest.get((trace_id, "crash_no_rollback", repeat))
            if (
                not candidate
                or not baseline
                or candidate["completed"] != "True"
                or baseline["completed"] != "True"
            ):
                continue
            difference = int(baseline["replay_total_tokens"]) - int(
                candidate["replay_total_tokens"]
            )
            differences.append(difference)
            wins += difference > 0
        if not differences:
            continue
        low, high = bootstrap_mean_ci(differences)
        versus_no_rollback.append(
            {
                "policy": policy,
                "paired_cells": len(differences),
                "saved_tokens_mean": statistics.mean(differences),
                "saved_tokens_p50": statistics.median(differences),
                "saved_tokens_mean_ci95_low": low,
                "saved_tokens_mean_ci95_high": high,
                "win_rate": wins / len(differences),
            }
        )
    if versus_no_rollback:
        path = output_dir / "motivation_vs_no_rollback.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=list(versus_no_rollback[0]), lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(versus_no_rollback)

    report = [
        "# M3 real-model replay cost",
        "",
        "Rows are deduplicated by `(trace_id, policy, repeat)` using the latest attempt. "
        "Failed attempts remain in the raw CSV for audit.",
        "All replays use the opaque v3 artifact contract: fault location, affected paths, retained count, and path-specific repairs are hidden from every fault policy. The zero-based index range is explicit and each write-producing tool invocation may recover at most one artifact. Recovery policies use compact post-rollback context; `crash_no_rollback` retains the full active pre-crash tool history and receives no repair plan.",
        "Before policy application, every fault arm uses the same artifact-contract scan; injected fault truth is used only for post-hoc localization evaluation.",
        "",
        "| policy | cells | attempts | failed cells | failed attempts | success | all-attempt mean | tokens/success | successful mean | P50 | P95 | P99 | mean wall s | mean overhead vs clean |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary:
        tokens_per_success = row["observed_tokens_per_success"]
        tokens_per_success_text = (
            "" if tokens_per_success == "" else f"{tokens_per_success:.1f}"
        )
        report.append(
            f"| {row['policy']} | {row['cells']} | {row['attempts']} | "
            f"{row['failed']} | {row['failed_attempts']} | {row['success_rate']:.4f} | "
            f"{row['observed_tokens_mean_all_attempts']:.1f} | "
            f"{tokens_per_success_text} | "
            f"{row['replay_tokens_mean']:.1f} | {row['replay_tokens_p50']:.1f} | "
            f"{row['replay_tokens_p95']:.1f} | {row['replay_tokens_p99']:.1f} | "
            f"{row['wall_s_mean']:.1f} | "
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
    report.extend(
        [
            "",
            "Paired comparison against the context-faithful no-rollback baseline. Positive saved tokens mean the recovery policy used fewer tokens.",
            "",
            "| policy | pairs | mean saved vs no rollback | P50 saved | mean 95% CI | win rate |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in versus_no_rollback:
        report.append(
            f"| {row['policy']} | {row['paired_cells']} | "
            f"{row['saved_tokens_mean']:.1f} | {row['saved_tokens_p50']:.1f} | "
            f"[{row['saved_tokens_mean_ci95_low']:.1f}, "
            f"{row['saved_tokens_mean_ci95_high']:.1f}] | {row['win_rate']:.2%} |"
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
    parser.add_argument(
        "--plan-index",
        type=int,
        default=None,
        help="Run one zero-based entry from the deterministic representative plan list.",
    )
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--num-shards", type=int, default=1)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--reasoning-effort", default="max")
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
    if args.plan_index is not None and args.plan_index < 0:
        parser.error("plan index must be non-negative")
    if args.plan_index is not None and args.max_plans is not None:
        parser.error("--plan-index and --max-plans are mutually exclusive")
    if args.max_steps < 1 or args.max_model_calls < 1:
        parser.error("step and model-call limits must be positive")

    if args.harness == "codex":
        model = args.model
        harness: WorkloadHarness = CodexHarness(
            model=model,
            reasoning_effort=args.reasoning_effort,
            timeout_s=args.timeout,
        )
        harness_config = {
            "harness_backend": "codex",
            "reasoning_effort": args.reasoning_effort,
        }
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
    raw_path = args.output_dir / "motivation_replay_raw.csv"
    config_payload = {
        "schema": "agenttx.motivation.replay_config/v8",
        "model": model,
        "repeats": args.repeats,
        "policies": args.policies,
        "max_plans": args.max_plans,
        "plan_index": args.plan_index,
        "prompt_projection": "opaque_contract_v3_with_contaminated_active_context_v2",
        "fault_payload_encoding": "opaque_stale_artifact_v1",
        "fault_location_input": "hidden_for_all_fault_policies",
        "recovery_localization": "shared_artifact_contract_scan",
        "replay_protocol": "one_artifact_per_completed_write_event_strict_audit",
        "write_event_audit": "codex_jsonl_or_sweagent_native_trajectory",
        "crash_no_rollback_context": (
            "full_active_precrash_tool_history_with_accepted_success_beliefs"
        ),
        "causal_recovery_context": "compact",
        **harness_config,
    }
    if raw_path.exists() and raw_path.stat().st_size:
        if not config_path.exists():
            raise RuntimeError("refusing to resume M3 rows without their config")
        prior_config = json.loads(config_path.read_text(encoding="utf-8"))
        for key in (
            "schema",
            "model",
            "repeats",
            "max_plans",
            "plan_index",
            "prompt_projection",
            "fault_payload_encoding",
            "fault_location_input",
            "recovery_localization",
            "replay_protocol",
            "write_event_audit",
            "crash_no_rollback_context",
            "causal_recovery_context",
            "harness_backend",
            "reasoning_effort",
        ):
            prior_value = config_compat_value(prior_config, key)
            current_value = config_compat_value(config_payload, key)
            if prior_value != current_value:
                raise RuntimeError(
                    f"refusing to mix incompatible M3 semantics for {key}: "
                    f"existing={prior_value!r}, current={current_value!r}"
                )
        selected = set(prior_config.get("policies") or ()) | set(args.policies)
        config_payload["policies"] = [policy for policy in POLICIES if policy in selected]
    config_path.write_text(
        json.dumps(config_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    existing = completed_keys(raw_path)
    plans = representative_plans()
    if args.plan_index is not None:
        if args.plan_index >= len(plans):
            parser.error(
                f"plan index {args.plan_index} is outside the available 0..{len(plans) - 1} range"
            )
        plans = [plans[args.plan_index]]
    elif args.max_plans is not None:
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
