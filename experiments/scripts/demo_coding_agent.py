#!/usr/bin/env python3
"""Step 4 demo: coding-agent harness + policy-gated commit."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from agenttx.harness import CodingAgentHarness
from agenttx.policy import CommitPolicy
from experiments.workloads.coding_traj import build_coding_trajectory, seed_repo


def main() -> int:
    scratch = Path(tempfile.mkdtemp(prefix="agenttx-coding-", dir="/tmp"))
    ws = scratch / "ws"
    ws.mkdir()
    seed_repo(ws)
    try:
        h = CodingAgentHarness(
            workdir=ws,
            session_dir=scratch / "sess",
            policy=CommitPolicy(workdir=ws),
        )
        steps = build_coding_trajectory()
        print(f"trajectory steps: {len(steps)}")
        assert len(steps) >= 24, len(steps)
        result = h.run_trajectory(steps, commit=False)
        print(f"ran {len(result.records)} tools in {result.wall_s:.3f}s")
        print(f"failures={sum(1 for r in result.records if r.returncode != 0)}")
        # host should not see speculative writes
        assert not (ws / "notes" / "step1.md").exists()
        # policy commit
        up_to = result.records[-1].step_id
        h.policy.assert_committable(h.tx.ledger, up_to)
        h.tx.commit(up_to)
        assert (ws / "notes" / "step1.md").exists()
        out = ROOT / "experiments" / "results" / "coding_agent_ledger.json"
        h.dump_ledger(out)
        print(f"wrote {out}")
        # deny dangerous commit attempt in a fresh ledger sense: policy unit covers it
        h.close(destroy=True)
        print("demo_coding_agent: ok")
        return 0
    finally:
        subprocess.run(["bash", "-lc", f"chmod -R u+rwX '{scratch}' 2>/dev/null || true"], check=False)
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
