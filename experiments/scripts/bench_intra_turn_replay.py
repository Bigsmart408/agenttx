#!/usr/bin/env python3
"""Real-model replay for the registered Motivation microbenchmark."""

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
from experiments.motivation_strategies import (  # noqa: E402
    MOTIVATION_REPLAY_POLICIES,
    REPLAY_POLICIES,
)
from experiments.scripts.bench_external_recovery_artifacts import (  # noqa: E402
    motivation_400_cases,
)
from experiments.workloads.intra_turn_recovery_dag import (  # noqa: E402
    IntraTurnPlan,
    build_extreme_plan,
    build_intra_turn_plan,
    build_multiturn_extreme_plan,
    causal_closure,
)


class WorkloadHarness(Protocol):
    model: str

    def run(self, task: str, workdir: Path, env: dict[str, str] | None = None): ...


POLICIES = REPLAY_POLICIES
COARSE_POLICIES = frozenset(
    {"turn_checkpoint_proxy", "temporal_step", "whole_branch"}
)
COMPARISON_POLICIES = (
    "turn_checkpoint_proxy",
    "temporal_step",
    "whole_branch",
)

FAULT_NOTICE = "A crash was detected."
ARTIFACT_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])step_\d{3}\.txt(?![A-Za-z0-9_])")
STEP_RUNNER = "workload.py"
STEP_RUNNER_RE = re.compile(
    r"(?:^|[\s/])workload\.py\s+(?:repair\s+)?(\d+)(?=\s|$|[;&|'\"])"
)
DIAGNOSTIC_RE = re.compile(
    r"(?:^|[\s/])workload\.py\s+(check-turn|check-step)\s+(\d+)"
)
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
    return [*plans, build_multiturn_extreme_plan()]


def replay_cases(suite: str) -> list[tuple[str, IntraTurnPlan]]:
    """Return named cases without changing the registered workload manifests."""
    if suite == "representative":
        return [("representative", plan) for plan in representative_plans()]
    if suite == "motivation400":
        return [
            (case.suite_slice, case.plan)
            for case in motivation_400_cases()
        ]
    raise ValueError(suite)


def rollback_targets(plan: IntraTurnPlan, policy: str) -> set[int]:
    if policy in {"clean_recovery", "no_recovery", "crash_no_rollback"}:
        return set()
    if policy == "root_only":
        return set(plan.fault_roots)
    if policy == "whole_branch":
        return set(range(len(plan.steps)))
    if policy == "turn_checkpoint_proxy":
        affected_turns = {
            plan.steps[step_id].turn_index for step_id in plan.oracle_closure
        }
        return {
            step.index for step in plan.steps if step.turn_index in affected_turns
        }
    if policy == "temporal_step":
        return set(range(plan.fault_step_id, len(plan.steps)))
    if policy in {"agenttx_causal", "ideal_oracle"}:
        return set(plan.oracle_closure)
    raise ValueError(policy)


def desired(step_id: int) -> str:
    return f"artifact-{step_id:03d}-valid\n"


def desired_for_plan(plan: IntraTurnPlan, step_id: int) -> str:
    if plan.turn_count == 1:
        return desired(step_id)
    digest = hashlib.sha256(
        f"agenttx-m3-content:{plan.seed}:{step_id}".encode("utf-8")
    ).hexdigest()
    return f"effect-{digest}\n"


def injected_stale_payload(
    step_id: int,
    step_count: int,
    plan: IntraTurnPlan | None = None,
) -> str:
    """Return a plausible prior artifact without embedding an evaluator label."""
    previous = (step_id - 1) % step_count
    return desired_for_plan(plan, previous) if plan is not None else desired(previous)


