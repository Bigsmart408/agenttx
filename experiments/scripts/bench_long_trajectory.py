#!/usr/bin/env python3
"""Benchmark a long, failure-injecting Coding Agent workload.

The overhead suite runs the exact same trajectory for each runnable baseline.
The recovery suite stops immediately before repair, preserves independent docs
and config edits, then tests causal rollback before continuing with the repair.
"""
from __future__ import annotations

import argparse
import csv
import json
import shlex
import shutil
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "experiments" / "results"
import sys
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from agenttx.harness import CodingAgentHarness, TrajectoryStep  # noqa: E402
from agenttx.semisolate import SharedSemisolate  # noqa: E402
from experiments.workloads.long_coding_traj import (  # noqa: E402
    build_long_coding_trajectory,
    find_tagged_step,
    fault_step_index,
    independent_paths,
    seed_long_repo,
)


MODES = (
    "bare",
    "per_call_try",
    "shared_try",
    "shared_checkpoint",
    "agenttx_without_read_tracing",
    "agenttx_full",
)
CHECK_CMD = (
    "test ! -e lib/formatting.py && "
    "test ! -e build/format-report.txt && "
    "test -e docs/CHANGELOG.md && "
    "test -e config/feature.flags && "
    "test -e docs/attempt-1.md"
)


