#!/usr/bin/env python3
"""Stronger evidence suite for AgentTX Problem A claims."""
from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from agenttx.harness import CodingAgentHarness
from agenttx.policy import CommitPolicy
from agenttx.runtime import AgentTX
from experiments.workloads import mistake_recovery_traj as mr
from experiments.workloads.coding_traj import build_coding_trajectory, seed_repo as seed_coding


OUT = ROOT / "experiments" / "results"


def _cleanup(scratch: Path) -> None:
    subprocess.run(
        ["bash", "-lc", f"chmod -R u+rwX '{scratch}' 2>/dev/null || true"],
        check=False,
    )
    shutil.rmtree(scratch, ignore_errors=True)


def host_markers(ws: Path, names: list[str]) -> dict:
    return {n: (ws / n).exists() for n in names}


def exp_cascade_rollback() -> dict:
    scratch = Path(tempfile.mkdtemp(prefix="agenttx-ev-cascade-", dir="/tmp"))
    ws = scratch / "ws"
    ws.mkdir()
    (ws / "seed.txt").write_text("seed\n", encoding="utf-8")
    t0 = time.perf_counter()
    try:
        tx = AgentTX.begin(workdir=ws, session_dir=scratch / "sess")
        tx.run_tool("w0", ["bash", "-c", "echo A > chain_a.txt"])
        tx.run_tool("w1", ["bash", "-c", "echo B > chain_b.txt && cat chain_a.txt >/dev/null"])
        tx.run_tool("w2", ["bash", "-c", "echo C > chain_c.txt && cat chain_b.txt >/dev/null"])
        tx.run_tool("w3", ["bash", "-c", "echo D > chain_d.txt"])
        aborted = tx.rollback(1)
        upper = tx.pool.sandbox_dir / "upperdir"
        names = {p.name for p in upper.rglob("*") if p.is_file()}
        # continue after surgical rollback
        tx.run_tool("w4", ["bash", "-c", "echo E > chain_e.txt"])
        names2 = {p.name for p in upper.rglob("*") if p.is_file()}
        host_before = host_markers(ws, ["chain_a.txt", "chain_b.txt", "chain_c.txt", "chain_d.txt", "chain_e.txt"])
        up = max(s.step_id for s in tx.ledger.steps if s.status != "rolled_back")
        tx.commit(up)
        host_after = host_markers(ws, ["chain_a.txt", "chain_b.txt", "chain_c.txt", "chain_d.txt", "chain_e.txt"])
        tx.close(destroy=True)
        ok = (
            "chain_a.txt" in names
            and "chain_b.txt" not in names
            and "chain_c.txt" not in names
            and "chain_d.txt" not in names
            and "chain_e.txt" in names2
            and not any(host_before.values())
            and host_after["chain_a.txt"]
            and not host_after["chain_b.txt"]
            and not host_after["chain_c.txt"]
            and not host_after["chain_d.txt"]
            and host_after["chain_e.txt"]
            and set(aborted) >= {1, 2, 3}
        )
        return {
            "exp": "cascade_rollback",
            "ok": ok,
            "wall_s": time.perf_counter() - t0,
            "aborted": aborted,
            "upper_after_rollback": sorted(names),
            "host_before_commit": host_before,
            "host_after_commit": host_after,
            "detail": "rollback step1 cascades; host clean until commit; only a+e land",
        }
    finally:
        _cleanup(scratch)


