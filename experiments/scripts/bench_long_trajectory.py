#!/usr/bin/env python3
"""Step 4: long coding-agent trajectory under AgentTX vs baselines."""

from __future__ import annotations

import csv
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agenttx.harness import CodingAgentHarness  # noqa: E402
from agenttx.policy import CommitPolicy  # noqa: E402
from experiments.workloads.coding_traj import build_coding_trajectory, seed_repo  # noqa: E402


def try_bin() -> Path:
    return ROOT / "scripts" / "try-wrapper.sh"


def run_agenttx(ws: Path, sess: Path, commit: bool) -> dict:
    steps = build_coding_trajectory()
    h = CodingAgentHarness(workdir=ws, session_dir=sess, policy=CommitPolicy(workdir=ws))
    try:
        result = h.run_trajectory(steps, commit=commit)
        ledger = h.tx.ledger.to_dict()
        return {
            "mode": "agenttx",
            "n_steps": len(result.records),
            "wall_s": result.wall_s,
            "committed": result.committed,
            "failures": sum(1 for r in result.records if r.returncode != 0),
            "ledger": ledger,
        }
    finally:
        h.close(destroy=True)


def run_bare(ws: Path) -> dict:
    """Execute the same shell bodies without isolation (host writes)."""
    steps = build_coding_trajectory()
    # map tools to direct shell for fair-ish wall time of work itself
    t0 = time.perf_counter()
    failures = 0
    n = 0
    for step in steps:
        n += 1
        if step.tool == "write_file":
            p = ws / step.args["path"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(step.args.get("content", "") + "\n", encoding="utf-8")
        elif step.tool == "append_file":
            p = ws / step.args["path"]
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(step.args.get("content", "") + "\n")
        elif step.tool == "read_file":
            p = ws / step.args["path"]
            if p.exists():
                p.read_text(encoding="utf-8")
        elif step.tool in ("run_shell", "run_tests"):
            cmd = step.args.get("cmd", "true")
            cp = subprocess.run(["bash", "-c", cmd], cwd=str(ws))
            if cp.returncode != 0:
                failures += 1
        elif step.tool == "delete_file":
            p = ws / step.args["path"]
            if p.exists():
                p.unlink()
    return {
        "mode": "bare",
        "n_steps": n,
        "wall_s": time.perf_counter() - t0,
        "committed": True,
        "failures": failures,
        "ledger": None,
    }


def run_per_call_try(ws: Path) -> dict:
    steps = build_coding_trajectory()
    tb = try_bin()
    t0 = time.perf_counter()
    failures = 0
    n = 0
    for step in steps:
        n += 1
        if step.tool == "write_file":
            p = ws / step.args["path"]
            content = step.args.get("content", "")
            cmd = f"mkdir -p '{p.parent}' && cat > '{p}' <<'EOF'\n{content}\nEOF"
        elif step.tool == "append_file":
            p = ws / step.args["path"]
            content = step.args.get("content", "")
            cmd = f"mkdir -p '{p.parent}' && cat >> '{p}' <<'EOF'\n{content}\nEOF"
        elif step.tool == "read_file":
            cmd = f"cat '{ws / step.args['path']}'"
        elif step.tool in ("run_shell", "run_tests"):
            cmd = step.args.get("cmd", "true")
        elif step.tool == "delete_file":
            cmd = f"rm -f '{ws / step.args['path']}'"
        else:
            cmd = "true"
        cp = subprocess.run(
            [str(tb), "-n", "--", "bash", "-c", cmd],
            cwd=str(ws),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if cp.returncode != 0:
            failures += 1
    return {
        "mode": "per_call_try",
        "n_steps": n,
        "wall_s": time.perf_counter() - t0,
        "committed": False,
        "failures": failures,
        "ledger": None,
    }


def main() -> int:
    repeats = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    out_dir = ROOT / "experiments" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    # make workloads importable
    sys.path.insert(0, str(ROOT))

    rows = []
    scratch = Path(tempfile.mkdtemp(prefix="agenttx-long-", dir="/tmp"))
    try:
        for mode in ("bare", "per_call_try", "agenttx"):
            samples = []
            last = None
            for r in range(repeats):
                ws = scratch / f"{mode}-{r}" / "ws"
                ws.mkdir(parents=True)
                seed_repo(ws)
                if mode == "bare":
                    last = run_bare(ws)
                elif mode == "per_call_try":
                    last = run_per_call_try(ws)
                else:
                    sess = scratch / f"{mode}-{r}" / "sess"
                    last = run_agenttx(ws, sess, commit=False)
                samples.append(last["wall_s"])
                print(
                    f"{mode:14s} r{r}: steps={last['n_steps']} wall={last['wall_s']:.3f}s "
                    f"fail={last['failures']}"
                )
            mean = statistics.mean(samples)
            stdev = statistics.stdev(samples) if len(samples) > 1 else 0.0
            rows.append({
                "mode": mode,
                "n_steps": last["n_steps"],
                "repeats": repeats,
                "wall_s_mean": f"{mean:.6f}",
                "wall_s_stdev": f"{stdev:.6f}",
                "per_step_s_mean": f"{mean / max(last['n_steps'], 1):.6f}",
            })
            if last and last.get("ledger"):
                dump = out_dir / "long_traj_ledger.json"
                dump.write_text(json.dumps(last["ledger"], indent=2) + "\n", encoding="utf-8")
                print(f"wrote {dump}")
    finally:
        subprocess.run(["bash", "-lc", f"chmod -R u+rwX '{scratch}' 2>/dev/null || true"], check=False)
        shutil.rmtree(scratch, ignore_errors=True)

    csv_path = out_dir / "long_trajectory.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {csv_path}")
    for row in rows:
        print(row)
    return 0


if __name__ == "__main__":
    # fix import path for workloads package
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