def cleanup(path: Path) -> None:
    subprocess.run(
        ["bash", "-lc", f"chmod -R u+rwX {shlex.quote(str(path))} 2>/dev/null || true"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    shutil.rmtree(path, ignore_errors=True)


def try_bin() -> Path:
    path = ROOT / "scripts" / "try-wrapper.sh"
    if not path.exists():
        raise RuntimeError(f"missing try wrapper: {path}")
    return path


def command_for_step(step: TrajectoryStep, ws: Path) -> str:
    """Translate a harness step to the same shell body used by baselines."""
    tool = step.tool
    args = step.args
    if tool == "write_file":
        path = (ws / str(args["path"])).resolve()
        content = str(args.get("content", ""))
        return (
            f"mkdir -p {shlex.quote(str(path.parent))} && "
            f"cat > {shlex.quote(str(path))} <<'AGENTTX_EOF'\n"
            f"{content}\nAGENTTX_EOF"
        )
    if tool == "append_file":
        path = (ws / str(args["path"])).resolve()
        content = str(args.get("content", ""))
        return (
            f"mkdir -p {shlex.quote(str(path.parent))} && "
            f"cat >> {shlex.quote(str(path))} <<'AGENTTX_EOF'\n"
            f"{content}\nAGENTTX_EOF"
        )
    if tool == "read_file":
        return f"cat {shlex.quote(str((ws / str(args['path'])).resolve()))}"
    if tool in ("run_shell", "run_tests"):
        return str(args.get("cmd", "true"))
    if tool == "delete_file":
        return f"rm -f {shlex.quote(str((ws / str(args['path'])).resolve()))}"
    raise ValueError(f"unsupported workload tool: {tool}")


def run_command(command: str, ws: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", command],
        cwd=str(ws),
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def run_try(command: str, ws: Path, sandbox: Path, *, shared: bool) -> subprocess.CompletedProcess:
    if shared:
        sandbox.mkdir(parents=True, exist_ok=True)
        args = [str(try_bin()), "-N", str(sandbox), "--", "bash", "-c", command]
    else:
        args = [str(try_bin()), "-n", "--", "bash", "-c", command]
    return subprocess.run(
        args,
        cwd=str(ws),
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def tree_snapshot(ws: Path) -> Dict[str, bytes]:
    snapshot: Dict[str, bytes] = {}
    if not ws.exists():
        return snapshot
    for path in ws.rglob("*"):
        if path.is_file() and ".pytest_cache" not in path.parts and "__pycache__" not in path.parts:
            snapshot[str(path.relative_to(ws))] = path.read_bytes()
    return snapshot


def host_polluted(before: Dict[str, bytes], ws: Path) -> bool:
    return before != tree_snapshot(ws)


def result_summary(mode: str, records: Sequence[int], wall_s: float, polluted: bool, **extra: object) -> dict:
    failures = sum(1 for rc in records if rc != 0)
    return {
        "mode": mode,
        "n_steps": len(records),
        "wall_s": wall_s,
        "per_step_ms": wall_s / max(len(records), 1) * 1000.0,
        "failures": failures,
        "final_rc": records[-1] if records else "",
        "host_polluted": polluted,
        **extra,
    }


def run_bare(ws: Path, steps: Sequence[TrajectoryStep], before: Dict[str, bytes]) -> dict:
    t0 = time.perf_counter()
    records: List[int] = []
    for step in steps:
        records.append(run_command(command_for_step(step, ws), ws).returncode)
    return result_summary("bare", records, time.perf_counter() - t0, host_polluted(before, ws), ledger_steps="")


def run_per_call_try(ws: Path, steps: Sequence[TrajectoryStep], before: Dict[str, bytes], root: Path) -> dict:
    t0 = time.perf_counter()
    records: List[int] = []
    for index, step in enumerate(steps):
        scratch = root / f"per-call-{index}"
        try:
            records.append(run_try(command_for_step(step, ws), ws, scratch / "sandbox", shared=False).returncode)
        finally:
            cleanup(scratch)
    return result_summary("per_call_try", records, time.perf_counter() - t0, host_polluted(before, ws), ledger_steps="")


def run_shared_try(ws: Path, steps: Sequence[TrajectoryStep], before: Dict[str, bytes], root: Path) -> dict:
    session = root / "shared-session"
    t0 = time.perf_counter()
    records = [run_try(command_for_step(step, ws), ws, session, shared=True).returncode for step in steps]
    wall = time.perf_counter() - t0
    cleanup(session)
    return result_summary("shared_try", records, wall, host_polluted(before, ws), ledger_steps="")


def run_shared_checkpoint(ws: Path, steps: Sequence[TrajectoryStep], before: Dict[str, bytes], root: Path) -> dict:
    t0 = time.perf_counter()
    records: List[int] = []
    pool = SharedSemisolate(workspace=ws, sandbox_dir=root / "checkpoint-session", trace_reads=False)
    try:
        for step in steps:
            records.append(pool.run(["bash", "-c", command_for_step(step, ws)]).returncode)
    finally:
        pool.close(destroy=True)
    return result_summary("shared_checkpoint", records, time.perf_counter() - t0, host_polluted(before, ws), ledger_steps="")


def run_agenttx(ws: Path, steps: Sequence[TrajectoryStep], before: Dict[str, bytes], root: Path, *, trace: bool) -> dict:
    mode = "agenttx_full" if trace else "agenttx_without_read_tracing"
    t0 = time.perf_counter()
    harness = CodingAgentHarness(workdir=ws, session_dir=root / "agenttx-session", trace_reads=trace)
    try:
        result = harness.run_trajectory(steps, commit=False)
        records = [record.returncode for record in result.records]
        ledger = harness.tx.ledger
        effects = sum(len(step.effects) for step in ledger.steps)
        reads = sum(sum(1 for effect in step.effects if effect.kind.value in ("R", "N")) for step in ledger.steps)
        return result_summary(
            mode,
            records,
            time.perf_counter() - t0,
            host_polluted(before, ws),
            ledger_steps=len(ledger.steps),
            ledger_effects=effects,
            read_effects=reads,
        )
    finally:
        harness.close(destroy=True)


def run_overhead(mode: str, ws: Path, steps: Sequence[TrajectoryStep], before: Dict[str, bytes], root: Path) -> dict:
    if mode == "bare":
        return run_bare(ws, steps, before)
    if mode == "per_call_try":
        return run_per_call_try(ws, steps, before, root)
    if mode == "shared_try":
        return run_shared_try(ws, steps, before, root)
    if mode == "shared_checkpoint":
        return run_shared_checkpoint(ws, steps, before, root)
    if mode == "agenttx_without_read_tracing":
        return run_agenttx(ws, steps, before, root, trace=False)
    if mode == "agenttx_full":
        return run_agenttx(ws, steps, before, root, trace=True)
    raise ValueError(mode)


def run_recovery(mode: str, length: int) -> dict:
    steps = build_long_coding_trajectory(length)
    fault = fault_step_index(steps)
    repair = find_tagged_step(steps, "repair_formatting")
    scratch = Path(tempfile.mkdtemp(prefix=f"agenttx-long-recovery-{mode}-", dir="/tmp"))
    ws = scratch / "ws"
    ws.mkdir()
    seed_long_repo(ws)
    before = tree_snapshot(ws)
    records: List[int] = []
    targets: List[int] = []
    check_rc: object = ""
    note = ""
    t0 = time.perf_counter()
    try:
        if mode == "bare":
            for step in steps[:repair]:
                records.append(run_command(command_for_step(step, ws), ws).returncode)
            polluted = host_polluted(before, ws)
            note = "host is already modified before recovery; no causal rollback"
            for step in steps[repair:]:
                records.append(run_command(command_for_step(step, ws), ws).returncode)
            return {
                "suite": "recovery", "mode": mode, "length": length,
                "fault_step": fault, "repair_step": repair, "rollback_targets": "",
                "host_polluted_before_recovery": polluted,
                "causal_retention_correct": False,
                "check_rc": "", "final_rc": records[-1],
                "wall_s": time.perf_counter() - t0, "note": note,
            }

        if mode == "per_call_try":
            for index, step in enumerate(steps[:repair]):
                one = scratch / f"per-call-{index}"
                try:
                    records.append(run_try(command_for_step(step, ws), ws, one / "sandbox", shared=False).returncode)
                finally:
                    cleanup(one)
            note = "each call is isolated, so the prefix and independent edits never form one state"
            return {
                "suite": "recovery", "mode": mode, "length": length,
                "fault_step": fault, "repair_step": repair, "rollback_targets": "",
                "host_polluted_before_recovery": host_polluted(before, ws),
                "causal_retention_correct": False, "check_rc": "", "final_rc": "",
                "wall_s": time.perf_counter() - t0, "note": note,
            }

        if mode == "shared_try":
            session = scratch / "shared-session"
            for step in steps[:repair]:
                records.append(run_try(command_for_step(step, ws), ws, session, shared=True).returncode)
            polluted = host_polluted(before, ws)
            cleanup(session)
            note = "shared overlay preserves the prefix but recovery is whole-session discard"
            return {
                "suite": "recovery", "mode": mode, "length": length,
                "fault_step": fault, "repair_step": repair, "rollback_targets": "",
                "host_polluted_before_recovery": polluted,
                "causal_retention_correct": False, "check_rc": "", "final_rc": "",
                "wall_s": time.perf_counter() - t0, "note": note,
            }

        if mode == "shared_checkpoint":
            pool = SharedSemisolate(workspace=ws, sandbox_dir=scratch / "checkpoint-session", trace_reads=False)
            try:
                for step in steps[:repair]:
                    records.append(pool.run(["bash", "-c", command_for_step(step, ws)]).returncode)
                polluted = host_polluted(before, ws)
                pool.rollback_steps([fault])
                check_rc = pool.run(["bash", "-c", CHECK_CMD]).returncode
            finally:
                pool.close(destroy=True)
            note = "full checkpoint rollback removes independent docs/config along with the fault"
            return {
                "suite": "recovery", "mode": mode, "length": length,
                "fault_step": fault, "repair_step": repair, "rollback_targets": "whole-session",
                "host_polluted_before_recovery": polluted,
                "causal_retention_correct": False, "check_rc": check_rc, "final_rc": "",
                "wall_s": time.perf_counter() - t0, "note": note,
            }

        trace = mode == "agenttx_full"
        harness = CodingAgentHarness(workdir=ws, session_dir=scratch / "agenttx-session", trace_reads=trace)
        try:
            for step in steps[:repair]:
                records.append(harness.call_tool(step.tool, step.args).returncode)
            polluted = host_polluted(before, ws)
            targets = harness.tx.rollback_causal(fault)
            check_rc = harness.call_tool("run_shell", {"cmd": CHECK_CMD}).returncode
            for step in steps[repair:]:
                records.append(harness.call_tool(step.tool, step.args).returncode)
            final_rc = records[-1]
            correct = bool(check_rc == 0 and final_rc == 0 and not polluted and mode == "agenttx_full")
            if not trace:
                note = "read tracing disabled: derived build/format-report.txt is retained"
            else:
                note = "read tracing links the derived artifact to the faulty formatter"
            return {
                "suite": "recovery", "mode": mode, "length": length,
                "fault_step": fault, "repair_step": repair, "rollback_targets": targets,
                "host_polluted_before_recovery": polluted,
                "causal_retention_correct": correct, "check_rc": check_rc,
                "final_rc": final_rc, "wall_s": time.perf_counter() - t0, "note": note,
            }
        finally:
            harness.close(destroy=True)
    finally:
        cleanup(scratch)


def aggregate(samples: Sequence[dict], mode: str, length: int, repeats: int) -> dict:
    walls = [float(sample["wall_s"]) for sample in samples]
    per_step = [float(sample["per_step_ms"]) for sample in samples]
    failures = [int(sample["failures"]) for sample in samples]
    last = samples[-1]
    return {
        "suite": "overhead", "mode": mode, "length": length, "repeats": repeats,
        "wall_s_mean": round(statistics.mean(walls), 6),
        "wall_s_stdev": round(statistics.stdev(walls), 6) if len(walls) > 1 else 0.0,
        "per_step_ms_mean": round(statistics.mean(per_step), 3),
        "failures_mean": round(statistics.mean(failures), 3),
        "final_rc": last.get("final_rc", ""),
        "host_polluted": last.get("host_polluted", ""),
        "ledger_steps": last.get("ledger_steps", ""),
        "ledger_effects": last.get("ledger_effects", ""),
        "read_effects": last.get("read_effects", ""),
        "note": "same deterministic trajectory; expected failures are the missing architecture read and injected CI failure",
    }


def write_outputs(rows: Sequence[dict], length: int, repeats: int) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    csv_path = OUT / "long_workload_matrix.csv"
    fields = sorted({key for row in rows for key in row})
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    (OUT / "long_workload_matrix.json").write_text(json.dumps(list(rows), indent=2) + "\n", encoding="utf-8")
    overhead = [row for row in rows if row.get("suite") == "overhead"]
    recovery = [row for row in rows if row.get("suite") == "recovery"]
    lines = [
        "# Long Agent workload matrix", "",
        f"Deterministic {length}-tool-call trajectory; overhead repeats={repeats}.",
        "Phases: exploration -> modular refactor -> failing CI -> independent docs/config -> repair -> cleanup.",
        "", "## Overhead", "",
        "| mode | wall mean (s) | stdev (s) | ms/step | failures | host polluted | ledger steps | read effects |",
        "|---|---:|---:|---:|---:|:---:|---:|---:|",
    ]
    for row in overhead:
        lines.append(
            f"| {row['mode']} | {row['wall_s_mean']} | {row['wall_s_stdev']} | "
            f"{row['per_step_ms_mean']} | {row['failures_mean']} | {row['host_polluted']} | "
            f"{row.get('ledger_steps','')} | {row.get('read_effects','')} |"
        )
    lines += [
        "", "## Recovery semantics", "",
        "The recovery prefix stops before the repair. The expected state after causal rollback is:",
        "faulty `lib/formatting.py` absent, derived `build/format-report.txt` absent, and the three independent docs/config files retained.",
        "", "| mode | host polluted before recovery | causal retention correct | check rc | rollback targets | final rc | note |",
        "|---|:---:|:---:|---:|---|---:|---|",
    ]
    for row in recovery:
        lines.append(
            f"| {row['mode']} | {row['host_polluted_before_recovery']} | {row['causal_retention_correct']} | "
            f"{row['check_rc']} | `{row['rollback_targets']}` | {row['final_rc']} | {row['note']} |"
        )
    lines += [
        "", "The comparison is a VM-local systems measurement, not a universal speed claim.",
        "Per-call try is intentionally included as a continuity baseline; external systems remain outside this runnable matrix.",
    ]
    (OUT / "long_workload_matrix.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {csv_path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--length", type=int, default=64)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    args = parser.parse_args()
    steps = build_long_coding_trajectory(args.length)
    rows: List[dict] = []
    for mode in args.modes:
        samples: List[dict] = []
        for repeat in range(args.repeats):
            scratch = Path(tempfile.mkdtemp(prefix=f"agenttx-long-{mode}-", dir="/tmp"))
            ws = scratch / "ws"
            ws.mkdir()
            seed_long_repo(ws)
            before = tree_snapshot(ws)
            try:
                sample = run_overhead(mode, ws, steps, before, scratch)
                samples.append(sample)
                print(
                    f"overhead {mode:32s} r{repeat}: steps={sample['n_steps']} "
                    f"wall={sample['wall_s']:.3f}s fail={sample['failures']} "
                    f"host_polluted={sample['host_polluted']}", flush=True
                )
            finally:
                cleanup(scratch)
        rows.append(aggregate(samples, mode, args.length, args.repeats))
    for mode in args.modes:
        row = run_recovery(mode, args.length)
        rows.append(row)
        print(
            f"recovery {mode:32s} correct={row['causal_retention_correct']} "
            f"host_polluted={row['host_polluted_before_recovery']} targets={row['rollback_targets']}",
            flush=True,
        )
    write_outputs(rows, args.length, args.repeats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())