def exp_selective_commit_via_rollback() -> dict:
    """Supported selective-commit workflow: rollback later steps, then commit."""
    scratch = Path(tempfile.mkdtemp(prefix="agenttx-ev-sel-", dir="/tmp"))
    ws = scratch / "ws"
    ws.mkdir()
    t0 = time.perf_counter()
    try:
        tx = AgentTX.begin(workdir=ws, session_dir=scratch / "sess")
        tx.run_tool("keep0", ["bash", "-c", "echo keep0 > keep0.txt"])
        tx.run_tool("keep1", ["bash", "-c", "echo keep1 > keep1.txt"])
        tx.run_tool("drop2", ["bash", "-c", "echo drop2 > drop2.txt"])
        tx.run_tool("drop3", ["bash", "-c", "echo drop3 > drop3.txt"])
        aborted = tx.rollback(2)  # drop drop2/drop3 from overlay
        tx.commit(1)
        host = host_markers(ws, ["keep0.txt", "keep1.txt", "drop2.txt", "drop3.txt"])
        tx.close(destroy=True)
        ok = (
            set(aborted) >= {2, 3}
            and host["keep0.txt"]
            and host["keep1.txt"]
            and not host["drop2.txt"]
            and not host["drop3.txt"]
        )
        return {
            "exp": "selective_commit_via_rollback",
            "ok": ok,
            "wall_s": time.perf_counter() - t0,
            "aborted": aborted,
            "host_after_commit": host,
            "detail": "rollback(2)+commit keeps keep0/1 only",
        }
    finally:
        _cleanup(scratch)


def exp_frontier_selective_commit() -> dict:
    """Verify commit(up_to) leaves independent later paths speculative."""
    scratch = Path(tempfile.mkdtemp(prefix="agenttx-ev-frontier-", dir="/tmp"))
    ws = scratch / "ws"
    ws.mkdir()
    t0 = time.perf_counter()
    try:
        tx = AgentTX.begin(workdir=ws, session_dir=scratch / "sess")
        tx.run_tool("keep0", ["bash", "-c", "echo keep0 > keep0.txt"])
        tx.run_tool("keep1", ["bash", "-c", "echo keep1 > keep1.txt"])
        tx.run_tool("later2", ["bash", "-c", "echo later2 > later2.txt"])
        tx.commit(1)
        host = host_markers(ws, ["keep0.txt", "keep1.txt", "later2.txt"])
        tx.close(destroy=True)
        fs_selective = host["keep0.txt"] and host["keep1.txt"] and not host["later2.txt"]
        return {
            "exp": "frontier_selective_commit",
            "ok": fs_selective,
            "wall_s": time.perf_counter() - t0,
            "fs_selective": fs_selective,
            "host_after_commit": host,
            "detail": "commit(1) writes keep0/1 while later2 remains speculative",
        }
    finally:
        _cleanup(scratch)

def exp_host_pollution_vs_bare() -> dict:
    """Same multi-write traj: bare pollutes immediately; agenttx only after commit."""
    scratch = Path(tempfile.mkdtemp(prefix="agenttx-ev-poll-", dir="/tmp"))
    marker = "POLLUTE_MARKER.txt"
    t0 = time.perf_counter()
    try:
        # bare
        ws_b = scratch / "bare"
        ws_b.mkdir()
        polluted_mid_bare = False
        for i in range(5):
            (ws_b / f"f{i}.txt").write_text(f"{i}\n", encoding="utf-8")
            if i == 2:
                (ws_b / marker).write_text("bare\n", encoding="utf-8")
                polluted_mid_bare = (ws_b / marker).exists()
        # agenttx
        ws_a = scratch / "agenttx"
        ws_a.mkdir()
        tx = AgentTX.begin(workdir=ws_a, session_dir=scratch / "sess")
        mid_polluted = False
        for i in range(5):
            tx.run_tool(f"w{i}", ["bash", "-c", f"echo {i} > f{i}.txt"])
            if i == 2:
                tx.run_tool("mark", ["bash", "-c", f"echo agenttx > {marker}"])
                mid_polluted = (ws_a / marker).exists()
        after_spec = (ws_a / marker).exists()
        tx.commit()
        after_commit = (ws_a / marker).exists()
        tx.close(destroy=True)
        ok = polluted_mid_bare and (not mid_polluted) and (not after_spec) and after_commit
        return {
            "exp": "host_pollution_vs_bare",
            "ok": ok,
            "wall_s": time.perf_counter() - t0,
            "bare_polluted_mid_traj": polluted_mid_bare,
            "agenttx_polluted_mid_traj": mid_polluted,
            "agenttx_polluted_before_commit": after_spec,
            "agenttx_present_after_commit": after_commit,
            "detail": "bare writes visible mid-traj; agenttx host clean until commit",
        }
    finally:
        _cleanup(scratch)


