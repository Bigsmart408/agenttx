#!/usr/bin/env python3
"""Run the seven-mode Codex smoke over diverse representative task types."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from experiments.scripts import bench_official_tasks as bench


SMOKE_MODES = (
    "no_fault",
    "crash_direct",
    "clean_recovery",
    "crash_no_rollback",
    "causal",
    "temporal_checkpoint",
    "whole_branch_abort",
)

SMOKE_TASKS = (
    "tb:hello-world",
    "tb:csv-to-parquet",
    "tb:log-summary",
    "swe:pytest-dev__pytest-8906",
    "swe:pallets__flask-4992",
    "swe:pylint-dev__pylint-5859",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--harness-timeout", type=float, default=1800.0)
    parser.add_argument(
        "--result-subdir", default="codex_7mode_multicase_smoke"
    )
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args(argv)

    command = [
        "--suite",
        "all",
        "--task-set",
        "representative",
        "--tasks",
        *SMOKE_TASKS,
        "--modes",
        *SMOKE_MODES,
        "--repeats",
        str(args.repeats),
        "--harness",
        "codex",
        "--model",
        args.model,
        "--harness-timeout",
        str(args.harness_timeout),
        "--result-subdir",
        args.result_subdir,
        "--python",
        os.environ.get("AGENTTX_PYTHON", bench.DEFAULT_PYTHON),
    ]
    if args.preflight_only:
        command.append("--preflight-only")
    return bench.main(command)


if __name__ == "__main__":
    raise SystemExit(main())
