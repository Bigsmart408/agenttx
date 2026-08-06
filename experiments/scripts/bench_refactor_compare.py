#!/usr/bin/env python3
"""Compare AgentTX-LLM (intercepted) vs Aider baseline on multi-file refactor."""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from experiments.workloads.refactor_traj import REFACTOR_TASK, seed_refactor_repo

AIDER_BIN = os.environ.get(
    "AIDER_BIN",
    "/home/bfq/miniconda3/envs/agenttx/bin/aider",
)
AIDER_TIMEOUT_S = float(os.environ.get("AIDER_TIMEOUT_S", "180"))

AIDER_TASK = """You are refactoring a small Python package.

Goals:
1. Split src/calc.py into modules: src/ops_add.py, src/ops_mul.py, src/ops_sub.py (move add/mul/sub).
2. Make src/calc.py a thin re-export facade importing those functions.
3. Add src/ops_div.py with div(a,b) that raises ZeroDivisionError on b==0.
4. Update tests/test_calc.py to cover add/mul/sub/div (including zero-division).
5. Add notes/REFACTOR.md describing the new layout in <=10 lines.
6. Ensure PYTHONPATH=. python -m pytest -q would pass.

Constraints: stay inside the workspace; do not touch files outside it.
Make all edits now, then stop.
"""


def load_llm_env() -> None:
    envfile = Path.home() / ".agenttx_llm.env"
    if not envfile.exists():
        return
    for line in envfile.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'").strip('"'))


def host_has_refactor_markers(ws: Path) -> bool:
    calc = ws / "src" / "calc.py"
    text = calc.read_text(encoding="utf-8") if calc.exists() else ""
    return (ws / "src" / "ops_add.py").exists() or "ops_add" in text or (ws / "notes" / "REFACTOR.md").exists()


def run_agenttx(ws: Path, sess: Path) -> dict:
    from agenttx.agents.llm_agent import LLMToolAgent

    t0 = time.perf_counter()
    agent = LLMToolAgent(workdir=ws, session_dir=sess, max_turns=35)
    try:
        result = agent.run(REFACTOR_TASK, commit=False)
        wall = time.perf_counter() - t0
        leaked = host_has_refactor_markers(ws)
        committed = False
        commit_ok = False
        if result.ledger.get("steps"):
            up = max(s["step_id"] for s in result.ledger["steps"] if s.get("status") != "rolled_back")
            agent.harness.policy.assert_committable(agent.harness.tx.ledger, up)
            agent.harness.tx.commit(up)
            committed = True
            commit_ok = (ws / "src" / "ops_add.py").exists() and (ws / "notes" / "REFACTOR.md").exists()
        cp = subprocess.run(
            ["bash", "-c", "PYTHONPATH=. python -m pytest -q"],
            cwd=str(ws),
            capture_output=True,
            text=True,
        )
        return {
            "mode": "agenttx_llm",
            "wall_s": wall,
            "finished": result.finished,
            "tool_calls": result.tool_calls,
            "ledger_steps": len(result.ledger.get("steps", [])),
            "host_polluted_before_commit": leaked,
            "committed": committed,
            "commit_ok": commit_ok,
            "tests_rc": cp.returncode,
            "timed_out": False,
            "summary": result.summary,
            "ledger": result.ledger,
        }
    finally:
        agent.close(destroy=True)


