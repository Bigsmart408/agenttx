#!/usr/bin/env python3
"""Step 3 demo: cascade rollback restores upperdir without full session reset."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agenttx.runtime import AgentTX


def main() -> int:
    scratch = Path(tempfile.mkdtemp(prefix="agenttx-surg-", dir="/tmp"))
    ws = scratch / "ws"
    ws.mkdir()
    (ws / "seed.txt").write_text("seed\n", encoding="utf-8")
    try:
        tx = AgentTX.begin(workdir=ws, session_dir=scratch / "sess")
        tx.run_tool("w0", ["bash", "-c", "echo one > a.txt"])
        tx.run_tool("w1", ["bash", "-c", "echo two > b.txt"])
        tx.run_tool("w2", ["bash", "-c", "echo three > c.txt"])
        upper = tx.pool.sandbox_dir / "upperdir"
        assert (upper / str(ws / "a.txt").lstrip("/")).exists() or any(upper.rglob("a.txt"))
        aborted = tx.rollback(1)  # abort b and dependents (c)
        print("aborted", aborted)
        files = sorted(p.name for p in upper.rglob("*") if p.is_file())
        print("upper files after rollback:", files)
        # a.txt should remain; b/c should be gone
        names = set(files)
        assert "a.txt" in names
        assert "b.txt" not in names
        assert "c.txt" not in names
        # can continue
        tx.run_tool("w3", ["bash", "-c", "echo four > d.txt"])
        files2 = sorted(p.name for p in upper.rglob("*") if p.is_file())
        print("upper files after continue:", files2)
        assert "d.txt" in set(files2)
        tx.close(destroy=True)
        print("demo_surgical_rollback: ok")
        return 0
    finally:
        subprocess.run(["bash", "-lc", f"chmod -R u+rwX '{scratch}' 2>/dev/null || true"], check=False)
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