def exp_mistake_recovery() -> dict:
    scratch = Path(tempfile.mkdtemp(prefix="agenttx-ev-mr-", dir="/tmp"))
    ws = scratch / "ws"
    ws.mkdir()
    mr.seed_repo(ws)
    t0 = time.perf_counter()
    try:
        h = CodingAgentHarness(
            workdir=ws, session_dir=scratch / "sess", policy=CommitPolicy(workdir=ws)
        )
        bad = mr.build_bad_then_good()
        recs = []
        for step in bad:
            recs.append(h.run_step(step))
        # find first write step id for buggy mul (step after read)
        bug_step = next(r.step_id for r in recs if r.tool_name == "write_file")
        test_rc = recs[-1].returncode
        host_bug_before_rb = "return a + b  # BUG" in (ws / "src" / "mathy.py").read_text(encoding="utf-8")
        aborted = h.tx.rollback(bug_step)
        # host still clean
        host_after_rb = (ws / "src" / "mathy.py").read_text(encoding="utf-8")
        host_still_seed = "def mul" not in host_after_rb
        for step in mr.build_good_fix():
            recs.append(h.run_step(step))
        final_test = recs[-2] if recs[-1].tool_name == "write_file" else recs[-1]
        # last run_tests among good path
        test_recs = [r for r in recs if r.tool_name == "run_tests"]
        good_test_rc = test_recs[-1].returncode
        host_before_commit_has_mul = "def mul" in (ws / "src" / "mathy.py").read_text(encoding="utf-8")
        # commit active frontier
        active = [s.step_id for s in h.tx.ledger.steps if s.status != "rolled_back"]
        up = max(active)
        h.policy.assert_committable(h.tx.ledger, up)
        h.tx.commit(up)
        final_src = (ws / "src" / "mathy.py").read_text(encoding="utf-8")
        notes_ok = (ws / "notes" / "RECOVERY.md").exists()
        mul_ok = "return a * b" in final_src and "# BUG" not in final_src
        cp = subprocess.run(
            ["bash", "-c", "PYTHONPATH=. python3 -m pytest -q tests/test_mathy.py"],
            cwd=str(ws),
            capture_output=True,
            text=True,
        )
        h.close(destroy=True)
        ok = (
            test_rc != 0
            and not host_bug_before_rb
            and host_still_seed
            and not host_before_commit_has_mul
            and good_test_rc == 0
            and mul_ok
            and notes_ok
            and cp.returncode == 0
            and len(aborted) >= 1
        )
        return {
            "exp": "mistake_recovery",
            "ok": ok,
            "wall_s": time.perf_counter() - t0,
            "bad_test_rc": test_rc,
            "good_test_rc": good_test_rc,
            "host_saw_bug_before_rollback": host_bug_before_rb,
            "host_stayed_seed_after_rollback": host_still_seed,
            "host_clean_before_commit": not host_before_commit_has_mul,
            "aborted_steps": aborted,
            "post_commit_tests_rc": cp.returncode,
            "detail": "buggy mul never hits host; rollback; fixed mul committed; pytest pass",
        }
    finally:
        _cleanup(scratch)


