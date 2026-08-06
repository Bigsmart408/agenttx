"""OpenAI-compatible tool-calling coding agent routed through AgentTX."""
from __future__ import annotations
import json, os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from agenttx.harness import CodingAgentHarness
from agenttx.policy import CommitPolicy

def _tool(name, desc, props, required=None):
    params = {"type": "object", "properties": props}
    if required:
        params["required"] = required
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": params}}

TOOLS = [
    _tool("write_file", "Create or overwrite a text file under the workspace.", {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    _tool("append_file", "Append text to a file under the workspace.", {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    _tool("read_file", "Read a text file from the workspace.", {"path": {"type": "string"}}, ["path"]),
    _tool("run_shell", "Run a bash command in the workspace cwd.", {"cmd": {"type": "string"}}, ["cmd"]),
    _tool("run_tests", "Run the project test command.", {"cmd": {"type": "string"}}),
    _tool("delete_file", "Delete a file under the workspace.", {"path": {"type": "string"}}, ["path"]),
    _tool("finish", "Finish the task and optionally request commit.", {"summary": {"type": "string"}, "commit": {"type": "boolean"}}, ["summary"]),
]
SYSTEM = "You are a coding agent in an AgentTX-protected workspace. Use tools for edits. Call finish when done."

@dataclass
class AgentRunResult:
    messages: List[dict]
    tool_calls: int
    finished: bool
    summary: str = ""
    committed: bool = False
    ledger: dict = field(default_factory=dict)

class LLMToolAgent:
    def __init__(self, workdir, model=None, session_dir=None, max_turns=30, api_base=None, api_key=None):
        self.workdir = Path(workdir).resolve()
        self.model = model or os.environ.get("AGENTTX_MODEL") or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self.max_turns = max_turns
        self.api_base = api_base or os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
        self.harness = CodingAgentHarness(workdir=self.workdir, session_dir=session_dir, policy=CommitPolicy(workdir=self.workdir))

    def close(self, destroy=True):
        self.harness.close(destroy=destroy)

    def _client(self):
        from openai import OpenAI
        kw = {}
        if self.api_key:
            kw["api_key"] = self.api_key
        if self.api_base:
            kw["base_url"] = self.api_base
        return OpenAI(**kw)

    def _dispatch(self, name, args):
        if name == "finish":
            return json.dumps({"ok": True, **args})
        if name == "run_tests" and "cmd" not in args:
            args = {**args, "cmd": "PYTHONPATH=. python -m pytest -q"}
        rec = self.harness.call_tool(name, args)
        return json.dumps({
            "exit_code": rec.exit_code,
            "stdout": (rec.stdout or "")[-4000:],
            "stderr": (rec.stderr or "")[-2000:],
            "effects": [e.to_dict() for e in rec.effects],
            "step_id": rec.step_id,
            "parents": rec.parents,
        })

    def run(self, task, commit=False):
        if not self.api_key:
            raise RuntimeError("No API key. Set OPENAI_API_KEY or OPENROUTER_API_KEY.")
        client = self._client()
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}]
        tool_calls = 0
        finished = False
        summary = ""
        want_commit = commit
        for _ in range(self.max_turns):
            resp = client.chat.completions.create(model=self.model, messages=messages, tools=TOOLS, tool_choice="auto")
            msg = resp.choices[0].message
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [{"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}} for tc in (msg.tool_calls or [])] or None,
            })
            if not msg.tool_calls:
                summary = msg.content or ""
                finished = True
                break
            for tc in msg.tool_calls:
                tool_calls += 1
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = self._dispatch(name, args)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
                if name == "finish":
                    finished = True
                    summary = args.get("summary", "")
                    if args.get("commit"):
                        want_commit = True
                    break
            if finished:
                break
        committed = False
        if want_commit and self.harness.tx.ledger.steps:
            up_to = max(s.step_id for s in self.harness.tx.ledger.steps if s.status != "rolled_back")
            self.harness.policy.assert_committable(self.harness.tx.ledger, up_to)
            self.harness.tx.commit(up_to)
            committed = True
        return AgentRunResult(messages=messages, tool_calls=tool_calls, finished=finished, summary=summary, committed=committed, ledger=self.harness.tx.ledger.to_dict())