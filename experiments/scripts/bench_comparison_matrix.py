#!/usr/bin/env python3
"""Compare supported AgentTX baselines on one fixed trajectory."""
from __future__ import annotations
import argparse, csv, json, shutil, statistics, subprocess, tempfile, time
from pathlib import Path
from typing import Callable, Dict, List
ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / "src"))
from agenttx.runtime import AgentTX
from agenttx.semisolate import SharedSemisolate
OUT = ROOT / "experiments" / "results"
TRAJECTORY = [("producer", "echo bad > a.txt"), ("consumer", "cat a.txt > b.txt"), ("independent", "echo independent > c.txt")]

def cleanup(path: Path) -> None:
    subprocess.run(["bash", "-lc", f"chmod -R u+rwX '{path}' 2>/dev/null || true"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    shutil.rmtree(path, ignore_errors=True)

def try_bin() -> Path:
    value = ROOT / "scripts" / "try-wrapper.sh"
    if not value.exists(): raise RuntimeError(f"missing try wrapper: {value}")
    return value

def run_command(command: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", command], cwd=str(cwd), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

def run_try(command: str, cwd: Path, sandbox: Path, *, shared: bool) -> subprocess.CompletedProcess:
    if shared:
        sandbox.mkdir(parents=True, exist_ok=True)
    args = [str(try_bin()), "-N", str(sandbox), "--", "bash", "-c", command] if shared else [str(try_bin()), "-n", "--", "bash", "-c", command]
    return subprocess.run(args, cwd=str(cwd), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

def run_session_try(commands: List[str], cwd: Path, sandbox: Path) -> subprocess.CompletedProcess:
    return run_try("set -e; " + "; ".join(commands), cwd, sandbox, shared=True)

def run_bubblewrap(commands: List[str]) -> subprocess.CompletedProcess:
    body = "set -e; mkdir -p /tmp/agenttx-bwrap; cd /tmp/agenttx-bwrap; " + "; ".join(commands)
    args = ["bwrap", "--die-with-parent", "--ro-bind", "/", "/", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--unshare-net", "bash", "-c", body]
    return subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

def run_agenttx(commands: List[str], cwd: Path, session: Path, *, trace: bool) -> float:
    t0 = time.perf_counter()
    tx = AgentTX.begin(workdir=cwd, session_dir=session, trace_reads=trace)
    try:
        for index, command in enumerate(commands):
            result = tx.run_tool(f"step-{index}", ["bash", "-c", command])
            if result.returncode != 0: raise RuntimeError(result.stderr or f"AgentTX command failed: {command}")
    finally:
        tx.close(destroy=True)
    return time.perf_counter() - t0

def overhead_modes(n: int) -> Dict[str, Callable[[Path, List[str]], float]]:
    def bare(ws, commands):
        t0 = time.perf_counter()
        for command in commands:
            cp = run_command(command, ws)
            if cp.returncode: raise RuntimeError(cp.stderr)
        return time.perf_counter() - t0
    def per_call(ws, commands):
        t0 = time.perf_counter()
        for command in commands:
            scratch = Path(tempfile.mkdtemp(prefix="agenttx-pc-", dir="/tmp"))
            try:
                cp = run_try(command, ws, scratch / "sandbox", shared=False)
                if cp.returncode: raise RuntimeError(cp.stderr)
            finally: cleanup(scratch)
        return time.perf_counter() - t0
    def session(ws, commands):
        scratch = Path(tempfile.mkdtemp(prefix="agenttx-session-", dir="/tmp"))
        try:
            t0 = time.perf_counter(); cp = run_session_try(commands, ws, scratch / "sandbox")
            if cp.returncode: raise RuntimeError(cp.stderr)
            return time.perf_counter() - t0
        finally: cleanup(scratch)
    def shared(ws, commands):
        scratch = Path(tempfile.mkdtemp(prefix="agenttx-shared-", dir="/tmp"))
        try:
            t0 = time.perf_counter()
            for command in commands:
                cp = run_try(command, ws, scratch / "sandbox", shared=True)
                if cp.returncode: raise RuntimeError(cp.stderr)
            return time.perf_counter() - t0
        finally: cleanup(scratch)
    def checkpoint(ws, commands):
        scratch = Path(tempfile.mkdtemp(prefix="agenttx-checkpoint-", dir="/tmp"))
        try:
            t0 = time.perf_counter(); pool = SharedSemisolate(workspace=ws, sandbox_dir=scratch / "sandbox", trace_reads=False)
            try:
                for command in commands:
                    result = pool.run(["bash", "-c", command])
                    if result.returncode: raise RuntimeError(result.stderr)
            finally: pool.close(destroy=True)
            return time.perf_counter() - t0
        finally: cleanup(scratch)
    def bwrap(ws, commands):
        t0 = time.perf_counter(); cp = run_bubblewrap(commands)
        if cp.returncode: raise RuntimeError(cp.stderr)
        return time.perf_counter() - t0
    def tx_off(ws, commands):
        scratch = Path(tempfile.mkdtemp(prefix="agenttx-off-", dir="/tmp"))
        try: return run_agenttx(commands, ws, scratch / "session", trace=False)
        finally: cleanup(scratch)
    def tx_full(ws, commands):
        scratch = Path(tempfile.mkdtemp(prefix="agenttx-full-", dir="/tmp"))
        try: return run_agenttx(commands, ws, scratch / "session", trace=True)
        finally: cleanup(scratch)
    return {"bare": bare, "per_call_try": per_call, "session_try": session, "shared_try": shared, "shared_checkpoint": checkpoint, "bubblewrap": bwrap, "agenttx_without_read_tracing": tx_off, "agenttx_full": tx_full}

def run_overhead(repeats: int, n: int) -> List[dict]:
    rows=[]; commands=[f"echo {i} >> out.txt" for i in range(n)]
    for mode, fn in overhead_modes(n).items():
        samples=[]; supported=True; note=""
        for _ in range(repeats):
            scratch=Path(tempfile.mkdtemp(prefix=f"agenttx-matrix-{mode}-", dir="/tmp")); ws=scratch / "ws"; ws.mkdir()
            try:
                samples.append(fn(ws, commands))
            except (FileNotFoundError, RuntimeError, subprocess.SubprocessError) as exc:
                if mode == "bubblewrap": supported=False; note=f"unavailable: {str(exc).strip()[:180]}"; break
                raise
            finally: cleanup(scratch)
        if not samples:
            rows.append({"suite":"overhead","mode":mode,"supported":supported,"repeats":repeats,"n_calls":n,"wall_s_mean":"","wall_s_stdev":"","per_step_ms":"","note":note}); continue
        mean=statistics.mean(samples); stdev=statistics.stdev(samples) if len(samples)>1 else 0.0
        rows.append({"suite":"overhead","mode":mode,"supported":supported,"repeats":len(samples),"n_calls":n,"wall_s_mean":round(mean,6),"wall_s_stdev":round(stdev,6),"per_step_ms":round(mean/n*1000.0,3),"note":note})
        print(f"overhead {mode:32s} {mean:.3f}s +/- {stdev:.3f}s", flush=True)
    return rows

def marker_state(ws: Path) -> dict:
    return {name:(ws/name).exists() for name in ("a.txt","b.txt","c.txt")}

def run_recovery(mode: str) -> dict:
    scratch=Path(tempfile.mkdtemp(prefix=f"agenttx-recovery-{mode}-", dir="/tmp")); ws=scratch / "ws"; ws.mkdir()
    try:
        before={}; after={}; exit_codes=[]; note=""
        if mode == "bare":
            for _, command in TRAJECTORY: exit_codes.append(run_command(command, ws).returncode)
            before=marker_state(ws)
            for name in ("a.txt","b.txt","c.txt"): (ws/name).unlink(missing_ok=True)
            after=marker_state(ws); note="host writes visible before recovery; whole cleanup loses c"
        elif mode == "per_call_try":
            for _, command in TRAJECTORY:
                scratch_step=Path(tempfile.mkdtemp(prefix="agenttx-pc-step-", dir="/tmp"))
                try: exit_codes.append(run_try(command, ws, scratch_step/"sandbox", shared=False).returncode)
                finally: cleanup(scratch_step)
            before=marker_state(ws); after=marker_state(ws); note="isolated calls cannot pass a from step 0 to step 1"
        elif mode == "session_try":
            cp=run_session_try([command for _,command in TRAJECTORY], ws, scratch/"session"); exit_codes.append(cp.returncode); before=marker_state(ws); after=marker_state(ws); note="one session can see state, but abort discards the whole session"
        elif mode == "shared_try":
            session=scratch/"session"
            for _, command in TRAJECTORY: exit_codes.append(run_try(command, ws, session, shared=True).returncode)
            before=marker_state(ws); cleanup(session); after=marker_state(ws); note="shared upperdir preserves state, but recovery is whole-session discard"
        elif mode == "shared_checkpoint":
            pool=SharedSemisolate(workspace=ws, sandbox_dir=scratch/"session", trace_reads=False)
            try:
                for _, command in TRAJECTORY: exit_codes.append(pool.run(["bash","-c",command]).returncode)
                before=marker_state(ws); pool.rollback_steps([0,1,2]); after=marker_state(ws)
            finally: pool.close(destroy=True)
            note="full checkpoint restore removes independent c with the failed prefix"
        elif mode in {"agenttx_without_read_tracing","agenttx_full"}:
            trace=mode == "agenttx_full"; tx=AgentTX.begin(workdir=ws, session_dir=scratch/"session", trace_reads=trace)
            try:
                records=[]
                for name, command in TRAJECTORY: records.append(tx.run_tool(name,["bash","-c",command]))
                failed=tx.run_tool("failure",["bash","-c","false"]); exit_codes=[r.returncode for r in records]+[failed.returncode]; before=marker_state(ws)
                targets=tx.rollback_causal(records[0].step_id); active=[s.step_id for s in tx.ledger.steps if s.status != "rolled_back"]
                if active: tx.commit(max(active))
                after=marker_state(ws); note=f"causal targets={targets}; read tracing={trace}"
            finally: tx.close(destroy=True)
        elif mode == "bubblewrap":
            cp=run_bubblewrap([command for _,command in TRAJECTORY]); exit_codes=[cp.returncode]; before=marker_state(ws); after=marker_state(ws); note="whole-session namespace abort; no causal retention"
        else: raise ValueError(mode)
        causal_correct=after == {"a.txt":False,"b.txt":False,"c.txt":True}
        return {"suite":"recovery","mode":mode,"supported":True,"exit_codes":exit_codes,"host_before_recovery":before,"host_after_recovery":after,"host_clean_before_recovery":not any(before.values()),"causal_retention_correct":causal_correct,"note":note}
    except (FileNotFoundError, RuntimeError, subprocess.SubprocessError) as exc:
        if mode == "bubblewrap": return {"suite":"recovery","mode":mode,"supported":False,"exit_codes":[],"host_before_recovery":{},"host_after_recovery":{},"host_clean_before_recovery":"","causal_retention_correct":"","note":f"unavailable: {str(exc).strip()[:180]}"}
        raise
    finally: cleanup(scratch)

def write_outputs(rows: List[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True); csv_path=OUT/"comparison_matrix.csv"; fields=sorted({key for row in rows for key in row})
    with csv_path.open("w",newline="",encoding="utf-8") as handle:
        writer=csv.DictWriter(handle,fieldnames=fields,extrasaction="ignore",lineterminator="\n"); writer.writeheader(); writer.writerows(rows)
    (OUT/"comparison_matrix.json").write_text(json.dumps(rows,indent=2)+"\n",encoding="utf-8")
    overhead=[row for row in rows if row["suite"]=="overhead"]; recovery=[row for row in rows if row["suite"]=="recovery"]
    lines=["# AgentTX comparison matrix","","This artifact separates runtime overhead from causal-recovery semantics.","The recovery workload is fixed: `a -> b`, independent `c`, then failure.","","## Overhead (same 10-write trajectory, 3 repeats)","","| mode | supported | wall mean (s) | stdev (s) | per step (ms) | note |","|---|:---:|---:|---:|---:|---|"]
    for row in overhead: lines.append(f"| {row['mode']} | {row['supported']} | {row['wall_s_mean']} | {row['wall_s_stdev']} | {row['per_step_ms']} | {row.get('note','')} |")
    lines += ["","## Recovery semantics","","| mode | supported | host before recovery | host after recovery | causal retention correct | note |","|---|:---:|---|---|:---:|---|"]
    for row in recovery: lines.append(f"| {row['mode']} | {row['supported']} | `{row['host_before_recovery']}` | `{row['host_after_recovery']}` | {row['causal_retention_correct']} | {row['note']} |")
    (OUT/"comparison_matrix.md").write_text("\n".join(lines)+"\n",encoding="utf-8"); print(f"wrote {csv_path}"); print("\n".join(lines))

def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--repeats",type=int,default=3); parser.add_argument("--n",type=int,default=10); args=parser.parse_args(); rows=run_overhead(args.repeats,args.n)
    for mode in ["bare","per_call_try","session_try","shared_try","shared_checkpoint","bubblewrap","agenttx_without_read_tracing","agenttx_full"]:
        row=run_recovery(mode); rows.append(row); print(f"recovery {mode:32s} correct={row['causal_retention_correct']} supported={row['supported']}",flush=True)
    write_outputs(rows); return 0
if __name__ == "__main__": raise SystemExit(main())