#!/usr/bin/env python3
"""Demo: multi-step trajectory in a shared semisolate with ledger edges."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agenttx.runtime import AgentTXRuntime


def main() -> int:
    print("demo: start", flush=True)
    scratch = Path(tempfile.mkdtemp(prefix="agenttx-demo-", dir="/tmp"))
    ws = scratch / "ws"
    ws.mkdir()
    (ws / "seed.txt").write_text("seed\n", encoding="utf-8")
    try:
        with AgentTXRuntime(workspace=ws) as rt:
            rt.run_tool("write_a", ["bash", "-lc", "echo hello > a.txt"])
            rt.run_tool(
                "read_a_write_b",
                ["bash", "-lc", "cat a.txt > b.txt"],
                extra_reads=[str((ws / "a.txt").resolve())],
            )
            rt.run_tool("write_c", ["bash", "-lc", "echo c > c.txt"])
            print("steps:")
            for s in rt.ledger.steps:
                print(
                    f"  {s.step_id} {s.tool_name} parents={sorted(s.parents)} effects={s.effects}"
                )
            print("host before commit:", sorted(p.name for p in ws.iterdir()))
            assert not (ws / "a.txt").exists()
            cascade = rt.ledger.cascade_rollback_targets(0)
            print("cascade_rollback_targets(0) =", cascade)
            out = ROOT / "experiments" / "results" / "demo_ledger.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            rt.dump_ledger(out)
            print(f"wrote {out}")
            print("demo_trajectory: ok")
            return 0
    finally:
        subprocess.run(
            ["bash", "-lc", f"chmod -R u+rwX '{scratch}' 2>/dev/null || true"],
            check=False,
        )
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())