def exp_policy_blocks_dangerous_commit() -> dict:
    scratch = Path(tempfile.mkdtemp(prefix="agenttx-ev-pol-", dir="/tmp"))
    ws = scratch / "ws"
    ws.mkdir()
    t0 = time.perf_counter()
    try:
        policy = CommitPolicy(workdir=ws, deny_globs=list(CommitPolicy(workdir=ws).deny_globs) + ["*/secrets/*", "secrets/*"])
        h = CodingAgentHarness(workdir=ws, session_dir=scratch / "sess", policy=policy)
        h.call_tool("write_file", {"path": "ok.txt", "content": "fine\n"})
        h.call_tool("write_file", {"path": "secrets/key.pem", "content": "PRIVATE\n"})
        blocked = False
        reason = ""
        try:
            active = [s.step_id for s in h.tx.ledger.steps if s.status != "rolled_back"]
            h.policy.assert_committable(h.tx.ledger, max(active))
            h.tx.commit(max(active))
        except Exception as e:
            blocked = True
            reason = str(e)
        host_secret = (ws / "secrets" / "key.pem").exists()
        # selective: rollback secret step then commit ok
        secret_step = max(s.step_id for s in h.tx.ledger.steps)
        h.tx.rollback(secret_step)
        active2 = [s.step_id for s in h.tx.ledger.steps if s.status != "rolled_back"]
        h.policy.assert_committable(h.tx.ledger, max(active2))
        h.tx.commit(max(active2))
        host_ok = (ws / "ok.txt").exists()
        host_secret_final = (ws / "secrets" / "key.pem").exists()
        h.close(destroy=True)
        ok = blocked and (not host_secret) and host_ok and (not host_secret_final)
        return {
            "exp": "policy_blocks_dangerous_commit",
            "ok": ok,
            "wall_s": time.perf_counter() - t0,
            "commit_blocked": blocked,
            "block_reason": reason[:200],
            "host_had_secret_when_blocked": host_secret,
            "host_ok_after_selective_commit": host_ok,
            "host_secret_after_selective_commit": host_secret_final,
            "detail": "deny secrets/*.pem blocks full commit; after rollback, ok.txt commits alone",
        }
    finally:
        _cleanup(scratch)


def exp_isolation_matrix(repeats: int = 2) -> list:
    """Long-ish coding traj: bare vs agenttx on pollution + failures + wall."""
    rows = []
    for mode in ("bare", "agenttx"):
        walls = []
        fails = []
        polluted = []
        for r in range(repeats):
            scratch = Path(tempfile.mkdtemp(prefix=f"agenttx-ev-mx-{mode}-", dir="/tmp"))
            ws = scratch / "ws"
            ws.mkdir()
            seed_coding(ws)
            t0 = time.perf_counter()
            try:
                if mode == "bare":
                    steps = build_coding_trajectory()
                    fail = 0
                    for step in steps:
                        if step.tool == "write_file":
                            p = ws / step.args["path"]
                            p.parent.mkdir(parents=True, exist_ok=True)
                            p.write_text(step.args.get("content", "") + "\n", encoding="utf-8")
                        elif step.tool == "append_file":
                            p = ws / step.args["path"]
                            p.parent.mkdir(parents=True, exist_ok=True)
                            with p.open("a", encoding="utf-8") as f:
                                f.write(step.args.get("content", "") + "\n")
                        elif step.tool == "read_file":
                            p = ws / step.args["path"]
                            if p.exists():
                                p.read_text(encoding="utf-8")
                        elif step.tool in ("run_shell", "run_tests"):
                            cp = subprocess.run(
                                ["bash", "-c", step.args.get("cmd", "true")],
                                cwd=str(ws),
                                capture_output=True,
                                text=True,
                            )
                            if cp.returncode != 0 and not step.args.get("ignore_errors"):
                                fail += 1
                        elif step.tool == "delete_file":
                            p = ws / step.args["path"]
                            if p.exists():
                                p.unlink()
                    # bare always polluted if trajectory wrote extras
                    pol = (ws / "src" / "ops_add.py").exists() or "pow2" in (ws / "src" / "calc.py").read_text(encoding="utf-8")
                    walls.append(time.perf_counter() - t0)
                    fails.append(fail)
                    polluted.append(pol)
                else:
                    h = CodingAgentHarness(
                        workdir=ws,
                        session_dir=scratch / "sess",
                        policy=CommitPolicy(workdir=ws),
                    )
                    result = h.run_trajectory(build_coding_trajectory(), commit=False)
                    calc = (ws / "src" / "calc.py").read_text(encoding="utf-8")
                    pol_before = ("def pow2" in calc) or (ws / "src" / "ops_add.py").exists()
                    active = [s.step_id for s in h.tx.ledger.steps if s.status != "rolled_back"]
                    if active:
                        up = max(active)
                        h.policy.assert_committable(h.tx.ledger, up)
                        h.tx.commit(up)
                    fail = sum(1 for rec in result.records if rec.returncode != 0)
                    walls.append(result.wall_s)
                    fails.append(fail)
                    polluted.append(pol_before)
                    h.close(destroy=True)
            finally:
                _cleanup(scratch)
        rows.append(
            {
                "exp": "isolation_matrix",
                "mode": mode,
                "repeats": repeats,
                "wall_s_mean": sum(walls) / len(walls),
                "failures_mean": sum(fails) / len(fails),
                "host_polluted_before_commit_rate": sum(1 for p in polluted if p) / len(polluted),
                "ok": (mode == "bare" and all(polluted)) or (mode == "agenttx" and not any(polluted)),
                "detail": "coding traj pollution/failures/wall",
            }
        )
    return rows


