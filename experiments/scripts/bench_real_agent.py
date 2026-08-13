#!/usr/bin/env python3
"""Run repeated real LLM coding-agent tasks through AgentTX.

The deterministic robustness benchmark isolates runtime variance.  This script
adds the decision-making layer back in by running the existing OpenAI-compatible
``LLMToolAgent`` on the same seeded refactor task for independent repeats.
"""
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
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from experiments.scripts.bench_robustness import percentile  # noqa: E402
from experiments.workloads.refactor_traj import (  # noqa: E402
    REFACTOR_TASK,
    seed_refactor_repo,
)
from agenttx.providers import configured_provider, load_provider_env, provider_names, provider_result_dir, resolve_provider  # noqa: E402


def load_llm_env() -> None:
    """Load local agent configuration without printing or persisting secrets."""
    load_provider_env()


def _cleanup(path: Path) -> None:
    subprocess.run(
        ["bash", "-lc", f"chmod -R u+rwX '{path}' 2>/dev/null || true"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    shutil.rmtree(path, ignore_errors=True)


def _tree_snapshot(workdir: Path) -> Dict[str, bytes]:
    snapshot: Dict[str, bytes] = {}
    for path in workdir.rglob("*"):
        if (
            path.is_file()
            and ".pytest_cache" not in path.parts
            and "__pycache__" not in path.parts
        ):
            snapshot[str(path.relative_to(workdir))] = path.read_bytes()
    return snapshot


def _run_host_tests(workdir: Path) -> int:
    result = subprocess.run(
        ["bash", "-c", "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python -m pytest -q"],
        cwd=str(workdir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=90,
    )
    return result.returncode


def run_once(repeat: int, model: Optional[str], max_turns: int, provider: Optional[str]) -> dict:
    """Run one real agent attempt and validate the protected commit boundary."""
    from agenttx.agents.llm_agent import LLMToolAgent

    scratch = Path(tempfile.mkdtemp(prefix=f"agenttx-real-agent-{repeat}-", dir="/tmp"))
    workdir = scratch / "ws"
    workdir.mkdir()
    seed_refactor_repo(workdir)
    baseline = _tree_snapshot(workdir)
    started = time.perf_counter()
    result = None
    error = ""
    finished = False
    tool_calls = 0
    ledger_steps = 0
    committed = False
    commit_ok = False
    host_polluted_before_commit = False
    tests_rc = ""
    agent = None
    try:
        agent = LLMToolAgent(
            workdir=workdir,
            session_dir=scratch / "session",
            model=model,
            provider=provider,
            max_turns=max_turns,
        )
        result = agent.run(REFACTOR_TASK, commit=False)
        finished = result.finished
        tool_calls = result.tool_calls
        ledger_steps = len(result.ledger.get("steps", []))
        host_polluted_before_commit = baseline != _tree_snapshot(workdir)
        active = [
            step["step_id"]
            for step in result.ledger.get("steps", [])
            if step.get("status") != "rolled_back"
        ]
        if active:
            up_to = max(active)
            agent.harness.policy.assert_committable(agent.harness.tx.ledger, up_to)
            agent.harness.tx.commit(up_to)
            committed = True
        tests_rc = _run_host_tests(workdir)
        commit_ok = (
            (workdir / "src" / "ops_add.py").exists()
            and (workdir / "src" / "ops_mul.py").exists()
            and (workdir / "src" / "ops_sub.py").exists()
            and (workdir / "src" / "ops_div.py").exists()
            and (workdir / "notes" / "REFACTOR.md").exists()
            and tests_rc == 0
        )
    except Exception as exc:  # keep a failed repeat in the aggregate statistics
        error = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        if agent is not None:
            agent.close(destroy=True)
        _cleanup(scratch)
    wall_s = time.perf_counter() - started
    return {
        "repeat": repeat,
        "provider": resolve_provider(provider).name,
        "model": model or resolve_provider(provider).model,
        "wall_s": round(wall_s, 6),
        "finished": finished,
        "tool_calls": tool_calls,
        "ledger_steps": ledger_steps,
        "host_polluted_before_commit": host_polluted_before_commit,
        "committed": committed,
        "commit_ok": commit_ok,
        "tests_rc": tests_rc,
        "error": error,
        "success": bool(finished and committed and commit_ok and not host_polluted_before_commit),
    }


def summarize(rows: Sequence[dict]) -> dict:
    walls = [float(row["wall_s"]) for row in rows]
    calls = [float(row["tool_calls"]) for row in rows if row["tool_calls"]]
    return {
        "suite": "real_agent",
        "repeats": len(rows),
        "wall_p50_s": round(percentile(walls, 0.50), 6),
        "wall_p95_s": round(percentile(walls, 0.95), 6),
        "tool_calls_p50": round(percentile(calls, 0.50), 3),
        "tool_calls_p95": round(percentile(calls, 0.95), 3),
        "finished_rate": round(sum(bool(row["finished"]) for row in rows) / max(len(rows), 1), 6),
        "success_rate": round(sum(bool(row["success"]) for row in rows) / max(len(rows), 1), 6),
        "host_leak_rate": round(sum(bool(row["host_polluted_before_commit"]) for row in rows) / max(len(rows), 1), 6),
        "tests_pass_rate": round(sum(row["tests_rc"] == 0 for row in rows) / max(len(rows), 1), 6),
        "model": rows[0].get("model", "") if rows else "",
        "provider": rows[0].get("provider", "") if rows else "",
        "rows": list(rows),
    }


def write_outputs(summary: dict, provider: Optional[str] = None) -> None:
    out = provider_result_dir(ROOT, provider)
    out.mkdir(parents=True, exist_ok=True)
    rows = summary.pop("rows")
    summary["rows"] = rows
    (out / "real_agent_robustness.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    fields = [
        "repeat",
        "provider",
        "model",
        "wall_s",
        "finished",
        "tool_calls",
        "ledger_steps",
        "host_polluted_before_commit",
        "committed",
        "commit_ok",
        "tests_rc",
        "success",
        "error",
    ]
    with (out / "real_agent_robustness.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    lines = [
        "# Real AgentTX agent robustness",
        "",
        f"Provider: `{summary['provider']}`; model: `{summary['model']}`; repeats: {summary['repeats']}; task: seeded multi-file refactor.",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| wall p50 (s) | {summary['wall_p50_s']} |",
        f"| wall p95 (s) | {summary['wall_p95_s']} |",
        f"| tool calls p50 / p95 | {summary['tool_calls_p50']} / {summary['tool_calls_p95']} |",
        f"| finished rate | {summary['finished_rate']} |",
        f"| success rate | {summary['success_rate']} |",
        f"| host leak rate before commit | {summary['host_leak_rate']} |",
        f"| tests pass rate after commit | {summary['tests_pass_rate']} |",
        "",
        "Each repeat uses a fresh workspace and session. The API key is read only from the environment and is never serialized.",
    ]
    (out / "real_agent_robustness.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out / 'real_agent_robustness.csv'}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-turns", type=int, default=35)
    parser.add_argument("--model", default=None)
    parser.add_argument("--provider", choices=provider_names(), default=None)
    args = parser.parse_args()
    if args.repeats <= 0 or args.max_turns <= 0:
        parser.error("repeats and max-turns must be positive")
    load_llm_env()
    if not configured_provider(args.provider):
        profile = resolve_provider(args.provider)
        print(f"skip: no {profile.name.upper()}_API_KEY", file=sys.stderr)
        return 0
    rows: List[dict] = []
    for repeat in range(args.repeats):
        row = run_once(repeat, args.model, args.max_turns, args.provider)
        rows.append(row)
        print(json.dumps({k: v for k, v in row.items() if k != "error"}, indent=2), flush=True)
        if row["error"]:
            print(f"error: {row['error']}", file=sys.stderr, flush=True)
    write_outputs(summarize(rows), args.provider)
    return 0 if rows and all(row["success"] for row in rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
