#!/usr/bin/env python3
"""Robustness evaluation: tail latency, worker crashes, long sessions, and concurrency.

All four experiments use the real AgentTX runtime and write one reproducible
result bundle under ``experiments/results/robustness.{csv,json,md}``.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import statistics
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

ROOT = Path(__file__).resolve().parents[2]
import sys

sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from agenttx.runtime import AgentTX  # noqa: E402
from experiments.workloads.long_coding_traj import (  # noqa: E402
    build_long_coding_trajectory,
    seed_long_repo,
)
from agenttx.harness import CodingAgentHarness  # noqa: E402


def percentile(values: Sequence[float], quantile: float) -> float:
    """Return a linearly interpolated percentile in the same units as values."""
    if not values:
        return 0.0
    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile must be between 0 and 1")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _cleanup(path: Path) -> None:
    subprocess.run(
        ["bash", "-lc", f"chmod -R u+rwX '{path}' 2>/dev/null || true"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    shutil.rmtree(path, ignore_errors=True)


def _write_command(index: int) -> List[str]:
    return [
        "bash",
        "-c",
        f"mkdir -p long && printf '%s\\n' {index} > long/step_{index}.txt",
    ]


def run_tail_latency(mode: str, length: int, repeats: int) -> dict:
    """Collect end-to-end per-tool and per-run p50/p95 samples."""
    trace = mode == "agenttx_full"
    step_samples: List[float] = []
    run_samples: List[float] = []
    returncodes: List[int] = []
    steps = build_long_coding_trajectory(length)
    for repeat in range(repeats):
        scratch = Path(tempfile.mkdtemp(prefix=f"agenttx-tail-{mode}-", dir="/tmp"))
        ws = scratch / "ws"
        ws.mkdir()
        seed_long_repo(ws)
        harness = CodingAgentHarness(
            workdir=ws,
            session_dir=scratch / "session",
            trace_reads=trace,
        )
        try:
            run_start = time.perf_counter()
            for step in steps:
                call_start = time.perf_counter()
                record = harness.call_tool(step.tool, step.args)
                step_samples.append((time.perf_counter() - call_start) * 1000.0)
                returncodes.append(record.returncode)
            run_samples.append((time.perf_counter() - run_start) * 1000.0)
            print(
                f"tail mode={mode} repeat={repeat} steps={len(steps)} "
                f"wall_ms={run_samples[-1]:.1f}",
                flush=True,
            )
        finally:
            harness.close(destroy=True)
            _cleanup(scratch)
    return {
        "suite": "p50_p95",
        "mode": mode,
        "length": length,
        "repeats": repeats,
        "samples": len(step_samples),
        "step_p50_ms": round(percentile(step_samples, 0.50), 3),
        "step_p95_ms": round(percentile(step_samples, 0.95), 3),
        "run_p50_ms": round(percentile(run_samples, 0.50), 3),
        "run_p95_ms": round(percentile(run_samples, 0.95), 3),
        "wall_ms": round(percentile(run_samples, 0.50), 3),
        "failure_rate": round(sum(rc != 0 for rc in returncodes) / max(len(returncodes), 1), 6),
        "ok": True,
        "note": "end-to-end call wall time includes AgentTX ledger persistence",
    }


def run_worker_crash() -> dict:
    """Kill the persistent worker once and verify one-shot fallback + restart."""
    scratch = Path(tempfile.mkdtemp(prefix="agenttx-worker-crash-", dir="/tmp"))
    ws = scratch / "ws"
    ws.mkdir()
    tx = AgentTX.begin(workdir=ws, session_dir=scratch / "session", trace_reads=False)
    try:
        first = tx.run_tool("before", ["bash", "-c", "echo before > before.txt"])
        assert tx.pool is not None
        worker_before = tx.pool._worker_process
        assert worker_before is not None
        tx.pool.inject_worker_crash_once()
        injected_start = time.perf_counter()
        recovered = tx.run_tool(
            "recovered",
            ["bash", "-c", "echo recovered > recovered.txt"],
        )
        fallback_ms = (time.perf_counter() - injected_start) * 1000.0
        failures_after_fallback = tx.pool.worker_failure_count
        restarted = tx.run_tool(
            "restart",
            ["bash", "-c", "echo restarted > restarted.txt"],
        )
        worker_after = tx.pool._worker_process
        tx.commit()
        ok = all(
            record.returncode == 0 for record in (first, recovered, restarted)
        ) and failures_after_fallback == 1 and worker_after is not None and (
            worker_after is not worker_before
        ) and all((ws / name).exists() for name in ("before.txt", "recovered.txt", "restarted.txt"))
        return {
            "suite": "worker_crash",
            "mode": "agenttx_without_read_tracing",
            "injected": True,
            "fallback_used": failures_after_fallback == 1,
            "worker_restarted": worker_after is not None and worker_after is not worker_before,
            "fallback_ms": round(fallback_ms, 3),
            "failure_count": failures_after_fallback,
            "ok": ok,
            "note": "worker killed before dispatch; command completed through one-shot try fallback",
        }
    finally:
        tx.close(destroy=True)
        _cleanup(scratch)


def run_long_session(steps: int, resume_at: int) -> dict:
    """Run a long session, close/reload halfway, then commit all work."""
    if steps < 2 or not 1 <= resume_at < steps:
        raise ValueError("long session requires 2+ steps and 1 <= resume_at < steps")
    scratch = Path(tempfile.mkdtemp(prefix="agenttx-long-session-", dir="/tmp"))
    ws = scratch / "ws"
    ws.mkdir()
    durations: List[float] = []
    failures = 0
    tx = AgentTX.begin(workdir=ws, session_dir=scratch / "session", trace_reads=False)
    try:
        for index in range(resume_at):
            start = time.perf_counter()
            record = tx.run_tool(f"long-{index}", _write_command(index))
            durations.append((time.perf_counter() - start) * 1000.0)
            failures += record.returncode != 0
        tx.close(destroy=False)
        tx = AgentTX.load(scratch / "session")
        for index in range(resume_at, steps):
            start = time.perf_counter()
            record = tx.run_tool(f"long-{index}", _write_command(index))
            durations.append((time.perf_counter() - start) * 1000.0)
            failures += record.returncode != 0
        frontier = tx.commit()
        files = list((ws / "long").glob("step_*.txt"))
        ok = (
            failures == 0
            and len(files) == steps
            and frontier == steps - 1
            and all(path.read_text(encoding="utf-8").strip() for path in files)
        )
        return {
            "suite": "long_session",
            "mode": "agenttx_without_read_tracing",
            "steps": steps,
            "resume_at": resume_at,
            "resumed": True,
            "wall_ms": round(sum(durations), 3),
            "step_p50_ms": round(percentile(durations, 0.50), 3),
            "step_p95_ms": round(percentile(durations, 0.95), 3),
            "failures": failures,
            "materialized_files": len(files),
            "ok": ok,
            "note": "session was closed and reloaded at the midpoint before final commit",
        }
    finally:
        tx.close(destroy=True)
        _cleanup(scratch)


def _concurrent_agent(root: Path, agent_id: int, steps: int) -> dict:
    host = root / "host"
    ws = host / f"agent_{agent_id}"
    ws.mkdir(parents=True, exist_ok=True)
    tx = AgentTX.begin(
        workdir=ws,
        session_dir=root / f"session_{agent_id}",
        trace_reads=False,
    )
    try:
        start = time.perf_counter()
        for index in range(steps):
            record = tx.run_tool(
                f"agent-{agent_id}-{index}",
                [
                    "bash",
                    "-c",
                    f"mkdir -p output && printf '%s\\n' {agent_id}:{index} > output/{index}.txt",
                ],
            )
            if record.returncode != 0:
                raise RuntimeError(f"agent {agent_id} step {index} failed")
        frontier = tx.commit()
        files = sorted((ws / "output").glob("*.txt"))
        values = {path.read_text(encoding="utf-8").strip() for path in files}
        expected = {f"{agent_id}:{index}" for index in range(steps)}
        return {
            "agent_id": agent_id,
            "steps": steps,
            "wall_ms": round((time.perf_counter() - start) * 1000.0, 3),
            "frontier": frontier,
            "files": len(files),
            "isolated": values == expected,
            "ok": frontier == steps - 1 and values == expected,
        }
    finally:
        tx.close(destroy=True)


def run_concurrent_agents(agents: int, steps: int) -> dict:
    if agents < 2 or steps <= 0:
        raise ValueError("concurrent agents requires agents >= 2 and steps > 0")
    root = Path(tempfile.mkdtemp(prefix="agenttx-concurrent-", dir="/tmp"))
    start = time.perf_counter()
    results: List[dict] = []
    try:
        with ThreadPoolExecutor(max_workers=agents) as executor:
            futures = [executor.submit(_concurrent_agent, root, agent_id, steps) for agent_id in range(agents)]
            for future in as_completed(futures):
                results.append(future.result())
        results.sort(key=lambda row: row["agent_id"])
        ok = len(results) == agents and all(row["ok"] for row in results)
        return {
            "suite": "concurrent_agents",
            "mode": "agenttx_without_read_tracing",
            "agents": agents,
            "steps_per_agent": steps,
            "wall_ms": round((time.perf_counter() - start) * 1000.0, 3),
            "agent_p50_ms": round(percentile([row["wall_ms"] for row in results], 0.50), 3),
            "agent_p95_ms": round(percentile([row["wall_ms"] for row in results], 0.95), 3),
            "successful_agents": sum(row["ok"] for row in results),
            "cross_contamination": not all(row["isolated"] for row in results),
            "ok": ok,
            "details": results,
            "note": "agents use separate session overlays and commit into separate workspace subdirectories concurrently",
        }
    finally:
        _cleanup(root)


def write_outputs(rows: Sequence[dict]) -> None:
    out = ROOT / "experiments" / "results"
    out.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row if key != "details"})
    with (out / "robustness.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    (out / "robustness.json").write_text(json.dumps(list(rows), indent=2) + "\n", encoding="utf-8")
    lines = [
        "# AgentTX robustness evaluation",
        "",
        "The bundle reports end-to-end tail latency, persistent-worker crash recovery, a long reloadable session, and concurrent isolated agents.",
        "",
        "| suite | mode | p50 ms | p95 ms | wall ms | steps/agents | ok | note |",
        "|---|---|---:|---:|---:|---:|:---:|---|",
    ]
    for row in rows:
        p50 = row.get("step_p50_ms", row.get("agent_p50_ms", ""))
        p95 = row.get("step_p95_ms", row.get("agent_p95_ms", ""))
        count = row.get("length", row.get("steps", row.get("agents", "")))
        lines.append(
            f"| {row['suite']} | {row.get('mode','')} | {p50} | {p95} | {row.get('wall_ms','')} | {count} | {row.get('ok','')} | {row.get('note','')} |"
        )
    (out / "robustness.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out / 'robustness.csv'}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tail-length", type=int, default=64)
    parser.add_argument("--tail-repeats", type=int, default=3)
    parser.add_argument("--tail-modes", nargs="+", choices=("agenttx_without_read_tracing", "agenttx_full"), default=["agenttx_without_read_tracing", "agenttx_full"])
    parser.add_argument("--long-steps", type=int, default=256)
    parser.add_argument("--long-resume-at", type=int, default=128)
    parser.add_argument("--agents", type=int, default=4)
    parser.add_argument("--concurrent-steps", type=int, default=16)
    args = parser.parse_args()
    if args.tail_length <= 0 or args.tail_repeats <= 0:
        parser.error("tail length and repeats must be positive")
    rows: List[dict] = [
        run_tail_latency(mode, args.tail_length, args.tail_repeats)
        for mode in args.tail_modes
    ]
    rows.extend(
        [
            run_worker_crash(),
            run_long_session(args.long_steps, args.long_resume_at),
            run_concurrent_agents(args.agents, args.concurrent_steps),
        ]
    )
    write_outputs(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
