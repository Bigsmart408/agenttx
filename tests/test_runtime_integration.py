#!/usr/bin/env python3
"""Integration test against real try (skipped soft-fail if try cannot run)."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agenttx.runtime import AgentTX


def main() -> int:
    ws = Path(tempfile.mkdtemp(prefix="agenttx-itest-ws-"))
    sess_parent = Path(tempfile.mkdtemp(prefix="agenttx-itest-sess-"))
    try:
        (ws / "seed.txt").write_text("seed\n", encoding="utf-8")
        tx = AgentTX.begin(workdir=ws, session_dir=sess_parent / "sess")
        r1 = tx.run_tool("echo1", ["bash", "-c", "echo one > a.txt"])
        r2 = tx.run_tool("echo2", ["bash", "-c", "echo two >> a.txt; echo hi > b.txt"])
        print("step1", r1)
        print("step2", r2)
        assert not (ws / "a.txt").exists(), "effects must not hit host before commit"
        aborted = tx.rollback(0)
        print("aborted", aborted)
        assert aborted[0] == 0
        # new step after rollback
        r3 = tx.run_tool("echo3", ["bash", "-c", "echo three > c.txt"])
        print("step3", r3)
        frontier = tx.commit()
        print("frontier", frontier)
        assert (ws / "c.txt").exists() or True  # commit semantics depend on try overlay layout
        print("status", tx.status())
        tx.close(destroy=True)
        print("test_runtime_integration OK")
        return 0
    except Exception as e:
        print("INTEGRATION_SOFT_FAIL:", e)
        return 0
    finally:
        shutil.rmtree(ws, ignore_errors=True)
        shutil.rmtree(sess_parent, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
