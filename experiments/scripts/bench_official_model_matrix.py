#!/usr/bin/env python3
"""Run the official application matrix with one external harness.

Defaults intentionally use the inexpensive tiers for this evaluation:
DeepSeek Harness uses ``deepseek-v4-flash`` and Codex uses ``gpt-5.6-luna``.
Passing ``--models`` explicitly requests a separate model sweep.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from experiments.workloads.swe_bench_suite import TASKS as SWE_TASKS
from experiments.workloads.terminal_bench_suite import TASKS as TB_TASKS
from experiments.scripts.bench_official_tasks import MODES

PY = os.environ.get("AGENTTX_PYTHON", sys.executable)
BENCH = ROOT / "experiments" / "scripts" / "bench_official_tasks.py"
HARNESSES = ("deepseek_harness", "codex")
CHEAP_MODELS = {
    "deepseek_harness": "deepseek-v4-flash",
    "codex": "gpt-5.6-luna",
}


def successful_cells(path: Path) -> set[tuple[str, str, str, str]]:
    done: set[tuple[str, str, str, str]] = set()
    if not path.exists():
        return done
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            model = (row.get("model") or "").strip()
            suite = (row.get("suite") or "").strip()
            task = (row.get("task") or "").strip()
            mode = (row.get("mode") or "").strip()
            success = str(row.get("success") or "").strip().lower() in {"true", "1", "yes"}
            if model and suite and task and mode and success:
                done.add((model, suite, task, mode))
    return done


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness", choices=HARNESSES, default="deepseek_harness")
    parser.add_argument("--suite", choices=("swe", "tb", "all"), default="all")
    parser.add_argument("--models", nargs="*", default=None)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    os.chdir(ROOT)
    os.environ.setdefault("PYTHONPATH", "src:.")
    os.environ.setdefault("TRY_SKIP_MOUNTS", "/data")

    outdir = ROOT / "experiments" / "results" / args.harness
    raw = outdir / "official_tasks_raw.csv"
    outdir.mkdir(parents=True, exist_ok=True)

    selected = list(args.models) if args.models else [CHEAP_MODELS[args.harness]]
    if args.limit and args.limit > 0:
        selected = selected[: args.limit]

    jobs = []
    if args.suite in {"swe", "all"}:
        jobs.extend(("swe", name) for name in SWE_TASKS)
    if args.suite in {"tb", "all"}:
        jobs.extend(("tb", name) for name in TB_TASKS)

    print("harness", args.harness, "models", selected, "jobs", jobs, flush=True)
    skip = successful_cells(raw)
    t0 = time.time()
    n = 0
    total = len(selected) * len(jobs)
    for model in selected:
        for suite, task in jobs:
            n += 1
            missing = [mode for mode in MODES if (model, suite, task, mode) not in skip]
            if not missing:
                print("[%s/%s] SKIP %s %s %s" % (n, total, model, suite, task), flush=True)
                continue
            cmd = [
                PY,
                str(BENCH),
                "--harness", args.harness,
                "--model", model,
                "--suite", suite,
                "--tasks", task,
                "--modes", *missing,
                "--repeats", str(args.repeats),
            ]
            print("[%s/%s] %s %s %s modes=%s" % (n, total, model, suite, task, ",".join(missing)), flush=True)
            started = time.time()
            proc = subprocess.run(
                cmd,
                cwd=str(ROOT),
                env={**os.environ, "PATH": str(Path.home() / ".local/bin") + ":" + os.environ.get("PATH", "")},
            )
            print("  rc=%s wall=%.1fs" % (proc.returncode, time.time() - started), flush=True)
    print("DONE_MATRIX elapsed=%.0fs" % (time.time() - t0), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
