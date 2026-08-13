"""CLI for AgentTX-native LLM tool agent and Aider baseline."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

def cmd_llm(args):
    from agenttx.agents.llm_agent import LLMToolAgent
    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    agent = LLMToolAgent(workdir=workdir, model=args.model, provider=args.provider, max_turns=args.max_turns)
    try:
        result = agent.run(args.task, commit=args.commit)
        print(json.dumps({
            "finished": result.finished,
            "tool_calls": result.tool_calls,
            "summary": result.summary,
            "committed": result.committed,
            "steps": len(result.ledger.get("steps", [])),
        }, indent=2))
        if args.dump_ledger:
            Path(args.dump_ledger).write_text(json.dumps(result.ledger, indent=2) + "\n", encoding="utf-8")
            print(f"ledger -> {args.dump_ledger}")
        return 0 if result.finished else 2
    finally:
        agent.close(destroy=not args.keep_session)

def cmd_aider(args):
    import shutil, subprocess
    if not shutil.which("aider"):
        print("aider not found. Activate conda env agenttx.", file=sys.stderr)
        return 1
    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    cmd = ["aider", "--yes", "--message", args.task]
    if args.model:
        cmd.extend(["--model", args.model])
    print("NOTE: Aider edits files directly (baseline). For intercepted tools use: agenttx-agent llm ...", file=sys.stderr)
    return subprocess.call(cmd, cwd=str(workdir))

def main(argv=None):
    p = argparse.ArgumentParser(prog="agenttx-agent")
    sp = p.add_subparsers(dest="cmd", required=True)
    l = sp.add_parser("llm", help="AgentTX-native tool-calling agent (intercepted)")
    l.add_argument("--task", required=True)
    l.add_argument("--workdir", default=".")
    l.add_argument("--model", default=None)
    l.add_argument("--provider", choices=["deepseek", "openai", "openrouter"], default=None)
    l.add_argument("--max-turns", type=int, default=30)
    l.add_argument("--commit", action="store_true")
    l.add_argument("--dump-ledger", default=None)
    l.add_argument("--keep-session", action="store_true")
    l.set_defaults(func=cmd_llm)
    a = sp.add_parser("aider", help="Run installed open-source Aider (baseline, not intercepted)")
    a.add_argument("--task", required=True)
    a.add_argument("--workdir", default=".")
    a.add_argument("--model", default=None)
    a.set_defaults(func=cmd_aider)
    args = p.parse_args(argv)
    return args.func(args)

if __name__ == "__main__":
    raise SystemExit(main())
