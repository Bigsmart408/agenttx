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
    parser.add_argument("--cache-dir", type=Path, default=Path(os.environ.get("AGENTTX_SWE_VERIFIER_CACHE", str(ROOT / "experiments" / "cache"))))
    parser.add_argument("--image-id")
    parser.add_argument("--expected-python")
    args = parser.parse_args()
    try:
        return _verify(args)
    except Exception as exc:
        print(f"Official evaluator infrastructure error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 70


def _verify(args: argparse.Namespace) -> int:
    tasks = swe.load_tasks(args.cache_dir)
    task = tasks[args.instance_id]
    instance = swe.fetch_instance(args.instance_id, args.cache_dir)
    runtime = swe.official_runtime_info(args.instance_id, args.image_id)
    if args.image_id and args.image_id != runtime["image_id"]:
        raise RuntimeError("official evaluator image changed since run preflight")
    if args.expected_python and args.expected_python != runtime["python_version"]:
        raise RuntimeError("official evaluator interpreter changed since run preflight")
    instance = {**instance, "_agenttx_eval_runtime": runtime}
    print(f"Official evaluator Python {runtime['python_version']} ({runtime['image_id']})", flush=True)
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
    if str(verdict.get("tests_rc")) in {"error", "timeout"}:
        return 70
    return 0 if verdict.get("tests_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
