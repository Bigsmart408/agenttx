#!/usr/bin/env python3
"""Compatibility entry point for the official application token benchmark.

The old implementation used a synthetic document-replay repository.  Token
results now come only from the official SWE-Bench Lite and Terminal-Bench
runner; this name remains so existing automation can migrate without silently
running the obsolete workload.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from experiments.scripts.bench_official_tasks import HARNESSES, MODES, main as official_main  # noqa: E402


def _apply_policy(agent, mode: str, injected: dict):
    """Compatibility helper for runtime unit tests; no workload is created."""
    if mode == "causal":
        return agent.harness.tx.rollback_causal(injected["root_step"])
    if mode == "temporal_checkpoint":
        return agent.harness.tx.rollback(injected["root_step"])
    if mode == "whole_branch_abort":
        return agent.harness.tx.rollback(0)
    raise ValueError(mode)


def summarize(rows):
    """Summarize historical-shaped rows for compatibility tests only."""
    by_mode = {row["mode"]: row for row in rows}
    causal = by_mode.get("causal", {})
    base_total = float(causal.get("total_tokens", 0) or 0)
    base_completion = float(causal.get("completion_tokens", 0) or 0)
    out = []
    for mode, row in by_mode.items():
        total = float(row.get("total_tokens", 0) or 0)
        completion = float(row.get("completion_tokens", 0) or 0)
        saved = total - base_total if mode != "causal" else 0.0
        completion_saved = completion - base_completion if mode != "causal" else 0.0
        item = dict(row)
        item.update(
            agenttx_total_tokens_saved=int(saved),
            agenttx_total_tokens_saved_pct=round(saved / total, 3) if total else 0.0,
            agenttx_completion_tokens_saved=int(completion_saved),
        )
        out.append(item)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run official SWE-Bench Lite + Terminal-Bench token recovery."
    )
    parser.add_argument("--harness", choices=HARNESSES, default="deepseek_harness")
    parser.add_argument("--suite", choices=("swe", "tb", "all"), default="all")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--max-turns", type=int, default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--trace-backend", choices=("strace", "bpf_persistent"), default="strace")
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args(argv)
    forwarded = [
        "--harness", args.harness, "--suite", args.suite,
        "--repeats", str(args.repeats), "--modes", *args.modes,
        "--trace-backend", args.trace_backend,
    ]
    if args.max_turns is not None:
        forwarded += ["--max-turns", str(args.max_turns)]
    if args.model:
        forwarded += ["--model", args.model]
    if args.preflight_only:
        forwarded.append("--preflight-only")
    return official_main(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
