#!/usr/bin/env python3
"""Run official-task experiments in isolated task shards and merge results."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import time
from typing import Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.scripts.experiment_storage import experiment_path
DEFAULT_RUN_NAME = "codex_7mode_full_r1_parallel_20260903"
DEFAULT_MODES = (
    "no_fault",
    "crash_direct",
    "clean_recovery",
    "crash_no_rollback",
    "causal",
    "temporal_checkpoint",
    "whole_branch_abort",
)
REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")
RECOVERY_CONTEXTS = ("inherit", "full", "compact")


def catalog_tasks(cache_dir: Path, suite: str = "all") -> list[str]:
    from experiments.workloads import swe_bench_suite as swe
    from experiments.workloads import terminal_bench_suite as tb

    swe_tasks = swe.load_tasks(cache_dir) if suite in {"all", "swe"} else {}
    tb_tasks = tb.load_tasks(cache_dir) if suite in {"all", "tb"} else {}
    return [
        *(f"swe:{name}" for name in swe_tasks),
        *(f"tb:{name}" for name in tb_tasks),
    ]


def partition_tasks(tasks: Sequence[str], workers: int) -> list[list[str]]:
    if workers < 1:
        raise ValueError("workers must be positive")
    return [list(tasks[index::workers]) for index in range(workers)]


def partition_work(
    tasks: Sequence[str],
    modes: Sequence[str],
    workers: int,
    *,
    mode_parallel: bool,
    paired_prefix: int = 0,
) -> list[tuple[list[str], list[str]]]:
    if mode_parallel:
        if workers > len(modes):
            raise ValueError("mode-parallel execution cannot use more workers than modes")
        if paired_prefix < 0:
            raise ValueError("paired_prefix cannot be negative")
        if paired_prefix:
            prefix = list(tasks[:paired_prefix])
            remainder = list(tasks[paired_prefix:])
            assignments = [(prefix, [mode]) for mode in modes if prefix]
            assignments.extend(
                (remainder, [mode]) for mode in modes if remainder
            )
            return assignments
        # Each mode is an isolated queue item. The supervisor starts at most
        # ``workers`` items at once and backfills a slot when a sweep finishes.
        return [(list(tasks), [mode]) for mode in modes]
    return [(shard, list(modes)) for shard in partition_tasks(tasks, workers)]


def result_shard_count(run_name: str, fallback: int) -> int:
    config_path = experiment_path("results", project_root=ROOT) / run_name / "run_config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback
    try:
        return int(config.get("result_shards", fallback))
    except (TypeError, ValueError):
        return fallback


def result_subdir(run_name: str, shard_index: int) -> str:
    return f"{run_name}/shards/shard_{shard_index:02d}"


def merged_rows(
    run_name: str,
    workers: int,
    modes: Sequence[str] | None = None,
    *,
    completed_only: bool = True,
) -> list[dict]:
    from experiments.scripts import bench_official_tasks as bench

    rows_by_key: dict[tuple[str, ...], dict] = {}
    allowed_modes = set(modes) if modes is not None else None
    for index in range(result_shard_count(run_name, workers)):
        path = (
            ROOT
            / "experiments"
            / "results"
            / result_subdir(run_name, index)
            / "official_tasks_raw.csv"
        )
        for row in bench._load_prior_rows(path):
            if completed_only and not bench._completed_attempt(row):
                continue
            if allowed_modes is not None and row.get("mode") not in allowed_modes:
                continue
            key = (
                str(row.get("suite", "")),
                str(row.get("task", "")),
                str(row.get("mode", "")),
                str(row.get("repeat", "")),
                str(row.get("harness_backend", "")),
            )
            rows_by_key[key] = row
    rows = list(rows_by_key.values())
    config_path = experiment_path("results", project_root=ROOT) / run_name / "run_config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        config = {}
    task_order = {
        task: index for index, task in enumerate(config.get("tasks", ()))
    }
    mode_order = {
        mode: index for index, mode in enumerate(config.get("modes", ()))
    }

    def row_order(row: dict) -> tuple:
        suite_task = f"{row.get('suite', '')}:{row.get('task', '')}"
        try:
            repeat = int(row.get("repeat", 0))
        except (TypeError, ValueError):
            repeat = 0
        return (
            task_order.get(suite_task, len(task_order)),
            mode_order.get(str(row.get("mode", "")), len(mode_order)),
            repeat,
            str(row.get("harness_backend", "")),
            suite_task,
        )

    return sorted(rows, key=row_order)


def merge_outputs(
    run_name: str,
    workers: int,
    modes: Sequence[str] | None = None,
) -> Path | None:
    from experiments.scripts import bench_official_tasks as bench

    rows = merged_rows(run_name, workers, modes)
    if not rows:
        return None
    summary = bench.summarize(rows)
    config_path = experiment_path("results", project_root=ROOT) / run_name / "run_config.json"
    try:
        harness = json.loads(config_path.read_text(encoding="utf-8")).get("harness", "codex")
    except (OSError, ValueError):
        harness = "codex"
    return bench.write_outputs(
        summary,
        provider=None,
        oracle=False,
        harness_backend=harness,
        result_subdir=f"{run_name}/merged",
    )


def status(
    run_name: str,
    workers: int,
    modes: Sequence[str],
    repeats: int,
    max_requests: int,
    tasks: Sequence[str] | None = None,
    suite: str = "all",
) -> dict:
    selected_tasks = (
        list(tasks)
        if tasks is not None
        else catalog_tasks(experiment_path("cache", project_root=ROOT), suite)
    )
    rows = merged_rows(run_name, workers, modes)
    attempts = merged_rows(run_name, workers, modes, completed_only=False)
    expected = len(selected_tasks) * len(modes) * repeats
    successes = sum(bool(row.get("success")) for row in rows)
    environment_errors = sum(bool(row.get("environment_error")) for row in attempts)
    return {
        "run_name": run_name,
        "workers": workers,
        "max_concurrent_requests": max_requests,
        "tasks": len(selected_tasks),
        "modes": list(modes),
        "repeats": repeats,
        "attempted": len(attempts),
        "completed": len(rows),
        "expected": expected,
        "progress_pct": round(100 * len(rows) / expected, 3) if expected else 100.0,
        "successes": successes,
        "environment_errors": environment_errors,
    }


def stop_reason_for_status(
    failed_children: Sequence[tuple[int, int]],
    current_status: dict,
) -> str:
    """Stop only for worker failure; individual cell errors remain retryable."""
    del current_status
    if failed_children:
        return f"shard failure detected: {list(failed_children)}"
    return ""


def signal_stop_reason(signum: int) -> str:
    return f"received {signal.Signals(signum).name}; stopping all shards"


def worker_command(
    *,
    tasks: Sequence[str],
    run_name: str,
    shard_index: int,
    modes: Sequence[str],
    repeats: int,
    model: str,
    reasoning_effort: str,
    harness_timeout: float,
    python: str,
    harness: str = "codex",
    max_turns: int | None = None,
    suite: str = "all",
) -> list[str]:
    command = [
        python,
        "-u",
        str(ROOT / "experiments" / "scripts" / "bench_official_tasks.py"),
        "--suite",
        suite,
        "--task-set",
        "selected",
        "--tasks",
        *tasks,
        "--modes",
        *modes,
        "--repeats",
        str(repeats),
        "--harness",
        harness,
        "--model",
        model,
        "--harness-timeout",
        str(harness_timeout),
        "--allow-result-failures",
        "--result-subdir",
        result_subdir(run_name, shard_index),
        "--python",
        python,
    ]
    if harness == "codex":
        codex = shutil.which("codex")
        if codex is None:
            raise FileNotFoundError("codex executable not found on PATH")
        harness_command = shlex.join(
            [
                codex,
                "exec",
                "--model",
                model,
                "-c",
                "features.plugins=false",
                "-c",
                f'model_reasoning_effort="{reasoning_effort}"',
                "--dangerously-bypass-approvals-and-sandbox",
                "--skip-git-repo-check",
                "--json",
            ]
        )
        command.extend(["--harness-command", harness_command])
    elif harness == "sweagent":
        command.extend(["--reasoning-effort", reasoning_effort])
        if max_turns is not None:
            command.extend(["--max-turns", str(max_turns)])
        bwrap = shutil.which("bwrap")
        if bwrap is None:
            raise FileNotFoundError("bwrap executable not found on PATH")
        command = [
            bwrap,
            "--bind", "/", "/",
            "--dev-bind", "/dev", "/dev",
            "--proc", "/proc",
            "--tmpfs", "/root",
            "--setenv", "HOME", "/root",
            "--setenv", "AGENTTX_MOTIVATION_BWRAP", "1",
            *command,
        ]
    else:
        raise ValueError(f"unsupported official-task harness: {harness}")
    return command


def worker_environment(
    base_env: dict[str, str],
    worker_modes: Sequence[str],
    clean_recovery_context: str,
    causal_recovery_context: str = "compact",
) -> dict[str, str]:
    """Apply mode-local context overrides without changing other workers."""
    env = base_env.copy()
    for name in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
    ):
        env.pop(name, None)
    env.update({"NO_PROXY": "*", "no_proxy": "*"})
    env["AGENTTX_NO_PROXY"] = "1"
    overrides = {
        "clean_recovery": clean_recovery_context,
        "causal": causal_recovery_context,
    }
    selected = [
        (mode, value)
        for mode, value in overrides.items()
        if value != "inherit" and mode in worker_modes
    ]
    if not selected:
        return env
    if len(worker_modes) != 1:
        flags = ", ".join(f"--{mode.replace('_', '-')}-context" for mode, _ in selected)
        raise ValueError(f"{flags} requires each overridden mode to run in its own worker")
    env["AGENTTX_RECOVERY_CONTEXT"] = selected[0][1]
    return env


def recovery_context_by_mode(
    modes: Sequence[str],
    base_env: dict[str, str],
    clean_recovery_context: str,
    causal_recovery_context: str = "compact",
) -> dict[str, str]:
    value = base_env.get("AGENTTX_RECOVERY_CONTEXT", "full").strip().lower()
    inherited = "compact" if value in {"1", "true", "yes", "on", "compact"} else "full"
    contexts = {}
    for mode in modes:
        if mode in {"no_fault", "crash_direct"}:
            contexts[mode] = "none"
        elif mode == "clean_recovery":
            contexts[mode] = (
                inherited
                if clean_recovery_context == "inherit"
                else clean_recovery_context
            )
        elif mode == "causal":
            contexts[mode] = (
                inherited
                if causal_recovery_context == "inherit"
                else causal_recovery_context
            )
        else:
            contexts[mode] = inherited
    return contexts


def write_run_config(
    args: argparse.Namespace,
    tasks: Sequence[str],
    env: dict[str, str],
    *,
    result_shards: int | None = None,
) -> Path:
    from experiments.scripts.experiment_provenance import source_manifest

    result_dir = experiment_path("results", project_root=ROOT) / args.run_name
    result_dir.mkdir(parents=True, exist_ok=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    ).stdout
    sources = source_manifest(ROOT)
    config = {
        "run_name": args.run_name,
        "suite": args.suite,
        "harness": args.harness,
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "modes": list(args.modes),
        "repeats": args.repeats,
        "workers": args.workers,
        "result_shards": (
            result_shards
            if result_shards is not None
            else len(args.modes) if args.mode_parallel else args.workers
        ),
        "scheduling": "mode_parallel" if args.mode_parallel else "task_sharded",
        "paired_prefix": args.paired_prefix,
        "max_concurrent_requests": args.max_requests,
        "harness_timeout_s": args.harness_timeout,
        "max_turns": args.max_turns,
        "task_count": len(tasks),
        "tasks": list(tasks),
        "expected_cells": len(tasks) * len(args.modes) * args.repeats,
        "python": str(Path(args.python).resolve()),
        "keep_eval_images": env["AGENTTX_KEEP_EVAL_IMAGES"],
        "randomize_crash": env.get("AGENTTX_RANDOMIZE_CRASH", "0") == "1",
        "fault_seed": env.get("AGENTTX_FAULT_SEED", "0"),
        "auto_localize": "causal" in args.modes,
        "fault_location_protocol": "causal_localizer_coarse_hidden_oracle_registered_truth_v1",
        "source_manifest_sha256": sources["sha256"],
        "swe_verify": env.get("AGENTTX_SWE_VERIFY", "auto"),
        "recovery_context_by_mode": recovery_context_by_mode(
            args.modes,
            env,
            args.clean_recovery_context,
            args.causal_recovery_context,
        ),
        "git_commit": commit,
        "git_dirty": bool(dirty),
        "git_status_entries": len(dirty.splitlines()),
    }
    path = result_dir / "run_config.json"
    encoded = json.dumps(config, indent=2, sort_keys=True) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") != encoded:
        previous = json.loads(path.read_text(encoding="utf-8"))
        invariant_fields = (
            "suite",
            "harness",
            "model",
            "reasoning_effort",
            "repeats",
            "paired_prefix",
            "harness_timeout_s",
            "max_turns",
            "task_count",
            "tasks",
            "python",
            "keep_eval_images",
            "randomize_crash",
            "fault_seed",
            "auto_localize",
            "fault_location_protocol",
            "source_manifest_sha256",
            "swe_verify",
        )
        if not args.resume_scale_workers:
            invariant_fields += ("workers",)
        invariant_mismatch = [
            field
            for field in invariant_fields
            if previous.get(field, "codex" if field == "harness" else "all" if field == "suite" else None)
            != config.get(field)
        ]
        if (
            "recovery_context_by_mode" in previous
            and previous["recovery_context_by_mode"] != config["recovery_context_by_mode"]
        ):
            invariant_mismatch.append("recovery_context_by_mode")
        previous_scheduling = previous.get("scheduling", "task_sharded")
        subset_resume_ok = bool(
            args.resume_mode_subset
            and set(config["modes"]).issubset(set(previous.get("modes", ())))
            and int(config["max_concurrent_requests"])
            <= int(previous.get("max_concurrent_requests", 0))
            and config["scheduling"] == previous_scheduling
            and not invariant_mismatch
        )
        scale_resume_ok = bool(
            args.resume_scale_workers
            and config["scheduling"] == previous_scheduling == "mode_parallel"
            and config["modes"] == previous.get("modes")
            and config["result_shards"] == previous.get("result_shards")
            and int(config["workers"]) >= int(previous.get("workers", 0))
            and int(config["max_concurrent_requests"])
            >= int(previous.get("max_concurrent_requests", 0))
            and not invariant_mismatch
        )
        if not (subset_resume_ok or scale_resume_ok):
            raise RuntimeError(f"run configuration changed since initial launch: {path}")
        if scale_resume_ok:
            previous["workers"] = config["workers"]
            previous["max_concurrent_requests"] = config["max_concurrent_requests"]
            path.write_text(json.dumps(previous, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path
    (result_dir / "source_manifest.json").write_text(json.dumps(sources, indent=2) + "\n", encoding="utf-8")
    path.write_text(encoded, encoding="utf-8")
    return path


def run(args: argparse.Namespace) -> int:
    from agenttx.semisolate import _default_try_bin, _probe_try_backend
    from experiments.workloads import swe_bench_suite as swe

    catalog = catalog_tasks(args.cache_dir, args.suite)
    tasks = list(args.tasks) if args.tasks else catalog
    unknown = sorted(set(tasks) - set(catalog))
    if unknown:
        raise ValueError(f"unknown selected tasks: {unknown}")
    if "causal" in args.modes and args.causal_recovery_context != "compact":
        raise ValueError("causal experiments require --causal-recovery-context compact")
    assignments = partition_work(
        tasks,
        args.modes,
        args.workers,
        mode_parallel=args.mode_parallel,
        paired_prefix=args.paired_prefix,
    )
    context_overrides = {
        "clean_recovery": args.clean_recovery_context,
        "causal": args.causal_recovery_context,
    }
    mixed_overrides = [
        mode
        for mode, value in context_overrides.items()
        if value != "inherit" and mode in args.modes and set(args.modes) != {mode}
    ]
    if mixed_overrides and not args.mode_parallel:
        raise ValueError("mode-local recovery context overrides require --mode-parallel")
    logs_dir = experiment_path("logs", project_root=ROOT) / args.run_name
    logs_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    for name in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
    ):
        env.pop(name, None)
    env.update({"NO_PROXY": "*", "no_proxy": "*"})
    env["AGENTTX_NO_PROXY"] = "1"
    env["AGENTTX_SKIP_ORPHAN_REAP"] = "1"
    env["AGENTTX_KEEP_EVAL_IMAGES"] = "1"
    if args.randomize_crash:
        env["AGENTTX_RANDOMIZE_CRASH"] = "1"
    env.setdefault("AGENTTX_DOCKER_PULL_ATTEMPTS", "3")
    slot_dir = experiment_path("results", project_root=ROOT) / args.run_name / ".request_slots"
    slot_dir.mkdir(parents=True, exist_ok=True)
    env["AGENTTX_EXTERNAL_MAX_CONCURRENCY"] = str(args.max_requests)
    env["AGENTTX_EXTERNAL_SLOT_DIR"] = str(slot_dir.resolve())
    env.setdefault("AGENTTX_SWE_VERIFY", "docker")
    if env["AGENTTX_SWE_VERIFY"] == "docker" and not swe.docker_available():
        print(
            "Docker preflight failed: worker would be unable to run official SWE verification",
            file=sys.stderr,
        )
        return 2
    try_ok, try_detail = _probe_try_backend(_default_try_bin(), ROOT)
    if not try_ok:
        print(f"try backend preflight failed: {try_detail}", file=sys.stderr)
        return 2
    env["AGENTTX_ASSUME_TRY_AVAILABLE"] = "1"
    print(f"try backend preflight passed: {try_detail}", flush=True)
    config_path = write_run_config(args, tasks, env, result_shards=len(assignments))
    print(f"frozen run configuration: {config_path}", flush=True)

    active_assignments = [
        index for index, (_tasks, modes) in enumerate(assignments)
        if not set(modes).intersection(args.skip_modes)
    ]
    children: list[tuple[int, subprocess.Popen, object]] = []
    stop_reason = ""

    def stop_children(signum=None, _frame=None) -> None:
        nonlocal stop_reason
        if signum is not None:
            stop_reason = signal_stop_reason(signum)
            print(stop_reason, file=sys.stderr, flush=True)
        for _, child, _handle in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
        deadline = time.monotonic() + 20
        for _, child, _handle in children:
            if child.poll() is None:
                try:
                    child.wait(timeout=max(0.0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)

    signal.signal(signal.SIGTERM, stop_children)
    signal.signal(signal.SIGINT, stop_children)

    print(
        json.dumps(
            status(
                args.run_name,
                args.workers,
                args.modes,
                args.repeats,
                args.max_requests,
                tasks,
            )
        ),
        flush=True,
    )
    def start_assignment(index: int) -> None:
        shard, worker_modes = assignments[index]
        log_path = logs_dir / f"shard_{index:02d}.log"
        handle = log_path.open("a", encoding="utf-8")
        command = worker_command(
            tasks=shard,
            run_name=args.run_name,
            shard_index=index,
            modes=worker_modes,
            repeats=args.repeats,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            harness_timeout=args.harness_timeout,
            python=args.python,
            harness=args.harness,
            max_turns=args.max_turns,
            suite=args.suite,
        )
        worker_env = worker_environment(
            env,
            worker_modes,
            args.clean_recovery_context,
            args.causal_recovery_context,
        )
        child = subprocess.Popen(
            command,
            cwd=ROOT,
            env=worker_env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        children.append((index, child, handle))
        print(
            f"started shard={index:02d} pid={child.pid} tasks={len(shard)} "
            f"modes={','.join(worker_modes)} log={log_path}",
            flush=True,
        )

    next_assignment = 0
    while next_assignment < len(active_assignments) and next_assignment < args.workers:
        start_assignment(active_assignments[next_assignment])
        next_assignment += 1

    last_report = 0.0
    while next_assignment < len(active_assignments) or any(
        child.poll() is None for _, child, _ in children
    ):
        now = time.monotonic()
        failed_children = [
            (index, child.returncode)
            for index, child, _ in children
            if child.poll() not in (None, 0)
        ]
        current_status = status(
            args.run_name,
            args.workers,
            args.modes,
            args.repeats,
            args.max_requests,
            tasks,
        )
        if not stop_reason:
            stop_reason = stop_reason_for_status(failed_children, current_status)
        if stop_reason:
            print(stop_reason, file=sys.stderr, flush=True)
            stop_children()
            break
        running = sum(child.poll() is None for _, child, _ in children)
        while next_assignment < len(active_assignments) and running < args.workers:
            start_assignment(active_assignments[next_assignment])
            next_assignment += 1
            running += 1
        if now - last_report >= 60:
            print(json.dumps(current_status), flush=True)
            last_report = now
        time.sleep(5)

    failures = []
    for index, child, handle in children:
        handle.close()
        if child.returncode != 0:
            failures.append({"shard": index, "returncode": child.returncode})
    merged = merge_outputs(args.run_name, args.workers, args.modes)
    final_status = status(
        args.run_name,
        args.workers,
        args.modes,
        args.repeats,
        args.max_requests,
        tasks,
    )
    final_status["failed_shards"] = failures
    final_status["stop_reason"] = stop_reason
    final_status["merged_output"] = str(merged) if merged else None
    print(json.dumps(final_status), flush=True)
    return 1 if failures or stop_reason else 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-name", default=DEFAULT_RUN_NAME)
    parser.add_argument("--suite", choices=("all", "swe", "tb"), default="all")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--max-requests", type=int, default=4)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--modes", nargs="+", default=list(DEFAULT_MODES))
    parser.add_argument(
        "--clean-recovery-context",
        choices=RECOVERY_CONTEXTS,
        default="inherit",
        help="override AGENTTX_RECOVERY_CONTEXT only for clean_recovery workers",
    )
    parser.add_argument(
        "--causal-recovery-context",
        choices=RECOVERY_CONTEXTS,
        default="compact",
        help="pin causal workers to compact context (required for causal experiments)",
    )
    parser.add_argument(
        "--mode-parallel",
        action="store_true",
        help="assign complete mode sweeps across at most --workers workers",
    )
    parser.add_argument(
        "--paired-prefix",
        type=int,
        default=0,
        help=(
            "with --mode-parallel, run this many leading tasks across every mode "
            "before queueing the remaining full sweeps"
        ),
    )
    parser.add_argument(
        "--randomize-crash",
        action="store_true",
        help="randomize the shared crash position deterministically from AGENTTX_FAULT_SEED",
    )
    parser.add_argument(
        "--resume-mode-subset",
        action="store_true",
        help="resume an existing run with fewer modes and no higher concurrency",
    )
    parser.add_argument(
        "--resume-scale-workers",
        action="store_true",
        help="resume a mode-parallel run with more workers and the same frozen workload",
    )
    parser.add_argument(
        "--skip-modes",
        nargs="*",
        default=[],
        help="leave selected mode-parallel shards paused while resuming the others",
    )
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--harness", choices=("codex", "sweagent"), default="codex")
    parser.add_argument(
        "--reasoning-effort", choices=REASONING_EFFORTS, default="max"
    )
    parser.add_argument("--harness-timeout", type=float, default=1800.0)
    parser.add_argument(
        "--max-turns", type=int, default=None,
        help="optional SWE-agent step and model-call cap; omitted means no count cap",
    )
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--cache-dir", type=Path, default=experiment_path("cache", project_root=ROOT))
    parser.add_argument(
        "--tasks",
        nargs="+",
        default=None,
        help="run only these suite-prefixed task ids (for example swe:repo__issue)",
    )
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--merge-only", action="store_true")
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be positive")
    if args.max_requests < 1:
        parser.error("--max-requests must be positive")
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    if args.max_turns is not None and args.max_turns < 1:
        parser.error("--max-turns must be positive")
    if args.paired_prefix < 0:
        parser.error("--paired-prefix cannot be negative")
    if args.paired_prefix and not args.mode_parallel:
        parser.error("--paired-prefix requires --mode-parallel")
    if args.mode_parallel and args.workers > len(args.modes):
        parser.error("--mode-parallel cannot use more workers than modes")
    if args.skip_modes and not args.mode_parallel:
        parser.error("--skip-modes requires --mode-parallel")
    if set(args.skip_modes) - set(args.modes):
        parser.error("--skip-modes must be a subset of --modes")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.status:
        print(
            json.dumps(
                status(
                    args.run_name,
                    args.workers,
                    args.modes,
                    args.repeats,
                    args.max_requests,
                    args.tasks,
                    args.suite,
                ),
                indent=2,
            )
        )
        return 0
    if args.merge_only:
        output = merge_outputs(args.run_name, args.workers, args.modes)
        print(str(output) if output else "no completed rows")
        return 0
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
