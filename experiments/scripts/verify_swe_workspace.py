#!/usr/bin/env python3
"""Run one SWE-Bench FAIL_TO_PASS verifier in its official eval image."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from experiments.workloads import swe_bench_suite as swe  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--workdir", type=Path, default=Path.cwd())
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "experiments" / "cache")
    args = parser.parse_args()

    tasks = swe.load_tasks(args.cache_dir)
    task = tasks[args.instance_id]
    instance = swe.fetch_instance(args.instance_id, args.cache_dir)
    # Agent-visible verification must never silently fall back to the host
    # interpreter; old SWE-Bench projects depend on image-local compiled code.
    os.environ["AGENTTX_SWE_VERIFY"] = "docker"
    verdict = swe.verify(
        args.workdir.resolve(),
        task,
        instance,
        sys.executable,
        require_recovery_artifacts=False,
    )
    stdout = str(verdict.get("verifier_stdout") or "")
    stderr = str(verdict.get("verifier_stderr") or "")
    if stdout:
        print(stdout, end="" if stdout.endswith("\n") else "\n")
    if stderr:
        print(stderr, file=sys.stderr, end="" if stderr.endswith("\n") else "\n")
    return 0 if verdict.get("tests_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