def render_step_runner(plan: IntraTurnPlan) -> str:
    """Return the original workload tool retained across every rollback policy."""
    paths = json.dumps([step.relative_path for step in plan.steps])
    expected = json.dumps(
        [desired_for_plan(plan, step.index) for step in plan.steps]
    )
    turns = json.dumps([step.turn_index for step in plan.steps])
    return f'''#!/usr/bin/env python3
import sys
from pathlib import Path

PATHS = {paths}
EXPECTED = {expected}
TURNS = {turns}
STEP_COUNT = len(PATHS)

def valid(index):
    path = Path(PATHS[index])
    try:
        return path.read_text(encoding="utf-8") == EXPECTED[index]
    except (FileNotFoundError, UnicodeError):
        return False

def repair(index):
    path = Path(PATHS[index])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(EXPECTED[index], encoding="utf-8")

if len(sys.argv) == 2 and sys.argv[1].isdigit():
    index = int(sys.argv[1])
    if not 0 <= index < STEP_COUNT:
        raise SystemExit(f"index must be in [0, {{STEP_COUNT}})")
    repair(index)
elif len(sys.argv) == 3 and sys.argv[1] == "repair":
    index = int(sys.argv[2])
    if not 0 <= index < STEP_COUNT:
        raise SystemExit(f"index must be in [0, {{STEP_COUNT}})")
    repair(index)
elif len(sys.argv) == 3 and sys.argv[1] == "check-step":
    index = int(sys.argv[2])
    if not 0 <= index < STEP_COUNT:
        raise SystemExit(f"index must be in [0, {{STEP_COUNT}})")
    print(f"operation {{index}}: {{'valid' if valid(index) else 'invalid'}}")
elif len(sys.argv) == 3 and sys.argv[1] == "check-turn":
    turn = int(sys.argv[2])
    members = [index for index, value in enumerate(TURNS) if value == turn]
    if not members:
        raise SystemExit("unknown turn")
    print(f"turn {{turn}}: {{'valid' if all(valid(index) for index in members) else 'invalid'}}")
else:
    raise SystemExit(
        "usage: workload.py repair INDEX | check-turn TURN | check-step INDEX"
    )
'''


def install_step_runner(plan: IntraTurnPlan, workspace: Path) -> None:
    path = workspace / STEP_RUNNER
    path.write_text(render_step_runner(plan), encoding="utf-8")
    path.chmod(0o755)


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
        if content != desired_for_plan(plan, step.index):
            localized.add(step.index)
    return localized


def localized_fault_roots(plan: IntraTurnPlan, localized: set[int]) -> set[int]:
    """Reduce detected faulty effects to roots without consulting fault truth."""
    return {
        step_id
        for step_id in localized
        if not localized.intersection(plan.steps[step_id].parent_indices)
    }


def recovery_targets_from_localization(
    plan: IntraTurnPlan,
    policy: str,
    localized: set[int],
) -> set[int]:
    """Apply each recovery granularity to the same discovered fault set."""
    if policy in {"clean_recovery", "no_recovery", "crash_no_rollback"}:
        return set()
    if policy == "root_only":
        return set(localized_fault_roots(plan, localized))
    if policy in {"turn_checkpoint_proxy", "whole_branch"}:
        if policy == "whole_branch":
            return set(range(len(plan.steps)))
        affected_turns = {plan.steps[step_id].turn_index for step_id in localized}
        return {
            step.index for step in plan.steps if step.turn_index in affected_turns
        }
    if policy == "temporal_step":
        return set(range(min(localized), len(plan.steps))) if localized else set()
    if policy == "agenttx_causal":
        roots = localized_fault_roots(plan, localized)
        return set(causal_closure(plan.steps, roots))
    if policy == "ideal_oracle":
        return set(localized)
    raise ValueError(policy)