def run_aider(ws: Path) -> dict:
    t0 = time.perf_counter()
    env = os.environ.copy()
    key = env.get("OPENAI_API_KEY", "")
    env.setdefault("DEEPSEEK_API_KEY", key)
    if env.get("OPENAI_BASE_URL") and not env.get("OPENAI_API_BASE"):
        env["OPENAI_API_BASE"] = env["OPENAI_BASE_URL"]
    # Prefer conda env python for pytest later; ensure PATH has aider's env first.
    conda_bin = str(Path(AIDER_BIN).parent)
    env["PATH"] = conda_bin + os.pathsep + env.get("PATH", "")
    model = env.get("AIDER_MODEL") or "deepseek/deepseek-chat"
    cmd = [
        AIDER_BIN,
        "--yes-always",
        "--no-git",
        "--no-stream",
        "--map-tokens",
        "0",
        "--model",
        model,
        "src/calc.py",
        "tests/test_calc.py",
        "README.md",
        "--message",
        AIDER_TASK,
    ]
    timed_out = False
    try:
        cp = subprocess.run(
            cmd,
            cwd=str(ws),
            capture_output=True,
            text=True,
            env=env,
            timeout=AIDER_TIMEOUT_S,
        )
        rc = cp.returncode
        out = cp.stdout or ""
        err = cp.stderr or ""
    except subprocess.TimeoutExpired as e:
        timed_out = True
        rc = 124
        out = (e.stdout or "") if isinstance(e.stdout, str) else ((e.stdout or b"").decode("utf-8", "replace"))
        err = (e.stderr or "") if isinstance(e.stderr, str) else ((e.stderr or b"").decode("utf-8", "replace"))
        err = (err + f"\nTIMEOUT after {AIDER_TIMEOUT_S}s").strip()
    wall = time.perf_counter() - t0
    polluted = host_has_refactor_markers(ws)
    test = subprocess.run(
        ["bash", "-c", "PYTHONPATH=. python -m pytest -q"],
        cwd=str(ws),
        capture_output=True,
        text=True,
        env=env,
    )
    return {
        "mode": "aider_baseline",
        "wall_s": wall,
        "finished": (not timed_out) and rc == 0,
        "tool_calls": None,
        "ledger_steps": None,
        "host_polluted_before_commit": polluted,
        "committed": True,
        "commit_ok": polluted,
        "tests_rc": test.returncode,
        "timed_out": timed_out,
        "summary": out[-1000:],
        "ledger": None,
        "aider_rc": rc,
        "aider_err_tail": err[-800:],
    }


def main() -> int:
    load_llm_env()
    if not os.environ.get("OPENAI_API_KEY"):
        print("No API key; abort", file=sys.stderr)
        return 1

    out_dir = ROOT / "experiments" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="agenttx-cmp-", dir="/tmp"))
    rows = []
    try:
        for mode in ("agenttx_llm", "aider_baseline"):
            ws = scratch / mode / "ws"
            ws.mkdir(parents=True)
            seed_refactor_repo(ws)
            print(f"=== running {mode} ===", flush=True)
            if mode == "agenttx_llm":
                res = run_agenttx(ws, scratch / mode / "sess")
                if res.get("ledger"):
                    (out_dir / "refactor_agenttx_ledger.json").write_text(
                        json.dumps(res["ledger"], indent=2) + "\n", encoding="utf-8"
                    )
            else:
                res = run_aider(ws)
            rows.append(res)
            print(
                json.dumps(
                    {k: v for k, v in res.items() if k not in ("ledger", "summary", "aider_err_tail")},
                    indent=2,
                ),
                flush=True,
            )
            if res.get("aider_err_tail"):
                print("aider_err_tail:", res["aider_err_tail"][:400], flush=True)
            if res.get("summary") and mode == "aider_baseline":
                print("aider_summary_tail:", res["summary"][-400:], flush=True)
    finally:
        subprocess.run(["bash", "-lc", f"chmod -R u+rwX '{scratch}' 2>/dev/null || true"], check=False)
        shutil.rmtree(scratch, ignore_errors=True)

    csv_path = out_dir / "refactor_agent_compare.csv"
    fields = [
        "mode",
        "wall_s",
        "finished",
        "tool_calls",
        "ledger_steps",
        "host_polluted_before_commit",
        "committed",
        "commit_ok",
        "tests_rc",
        "timed_out",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"wrote {csv_path}")
    md = out_dir / "refactor_agent_compare.md"
    lines = [
        "# AgentTX-LLM vs Aider (multi-file refactor)",
        "",
        "| mode | wall_s | tool_calls | polluted_before_commit | commit_ok | tests_rc | timed_out |",
        "|---|---:|---:|---|---|---:|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['mode']} | {r['wall_s']:.1f} | {r.get('tool_calls')} | "
            f"{r['host_polluted_before_commit']} | {r['commit_ok']} | {r['tests_rc']} | {r.get('timed_out')} |"
        )
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(md.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
