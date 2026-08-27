#!/usr/bin/env python3
"""WAL phase fault-injection matrix.

Each phase crashes once during commit, reloads the session, and checks that
host content and the commit frontier converge without losing unrelated files.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from agenttx.commit_wal import CommitWAL
from agenttx.runtime import AgentTX

OUT = ROOT / "experiments" / "results"
REPEATS = int(os.environ.get("AGENTTX_WAL_REPEATS", "10"))

ORIG_PREPARE = CommitWAL.prepare
ORIG_MARK = CommitWAL.mark
ORIG_CLEANUP = CommitWAL.cleanup


def _cleanup(scratch: Path) -> None:
    subprocess.run(
        ["bash", "-lc", f"chmod -R u+rwX '{scratch}' 2>/dev/null || true"],
        check=False,
    )
    shutil.rmtree(scratch, ignore_errors=True)


def _restore_methods() -> None:
    CommitWAL.prepare = ORIG_PREPARE
    CommitWAL.mark = ORIG_MARK
    CommitWAL.cleanup = ORIG_CLEANUP


class Crash(Exception):
    pass


def run_one(phase: str, scratch: Path) -> dict:
    _restore_methods()
    ws = scratch / "ws"
    session = scratch / "sess"
    ws.mkdir()
    (ws / "target.txt").write_text("old\n", encoding="utf-8")
    (ws / "keep.txt").write_text("keep\n", encoding="utf-8")

    tx = AgentTX.begin(workdir=ws, session_dir=session)
    tx.run_tool("rewrite", ["bash", "-c", "echo new > target.txt"])
    assert (ws / "target.txt").read_text(encoding="utf-8") == "old\n"

    if phase == "before_prepare":
        @classmethod
        def prepare_crash(cls, *args, **kwargs):
            raise Crash("before_prepare")

        CommitWAL.prepare = prepare_crash  # type: ignore
    elif phase == "prepared":
        @classmethod
        def prepare_then_crash(cls, *args, **kwargs):
            wal = ORIG_PREPARE(*args, **kwargs)
            raise Crash("prepared")

        CommitWAL.prepare = prepare_then_crash  # type: ignore
    elif phase == "applying":
        def mark_crash(self, new_phase: str) -> None:
            ORIG_MARK(self, new_phase)
            if new_phase == "applying":
                raise Crash("applying")

        CommitWAL.mark = mark_crash  # type: ignore
    elif phase == "during_install":
        def crash_commit(paths=None, hardlink_groups=None):
            (ws / "target.txt").write_text("new\n", encoding="utf-8")
            raise Crash("during_install")

        assert tx.pool is not None
        tx.pool.commit = crash_commit  # type: ignore
    elif phase == "materialized":
        def mark_crash(self, new_phase: str) -> None:
            ORIG_MARK(self, new_phase)
            if new_phase == "materialized":
                raise Crash("materialized")

        CommitWAL.mark = mark_crash  # type: ignore
    elif phase == "committed":
        def mark_then_break_cleanup(self, new_phase: str) -> None:
            ORIG_MARK(self, new_phase)
            if new_phase == "committed":
                def boom(self2=None):
                    raise OSError("committed cleanup failure")

                self.cleanup = boom  # type: ignore

        CommitWAL.mark = mark_then_break_cleanup  # type: ignore
    else:
        raise ValueError(phase)

    crashed = False
    err = ""
    try:
        tx.commit()
    except Exception as exc:
        crashed = True
        err = f"{type(exc).__name__}: {exc}"
    host_after_crash = (ws / "target.txt").read_text(encoding="utf-8").strip()
    wal_after_crash = (session / "commit_wal.json").exists()
    tx.close(destroy=False)
    _restore_methods()

    resumed = AgentTX.load(session)
    host_after_reload = (ws / "target.txt").read_text(encoding="utf-8").strip()
    frontier_after_reload = resumed.ledger.committed_frontier
    wal_after_reload = (session / "commit_wal.json").exists()
    resume_err = ""
    try:
        if resumed.ledger.committed_frontier < 0 or host_after_reload == "old":
            # Re-attempt publication when crash left speculative work.
            max_step = max((s.step_id for s in resumed.ledger.steps), default=-1)
            if resumed.ledger.committed_frontier < max_step:
                resumed.commit()
    except Exception as exc:
        resume_err = f"{type(exc).__name__}: {exc}"
    host_final = (ws / "target.txt").read_text(encoding="utf-8").strip()
    frontier_final = resumed.ledger.committed_frontier
    wal_final = (session / "commit_wal.json").exists()
    keep_ok = (ws / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    resumed.close(destroy=True)

    if phase == "before_prepare":
        # No WAL; host unchanged; recommit should succeed.
        ok = (
            crashed
            and host_after_crash == "old"
            and host_after_reload == "old"
            and host_final == "new"
            and not wal_final
            and keep_ok
            and not resume_err
        )
        expected = "no_wal_recommit"
    elif phase in {"prepared", "applying", "during_install"}:
        ok = (
            crashed
            and host_after_reload == "old"
            and host_final == "new"
            and not wal_final
            and keep_ok
            and not resume_err
        )
        expected = "restore_then_recommit"
    elif phase == "materialized":
        # Host may already be new; reload must converge and clear WAL.
        ok = (
            crashed
            and host_final == "new"
            and not wal_final
            and keep_ok
        )
        expected = "finalize_or_converge"
    else:  # committed
        ok = host_final == "new" and not wal_final and keep_ok
        expected = "durable_frontier"

    return {
        "phase": phase,
        "expected": expected,
        "ok": bool(ok),
        "crashed": crashed,
        "error": (err + (" | " + resume_err if resume_err else ""))[:240],
        "host_after_crash": host_after_crash,
        "host_after_reload": host_after_reload,
        "host_final": host_final,
        "wal_after_crash": wal_after_crash,
        "wal_after_reload": wal_after_reload,
        "wal_final": wal_final,
        "frontier_after_reload": frontier_after_reload,
        "frontier_final": frontier_final,
        "keep_ok": keep_ok,
    }


PHASES = [
    "before_prepare",
    "prepared",
    "applying",
    "during_install",
    "materialized",
    "committed",
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    raw = []
    t0 = time.perf_counter()
    for phase in PHASES:
        for repeat in range(REPEATS):
            scratch = Path(tempfile.mkdtemp(prefix="agenttx-wal-", dir="/tmp"))
            try:
                row = run_one(phase, scratch)
                row["repeat"] = repeat
                raw.append(row)
                print(
                    f"{phase} r{repeat}: {'OK' if row['ok'] else 'FAIL'} "
                    f"{row['host_after_crash']}->{row['host_after_reload']}->{row['host_final']} "
                    f"wal={row['wal_after_crash']}/{row['wal_final']} err={row['error'][:60]}"
                )
            except Exception as exc:
                raw.append(
                    {
                        "phase": phase,
                        "expected": "n/a",
                        "ok": False,
                        "repeat": repeat,
                        "error": f"exception: {exc}",
                        "host_after_crash": "",
                        "host_after_reload": "",
                        "host_final": "",
                        "wal_after_crash": False,
                        "wal_after_reload": False,
                        "wal_final": False,
                        "frontier_after_reload": None,
                        "frontier_final": None,
                        "keep_ok": False,
                        "crashed": False,
                    }
                )
                print(f"{phase} r{repeat}: EXC {exc}")
            finally:
                _restore_methods()
                _cleanup(scratch)

    by_phase = {}
    for row in raw:
        by_phase.setdefault(row["phase"], []).append(row)
    summary = []
    for phase, rows in by_phase.items():
        summary.append(
            {
                "phase": phase,
                "expected": rows[0]["expected"],
                "repeats": len(rows),
                "recovery_rate": sum(1 for r in rows if r["ok"]) / float(len(rows)),
                "all_ok": all(r["ok"] for r in rows),
                "host_consistent_rate": sum(1 for r in rows if r.get("keep_ok"))
                / float(len(rows)),
            }
        )

    payload = {
        "host": os.uname().nodename,
        "repeats": REPEATS,
        "wall_s": time.perf_counter() - t0,
        "summary": summary,
        "raw": raw,
    }
    (OUT / "wal_fault_matrix.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    with (OUT / "wal_fault_matrix.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "phase",
                "expected",
                "repeats",
                "recovery_rate",
                "all_ok",
                "host_consistent_rate",
            ],
        )
        writer.writeheader()
        writer.writerows(summary)

    lines = [
        "# WAL phase fault-injection matrix",
        "",
        f"Repeats per phase: {REPEATS}. Crash, reload, then check host/frontier convergence.",
        "",
        "| phase | expected | recovery rate | host keep |",
        "|---|---|---:|---:|",
    ]
    for row in summary:
        lines.append(
            f"| {row['phase']} | {row['expected']} | {row['recovery_rate']:.2f} | "
            f"{row['host_consistent_rate']:.2f} |"
        )
    lines.append("")
    (OUT / "wal_fault_matrix.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    failed = [r for r in summary if not r["all_ok"]]
    if failed:
        print("FAILED:", ", ".join(r["phase"] for r in failed))
        sys.exit(1)
    print(f"wrote {OUT / 'wal_fault_matrix.md'}")


if __name__ == "__main__":
    main()
