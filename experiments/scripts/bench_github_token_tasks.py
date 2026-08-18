#!/usr/bin/env python3
"""Measure end-to-end recovery tokens on short/medium/long GitHub tasks."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".codex_tmp"))

from agenttx.providers import provider_result_dir, resolve_provider  # noqa: E402
from experiments.scripts.bench_real_agent import _cleanup, load_llm_env  # noqa: E402
from experiments.scripts.bench_robustness import percentile  # noqa: E402
from experiments.workloads.github_task_suite import (  # noqa: E402
    TASKS,
    GitHubTask,
    all_documents_valid,
    inject_task_trajectory,
    seed_task_workspace,
)


MODES = ("causal", "temporal_checkpoint", "whole_branch_abort")


def _git(args: Sequence[str], cwd: Optional[Path] = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


def ensure_repo(task: GitHubTask, cache_root: Path) -> Path:
    """Clone a pinned public repository once and reuse it across repeats."""

    cache_root.mkdir(parents=True, exist_ok=True)
    cache = cache_root / task.name
    if not (cache / ".git").exists():
        subprocess.run(
            ["git", "clone", "--depth", "1", task.repo_url, str(cache)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
    head = _git(["rev-parse", "HEAD"], cwd=cache)
    if head != task.commit:
        subprocess.run(
            ["git", "fetch", "--depth", "1", "origin", task.commit],
            cwd=str(cache),
            check=True,
            stdout=subprocess.DEVNULL,
        )
        _git(["checkout", "--detach", task.commit], cwd=cache)
    return cache


def copy_repo(cache: Path, workdir: Path) -> None:
    # Keep the real repository's source and build metadata, but omit its full
    # documentation, test corpus, and VCS history.  This preserves GitHub
    # project context while preventing a generic ``find .`` tool call from
    # turning the token experiment into a repository-indexing benchmark.
    keep = ("README.md", "pyproject.toml", "setup.py", "setup.cfg", "src")
    for name in keep:
        source = cache / name
        if not source.exists():
            continue
        destination = workdir / name
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


def task_tests(workdir: Path) -> int:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": "."}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "recovery_tests/test_task.py",
            "-p",
            "no:cacheprovider",
        ],
        cwd=str(workdir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=180,
        check=False,
    )
    return result.returncode


def _task_snapshot(workdir: Path) -> Dict[str, bytes]:
    result: Dict[str, bytes] = {}
    for root in (
        Path(workdir) / "agenttx_task_spec",
        Path(workdir) / "agenttx_solution",
        Path(workdir) / "recovery_tests",
        Path(workdir) / "recovery_notes",
        Path(workdir) / "recovery_build",
    ):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                result[str(path.relative_to(workdir))] = path.read_bytes()
    return result


def _apply_policy(agent, mode: str, root_step: int) -> List[int]:
    if mode == "causal":
        return agent.harness.tx.rollback_causal(root_step)
    if mode == "temporal_checkpoint":
        return agent.harness.tx.rollback(root_step)
    if mode == "whole_branch_abort":
        return agent.harness.tx.rollback(0)
    raise ValueError(mode)


def _paths_touched_after(steps: Sequence, first_step: int) -> List[str]:
    paths = set()
    for step in steps[first_step:]:
        if getattr(step, "status", "") == "rolled_back":
            continue
        for effect in getattr(step, "effects", []):
            path = getattr(effect, "path", "")
            if path:
                paths.add(path.lstrip("./"))
    return sorted(paths)


def run_once(
    task: GitHubTask,
    mode: str,
    repeat: int,
    cache_root: Path,
    model: Optional[str],
    provider: Optional[str],
    max_turns: Optional[int],
    trace_backend: str,
) -> dict:
    from agenttx.agents.llm_agent import LLMToolAgent

    cache = ensure_repo(task, cache_root)
    scratch = Path(
        tempfile.mkdtemp(prefix=f"agenttx-github-{task.name}-{mode}-{repeat}-", dir="/tmp")
    )
    workdir = scratch / "repo"
    workdir.mkdir()
    copy_repo(cache, workdir)
    seed_task_workspace(workdir, task)
    host_baseline = _task_snapshot(workdir)

    agent = None
    error = ""
    injected: dict = {}
    targets: List[int] = []
    result = None
    committed = False
    finished = False
    finish_called = False
    host_leak = False
    task_rc = ""
    docs_before = {}
    started = time.perf_counter()
    recovery_started = 0.0
    try:
        agent = LLMToolAgent(
            workdir=workdir,
            session_dir=scratch / "session",
            model=model,
            provider=provider,
            max_turns=max_turns or task.max_turns,
            trace_backend=trace_backend,
        )
        # Token comparisons prioritize a stable tracing boundary over the
        # persistent-worker optimization; the latter is evaluated separately.
        if agent.harness.tx.pool is not None:
            agent.harness.tx.pool.persistent_worker = False
        injected = inject_task_trajectory(agent, task)
        if not (
            injected["tests_failed"]
            and injected["root_is_parent_of_derived"]
            and injected["root_is_parent_of_tests"]
            and not injected["independent_is_parent_of_derived"]
        ):
            raise RuntimeError(f"invalid GitHub task DAG: {injected}")
        docs_before = {
            path: task.document_content(prefix).encode("utf-8")
            for path, prefix in task.doc_specs
        }
        targets = _apply_policy(agent, mode, injected["root_step"])
        # Recovery-token accounting does not need new dependency edges after
        # the policy boundary.  Disable read tracing for the autonomous repair
        # loop so coarse rollback emulations are not confounded by tracer
        # failures while restoring a large repository snapshot.
        agent.harness.tx.trace_reads = False
        if agent.harness.tx.pool is not None:
            agent.harness.tx.pool.trace_reads = False
        recovery_first = len(agent.harness.tx.ledger.steps)
        recovery_started = time.perf_counter()
        result = agent.run(task.recovery_prompt(), commit=False)
        finished = bool(result.finished)
        finish_called = any(
            call.get("function", {}).get("name") == "finish"
            for message in result.messages
            for call in (message.get("tool_calls") or [])
        )
        # The lower workspace must remain unchanged until the common commit.
        # Speculative writes live in the try upperdir, so this snapshot should
        # still equal the pre-session lower-tree snapshot.
        host_leak = _task_snapshot(workdir) != host_baseline
        active = [
            step.step_id
            for step in agent.harness.tx.ledger.steps
            if step.status != "rolled_back"
            and step.step_id > agent.harness.tx.ledger.committed_frontier
        ]
        if active:
            agent.harness.tx.commit(max(active))
            committed = True
        task_rc = task_tests(workdir)
        independent_unchanged = all(
            (workdir / path).exists()
            and (workdir / path).read_bytes() == content
            for path, content in docs_before.items()
        )
        docs_ok = all_documents_valid(workdir, task)
        independent_retained = docs_ok
        solution_ok = (workdir / "agenttx_solution" / "solution.py").exists()
        derived_removed = not (workdir / "recovery_build" / "derived.txt").exists()
        recovery_steps = len(agent.harness.tx.ledger.steps) - recovery_first
        regenerated = _paths_touched_after(agent.harness.tx.ledger.steps, recovery_first)
        total_tokens = int(result.total_tokens)
        success = bool(
            finished
            and finish_called
            and committed
            and total_tokens > 0
            and task_rc == 0
            and docs_ok
            and solution_ok
            and derived_removed
            and independent_retained
            and not error
        )
    except Exception as exc:  # keep failed repeats in the raw output
        error = f"{type(exc).__name__}: {exc}"[:600]
        independent_retained = False
        independent_unchanged = False
        docs_ok = False
        solution_ok = False
        derived_removed = False
        recovery_steps = 0
        regenerated = []
        total_tokens = int(result.total_tokens) if result is not None else 0
        success = False
        task_rc = task_rc or "error"
    finally:
        wall_s = time.perf_counter() - started
        recovery_wall_s = time.perf_counter() - recovery_started if recovery_started else 0.0
        if agent is not None:
            agent.close(destroy=True)
        _cleanup(scratch)

    provider_name = resolve_provider(provider).name
    model_name = model or resolve_provider(provider).model
    return {
        "task": task.name,
        "scale": {"short": "short", "medium": "medium", "long": "long"}.get(task.name.split("_")[0], task.name),
        "repo": task.repo,
        "commit": task.commit,
        "issue_url": task.issue_url,
        "mode": mode,
        "repeat": repeat,
        "provider": provider_name,
        "model": model_name,
        "trace_backend": trace_backend,
        "document_lines": task.doc_lines,
        "wall_s": round(wall_s, 6),
        "recovery_wall_s": round(recovery_wall_s, 6),
        "prompt_tokens": int(result.prompt_tokens) if result is not None else 0,
        "completion_tokens": int(result.completion_tokens) if result is not None else 0,
        "total_tokens": total_tokens,
        "tool_calls": int(result.tool_calls) if result is not None else 0,
        "model_calls": sum(
            message.get("role") == "assistant"
            for message in (result.messages if result is not None else [])
        ),
        "recovery_ledger_steps": recovery_steps,
        "rollback_targets": targets,
        "regenerated_paths": regenerated,
        "independent_retained": independent_retained,
        "independent_unchanged": independent_unchanged,
        "documents_valid": docs_ok,
        "solution_present": solution_ok,
        "derived_removed": derived_removed,
        "host_leak_before_commit": host_leak,
        "tests_rc": task_rc,
        "finished": finished,
        "finish_called": finish_called,
        "committed": committed,
        "success": success,
        "error": error,
    }


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def summarize(rows: Sequence[dict]) -> dict:
    grouped: Dict[str, Dict[str, List[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        grouped[row["task"]][row["mode"]].append(row)
    task_summaries = []
    for task, modes in grouped.items():
        causal = {r["repeat"]: r for r in modes.get("causal", [])}
        for mode, mode_rows in modes.items():
            tokens = [float(r["total_tokens"]) for r in mode_rows if r["total_tokens"]]
            paired_savings = [
                causal[r["repeat"]]["total_tokens"] - r["total_tokens"]
                for r in mode_rows
                if mode != "causal" and r["repeat"] in causal
                and causal[r["repeat"]]["total_tokens"]
                and r["total_tokens"]
            ]
            task_summaries.append(
                {
                    "task": task,
                    "mode": mode,
                    "repeats": len(mode_rows),
                    "total_tokens_mean": round(_mean(tokens), 3),
                    "total_tokens_p50": round(percentile(tokens, 0.50), 3) if tokens else 0,
                    "total_tokens_p95": round(percentile(tokens, 0.95), 3) if tokens else 0,
                    "causal_minus_mode_tokens_mean": round(_mean(paired_savings), 3) if paired_savings else 0,
                    "success_rate": round(_mean(float(r["success"]) for r in mode_rows), 4),
                    "tests_pass_rate": round(_mean(float(r["tests_rc"] == 0) for r in mode_rows), 4),
                    "independent_retention_rate": round(_mean(float(r["independent_retained"]) for r in mode_rows), 4),
                    "host_leak_rate": round(_mean(float(r["host_leak_before_commit"]) for r in mode_rows), 4),
                }
            )
    return {"rows": list(rows), "task_summaries": task_summaries}


def write_outputs(summary: dict, provider: Optional[str]) -> None:
    out = provider_result_dir(ROOT, provider)
    out.mkdir(parents=True, exist_ok=True)
    rows = summary["rows"]
    fields = list(rows[0].keys()) if rows else ["task", "mode"]
    with (out / "github_token_tasks_raw.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    (out / "github_token_tasks.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# GitHub-context multi-scale token recovery",
        "",
        "The repositories are pinned public snapshots; task code lives under separate recovery directories and the benchmark charges only post-policy autonomous recovery tokens.",
        "",
        "| task | mode | repeats | total mean | p50 | p95 | causal-minus-mode | success |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in summary["task_summaries"]:
        lines.append(
            f"| {item['task']} | {item['mode']} | {item['repeats']} | "
            f"{item['total_tokens_mean']} | {item['total_tokens_p50']} | "
            f"{item['total_tokens_p95']} | {item['causal_minus_mode_tokens_mean']} | "
            f"{item['success_rate']:.2f} |"
        )
    lines += [
        "",
        "`causal-minus-mode` is paired savings relative to the causal policy; positive values mean the coarse policy used more tokens.",
        "The token count includes post-policy diagnosis, tool schemas/results, validation, and regenerated artifacts; pre-failure tokens are excluded.",
    ]
    (out / "github_token_tasks.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out / 'github_token_tasks_raw.csv'}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", nargs="+", choices=sorted(TASKS), default=sorted(TASKS))
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-turns", type=int, default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--trace-backend", choices=("strace", "bpf_persistent"), default="strace")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "experiments" / "cache" / "github")
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args(argv)
    load_llm_env()
    # The project VM exposes a large host-mounted /data image that is not part
    # of any GitHub task.  Avoid copying it into every one-shot try namespace;
    # the try backend remains unchanged for normal runs when this variable is
    # unset.
    os.environ.setdefault("TRY_SKIP_MOUNTS", "/data")
    if args.preflight_only:
        for name in args.tasks:
            path = ensure_repo(TASKS[name], args.cache_dir)
            print(f"{name}: {path}")
        profile = resolve_provider(args.provider)
        print(f"configured provider: {profile.name}; api_key={'yes' if profile.api_key else 'no'}")
        return 0
    if not resolve_provider(args.provider).api_key:
        print("missing provider API key; source ~/.agenttx_llm.env first", file=sys.stderr)
        return 2
    rows = []
    for name in args.tasks:
        task = TASKS[name]
        for repeat in range(args.repeats):
            for mode in args.modes:
                print(f"running task={name} mode={mode} repeat={repeat}", flush=True)
                rows.append(
                    run_once(
                        task,
                        mode,
                        repeat,
                        args.cache_dir,
                        args.model,
                        args.provider,
                        args.max_turns,
                        args.trace_backend,
                    )
                )
    # Keep one model-pure result file while allowing separate invocations per
    # task/mode.  The caller clears the file before a fresh v4-flash campaign;
    # subsequent invocations merge only rows from the explicitly requested
    # model, so stale deepseek-chat measurements can never be mixed in.
    out_path = provider_result_dir(ROOT, args.provider) / "github_token_tasks_raw.csv"
    model_name = args.model or resolve_provider(args.provider).model
    prior = []
    if out_path.exists():
        try:
            with out_path.open(newline="", encoding="utf-8") as handle:
                prior = [row for row in csv.DictReader(handle) if row.get("model") == model_name]
            bool_fields = {
                "independent_retained",
                "independent_unchanged",
                "documents_valid",
                "solution_present",
                "derived_removed",
                "host_leak_before_commit",
                "finished",
                "finish_called",
                "committed",
                "success",
            }
            int_fields = {
                "repeat",
                "document_lines",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "tool_calls",
                "model_calls",
                "recovery_ledger_steps",
                "tests_rc",
            }
            for row in prior:
                for field in bool_fields:
                    if field in row:
                        row[field] = str(row[field]).lower() == "true"
                for field in int_fields:
                    if field in row:
                        try:
                            row[field] = int(row[field])
                        except (TypeError, ValueError):
                            pass
        except (OSError, csv.Error):
            prior = []
    merged = {}
    for row in prior + rows:
        key = (row.get("task"), row.get("mode"), str(row.get("repeat")), row.get("model"))
        merged[key] = row
    rows = list(merged.values())
    summary = summarize(rows)
    write_outputs(summary, args.provider)
    failures = [row for row in rows if not row["success"]]
    print(json.dumps(summary["task_summaries"], indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
