#!/usr/bin/env python3
"""Measure persistent eBPF versus strace and no tracing.

Compares three dependency-capture modes on a short deterministic
workload and verifies that the chosen backend actually captures workspace
reads and negative lookups.  The eBPF mode requires root and a working
    bpftrace; without them the benchmark exits non-zero with a clear message and
writes no results (no placeholders).
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agenttx import bpf_trace
from agenttx.ledger import Effect, EffectKind
from agenttx.runtime import AgentTX


def _cleanup(path: Path) -> None:
    subprocess.run(
        ["chmod", "-R", "u+rwX", str(path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    shutil.rmtree(path, ignore_errors=True)


def measure(
    n_steps: int,
    workload: str,
    trace_reads: bool,
    trace_backend: str,
) -> tuple[list[float], dict]:
    """Run the workload; returns (per-step seconds, capture verdict dict).

    A traced step whose capture verification fails is retried once: the
    syscall-tracepoint stream is global and a fork event can occasionally be
    dropped on a noisy host, which would filter the whole command tree out
    of the effect parse.  The retry count is reported in the results so the
    retry policy is auditable, and the capture guarantee below is enforced
    on the retried step.
    """
    scratch = Path(tempfile.mkdtemp(prefix="agenttx-bpf-bench-", dir="/tmp"))
    workspace = scratch / "ws"
    workspace.mkdir()
    # The read workload must actually read a file that exists: `cat` on a
    # missing file would only yield a NEGATIVE lookup and the capture check
    # below would fail.
    (workspace / "input.txt").write_text("payload\n", encoding="utf-8")
    tx = None
    samples: list[float] = []
    capture = {"reads": 0, "negatives": 0, "expected": 0, "retries": 0}
    try:
        tx = AgentTX.begin(
            workdir=workspace,
            session_dir=scratch / "session",
            trace_reads=trace_reads,
            trace_backend=trace_backend,
        )
        for index in range(n_steps):
            if workload == "read":
                argv = [
                    "bash",
                    "-c",
                    "cat input.txt > /dev/null; test -e missing.txt || true",
                ]
            else:
                argv = ["bash", "-c", ":"]
            record = tx.run_tool(f"step-{index}", argv)
            duration = record.duration_s
            if trace_reads and workload == "read":
                reads = {
                    e.path
                    for e in record.effects
                    if e.kind == EffectKind.READ and e.path.endswith("input.txt")
                }
                negatives = {
                    e.path
                    for e in record.effects
                    if e.kind == EffectKind.NEGATIVE
                    and e.path.endswith("missing.txt")
                }
                if not (reads and negatives):
                    # transient trace loss: retry once, keep the retry's
                    # duration as the step's sample
                    capture["retries"] += 1
                    record = tx.run_tool(f"step-{index}-retry", argv)
                    duration = record.duration_s
                    reads = {
                        e.path
                        for e in record.effects
                        if e.kind == EffectKind.READ
                        and e.path.endswith("input.txt")
                    }
                    negatives = {
                        e.path
                        for e in record.effects
                        if e.kind == EffectKind.NEGATIVE
                        and e.path.endswith("missing.txt")
                    }
                if reads:
                    capture["reads"] += 1
                if negatives:
                    capture["negatives"] += 1
                capture["expected"] += 1
            samples.append(duration)
        return samples, capture
    finally:
        if tx is not None and tx.pool is not None:
            tx.close(destroy=True)
        _cleanup(scratch)


def _stats(samples: list[float]) -> dict:
    ordered = sorted(samples)
    def percentile(q: float) -> float:
        if not ordered:
            return 0.0
        index = min(len(ordered) - 1, int(q * len(ordered)))
        return ordered[index]
    return {
        "mean_ms": statistics.mean(samples) * 1000.0,
        "p50_ms": percentile(0.50) * 1000.0,
        "p95_ms": percentile(0.95) * 1000.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", "--steps", type=int, default=20)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--workload",
        choices=("noop", "read"),
        default="read",
        help="noop: `:` only; read: cat + negative lookup (default: read)",
    )
    args = parser.parse_args()
    if args.steps <= 0 or args.repeats <= 0:
        parser.error("steps and repeats must be positive")

    static_ok, static_detail = bpf_trace.bpf_static_available()
    if not static_ok:
        print(
            f"eBPF backend unavailable on this host ({static_detail}); "
            "run as root on a host with bpftrace to produce "
            "bpf_trace_overhead results",
            file=sys.stderr,
        )
        return 2

    modes = [
        ("off", False, "auto"),
        ("strace", True, "strace"),
        ("bpf", True, "bpf"),
    ]
    rows = []
    for mode, trace_reads, backend in modes:
        all_samples: list[float] = []
        captures: list[dict] = []
        for _ in range(args.repeats):
            samples, capture = measure(
                args.steps, args.workload, trace_reads, backend
            )
            all_samples.extend(samples)
            captures.append(capture)
        stats = _stats(all_samples)
        reads = sum(c["reads"] for c in captures)
        negatives = sum(c["negatives"] for c in captures)
        expected = sum(c["expected"] for c in captures)
        retries = sum(c["retries"] for c in captures)
        row = {
            "mode": mode,
            "trace_reads": trace_reads,
            "trace_backend": backend,
            "workload": args.workload,
            "steps": args.steps,
            "repeats": args.repeats,
            "samples": len(all_samples),
            "per_step_ms_mean": round(stats["mean_ms"], 3),
            "per_step_ms_p50": round(stats["p50_ms"], 3),
            "per_step_ms_p95": round(stats["p95_ms"], 3),
            "reads_captured": reads,
            "negatives_captured": negatives,
            "capture_expected": expected,
            "step_retries": retries,
        }
        rows.append(row)
        if trace_reads:
            if reads != expected or negatives != expected:
                print(
                    f"capture verification failed for {mode}: "
                    f"{reads}/{negatives} of {expected} read/negative steps",
                    file=sys.stderr,
                )
                return 1

    results = ROOT / "experiments" / "results"
    results.mkdir(parents=True, exist_ok=True)
    csv_path = results / "bpf_trace_overhead.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    by_mode = {row["mode"]: row for row in rows}
    baseline = by_mode["off"]["per_step_ms_mean"]
    md_lines = [
        "# eBPF vs strace dependency-tracing overhead",
        "",
        f"{args.workload} workload; {args.steps} steps per run, "
        f"{args.repeats} repeats.",
        "",
        "| mode | per_step_ms_mean | per_step_ms_p50 | per_step_ms_p95 |",
        "|---|---:|---:|---:|",
    ]
    for row in rows:
        md_lines.append(
            f"| {row['mode']} | {row['per_step_ms_mean']:.2f} | "
            f"{row['per_step_ms_p50']:.2f} | {row['per_step_ms_p95']:.2f} |"
        )
    strace_delta = by_mode["strace"]["per_step_ms_mean"] - baseline
    bpf_delta = by_mode["bpf"]["per_step_ms_mean"] - baseline
    retry_lines = []
    for row in rows:
        if row["step_retries"]:
            retry_lines.append(
                f"{row['mode']}: {row['step_retries']} step(s) retried once "
                "after transient trace loss."
            )
    md_lines.extend(
        [
            "",
            f"strace incremental cost: {strace_delta:.2f} ms/step "
            f"({strace_delta / baseline * 100.0:.1f}%).",
            f"Persistent eBPF incremental cost: {bpf_delta:.2f} ms/step "
            f"({bpf_delta / baseline * 100.0:.1f}%).",
            "",
            "Capture verification: every read step yielded both the `input.txt` "
            "READ and the `missing.txt` NEGATIVE effect for all traced modes.",
        ]
    )
    md_lines.extend(retry_lines)
    md_path = results / "bpf_trace_overhead.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    json_path = results / "bpf_trace_overhead.json"
    json_path.write_text(
        json.dumps({"rows": rows, "summary": md_lines[-6:]}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(md_path.read_text(encoding="utf-8"))
    print(f"wrote {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
