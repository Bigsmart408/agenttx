#!/usr/bin/env python3
"""Measure observed workspace dependencies in fault-free agent traces."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path

IGNORED_PARTS = {
    ".git", ".venv", ".tmp", "__pycache__", ".agenttx", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".tox", "node_modules", "build", "dist",
}


def workspace_path(raw: str, root: str) -> str | None:
    path = Path(raw)
    try:
        relative = path.relative_to(root)
    except ValueError:
        return None
    if (
        not relative.parts
        or set(relative.parts) & IGNORED_PARTS
        or relative.suffix in {".pyc", ".pyo"}
    ):
        return None
    return relative.as_posix()


def descendants(root: int, children: dict[int, set[int]]) -> set[int]:
    found = {root}
    frontier = [root]
    while frontier:
        for child in children.get(frontier.pop(), ()):
            if child not in found:
                found.add(child)
                frontier.append(child)
    return found


def trace_workspace_root(data: dict) -> str:
    if data.get("workspace_root"):
        return str(data["workspace_root"])
    for step in data["ledger"]["steps"]:
        for effect in step.get("effects") or ():
            path = str(effect.get("path") or "")
            marker = "/workspace/"
            if marker in path:
                return path.split(marker, 1)[0] + "/workspace"
    raise ValueError(f"workspace root unavailable for {data.get('task')}")


def verifier_status(data: dict) -> tuple[bool | None, bool, bool]:
    verifier = data.get("verifier") or {}
    source_success = data.get("source_success")
    infrastructure_error = bool(verifier.get("infrastructure_error"))
    protocol_error = False
    if data.get("suite") == "swe_bench_lite":
        stderr = str(verifier.get("stderr") or "")
        protocol_error = (
            verifier.get("runtime") == "official_docker"
            and not verifier.get("test_patch_applied")
        )
        if (
            verifier.get("tests_rc") in {"error", "timeout", 4}
            or "not found:" in stderr
            or "_FailedTest" in stderr
        ):
            infrastructure_error = True
            source_success = None
        if protocol_error:
            source_success = None
    return source_success, infrastructure_error, protocol_error


def trace_quality(data: dict) -> str:
    steps = (data.get("ledger") or {}).get("steps") or []
    turns = (data.get("conversation") or {}).get("turns") or []
    attributed = {
        call.get("step_id")
        for turn in turns
        for call in turn.get("calls") or []
        if call.get("step_id") is not None
    }
    if (
        data.get("capture_valid") is False
        or not steps
        or not all(step.get("step_id") in attributed for step in steps)
    ):
        return "invalid"
    if data.get("source_completed") and not data.get("commit_error"):
        return "complete"
    if (
        str(data.get("source_system") or "").startswith("SWE-agent native")
        and data.get("termination_reason") == "exit_format"
        and not data.get("step_limit_reached")
        and not data.get("commit_error")
    ):
        return "complete"
    return "prefix"


def analyze_trace(data: dict) -> tuple[dict, list[dict]]:
    steps = {int(item["step_id"]): item for item in data["ledger"]["steps"]}
    turn_of: dict[int, int] = {}
    for turn_index, turn in enumerate(data["conversation"]["turns"]):
        for call in turn.get("calls") or ():
            if call.get("step_id") is not None:
                turn_of[int(call["step_id"])] = turn_index
    nodes = sorted(set(steps) & set(turn_of))
    position = {step_id: index for index, step_id in enumerate(nodes)}
    root_path = trace_workspace_root(data)
    observed_paths = {
        path
        for step_id in nodes
        for effect in steps[step_id].get("effects") or ()
        if (path := workspace_path(str(effect.get("path") or ""), root_path))
    }
    directories = {
        parent.as_posix()
        for path in observed_paths
        for parent in Path(path).parents
        if parent.as_posix() != "."
    }
    last_writer: dict[str, int] = {}
    children: dict[int, set[int]] = defaultdict(set)
    parents: dict[int, set[int]] = defaultdict(set)
    mutating: set[int] = set()
    edge_rows = []
    for step_id in nodes:
        effects = steps[step_id].get("effects") or ()
        accesses = []
        for effect in effects:
            path = workspace_path(str(effect.get("path") or ""), root_path)
            kind = str(effect.get("kind") or "")
            if path and path not in directories and kind in {"R", "N", "W", "D"}:
                accesses.append((path, kind))
        writes = {path for path, kind in accesses if kind in {"W", "D"}}
        if writes:
            mutating.add(step_id)
        for path, _kind in accesses:
            parent = last_writer.get(path)
            if parent is not None and parent != step_id and parent not in parents[step_id]:
                parents[step_id].add(parent)
                children[parent].add(step_id)
                edge_rows.append(
                    {
                        "task": data["task"],
                        "parent": parent,
                        "child": step_id,
                        "step_span": position[step_id] - position[parent],
                        "turn_span": turn_of[step_id] - turn_of[parent],
                        "path_example": path,
                    }
                )
        for path in writes:
            last_writer[path] = step_id
    observed_depth = {}
    for step_id in nodes:
        observed_depth[step_id] = 1 + max(
            (observed_depth[parent] for parent in parents[step_id]), default=0
        )
    opaque_steps = [step_id for step_id in nodes if steps[step_id].get("opaque_context")]
    # A remote search can be informed by any earlier agent observation, and
    # its result can inform any later action. This deliberately overapproximates
    # those dependencies instead of claiming unobserved context is independent.
    for barrier in opaque_steps:
        for earlier in nodes:
            if earlier >= barrier:
                break
            parents[barrier].add(earlier)
            children[earlier].add(barrier)
        for later in nodes:
            if later <= barrier:
                continue
            parents[later].add(barrier)
            children[barrier].add(later)
    conservative_depth = {}
    for step_id in nodes:
        conservative_depth[step_id] = 1 + max(
            (conservative_depth[parent] for parent in parents[step_id]), default=0
        )
    rows = []
    quality = trace_quality(data)
    for root in sorted(mutating):
        later = {step_id for step_id in nodes if step_id > root}
        if not later:
            continue
        invalid = descendants(root, children)
        valid_later = later - invalid
        affected_turns = {turn_of[step_id] for step_id in invalid}
        checkpoint = {step_id for step_id in nodes if turn_of[step_id] in affected_turns}
        temporal = {step_id for step_id in nodes if step_id >= root}
        whole_branch = set(nodes)
        span = max((turn_of[step_id] - turn_of[root] for step_id in invalid), default=0)
        rows.append(
            {
                "task": data["task"],
                "trace_quality": quality,
                "root_step": root,
                "root_turn": turn_of[root],
                "closure_steps": len(invalid),
                "closure_turn_span": span,
                "valid_later_steps": len(valid_later),
                "valid_later_mutating_steps": len(valid_later & mutating),
                "checkpoint_extra_steps": len(checkpoint - invalid),
                "temporal_extra_steps": len(temporal - invalid),
                "whole_branch_extra_steps": len(whole_branch - invalid),
                "checkpoint_extra_mutating_steps": len((checkpoint - invalid) & mutating),
                "temporal_extra_mutating_steps": len((temporal - invalid) & mutating),
                "whole_branch_extra_mutating_steps": len((whole_branch - invalid) & mutating),
                "long_chain_with_interleaved_independent_work": bool(
                    span > 0 and any(
                        root < step_id < max(invalid)
                        for step_id in valid_later & mutating
                    )
                ),
            }
        )
    source_success, verifier_infrastructure_error, verifier_protocol_error = verifier_status(data)
    summary = {
        "task": data["task"],
        "suite": data.get("suite"),
        "source_system": data.get("source_system"),
        "source_completed": data.get("source_completed", data.get("replay_completed")),
        "trace_quality": quality,
        "commit_error": bool(data.get("commit_error")),
        "termination_reason": data.get("termination_reason"),
        "source_success": source_success,
        "verifier_runtime": (data.get("verifier") or {}).get("runtime"),
        "verifier_infrastructure_error": verifier_infrastructure_error,
        "verifier_protocol_error": verifier_protocol_error,
        "steps": len(nodes),
        "turns": len(data["conversation"]["turns"]),
        "mutating_steps": len(mutating),
        "opaque_context_steps": len(opaque_steps),
        "observed_workspace_edges": len(edge_rows),
        "cross_turn_edges": sum(row["turn_span"] > 0 for row in edge_rows),
        "max_edge_step_span": max((row["step_span"] for row in edge_rows), default=0),
        "max_edge_turn_span": max((row["turn_span"] for row in edge_rows), default=0),
        "longest_observed_dependency_path": max(observed_depth.values(), default=0),
        "longest_conservative_dependency_path": max(
            conservative_depth.values(), default=0
        ),
        "candidate_roots": len(rows),
        "roots_with_temporal_waste": sum(row["temporal_extra_steps"] > 0 for row in rows),
        "roots_with_independent_mutating_work": sum(
            row["temporal_extra_mutating_steps"] > 0 for row in rows
        ),
        "roots_with_interleaved_long_chain": sum(
            row["long_chain_with_interleaved_independent_work"] for row in rows
        ),
    }
    return summary, rows


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-dir", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cohort-manifest", type=Path)
    parser.add_argument("--cohort-suite", choices=("swe", "tb"))
    args = parser.parse_args()
    if bool(args.cohort_manifest) != bool(args.cohort_suite):
        parser.error("--cohort-manifest and --cohort-suite must be supplied together")
    cohort_ids = None
    if args.cohort_manifest:
        manifest = json.loads(args.cohort_manifest.read_text(encoding="utf-8"))
        cohort_ids = {
            task_id
            for scale in manifest["phase1"][args.cohort_suite].values()
            for task_id in scale
        }
    paths = [path for directory in args.trace_dir for path in sorted(directory.glob("*.json"))]
    if not paths:
        raise SystemExit("no native traces found")
    attempts = defaultdict(list)
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        if cohort_ids is not None and data.get("task") not in cohort_ids:
            continue
        key = (data.get("source_system"), data.get("suite"), data.get("task"))
        attempts[key].append((path, data))
    paths = [path for candidates in attempts.values() for path, _ in candidates]
    summaries = []
    roots = []
    duplicates = []
    for key, candidates in attempts.items():
        first_path = candidates[0][0]
        for attempt_index, (path, data) in enumerate(candidates):
            summary, rows = analyze_trace(data)
            summary["attempt_index"] = attempt_index
            summary["trace_path"] = str(path)
            summaries.append(summary)
            for row in rows:
                row["attempt_index"] = attempt_index
                row["trace_path"] = str(path)
            roots.extend(rows)
            if attempt_index:
                duplicates.append({
                    "source_system": key[0], "suite": key[1], "task": key[2],
                    "first": str(first_path), "other": str(path),
                })
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "task_summary.csv", summaries)
    write_csv(args.output_dir / "root_opportunities.csv", roots)
    write_csv(args.output_dir / "duplicate_attempts.csv", duplicates)

    def task_count(predicate) -> int:
        return len({
            (row["source_system"], row["suite"], row["task"])
            for row in summaries if predicate(row)
        })

    report = {
        "schema": "agenttx.native_dependency_opportunity/observed-path-v2",
        "caveat": (
            "Observed workspace path dependencies only; indirect context, aliases, "
            "dynamic tool state, and unobserved effects are not certified independent. "
            "Codex web searches are conservative context barriers. "
            "Every recorded step is included regardless of test, commit, or capture "
            "status; flagged attribution and partial runs make opportunity counts "
            "descriptive, not certified savings. Counterfactual step counts are "
            "not realized replay or model tokens."
        ),
        "tasks": len(attempts),
        "cohort_tasks": len(cohort_ids) if cohort_ids is not None else None,
        "missing_cohort_tasks": sorted(
            cohort_ids - {row["task"] for row in summaries}
        ) if cohort_ids is not None else [],
        "attempt_records": len(paths),
        "duplicate_attempt_records": len(duplicates),
        "completed_tasks": task_count(lambda row: row["source_completed"] is True),
        "recorded_trace_attempts": len(summaries),
        "complete_trace_attempts": sum(row["trace_quality"] == "complete" for row in summaries),
        "observed_prefix_attempts": sum(row["trace_quality"] == "prefix" for row in summaries),
        "capture_flagged_attempts": sum(row["trace_quality"] == "invalid" for row in summaries),
        "complete_trace_tasks": task_count(lambda row: row["trace_quality"] == "complete"),
        "observed_prefix_tasks": task_count(lambda row: row["trace_quality"] == "prefix"),
        "capture_flagged_tasks": task_count(lambda row: row["trace_quality"] == "invalid"),
        "agent_format_terminated_tasks": task_count(
            lambda row: row["termination_reason"] == "exit_format"
        ),
        "partial_tasks": task_count(lambda row: row["trace_quality"] == "prefix"),
        "verified_successful_tasks": task_count(lambda row: row["source_success"] is True),
        "verified_failed_tasks": task_count(lambda row: row["source_success"] is False),
        "completed_without_verifier_result": task_count(
            lambda row: row["source_success"] is None
        ),
        "verifier_infrastructure_errors": sum(
            row["verifier_infrastructure_error"] is True for row in summaries
        ),
        "verifier_protocol_errors": sum(
            row["verifier_protocol_error"] is True for row in summaries
        ),
        "tasks_with_cross_turn_edges": task_count(lambda row: row["cross_turn_edges"] > 0),
        "tasks_with_temporal_waste": task_count(
            lambda row: row["roots_with_temporal_waste"] > 0
        ),
        "tasks_with_independent_mutating_work": task_count(
            lambda row: row["roots_with_independent_mutating_work"] > 0
        ),
        "candidate_roots": len(roots),
        "tasks_with_opaque_context": task_count(lambda row: row["opaque_context_steps"] > 0),
        "roots_with_temporal_waste": sum(row["temporal_extra_steps"] > 0 for row in roots),
        "roots_with_independent_mutating_work": sum(
            row["temporal_extra_mutating_steps"] > 0 for row in roots
        ),
        "roots_with_interleaved_long_chain": sum(
            row["long_chain_with_interleaved_independent_work"] for row in roots
        ),
        "longest_observed_dependency_path_max": max(
            (row["longest_observed_dependency_path"] for row in summaries),
            default=0,
        ),
        "max_edge_turn_span": max(
            (row["max_edge_turn_span"] for row in summaries), default=0
        ),
        "mean_temporal_extra_steps_per_root": statistics.mean(
            row["temporal_extra_steps"] for row in roots
        ) if roots else 0,
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
