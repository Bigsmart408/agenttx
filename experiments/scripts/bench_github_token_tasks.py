#!/usr/bin/env python3
"""Compatibility entry point for the retired GitHub-context workload.

Application evaluation is now defined by the official SWE-Bench Lite and
Terminal-Bench manifests.  This filename remains so old experiment commands
fail over to the same external-harness runner instead of silently executing a
synthetic repository workload.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from experiments.scripts import bench_official_tasks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", choices=("deepseek_harness", "codex"), default="deepseek_harness")
    parser.add_argument("--suite", choices=("swe", "tb", "all"), default="all")
    parser.add_argument("--tasks", nargs="+", default=None)
    parser.add_argument("--modes", nargs="+", default=list(bench_official_tasks.MODES))
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-turns", type=int, default=None)
    parser.add_argument("--trace-backend", choices=("strace", "bpf_persistent"), default="strace")
    parser.add_argument("--oracle", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    # Historical scale/provider switches are accepted but intentionally do not
    # select a different workload or model in the official evaluation.
    parser.add_argument("--scales", nargs="*", help=argparse.SUPPRESS)
    parser.add_argument("--provider", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    forwarded = ["--harness", args.harness, "--suite", args.suite]
    if args.tasks:
        forwarded += ["--tasks", *args.tasks]
    if args.modes:
        forwarded += ["--modes", *args.modes]
    forwarded += ["--repeats", str(args.repeats)]
    if args.model:
        forwarded += ["--model", args.model]
    if args.max_turns is not None:
        forwarded += ["--max-turns", str(args.max_turns)]
    forwarded += ["--trace-backend", args.trace_backend]
    if args.oracle:
        forwarded.append("--oracle")
    if args.preflight_only:
        forwarded.append("--preflight-only")
    return bench_official_tasks.main(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
