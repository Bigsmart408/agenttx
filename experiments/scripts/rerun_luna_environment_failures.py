#!/usr/bin/env python3
"""Rerun repaired Luna SWE cases and replace their old environment failures."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
PYTHON = Path("/home/bfq/miniconda3/envs/agenttx/bin/python")
MODEL = "gpt-5.6-luna"
EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")
TASKS = ("astropy__astropy-6938", "astropy__astropy-7746")
MODES = ("no_fault", "crash_direct")
SOURCE_TEMPLATE = "codex_luna_{effort}_bare_controls_10_sweep_20260906"
RERUN_TEMPLATE = "codex_luna_{effort}_astropy_envfix_20260907"


def row_key(row: dict) -> tuple[str, ...]:
    return (
        str(row.get("suite", "")),
        str(row.get("task", "")),
        str(row.get("mode", "")),
        str(row.get("repeat", "")),
        str(row.get("model", "")),
        str(row.get("harness_backend", "")),
    )


def harness_command(effort: str) -> str:
    codex = shutil.which("codex")
    if not codex:
        raise RuntimeError("codex executable is unavailable")
    return shlex.join(
        [
            codex,
            "exec",
            "--model",
            MODEL,
            "-c",
            "features.plugins=false",
            "-c",
            f'model_reasoning_effort="{effort}"',
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "--json",
        ]
    )


def merge_effort(effort: str) -> None:
    from experiments.scripts import bench_official_tasks as bench

    source_name = SOURCE_TEMPLATE.format(effort=effort)
    rerun_name = RERUN_TEMPLATE.format(effort=effort)
    source_dir = ROOT / "experiments" / "results" / source_name
    rerun_path = (
        ROOT / "experiments" / "results" / rerun_name / "official_tasks_raw.csv"
    )
    source_path = source_dir / "official_tasks_raw.csv"
    rerun_rows = bench._load_prior_rows(rerun_path)
    expected = {
        (task, mode) for task in TASKS for mode in MODES
    }
    actual = {(str(row.get("task")), str(row.get("mode"))) for row in rerun_rows}
    if actual != expected:
        raise RuntimeError(
            f"{effort}: expected repaired rows {sorted(expected)}, got {sorted(actual)}"
        )
    invalid = [
        row
        for row in rerun_rows
        if row.get("environment_error")
        or int(row.get("total_tokens") or 0) < 1
        or not bench._completed_attempt(row)
    ]
    if invalid:
        detail = [
            (row.get("task"), row.get("mode"), row.get("error")) for row in invalid
        ]
        raise RuntimeError(f"{effort}: refusing to merge invalid reruns: {detail}")

    backup_dir = source_dir.with_name(source_dir.name + "_before_envfix_20260907")
    if not backup_dir.exists():
        shutil.copytree(source_dir, backup_dir)

    merged = {row_key(row): row for row in bench._load_prior_rows(source_path)}
    for row in rerun_rows:
        merged[row_key(row)] = row
    rows = list(merged.values())
    if len(rows) != 20:
        raise RuntimeError(f"{effort}: merged row count is {len(rows)}, expected 20")
    bench.write_outputs(
        bench.summarize(rows),
        provider=None,
        oracle=False,
        harness_backend="codex",
        result_subdir=source_name,
    )
    print(f"MERGED effort={effort} rows={len(rows)}", flush=True)


def run_effort(effort: str) -> None:
    result_name = RERUN_TEMPLATE.format(effort=effort)
    command = [
        str(PYTHON),
        "-u",
        str(ROOT / "experiments" / "scripts" / "bench_official_tasks.py"),
        "--suite",
        "swe",
        "--task-set",
        "selected",
        "--tasks",
        *TASKS,
        "--modes",
        *MODES,
        "--repeats",
        "1",
        "--harness",
        "codex",
        "--model",
        MODEL,
        "--harness-command",
        harness_command(effort),
        "--harness-timeout",
        "1800",
        "--bare-direct-controls",
        "--result-subdir",
        result_name,
        "--python",
        str(PYTHON),
    ]
    print(f"START effort={effort} result={result_name}", flush=True)
    result = subprocess.run(command, cwd=ROOT, env=os.environ.copy(), check=False)
    if result.returncode not in {0, 1}:
        raise RuntimeError(f"{effort}: benchmark exited with rc={result.returncode}")
    merge_effort(effort)


def main() -> int:
    os.environ["AGENTTX_SWE_VERIFY"] = "docker"
    os.environ["AGENTTX_KEEP_EVAL_IMAGES"] = "1"
    os.environ["AGENTTX_EXTERNAL_MAX_CONCURRENCY"] = "1"
    os.environ["AGENTTX_EXTERNAL_SLOT_DIR"] = str(
        ROOT / "experiments" / "results" / ".luna_envfix_request_slots"
    )
    for effort in EFFORTS:
        run_effort(effort)
    print("ALL_EFFORTS_MERGED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