# helpers if harness API differs
def _patch_harness_helpers():
    if not hasattr(CodingAgentHarness, "run_step"):
        def run_step(self, step):
            return self.call_tool(step.tool, step.args)
        CodingAgentHarness.run_step = run_step  # type: ignore


def main() -> int:
    _patch_harness_helpers()
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    print("=== cascade_rollback ===", flush=True)
    rows.append(exp_cascade_rollback())
    print(json.dumps({k: rows[-1][k] for k in ("exp", "ok", "wall_s")}, indent=2), flush=True)

    print("=== selective_commit_via_rollback ===", flush=True)
    rows.append(exp_selective_commit_via_rollback())
    print(json.dumps({k: rows[-1][k] for k in ("exp", "ok", "wall_s")}, indent=2), flush=True)

    print("=== frontier_selective_commit ===", flush=True)
    rows.append(exp_frontier_selective_commit())
    print(json.dumps({k: rows[-1][k] for k in ("exp", "ok", "wall_s", "fs_selective") if k in rows[-1]}, indent=2), flush=True)

    print("=== host_pollution_vs_bare ===", flush=True)
    rows.append(exp_host_pollution_vs_bare())
    print(json.dumps({k: rows[-1][k] for k in ("exp", "ok", "wall_s")}, indent=2), flush=True)

    print("=== mistake_recovery ===", flush=True)
    rows.append(exp_mistake_recovery())
    print(json.dumps({k: v for k, v in rows[-1].items() if k != "detail"}, indent=2), flush=True)

    print("=== policy_blocks_dangerous_commit ===", flush=True)
    rows.append(exp_policy_blocks_dangerous_commit())
    print(json.dumps({k: v for k, v in rows[-1].items() if k != "detail"}, indent=2), flush=True)

    print("=== isolation_matrix ===", flush=True)
    mx = exp_isolation_matrix(repeats=2)
    rows.extend(mx)
    for r in mx:
        print(json.dumps({k: r[k] for k in r if k != "detail"}, indent=2), flush=True)

    # CSV summary
    csv_path = OUT / "evidence_suite.csv"
    fields = [
        "exp",
        "mode",
        "ok",
        "wall_s",
        "wall_s_mean",
        "failures_mean",
        "host_polluted_before_commit_rate",
        "detail",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    full = OUT / "evidence_suite.json"
    full.write_text(json.dumps(rows, indent=2, default=str) + "\n", encoding="utf-8")

    md = OUT / "evidence_suite.md"
    lines = [
        "# AgentTX evidence suite",
        "",
        "| exp | mode | ok | wall_s | notes |",
        "|---|---|---|---:|---|",
    ]
    for r in rows:
        wall = r.get("wall_s", r.get("wall_s_mean"))
        wall_s = f"{wall:.2f}" if isinstance(wall, (int, float)) else ""
        lines.append(
            f"| {r.get('exp')} | {r.get('mode', '')} | {r.get('ok')} | {wall_s} | {r.get('detail', '')} |"
        )
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {csv_path}", flush=True)
    print(md.read_text(encoding="utf-8"), flush=True)
    failed = [r for r in rows if not r.get("ok")]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
