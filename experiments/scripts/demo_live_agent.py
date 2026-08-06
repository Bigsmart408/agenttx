#!/usr/bin/env python3
"""Live DeepSeek/OpenAI-compatible agent through AgentTX (requires API key)."""
from __future__ import annotations
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

def main() -> int:
    envfile = Path.home() / ".agenttx_llm.env"
    if envfile.exists():
        for line in envfile.read_text(encoding="utf-8").splitlines():
            line=line.strip()
            if line.startswith("export "):
                line=line[len("export "):]
            if "=" in line and not line.startswith("#"):
                k,v=line.split("=",1)
                os.environ.setdefault(k.strip(), v.strip().strip("'").strip('"'))
    if not (os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENROUTER_API_KEY")):
        print("skip: no API key", file=sys.stderr)
        return 0

    from agenttx.agents.llm_agent import LLMToolAgent
    scratch = Path(tempfile.mkdtemp(prefix="agenttx-live-", dir="/tmp"))
    ws = scratch / "ws"
    ws.mkdir()
    (ws/"src").mkdir(); (ws/"tests").mkdir()
    (ws/"src"/"calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (ws/"tests"/"test_calc.py").write_text(
        "from src.calc import add\n\ndef test_add():\n    assert add(1,2)==3\n", encoding="utf-8")
    out = ROOT / "experiments" / "results" / "live_agent_ledger.json"
    try:
        agent = LLMToolAgent(workdir=ws, session_dir=scratch/"sess", max_turns=20)
        result = agent.run(
            "Add mul(a,b) to src/calc.py and test_mul in tests/test_calc.py. "
            "Run: PYTHONPATH=. python -m pytest -q. Call finish with commit=false.",
            commit=False,
        )
        print(json.dumps({"finished": result.finished, "tool_calls": result.tool_calls,
                          "summary": result.summary, "steps": len(result.ledger.get("steps",[]))}, indent=2))
        host = (ws/"src"/"calc.py").read_text(encoding="utf-8")
        assert "mul" not in host, "effects leaked to host before commit"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result.ledger, indent=2)+"\n", encoding="utf-8")
        print(f"wrote {out}")
        up = max(s["step_id"] for s in result.ledger["steps"] if s.get("status")!="rolled_back")
        agent.harness.policy.assert_committable(agent.harness.tx.ledger, up)
        agent.harness.tx.commit(up)
        host2 = (ws/"src"/"calc.py").read_text(encoding="utf-8")
        assert "mul" in host2, "commit did not apply mul"
        print("commit_ok: mul present on host")
        agent.close(destroy=True)
        print("demo_live_agent: ok")
        return 0
    finally:
        subprocess.run(["bash","-lc", f"chmod -R u+rwX '{scratch}' 2>/dev/null || true"], check=False)
        shutil.rmtree(scratch, ignore_errors=True)

if __name__ == "__main__":
    raise SystemExit(main())
