#!/usr/bin/env python3
"""Run AgentTX recovery on SWE-Bench Lite and Terminal-Bench tasks.

Application workloads are official instances.  A recovery DAG is injected so
AgentTX policies (causal, temporal_checkpoint, whole_branch_abort) and Crab
Figure 1 restore baselines (chat_only, chat_fs) can be compared.  Success
requires the official verifier plus independent-document retention.

Use --oracle to apply the official gold/oracle solution after the policy
(no LLM).  Live runs use a real external DeepSeek Harness or Codex process;
there is no implicit in-process agent fallback.  Use --preflight-only to
prefetch instances and check the selected harness.
"""

from __future__ import annotations

import argparse
import csv
csv.field_size_limit(50_000_000)
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

from agenttx.agents.external import ExternalHarnessResult, create_external_harness  # noqa: E402
from agenttx.providers import load_provider_env, provider_result_dir, resolve_provider  # noqa: E402
from experiments.scripts.bench_real_agent import load_llm_env  # noqa: E402


def _mounted_paths_under(root: Path) -> list[str]:
    prefix = str(root.resolve())
    mounts: list[str] = []
    try:
        for line in Path("/proc/mounts").read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.split()
            if len(parts) >= 2 and (parts[1] == prefix or parts[1].startswith(prefix + "/")):
                mounts.append(parts[1])
    except OSError:
        pass
    mounts.sort(key=len, reverse=True)
    return mounts