def prepare_recovery(
    plan: IntraTurnPlan,
    policy: str,
    workspace: Path,
) -> tuple[list[int], set[int], set[int]]:
    """Create one crash state and apply only policy-authorized knowledge.

    Coarse policies and no-rollback start from the same polluted workspace and
    must discover the affected boundary through their documented diagnostic
    protocol. Their evaluator targets are used only for replay auditing; they
    are never materialized into the workspace or prompt. AgentTX alone runs
    the workspace localizer, while the ideal arm alone reads oracle truth.
    """
    workspace.mkdir(parents=True, exist_ok=True)
    install_step_runner(plan, workspace)
    fault_injected = policy != "clean_recovery"
    injected_faults = set(plan.oracle_closure) if fault_injected else set()
    for step in plan.steps:
        path = workspace / step.relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if step.index in injected_faults:
            path.write_text(
                injected_stale_payload(step.index, len(plan.steps), plan),
                encoding="utf-8",
            )
        else:
            path.write_text(desired_for_plan(plan, step.index), encoding="utf-8")

    localized = (
        localize_artifact_faults(plan, workspace)
        if policy == "agenttx_causal"
        else set()
    )
    if policy == "ideal_oracle":
        targets = set(plan.oracle_closure)
    elif policy == "agenttx_causal":
        targets = recovery_targets_from_localization(plan, policy, localized)
    else:
        # Ground truth remains evaluator-only. The agent must determine which
        # coarse boundary applies from black-box turn/operation checks.
        targets = rollback_targets(plan, policy)
    if policy in {"root_only", "agenttx_causal", "ideal_oracle"}:
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
    oracle_fault_step_id: int | None = None,
    oracle_fault_step_ids: list[int] | None = None,
    detected_fault_step_ids: list[int] | None = None,
) -> dict:
    payload = {
        "schema": "agenttx.motivation.replay/multiturn-v1",
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
            "turn_count": plan.turn_count,
            "turn_widths": [
                sum(step.turn_index == turn for step in plan.steps)
                for turn in plan.turn_ids
            ],
            "index_start": 0,
            "index_end_exclusive": len(plan.steps),
            "path_template": "step_{index:03d}.txt",
            "content_template": "artifact-{index:03d}-valid\\n",
            "replay_unit": "one artifact per write-producing tool invocation",
        },
        "missing": [],
        "repair": [],
    }
    oracle_ids = (
        oracle_fault_step_ids
        if oracle_fault_step_ids is not None
        else [oracle_fault_step_id]
        if oracle_fault_step_id is not None
        else None
    )
    if oracle_ids is not None:
        payload["oracle_fault_points"] = [
            {
                "turn": plan.steps[step_id].turn_index,
                "operation": plan.steps[step_id].turn_step_index,
                "global_operation": step_id,
            }
            for step_id in oracle_ids
        ]
    if detected_fault_step_ids is not None:
        payload["detected_fault_points"] = [
            {
                "turn": plan.steps[step_id].turn_index,
                "operation": plan.steps[step_id].turn_step_index,
                "global_operation": step_id,
            }
            for step_id in detected_fault_step_ids
        ]
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
    if plan.turn_count > 1:
        turns = []
        for turn_id in plan.turn_ids:
            members = [step for step in plan.steps if step.turn_index == turn_id]
            turns.append(
                {
                    "turn_index": turn_id,
                    "operation_count": len(members),
                    "assistant_message": (
                        f"Execution turn {turn_id} completed its assigned operations."
                    ),
                    "tool_result": {
                        "exit_code": 0,
                        "stdout": f"turn {turn_id} completed",
                        "stderr": "",
                        "status": "completed",
                    },
                }
            )
        return json.dumps(
            {
                "schema": "agenttx.active_turn_history/v1",
                "status": "active_unrewound",
                "turns": turns,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    turns = []
    for step in plan.steps:
        turns.append(
            {
                "assistant_tool_call": {
                    "name": step.name,
                    "arguments": {"cmd": [f"./{STEP_RUNNER}", str(step.index)]},
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


def _format_fault_points(points: list[dict]) -> str:
    return ", ".join(
        f"t{point['turn']}/o{point['operation']}"
        for point in points
    )


def _balance_handoff_tokens(text: str, target: int = 72) -> str:
    """Keep policy instruction cost equal without adding semantic guidance."""
    encoding = tiktoken.get_encoding("o200k_base")
    if len(encoding.encode(text)) > target:
        # Fault-location payloads are intrinsically variable-sized. The
        # experiment records their actual token count instead of truncating
        # detected effects or changing the recovery instruction.
        return text
    for count in range(target + 1):
        suffix = "\n<!-- prompt-cost-control" + " #" * count + " -->"
        candidate = text + suffix
        if len(encoding.encode(candidate)) == target:
            return candidate
    raise ValueError("unable to balance policy handoff token count")


def policy_handoff(payload: dict, policy: str) -> str:
    if policy == "clean_recovery":
        return _balance_handoff_tokens(
            "No crash was injected. Recovery policy: clean control. Preserve the current "
            "effects and finish the original task without manufacturing a fault."
        )
    prefix = FAULT_NOTICE + " "
    if policy == "no_recovery":
        instruction = (
            "Recovery policy: no recovery. Preserve all completed effects, including the "
            "active failed execution, and do not roll back or replay completed operations."
        )
    elif policy == "crash_no_rollback":
        instruction = (
            "Recovery policy: no rollback. Check every turn and every operation in every "
            "turn, then repair only operations you independently find invalid."
        )
    elif policy == "root_only":
        instruction = (
            "Recovery policy: root only. Check every turn and operation, replay only missing "
            "failed producers, and preserve every other completed effect."
        )
    elif policy == "turn_checkpoint_proxy":
        instruction = (
            "Recovery policy: turn checkpoint. Check every turn. For each affected turn, "
            "do not check individual operations; directly repair every operation in that turn."
        )
    elif policy == "temporal_step":
        instruction = (
            "Recovery policy: temporal suffix. Check every turn and every operation in the "
            "earliest invalid turn, then directly repair the full suffix from the first invalid operation."
        )
    elif policy == "whole_branch":
        instruction = (
            "Recovery policy: whole branch. Do not diagnose the fault location; directly "
            "repair every operation in every execution turn."
        )
    elif policy == "agenttx_causal":
        points = payload.get("detected_fault_points")
        if not isinstance(points, list) or not points:
            raise ValueError("agenttx_causal requires detected fault points")
        instruction = (
            "Recovery policy: AgentTX causal. AgentTX localization found invalid "
            f"effect(s) at {_format_fault_points(points)}. Replay those operations."
        )
    elif policy == "ideal_oracle":
        points = payload.get("oracle_fault_points")
        if not isinstance(points, list) or not points:
            raise ValueError("ideal_oracle requires oracle fault points")
        instruction = (
            "Recovery policy: ideal oracle. Oracle invalid effect(s) are "
            f"{_format_fault_points(points)}. Replay those operations."
        )
    else:
        raise ValueError(policy)
    return _balance_handoff_tokens(prefix + instruction)


def make_prompt(
    payload: dict,
    *,
    policy: str,
    active_history: str = "",
    include_handoff: bool = True,
) -> str:
    step_count = payload.get("artifact_contract", {}).get("count")
    if not isinstance(step_count, int):
        raise ValueError("original task requires a step count")
    turn_count = payload.get("artifact_contract", {}).get("turn_count", 1)
    if not isinstance(turn_count, int):
        raise ValueError("original task requires a turn count")
    if turn_count > 1:
        original_task = (
            f"Original task: complete {step_count} operations across {turn_count} "
            "execution turns without redoing correct operations. "
        )
        replay_rule = (
            "Use ./workload.py check-turn TURN and ./workload.py check-step INDEX for "
            "diagnosis, and ./workload.py repair INDEX for writes. Invoke exactly one "
            "diagnostic or repair operation per tool call. Do not batch diagnostics, read "
            "opaque effect files directly, or inspect workload.py; these actions invalidate "
            "the run. "
        )
    else:
        replay_rule = (
            "Use the existing workspace step runner for write-producing work. Invoke at most "
            "one indexed step per write-producing tool call; do not batch steps in a loop, "
            "generated script, or multi-step shell command. Read-only inspection may cover "
            "multiple steps. "
        )
        original_task = (
            f"Original task: complete all {step_count} indexed operations of the existing "
            "workspace workload without redoing correct operations. "
        )
    if not include_handoff or (
        not payload.get("diagnosis_required") and turn_count == 1
    ):
        return original_task + replay_rule + "Use tools as needed, then stop."
    fault_handoff = policy_handoff(payload, policy) + " Continue the original task. "
    if policy in {"no_recovery", "crash_no_rollback"} and not active_history:
        raise ValueError(f"{policy} requires the active unrewound history")
    prompt = (
        original_task
        + fault_handoff
        + replay_rule
        + "Use tools as needed, then stop."
    )
    if active_history:
        prompt += "\n\nFixed pre-crash turn receipts:\n" + active_history
    return prompt


def audit_replay_write_events(
    events: list[dict],
    required_paths: set[str],
    *,
    plan: IntraTurnPlan | None = None,
    policy: str | None = None,
) -> dict:
    """Enforce the v8 one-artifact-per-completed-write-event protocol."""
    covered: set[str] = set()
    write_events = 0
    completed_tool_events = 0
    completed_commands: list[str] = []
    violations: list[dict] = []
    diagnostic_violations: list[dict] = []
    checked_turns: set[int] = set()
    checked_steps: set[int] = set()
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
            raw_command = item.get("command") or ""
            command = (
                " ".join(str(argument) for argument in raw_command)
                if isinstance(raw_command, list)
                else str(raw_command)
            )
            completed_commands.append(command)
            if SHELL_WRITE_RE.search(command):
                touched.update(ARTIFACT_PATH_RE.findall(command))
            runner_steps = {
                int(match) for match in STEP_RUNNER_RE.findall(command)
            }
            for step_id in runner_steps:
                relative_path = (
                    plan.steps[step_id].relative_path
                    if plan is not None and 0 <= step_id < len(plan.steps)
                    else f"step_{step_id:03d}.txt"
                )
                touched.add(relative_path)
            if plan is not None and plan.turn_count > 1:
                diagnostics = DIAGNOSTIC_RE.findall(command)
                if len(diagnostics) > 1 or (
                    diagnostics
                    and re.search(r"\b(?:for|while|xargs|seq)\b", command)
                ):
                    diagnostic_violations.append(
                        {"item_id": item.get("id"), "reason": "batched_diagnostic"}
                    )
                for kind, raw_index in diagnostics:
                    value = int(raw_index)
                    if kind == "check-turn":
                        checked_turns.add(value)
                    elif 0 <= value < len(plan.steps):
                        checked_steps.add(value)
                runner_inspection = (
                    STEP_RUNNER in command
                    and not diagnostics
                    and not runner_steps
                    and re.search(r"\b(?:cat|sed|head|tail|less|more|rg|grep|strings)\b", command)
                )
                direct_effect_read = (
                    "effects/" in command
                    and re.search(r"\b(?:cat|sed|head|tail|rg|grep|find|python|perl)\b", command)
                )
                if runner_inspection or direct_effect_read:
                    diagnostic_violations.append(
                        {"item_id": item.get("id"), "reason": "opaque_state_bypass"}
                    )
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
        elif item_type in {"mcp_tool_call", "dynamic_tool_call"}:
            completed_tool_events += 1
        if not touched:
            continue
        write_events += 1
        unauthorized = touched - required_paths
        if unauthorized:
            violations.append(
                {
                    "item_id": item.get("id"),
                    "reason": "unauthorized_replay",
                    "paths": sorted(unauthorized),
                }
            )
        if len(touched) > 1:
            violations.append(
                {"item_id": item.get("id"), "paths": sorted(touched)}
            )
        covered.update(touched & required_paths)
    missing = required_paths - covered
    available = bool(events) or not required_paths
    diagnostic_required_turns: set[int] = set()
    diagnostic_required_steps: set[int] = set()
    if plan is not None and plan.turn_count > 1:
        if policy in {
            "crash_no_rollback",
            "root_only",
            "turn_checkpoint_proxy",
            "temporal_step",
        }:
            diagnostic_required_turns = set(plan.turn_ids)
        affected_steps = {
            step.index for step in plan.steps if step.relative_path in required_paths
        }
        affected_turns = {
            plan.steps[step_id].turn_index for step_id in affected_steps
        }
        if policy in {"crash_no_rollback", "root_only"}:
            # No rollback has no recovery boundary or localization assistance.
            # A turn-level failure signal must not silently narrow its search
            # to the affected turns; it must establish each operation itself.
            diagnostic_required_steps = {step.index for step in plan.steps}
        elif policy == "temporal_step" and affected_turns:
            earliest_turn = min(affected_turns)
            diagnostic_required_steps = {
                step.index for step in plan.steps if step.turn_index == earliest_turn
            }
    missing_turn_checks = diagnostic_required_turns - checked_turns
    missing_step_checks = diagnostic_required_steps - checked_steps
    # A localized policy may conservatively confirm the controller-provided
    # location.  Those read-only checks cost tokens but do not invalidate an
    # otherwise exact repair.  Blind/coarse policies still have strict search
    # obligations, including strategy-specific forbidden diagnostics.
    diagnostics_are_advisory = policy in {
        "clean_recovery",
        "agenttx_causal",
        "ideal_oracle",
    }
    unexpected_turn_checks = (
        set() if diagnostics_are_advisory else checked_turns - diagnostic_required_turns
    )
    unexpected_step_checks = (
        set() if diagnostics_are_advisory else checked_steps - diagnostic_required_steps
    )
    if unexpected_turn_checks:
        diagnostic_violations.append(
            {
                "reason": "unexpected_turn_diagnostics",
                "turns": sorted(unexpected_turn_checks),
            }
        )
    if unexpected_step_checks:
        diagnostic_violations.append(
            {
                "reason": "unexpected_step_diagnostics",
                "steps": sorted(unexpected_step_checks),
            }
        )
    diagnostic_followed = not (
        diagnostic_violations or missing_turn_checks or missing_step_checks
    )
    return {
        "available": available,
        "followed": bool(
            available and not missing and not violations and diagnostic_followed
        ),
        "write_events": write_events,
        "completed_tool_events": completed_tool_events,
        "completed_commands": completed_commands,
        "covered_paths": sorted(covered),
        "missing_paths": sorted(missing),
        "violations": violations,
        "diagnostic_protocol_followed": diagnostic_followed,
        "diagnostic_checked_turns": sorted(checked_turns),
        "diagnostic_checked_steps": sorted(checked_steps),
        "diagnostic_missing_turns": sorted(missing_turn_checks),
        "diagnostic_missing_steps": sorted(missing_step_checks),
        "diagnostic_violations": diagnostic_violations,
    }


def verify(plan: IntraTurnPlan, workspace: Path) -> tuple[bool, list[str]]:
    errors = []
    for step in plan.steps:
        path = workspace / step.relative_path
        if not path.is_file():
            errors.append(f"missing:{step.relative_path}")
        elif path.read_text(encoding="utf-8") != desired_for_plan(plan, step.index):
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


def terminal_keys(
    path: Path, *, rerun_completed: bool = False
) -> set[tuple[str, str, int]]:
    """Return cells that resume must not run again.

    A coarse-policy timeout is an experimental outcome, not an infrastructure
    retry. Causal failures remain retryable because localization is the system
    under test and may be rerun under the fixed budget.
    """
    if not path.exists():
        return set()
    terminal: set[tuple[str, str, int]] = set()
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            key = (row["trace_id"], row["policy"], int(row["repeat"]))
            completed = row.get("completed") == "True"
            timed_out = row.get("harness_returncode") == "124"
            if (completed and not rerun_completed) or (
                timed_out and row.get("policy") in COARSE_POLICIES
            ):
                terminal.add(key)
    return terminal


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
    *,
    suite_slice: str = "representative",
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
            oracle_fault_step_ids=(
                list(plan.oracle_closure) if policy == "ideal_oracle" else None
            ),
            detected_fault_step_ids=(
                sorted(localized)
                if policy == "agenttx_causal"
                else None
            ),
        )
        cell_id = f"{plan.trace_id}__{policy}__r{repeat}"
        active_history = (
            render_unrewound_history(plan)
            if policy in {"no_recovery", "crash_no_rollback"}
            or plan.turn_count > 1
            else ""
        )
        prompt = make_prompt(
            payload,
            policy=policy,
            active_history=active_history,
        )
        run_env = {
            "AGENTTX_CELL_ID": cell_id,
            "AGENTTX_DIAGNOSIS_REQUIRED": "1" if diagnosis_required else "0",
        }
        if (
            plan.turn_count > 1
            and getattr(harness, "name", "") == "sweagent_native"
        ):
            base_payload = dict(payload)
            base_payload["diagnosis_required"] = False
            run_env.update(
                {
                    "AGENTTX_NATIVE_ACTIVE_HISTORY": active_history,
                    "AGENTTX_NATIVE_BASE_TASK": make_prompt(
                        base_payload,
                        policy="clean_recovery",
                        include_handoff=False,
                    ),
                    "AGENTTX_NATIVE_CRASH_NOTICE": (
                        policy_handoff(payload, policy)
                        + " Continue the original task."
                    ),
                }
            )
        result = harness.run(
            prompt,
            workspace,
            env=run_env,
        )
        passed, errors = verify(plan, workspace)
        useful = targets & set(plan.valid_ids)
        weights = call_token_weights(plan)
        replay_steps = (
            set(plan.oracle_closure)
            if policy == "crash_no_rollback"
            else targets
        )
        replay_paths = {plan.steps[index].relative_path for index in replay_steps}
        replay_units = len(replay_paths)
        write_audit = audit_replay_write_events(
            list(getattr(result, "events", []) or []),
            replay_paths,
            plan=plan,
            policy=policy,
        )
        replay_protocol_followed = write_audit["followed"]
        row = {
            "suite_slice": suite_slice,
            "trace_id": plan.trace_id,
            "fault_injected": fault_injected,
            "fault_step_id": plan.fault_step_id if fault_injected else "",
            "fault_step_ids": (
                json.dumps(list(plan.fault_roots)) if fault_injected else ""
            ),
            "seed": plan.seed,
            "manifest_hash": plan.manifest_hash,
            "replay_manifest_hash": payload["manifest_hash"],
            "policy": policy,
            "recovery_context_variant": (
                "fixed_multiturn_receipts"
                if plan.turn_count > 1
                else "full_unrewound"
                if policy in {"no_recovery", "crash_no_rollback"}
                else "compact"
            ),
            "active_history_chars": len(active_history),
            "active_history_tokens": len(
                tiktoken.get_encoding("o200k_base").encode(active_history)
            ),
            "fault_notice_hash": hashlib.sha256(FAULT_NOTICE.encode("utf-8")).hexdigest(),
            "agent_prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "policy_handoff_tokens": (
                len(
                    tiktoken.get_encoding("o200k_base").encode(
                        policy_handoff(payload, policy)
                    )
                )
                if fault_injected or plan.turn_count > 1
                else 0
            ),
            "repeat": repeat,
            "loop_width": len(plan.steps),
            "turn_count": plan.turn_count,
            "fault_root_count": len(plan.fault_roots) if fault_injected else 0,
            "fault_position": plan.requested_fault_position if fault_injected else "",
            "closure_ratio": plan.requested_closure_ratio if fault_injected else "",
            "shape": plan.shape,
            "interleaving": plan.interleaving,
            "missing_artifacts": len(missing),
            "repair_artifacts": 0,
            "diagnosis_required": diagnosis_required,
            "fault_location_disclosed": policy in {"agenttx_causal", "ideal_oracle"},
            "fault_location_source": (
                "agenttx_localizer"
                if policy == "agenttx_causal"
                else "oracle"
                if policy == "ideal_oracle"
                else "hidden"
            ),
            "oracle_fault_step_disclosed": policy == "ideal_oracle",
            "detected_fault_step_disclosed": policy == "agenttx_causal",
            "repair_hint_provided": False,
            "fault_payload_encoding": (
                "opaque_multiturn_effect_v1"
                if plan.turn_count > 1
                else "opaque_stale_artifact_v1"
            ),
            "active_history_projection": (
                "fixed_turn_receipts_v1"
                if plan.turn_count > 1
                else "accepted_tool_success_v2"
                if policy in {"no_recovery", "crash_no_rollback"}
                else "compact_invalid_history_removed"
            ),
            "active_history_transport": (
                "native_sweagent_messages_v1"
                if active_history
                and getattr(harness, "name", "") == "sweagent_native"
                else "black_box_prompt_projection_v2"
                if active_history
                else "none"
            ),
            "localization_source": (
                "artifact_contract_scan"
                if policy == "agenttx_causal"
                else "oracle"
                if policy == "ideal_oracle"
                else "not_run"
            ),
            "localized_fault_steps": json.dumps(sorted(localized)),
            "localization_matches_injected_faults": (
                localized == set(plan.oracle_closure)
                if policy == "agenttx_causal"
                else ""
            ),
            "recovery_targets": json.dumps(sorted(targets)),
            "replay_units_required": replay_units,
            "replay_protocol_followed": replay_protocol_followed,
            "replay_write_audit_available": write_audit["available"],
            "replay_write_events": write_audit["write_events"],
            "replay_completed_commands": json.dumps(
                write_audit["completed_commands"], sort_keys=True
            ),
            "replay_write_covered_paths": json.dumps(write_audit["covered_paths"]),
            "replay_write_missing_paths": json.dumps(write_audit["missing_paths"]),
            "replay_write_violations": json.dumps(write_audit["violations"]),
            "diagnostic_protocol_followed": write_audit[
                "diagnostic_protocol_followed"
            ],
            "diagnostic_checked_turns": json.dumps(
                write_audit["diagnostic_checked_turns"]
            ),
            "diagnostic_checked_steps": json.dumps(
                write_audit["diagnostic_checked_steps"]
            ),
            "diagnostic_missing_turns": json.dumps(
                write_audit["diagnostic_missing_turns"]
            ),
            "diagnostic_missing_steps": json.dumps(
                write_audit["diagnostic_missing_steps"]
            ),
            "diagnostic_violations": json.dumps(
                write_audit["diagnostic_violations"]
            ),
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
    for row in rows:
        latest[(row["trace_id"], row["policy"], int(row["repeat"]))] = row
    active_rows = [
        row
        for row in rows
        if row.get("agent_prompt_hash")
        == latest[(row["trace_id"], row["policy"], int(row["repeat"]))].get(
            "agent_prompt_hash"
        )
    ]
    attempts: dict[str, int] = {}
    for row in active_rows:
        policy = row["policy"]
        attempts[policy] = attempts.get(policy, 0) + 1
    groups: dict[str, list[dict]] = {}
    for row in latest.values():
        groups.setdefault(row["policy"], []).append(row)
    attempt_groups: dict[str, list[dict]] = {}
    for row in active_rows:
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
        "All replays use the same original-task contract and crash notice. `ideal_oracle` receives the injected fault step directly; `agenttx_causal` receives only roots produced by AgentTX localization; every other fault policy receives no location. No policy receives affected paths, closure, retained count, or path-specific repair actions. Each write-producing tool invocation may recover at most one artifact.",
        "AgentTX localization scans the crash state without consulting injected fault truth. Oracle truth is used by `ideal_oracle` and by the verifier; the remaining policies do not receive it in their agent prompt.",
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
    parser.add_argument(
        "--suite",
        choices=("representative", "motivation400"),
        default="motivation400",
        help="Use the exact 400 Motivation manifests or the legacy smoke plans.",
    )
    parser.add_argument("--harness", choices=("codex", "sweagent"), default="codex")
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Extra execution repeats; motivation400 already contains ten seeded cases per point.",
    )
    parser.add_argument(
        "--policies",
        nargs="+",
        choices=POLICIES,
        default=list(MOTIVATION_REPLAY_POLICIES),
    )
    parser.add_argument(
        "--rerun-completed",
        action="store_true",
        help="Append fresh attempts even when the selected cell already has a completed row.",
    )
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
        help="Run one zero-based entry from the selected deterministic suite.",
    )
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--num-shards", type=int, default=1)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--reasoning-effort", default="max")
    parser.add_argument("--timeout", type=float, default=2400)
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
    # The 128-operation extreme plan needs one tool/model turn per replayed
    # operation, plus diagnosis and submission.  A limit below that makes the
    # coarse policies structurally impossible rather than merely expensive.
    parser.add_argument("--max-steps", type=int, default=192)
    parser.add_argument("--max-model-calls", type=int, default=192)
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
        "schema": "agenttx.motivation.replay_config/multiturn-v2",
        "suite": args.suite,
        "model": model,
        "repeats": args.repeats,
        "policies": args.policies,
        "max_plans": args.max_plans,
        "plan_index": args.plan_index,
        "prompt_projection": "equal_token_multiturn_policy_handoff_v1",
        "fault_payload_encoding": "opaque_multiturn_effect_v1",
        "fault_location_input": (
            "oracle_invalid_effects_for_ideal_detected_invalid_effects_for_agenttx_"
            "hidden_for_others"
        ),
        "recovery_localization": "agenttx_workspace_scanner_or_policy_search",
        "replay_protocol": "one_artifact_per_completed_write_event_strict_audit",
        "write_event_audit": "codex_jsonl_or_sweagent_native_trajectory",
        "crash_no_rollback_context": "fixed_multiturn_receipt_history_v1",
        "no_rollback_history_transport": (
            "native_sweagent_messages_v1"
            if args.harness == "sweagent"
            else "black_box_prompt_projection_v2"
        ),
        "fault_history_transport": (
            "native_sweagent_messages_v1"
            if args.harness == "sweagent"
            else "black_box_prompt_projection_v2"
        ),
        "causal_recovery_context": "fixed_multiturn_receipt_history_v1",
        "fault_policy_prompt_tokens": 72,
        "multiturn_history": "fixed_turn_receipts_v1",
        "diagnostic_protocol": "one_turn_or_operation_check_per_tool_call_v1",
        "fault_handoff": "detected_invalid_effect_points_v1",
        "prompt_cost_control": "minimum_72_tokens_variable_location_payload_reported_v2",
        **harness_config,
    }
    if raw_path.exists() and raw_path.stat().st_size:
        if not config_path.exists():
            raise RuntimeError("refusing to resume M3 rows without their config")
        prior_config = json.loads(config_path.read_text(encoding="utf-8"))
        for key in (
            "schema",
            "suite",
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
            "no_rollback_history_transport",
            "fault_history_transport",
            "causal_recovery_context",
            "fault_policy_prompt_tokens",
            "multiturn_history",
            "diagnostic_protocol",
            "fault_handoff",
            "prompt_cost_control",
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
    existing = terminal_keys(raw_path, rerun_completed=args.rerun_completed)
    cases = replay_cases(args.suite)
    if args.plan_index is not None:
        if args.plan_index >= len(cases):
            parser.error(
                f"plan index {args.plan_index} is outside the available 0..{len(cases) - 1} range"
            )
        cases = [cases[args.plan_index]]
    elif args.max_plans is not None:
        cases = cases[: args.max_plans]
    all_cells = [
        (suite_slice, plan, policy, repeat)
        for suite_slice, plan in cases
        for policy in args.policies
        for repeat in range(args.repeats)
    ]
    cells = [
        cell
        for index, cell in enumerate(all_cells)
        if index % args.num_shards == args.shard_index
    ]
    for index, (suite_slice, plan, policy, repeat) in enumerate(cells, 1):
        key = (plan.trace_id, policy, repeat)
        if key in existing:
            continue
        print(f"m3 {index}/{len(cells)} {plan.trace_id} {policy} r{repeat}", flush=True)
        row = run_cell(
            plan,
            policy,
            repeat,
            harness,
            suite_slice=suite_slice,
        )
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
