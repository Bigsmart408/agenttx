#!/usr/bin/env python3
"""Single application-workload entry point used by motivation experiments.

Every model-bearing motivation run now uses the official SWE-Bench Lite and
Terminal-Bench task manifests. The recovery DAG is a controlled fault
injected into those real tasks; it is not a synthetic repository workload.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from experiments.scripts.bench_official_tasks import HARNESSES, MODES, main as official_main  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run official application motivation workloads.")
    parser.add_argument("--harness", choices=HARNESSES, default="deepseek_harness")
    parser.add_argument("--suite", choices=("swe", "tb", "all"), default="all")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-turns", type=int, default=None)
    parser.add_argument("--trace-backend", choices=("strace", "bpf_persistent"), default="strace")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--length", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--lengths", nargs="+", type=int, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    forwarded = [
        "--harness", args.harness, "--suite", args.suite,
        "--repeats", str(args.repeats), "--modes", *args.modes,
        "--trace-backend", args.trace_backend,
    ]
    if args.model:
        forwarded += ["--model", args.model]
    if args.max_turns is not None:
        forwarded += ["--max-turns", str(args.max_turns)]
    if args.preflight_only:
        forwarded.append("--preflight-only")
    return official_main(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