def _unmount_tree(root: Path) -> None:
    for mount in _mounted_paths_under(root):
        subprocess.run(["umount", "-l", mount], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _cleanup(path: Path) -> None:
    """Drop overlay mounts then delete a task scratch directory."""
    if path is None:
        return
    path = Path(path)
    if not path.exists():
        return
    _unmount_tree(path)
    subprocess.run(
        ["bash", "-lc", f"chmod -R u+rwX '{path}' 2>/dev/null || true"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    shutil.rmtree(path, ignore_errors=True)
    if path.exists():
        subprocess.run(["rm", "-rf", str(path)], check=False)


def _reap_orphan_sandboxes(*, keep: Path | None = None) -> None:
    """Remove leftover try/AgentTX scratch dirs after a finished task."""
    keep_s = str(keep.resolve()) if keep is not None and keep.exists() else None
    patterns = (
        "tmp.*.try-*",
        "agenttx-swe-*",
        "agenttx-tb-*",
        "agenttx-sandbox-*",
        "agenttx-cmd-*",
        "agenttx-try-probe-*",
        "agenttx-cmd-*",
    )
    roots = []
    tmp = Path("/tmp")
    for pat in patterns:
        roots.extend(tmp.glob(pat))
    for root in roots:
        if not root.is_dir():
            continue
        resolved = str(root.resolve())
        if keep_s and (resolved == keep_s or resolved.startswith(keep_s + "/")):
            continue
        _cleanup(root)

from experiments.scripts.bench_robustness import percentile  # noqa: E402
from experiments.workloads import swe_bench_suite as swe  # noqa: E402
from experiments.workloads import terminal_bench_suite as tb  # noqa: E402
from experiments.workloads.recovery_inject import dag_is_valid, document_content  # noqa: E402

POLICY_MODES = ("causal", "temporal_checkpoint", "whole_branch_abort")
# Crab Figure 1 lightweight restore baselines, aligned to this inject-then-repair
# protocol.  They are not the current default; run them after the policy sweep.
CRAB_BASELINE_MODES = ("chat_only", "chat_fs")
MODES = POLICY_MODES + CRAB_BASELINE_MODES
# The application path is intentionally limited to real black-box harnesses.
# The historical in-process loop remains readable for old result files but is
# not exposed as a benchmark choice anymore.
HARNESSES = ("deepseek_harness", "codex")
DEFAULT_PYTHON = os.environ.get(
    "AGENTTX_PYTHON",
    "/home/pengpeng/miniconda3/envs/agenttx/bin/python"
    if Path("/home/pengpeng/miniconda3/envs/agenttx/bin/python").exists()
    else sys.executable,
)


def _apply_policy(agent, mode: str, root_step: int) -> List[int]:
    """Select which injected effects to keep before the live agent/oracle runs.

    AgentTX policies undo a faulty producer on a live overlay:
    - causal: drop the fault cone, keep independent documents
    - temporal_checkpoint: roll the overlay back to the fault timestamp
    - whole_branch_abort: drop the whole uncommitted overlay (session restart)

    Crab Figure 1 is crash-restore, not causal undo.  In this protocol the
    injected DAG is the crash-time sandbox, and the recovery prompt is the
    chat analog (the producer was not an LLM conversation):
    - chat_only: keep the prompt, lose filesystem and process state
    - chat_fs: keep the prompt and crash-time filesystem, lose process state
    Process state is already dropped for external harnesses (persistent_worker=False).
    """
    if mode == "causal":
        return agent.harness.tx.rollback_causal(root_step)
    if mode == "temporal_checkpoint":
        return agent.harness.tx.rollback(root_step)
    if mode == "whole_branch_abort":
        return agent.harness.tx.rollback(0)
    if mode == "chat_only":
        # Crab: conversation only.  Overlay rollback to step 0 drops crash-time
        # files the same way a stateless sandbox restart would.
        return agent.harness.tx.rollback(0)
    if mode == "chat_fs":
        # Crab: conversation + filesystem, no in-memory process restore.
        # Leave the injected overlay in place.
        return []
    raise ValueError(mode)


def _externalize_prompt(prompt: str) -> str:
    """Remove the legacy synthetic finish-tool contract from live prompts."""
    return prompt.replace(
        "6. Call `finish` with `commit=false` and a one-sentence summary when the official verifier passes.",
        "6. When the official verifier passes, return a concise final summary and stop.",
    )


def _snapshot(workdir: Path, extra: Sequence[str] = ()) -> Dict[str, bytes]:
    result: Dict[str, bytes] = {}
    roots = [
        Path(workdir) / "agenttx_task_spec",
        Path(workdir) / "recovery_notes",
        Path(workdir) / "recovery_build",
        Path(workdir) / "tbench_verify",
    ]
    files = [Path(workdir) / rel for rel in extra]
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                result[str(path.relative_to(workdir))] = path.read_bytes()
    for path in files:
        if path.exists() and path.is_file():
            result[str(path.relative_to(workdir))] = path.read_bytes()
    return result


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


class _ExternalAgent:
    """AgentTX transaction bridge for a real external agent harness.

    The external process owns the agent loop and its tools.  The small local
    object exists only so the recovery workload can seed and roll back the
    protected overlay before the external process runs.
    """

    def __init__(self, workdir: Path, session_dir: Path, trace_backend: str, adapter) -> None:
        from agenttx.harness import CodingAgentHarness

        self.harness = CodingAgentHarness(
            workdir=workdir,
            session_dir=session_dir,
            trace_backend=trace_backend,
        )
        self.adapter = adapter

    def run(self, task: str) -> ExternalHarnessResult:
        return self.adapter.run_in_transaction(self.harness, task)

    def close(self, destroy: bool = True) -> None:
        self.harness.close(destroy=destroy)


def prefetch(
    cache_root: Path,
    suites: Sequence[str],
    python: str,
    swe_tasks=None,
    tb_tasks=None,
) -> None:
    cache_root = Path(cache_root)
    swe_catalog = swe_tasks or swe.TASKS
    tb_catalog = tb_tasks or tb.TASKS
    if "swe" in suites:
        for task in swe_catalog.values():
            repo, instance = swe.ensure_repo(task, cache_root)
            swe_python = swe.ensure_python_deps(task, python, cache_root)
            print(f"swe {task.instance_id}: {repo} ftp={swe.fail_to_pass(instance)} python={swe_python}")
    if "tb" in suites:
        repo = tb.ensure_tb_repo(cache_root)
        for task in tb_catalog.values():
            src = tb.task_source(cache_root, task)
            print(f"tb {task.task_id}: {src}")
        print(f"tb repo: {repo}")
        proc = subprocess.run(
            [python, "-c", "import pandas, pyarrow"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if proc.returncode != 0:
            subprocess.run(
                [python, "-m", "pip", "install", "-q", "pandas", "pyarrow"],
                check=False,
            )


def run_once(
    *,
    suite: str,
    task_name: str,
    mode: str,
    repeat: int,
    cache_root: Path,
    model: Optional[str],
    provider: Optional[str],
    max_turns: Optional[int],
    trace_backend: str,
    python: str,
    oracle: bool,
    harness_backend: str = "deepseek_harness",
    harness_root: Optional[Path] = None,
    harness_command: Optional[str] = None,
    harness_timeout_s: float = 1800.0,
    swe_tasks=None,
    tb_tasks=None,
) -> dict:
    from agenttx.harness import CodingAgentHarness

    scratch = Path(
        tempfile.mkdtemp(prefix=f"agenttx-{suite}-{task_name}-{mode}-{repeat}-", dir="/tmp")
    )
    workdir = scratch / "repo"
    workdir.mkdir()
    agent = None
    error = ""
    injected: dict = {}
    targets: List[int] = []
    result = None
    adapter = None
    committed = False
    finished = False
    finish_called = False
    host_leak = False
    started = time.perf_counter()
    recovery_started = 0.0
    instance = None
    task = None
    extra_watch: List[str] = []
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    harness_returncode = 0
    harness_stdout = ""
    harness_stderr = ""
    tool_calls = 0
    usage_source = "none"
    model_calls = 0
    recovery_steps = 0
    regenerated: List[str] = []
    independent_unchanged = False
    docs_ok = False
    derived_removed = False
    docs_before: dict = {}
    verdict = {
        "tests_rc": "error",
        "tests_ok": False,
        "documents_valid": False,
        "derived_removed": False,
        "verifier_stdout": "",
        "verifier_stderr": "",
    }
    try:
        if suite == "swe":
            task = (swe_tasks or swe.TASKS)[task_name]
            cache, instance = swe.ensure_repo(task, cache_root)
            swe_python = swe.ensure_venv(task, python, cache_root)
            swe.copy_repo(cache, workdir)
            swe.seed_task_workspace(workdir, task, instance)
            extra_watch = [task.faulty_relpath]
            prompt = swe.task_prompt(task, instance, swe_python)
            turns = max_turns or task.max_turns
            python = swe_python
        elif suite == "tb":
            task = (tb_tasks or tb.TASKS)[task_name]
            tb.materialize(task, workdir, cache_root)
            tb.seed_task_workspace(workdir, task)
            extra_watch = [task.faulty_relpath]
            prompt = tb.task_prompt(task, python)
            turns = max_turns or task.max_turns
        else:
            raise ValueError(suite)

        if not oracle and harness_backend != "legacy":
            prompt = _externalize_prompt(prompt)

        host_baseline = _snapshot(workdir, extra_watch)
        if oracle:

            class _OracleAgent:
                def __init__(self) -> None:
                    self.harness = CodingAgentHarness(
                        workdir=workdir,
                        session_dir=scratch / "session",
                        trace_backend=trace_backend,
                    )

                def close(self, destroy: bool = True) -> None:
                    self.harness.close(destroy=destroy)

            agent = _OracleAgent()
        elif harness_backend == "legacy":
            from agenttx.agents.llm_agent import LLMToolAgent

            agent = LLMToolAgent(
                workdir=workdir,
                session_dir=scratch / "session",
                model=model,
                provider=provider,
                max_turns=turns,
                trace_backend=trace_backend,
            )
        else:
            adapter = create_external_harness(
                harness_backend,
                root=harness_root,
                model=model,
                command=harness_command,
                timeout_s=harness_timeout_s,
            )
            agent = _ExternalAgent(
                workdir=workdir,
                session_dir=scratch / "session",
                trace_backend=trace_backend,
                adapter=adapter,
            )
        if getattr(agent.harness.tx, "pool", None) is not None:
            agent.harness.tx.pool.persistent_worker = False
        if suite == "swe":
            injected = swe.inject_task_trajectory(agent, task, instance, python)
        else:
            injected = tb.inject_task_trajectory(agent, task, python)
        if not dag_is_valid(injected):
            raise RuntimeError(f"invalid recovery DAG: {injected}")
        docs_before = {
            spec.path: document_content(spec.prefix, spec.lines, task.name)
            for spec in task.docs()
        }
        targets = _apply_policy(agent, mode, injected["root_step"])
        # External harnesses must retain their real workspace effects.  The
        # old in-process loop may disable post-recovery read tracing for its
        # historical timing numbers, but that shortcut is not allowed for the
        # Crab-aligned application path.
        if harness_backend == "legacy":
            agent.harness.tx.trace_reads = False
            if agent.harness.tx.pool is not None:
                agent.harness.tx.pool.trace_reads = False
        recovery_first = len(agent.harness.tx.ledger.steps)
        recovery_started = time.perf_counter()
        if oracle:
            if suite == "swe":
                swe.apply_oracle(agent, instance)
            else:
                tb.apply_oracle(agent, task, python)
            finished = True
            finish_called = True
            total_tokens = 0
            prompt_tokens = 0
            completion_tokens = 0
            tool_calls = 0
            usage_source = "oracle"
            model_calls = 0
            messages: list = []
        else:
            if harness_backend == "legacy":
                result = agent.run(prompt, commit=False)
                finish_called = any(
                    call.get("function", {}).get("name") == "finish"
                    for message in result.messages
                    for call in (message.get("tool_calls") or [])
                )
                messages = result.messages
                model_calls = sum(
                    message.get("role") == "assistant" for message in result.messages
                )
            else:
                result = agent.run(prompt)
                # External harnesses terminate after their own final answer;
                # they do not expose AgentTX's synthetic finish tool.
                finish_called = bool(result.finished)
                messages = []
                model_calls = 0
            finished = bool(result.finished)
            total_tokens = int(result.total_tokens)
            prompt_tokens = int(result.prompt_tokens)
            completion_tokens = int(result.completion_tokens)
            tool_calls = int(result.tool_calls)
            usage_source = str(getattr(result, "usage_source", "none"))
            if harness_backend != "legacy":
                harness_returncode = int(result.returncode)
                harness_stdout = str(result.stdout)[-2000:]
                harness_stderr = str(result.stderr)[-2000:]
        host_leak = _snapshot(workdir, extra_watch) != host_baseline
        active = [
            step.step_id
            for step in agent.harness.tx.ledger.steps
            if step.status != "rolled_back"
            and step.step_id > agent.harness.tx.ledger.committed_frontier
        ]
        if active:
            agent.harness.tx.commit(max(active))
            committed = True
        if suite == "swe":
            verdict = swe.verify(workdir, task, instance, python)
        else:
            verdict = tb.verify(workdir, task, python)
        independent_unchanged = all(
            (workdir / path).exists() and (workdir / path).read_text(encoding="utf-8") == content
            for path, content in docs_before.items()
        )
        docs_ok = bool(verdict["documents_valid"])
        derived_removed = bool(verdict["derived_removed"])
        recovery_steps = len(agent.harness.tx.ledger.steps) - recovery_first
        regenerated = _paths_touched_after(agent.harness.tx.ledger.steps, recovery_first)
        success = bool(
            finished
            and finish_called
            and committed
            and verdict["tests_ok"]
            and docs_ok
            and derived_removed
            and not error
            and (oracle or harness_backend != "legacy" or total_tokens > 0)
        )
    except Exception as exc:  # keep failed repeats in the raw output
        error = f"{type(exc).__name__}: {exc}"[:800]
        independent_unchanged = False
        docs_ok = False
        derived_removed = False
        recovery_steps = 0
        regenerated = []
        total_tokens = int(result.total_tokens) if result is not None else 0
        prompt_tokens = int(result.prompt_tokens) if result is not None else 0
        completion_tokens = int(result.completion_tokens) if result is not None else 0
        tool_calls = int(result.tool_calls) if result is not None else 0
        usage_source = str(getattr(result, "usage_source", "none")) if result is not None else "none"
        harness_returncode = int(result.returncode) if result is not None else 127
        harness_stdout = str(result.stdout)[-2000:] if result is not None else ""
        harness_stderr = str(result.stderr)[-2000:] if result is not None else ""
        model_calls = 0
        success = False
        verdict = {
            "tests_rc": "error",
            "tests_ok": False,
            "documents_valid": False,
            "derived_removed": False,
            "verifier_stdout": "",
            "verifier_stderr": "",
        }
        messages = []
        if "docs_before" not in locals():
            docs_before = {}
    finally:
        wall_s = time.perf_counter() - started
        recovery_wall_s = time.perf_counter() - recovery_started if recovery_started else 0.0
        if agent is not None:
            try:
                agent.close(destroy=True)
            except Exception:
                pass
        _cleanup(scratch)
        _reap_orphan_sandboxes(keep=None)

    if oracle:
        provider_name = "oracle"
        model_name = "oracle"
    elif harness_backend == "legacy":
        provider_name = resolve_provider(provider, ROOT).name
        model_name = model or resolve_provider(provider, ROOT).model
    else:
        provider_name = adapter.name if adapter is not None else harness_backend
        model_name = adapter.model if adapter is not None else (model or "unknown")
    scale = getattr(task, "scale", "") if task is not None else ""
    repo = ""
    commit = ""
    if suite == "swe" and instance is not None:
        repo = instance.get("repo", "")
        commit = instance.get("base_commit", "")
    elif suite == "tb" and task is not None:
        repo = f"terminal-bench:{task.task_id}"
    return {
        "suite": suite,
        "task": task_name,
        "scale": scale,
        "repo": repo,
        "commit": commit,
        "mode": mode,
        "repeat": repeat,
        "oracle": oracle,
        "provider": provider_name,
        "model": model_name,
        "harness_backend": "oracle" if oracle else harness_backend,
        "execution_boundary": (
            "oracle"
            if oracle
            else ("tool" if harness_backend == "legacy" else "external_task")
        ),
        "trace_backend": trace_backend,
        "wall_s": round(wall_s, 6),
        "recovery_wall_s": round(recovery_wall_s, 6),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "tool_calls": tool_calls,
        "usage_source": usage_source,
        "model_calls": model_calls,
        "harness_returncode": harness_returncode,
        "harness_stdout": harness_stdout,
        "harness_stderr": harness_stderr,
        "recovery_ledger_steps": recovery_steps,
        "rollback_targets": targets,
        "regenerated_paths": regenerated,
        "independent_retained": docs_ok,
        "independent_unchanged": independent_unchanged,
        "documents_valid": docs_ok,
        "derived_removed": derived_removed,
        "host_leak_before_commit": host_leak,
        "tests_rc": verdict.get("tests_rc"),
        "tests_ok": verdict.get("tests_ok"),
        "finished": finished,
        "finish_called": finish_called,
        "committed": committed,
        "success": success,
        "injected": injected,
        "error": error,
        "verifier_stdout": verdict.get("verifier_stdout", ""),
        "verifier_stderr": verdict.get("verifier_stderr", ""),
    }


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def _percentile(values: Iterable[float], quantile: float) -> float:
    """Linearly interpolated percentile for token/time summaries."""
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def token_summaries(rows: Sequence[dict]) -> List[dict]:
    """Aggregate the application rows used by token-saving plots.

    The grouping keeps the official suite/task/scale identity and the exact
    harness/model.  This prevents a motivation plot from accidentally mixing
    a SWE-Bench row with a Terminal-Bench row or a cheap model with a stale
    historical run.
    """
    groups: Dict[tuple, List[dict]] = defaultdict(list)
    for row in rows:
        key = (
            row.get("suite", ""),
            row.get("task", ""),
            row.get("scale", ""),
            row.get("mode", ""),
            row.get("harness_backend", ""),
            row.get("model", ""),
        )
        groups[key].append(row)
    summaries: List[dict] = []
    for (suite, task, scale, mode, harness, model), mode_rows in sorted(groups.items()):
        tokens = [float(r.get("total_tokens", 0) or 0) for r in mode_rows]
        prompt = [float(r.get("prompt_tokens", 0) or 0) for r in mode_rows]
        completion = [float(r.get("completion_tokens", 0) or 0) for r in mode_rows]
        recovery = [float(r.get("recovery_wall_s", 0) or 0) for r in mode_rows]
        successes = [float(bool(r.get("success"))) for r in mode_rows]
        usage_sources = sorted({str(r.get("usage_source", "none")) for r in mode_rows})
        summaries.append(
            {
                "suite": suite,
                "task": task,
                "scale": scale,
                "mode": mode,
                "harness_backend": harness,
                "model": model,
                "repeats": len(mode_rows),
                "usage_sources": ",".join(usage_sources),
                "prompt_tokens_mean": round(_mean(prompt), 3),
                "completion_tokens_mean": round(_mean(completion), 3),
                "total_tokens_mean": round(_mean(tokens), 3),
                "total_tokens_p50": round(_percentile(tokens, 0.50), 3),
                "total_tokens_p95": round(_percentile(tokens, 0.95), 3),
                "total_tokens_p99": round(_percentile(tokens, 0.99), 3),
                "recovery_wall_s_p50": round(_percentile(recovery, 0.50), 6),
                "recovery_wall_s_p95": round(_percentile(recovery, 0.95), 6),
                "success_rate": round(_mean(successes), 4),
            }
        )
    return summaries


def summarize(rows: Sequence[dict]) -> dict:
    grouped: Dict[tuple, Dict[str, List[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        key = (
            row.get("suite", ""),
            row.get("task", ""),
            row.get("scale", ""),
            row.get("harness_backend", "") or ("oracle" if row.get("oracle") else "legacy"),
            row.get("model", ""),
        )
        grouped[key][row["mode"]].append(row)
    task_summaries = []
    for (suite, task, scale, harness, model), modes in grouped.items():
        causal = {r["repeat"]: r for r in modes.get("causal", [])}
        for mode, mode_rows in modes.items():
            tokens = [float(r["total_tokens"]) for r in mode_rows if r.get("total_tokens")]
            successes = [float(bool(r["success"])) for r in mode_rows]
            retain = [float(bool(r["independent_retained"])) for r in mode_rows]
            tests = [float(bool(r.get("tests_ok"))) for r in mode_rows]
            paired_savings = [
                causal[r["repeat"]]["total_tokens"] - r["total_tokens"]
                for r in mode_rows
                if mode != "causal"
                and r["repeat"] in causal
                and causal[r["repeat"]].get("total_tokens")
                and r.get("total_tokens")
            ]
            task_summaries.append(
                {
                    "suite": suite,
                    "task": task,
                    "scale": scale,
                    "harness_backend": harness,
                    "model": model,
                    "mode": mode,
                    "repeats": len(mode_rows),
                    "total_tokens_mean": round(_mean(tokens), 3),
                    "success_rate": round(_mean(successes), 4),
                    "tests_pass_rate": round(_mean(tests), 4),
                    "independent_retention_rate": round(_mean(retain), 4),
                    "causal_minus_mode_tokens_mean": round(_mean(paired_savings), 3)
                    if paired_savings
                    else 0,
                }
            )
    return {
        "rows": list(rows),
        "task_summaries": task_summaries,
        "token_summaries": token_summaries(rows),
    }


def _result_dir(provider: Optional[str], oracle: bool, harness_backend: str, result_subdir: Optional[str] = None) -> Path:
    if result_subdir:
        return ROOT / "experiments" / "results" / result_subdir
    if oracle:
        return ROOT / "experiments" / "results" / "official"
    if harness_backend != "legacy":
        return ROOT / "experiments" / "results" / harness_backend
    return provider_result_dir(ROOT, provider)


def write_outputs(
    summary: dict,
    provider: Optional[str],
    oracle: bool,
    harness_backend: str = "deepseek_harness",
    result_subdir: Optional[str] = None,
) -> Path:
    out = _result_dir(provider, oracle, harness_backend, result_subdir)
    out.mkdir(parents=True, exist_ok=True)
    rows = summary["rows"]
    fields = list(rows[0].keys()) if rows else ["suite", "task", "mode"]
    raw_name = "official_tasks_raw.csv"
    with (out / raw_name).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        for row in rows:
            dumped = dict(row)
            for key, value in list(dumped.items()):
                if isinstance(value, (dict, list)):
                    dumped[key] = json.dumps(value)
            writer.writerow(dumped)
    (out / "official_tasks.json").write_text(
        json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8"
    )
    token_rows = summary.get("token_summaries", [])
    token_fields = [
        "suite", "task", "scale", "mode", "harness_backend", "model", "repeats",
        "usage_sources",
        "prompt_tokens_mean", "completion_tokens_mean", "total_tokens_mean",
        "total_tokens_p50", "total_tokens_p95", "total_tokens_p99",
        "recovery_wall_s_p50", "recovery_wall_s_p95", "success_rate",
    ]
    with (out / "official_token_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=token_fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(token_rows)
    (out / "official_token_summary.json").write_text(
        json.dumps(token_rows, indent=2) + "\n", encoding="utf-8"
    )
    token_lines = [
        f"# Official token summary ({harness_backend})",
        "",
        "| suite | task | scale | mode | model | repeats | usage source | mean tokens | p50 | p95 | p99 | success |",
        "|---|---|---|---|---|---:|---|---:|---:|---:|---:|---:|",
    ]
    for item in token_rows:
        token_lines.append(
            f"| {item['suite']} | {item['task']} | {item['scale']} | {item['mode']} | "
            f"{item['model']} | {item['repeats']} | {item['usage_sources']} | {item['total_tokens_mean']} | "
            f"{item['total_tokens_p50']} | {item['total_tokens_p95']} | "
            f"{item['total_tokens_p99']} | {item['success_rate']:.2f} |"
        )
    (out / "official_token_summary.md").write_text("\n".join(token_lines) + "\n", encoding="utf-8")
    lines = [
        f"# Official SWE-Bench Lite + Terminal-Bench recovery ({harness_backend})",
        "",
        "| suite | task | scale | harness | model | mode | repeats | success | tests | retention | tokens mean |",
        "|---|---|---|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for item in summary["task_summaries"]:
        lines.append(
            f"| {item['suite']} | {item['task']} | {item['scale']} | "
            f"{item['harness_backend']} | {item['model']} | {item['mode']} | {item['repeats']} | "
            f"{item['success_rate']:.2f} | {item['tests_pass_rate']:.2f} | "
            f"{item['independent_retention_rate']:.2f} | {item['total_tokens_mean']} |"
        )
    (out / "official_tasks.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out / raw_name}")
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=("swe", "tb", "all"), default="all")
    parser.add_argument(
        "--task-set",
        choices=("full", "representative", "selected"),
        default="full",
        help="full catalogs (default), representative smoke catalog, or only --tasks from the full catalogs",
    )
    parser.add_argument("--tasks", nargs="+", default=None)
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=MODES,
        default=list(POLICY_MODES),
        help="recovery policies (default: AgentTX trio).  Add chat_only chat_fs for Crab Figure 1 baselines.",
    )
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-turns", type=int, default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--provider", default=None)
    parser.add_argument(
        "--harness",
        choices=HARNESSES,
        default=os.environ.get("AGENTTX_HARNESS", "deepseek_harness"),
        help="external agent harness (no implicit in-process fallback)",
    )
    parser.add_argument(
        "--harness-root",
        type=Path,
        default=None,
        help="DeepSeek Harness checkout (defaults to DEEPSEEK_HARNESS_ROOT)",
    )
    parser.add_argument(
        "--harness-command",
        default=None,
        help="override the external harness command; task is appended as one argument",
    )
    parser.add_argument("--harness-timeout", type=float, default=1800.0)
    parser.add_argument(
        "--result-subdir",
        default=None,
        help="write under experiments/results/<name> instead of the harness folder",
    )
    parser.add_argument("--trace-backend", choices=("strace", "bpf_persistent"), default="strace")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "experiments" / "cache")
    parser.add_argument("--python", default=DEFAULT_PYTHON)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--oracle", action="store_true", help="apply official gold/oracle after policy")
    args = parser.parse_args(argv)
    load_provider_env(ROOT)
    load_llm_env()
    os.environ.setdefault("TRY_SKIP_MOUNTS", "/data")

    suites = ["swe", "tb"] if args.suite == "all" else [args.suite]
    if args.task_set in {"full", "selected"}:
        swe_catalog = swe.load_tasks(args.cache_dir) if "swe" in suites else {}
        tb_catalog = tb.load_tasks(args.cache_dir) if "tb" in suites else {}
    else:
        swe_catalog = swe.TASKS
        tb_catalog = tb.TASKS
    if args.task_set == "selected":
        if not args.tasks:
            raise SystemExit("--task-set selected requires --tasks")
        requested = set(args.tasks)
        swe_catalog = {
            name: task for name, task in swe_catalog.items()
            if name in requested or f"swe:{name}" in requested
        }
        tb_catalog = {
            name: task for name, task in tb_catalog.items()
            if name in requested or f"tb:{name}" in requested
        }
    if args.preflight_only:
        prefetch(args.cache_dir, suites, args.python, swe_catalog, tb_catalog)
        if args.oracle:
            return 0
        if args.harness == "legacy":
            profile = resolve_provider(args.provider, ROOT)
            print(f"configured provider: {profile.name}; api_key={'yes' if profile.api_key else 'no'}")
            return 0 if profile.api_key else 2
        adapter = create_external_harness(
            args.harness,
            root=args.harness_root,
            model=args.model,
            command=args.harness_command,
            timeout_s=args.harness_timeout,
        )
        probe = adapter.preflight(ROOT)
        print(json.dumps(probe, indent=2, sort_keys=True))
        return 0 if probe.get("available") else 2

    selected: List[tuple[str, str]] = []
    if args.tasks:
        for name in args.tasks:
            if name in swe_catalog:
                selected.append(("swe", name))
            elif name in tb_catalog:
                selected.append(("tb", name))
            elif ":" in name:
                suite, task = name.split(":", 1)
                selected.append((suite, task))
            else:
                raise SystemExit(f"unknown task {name}")
    else:
        if "swe" in suites:
            selected.extend(("swe", name) for name in swe_catalog)
        if "tb" in suites:
            selected.extend(("tb", name) for name in tb_catalog)

    if not args.oracle:
        if args.harness == "legacy":
            if not resolve_provider(args.provider, ROOT).api_key:
                print("missing provider API key; configure .agent.env or ~/.agenttx_llm.env", file=sys.stderr)
                return 2
        else:
            adapter = create_external_harness(
                args.harness,
                root=args.harness_root,
                model=args.model,
                command=args.harness_command,
                timeout_s=args.harness_timeout,
            )
            probe = adapter.preflight(ROOT)
            if not probe.get("available"):
                print(json.dumps(probe, indent=2, sort_keys=True), file=sys.stderr)
                return 2

    prefetch(
        args.cache_dir,
        sorted({suite for suite, _ in selected}),
        args.python,
        swe_catalog,
        tb_catalog,
    )
    out_dir_early = _result_dir(args.provider, args.oracle, args.harness, args.result_subdir)
    out_path_early = out_dir_early / "official_tasks_raw.csv"

    def _load_prior_rows(path: Path) -> list:
        prior_rows = []
        if not path.exists():
            return prior_rows
        try:
            with path.open(newline="", encoding="utf-8") as handle:
                prior_rows = list(csv.DictReader(handle))
            bool_fields = {
                "oracle",
                "independent_retained",
                "independent_unchanged",
                "documents_valid",
                "derived_removed",
                "host_leak_before_commit",
                "tests_ok",
                "finished",
                "finish_called",
                "committed",
                "success",
            }
            int_fields = {
                "repeat",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "tool_calls",
                "model_calls",
                "recovery_ledger_steps",
            }
            for row in prior_rows:
                if not row.get("harness_backend"):
                    row["harness_backend"] = "oracle" if row.get("oracle") else "legacy"
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
            return []
        return prior_rows

    rows = _load_prior_rows(out_path_early)
    done_keys = {
        (
            row.get("suite"),
            row.get("task"),
            row.get("mode"),
            str(row.get("repeat")),
            row.get("harness_backend", args.harness),
        )
        for row in rows
    }
    for suite, name in selected:
        for repeat in range(args.repeats):
            for mode in args.modes:
                key = (suite, name, mode, str(repeat), args.harness if not args.oracle else "oracle")
                if key in done_keys:
                    print(
                        f"skip existing harness={args.harness} suite={suite} task={name} "
                        f"mode={mode} repeat={repeat}",
                        flush=True,
                    )
                    continue
                print(
                    f"running harness={args.harness} suite={suite} task={name} "
                    f"mode={mode} repeat={repeat} oracle={args.oracle}",
                    flush=True,
                )
                row = run_once(
                    suite=suite,
                    task_name=name,
                    mode=mode,
                    repeat=repeat,
                    cache_root=args.cache_dir,
                    model=args.model,
                    provider=args.provider,
                    max_turns=args.max_turns,
                    trace_backend=args.trace_backend,
                    python=args.python,
                    oracle=args.oracle,
                    harness_backend=args.harness,
                    harness_root=args.harness_root,
                    harness_command=args.harness_command,
                    harness_timeout_s=args.harness_timeout,
                    swe_tasks=swe_catalog,
                    tb_tasks=tb_catalog,
                )
                rows.append(row)
                done_keys.add(key)
                summary_partial = summarize(rows)
                write_outputs(summary_partial, args.provider, args.oracle, args.harness, args.result_subdir)
    out_dir = _result_dir(args.provider, args.oracle, args.harness, args.result_subdir)
    out_path = out_dir / "official_tasks_raw.csv"
    prior = []
    if out_path.exists():
        try:
            with out_path.open(newline="", encoding="utf-8") as handle:
                prior = list(csv.DictReader(handle))
            bool_fields = {
                "oracle",
                "independent_retained",
                "independent_unchanged",
                "documents_valid",
                "derived_removed",
                "host_leak_before_commit",
                "tests_ok",
                "finished",
                "finish_called",
                "committed",
                "success",
            }
            int_fields = {
                "repeat",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "tool_calls",
                "model_calls",
                "recovery_ledger_steps",
            }
            for row in prior:
                if not row.get("harness_backend"):
                    row["harness_backend"] = "oracle" if row.get("oracle") else "legacy"
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
        if not row.get("harness_backend"):
            row["harness_backend"] = "oracle" if row.get("oracle") else "legacy"
        key = (
            row.get("suite"),
            row.get("task"),
            row.get("mode"),
            str(row.get("repeat")),
            row.get("model"),
            row.get("harness_backend", "legacy"),
        )
        merged[key] = row
    rows = list(merged.values())
    summary = summarize(rows)
    write_outputs(summary, args.provider, args.oracle, args.harness, args.result_subdir)
    print(json.dumps(summary["task_summaries"], indent=2))
    failures = [row for row in rows if not row["success"]]
    # Oracle mode intentionally exercises policies that discard the injected
    # fault branch, so their application-level success predicate is expected
    # to be false.  The oracle command still produced a complete result set;
    # reserve a non-zero exit status for failed live-harness runs.
    if args.oracle:
        return 0
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
