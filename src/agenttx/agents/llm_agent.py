"""OpenAI-compatible and Anthropic Messages tool-calling agent routed through AgentTX."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from agenttx.harness import CodingAgentHarness
from agenttx.policy import CommitPolicy
from agenttx.providers import ProviderProfile, load_provider_env, resolve_provider


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
    _tool(
        "inspect_ledger",
        "Inspect AgentTX step ids, dependency parents, effects, and rollback status before choosing a recovery action.",
        {},
    ),
    _tool(
        "rollback_causal",
        "Roll back one faulty step and every ledger descendant while retaining independent later work.",
        {"step_id": {"type": "integer"}},
        ["step_id"],
    ),
    _tool("finish", "Finish the task and optionally request commit.", {"summary": {"type": "string"}, "commit": {"type": "boolean"}}, ["summary"]),
]
SYSTEM = "You are a coding agent in an AgentTX-protected workspace. Use tools for edits. When recovering from a failed action, inspect the effect ledger and prefer causal rollback so independent later work survives. Call finish when done."


@dataclass
class AgentRunResult:
    messages: List[dict]
    tool_calls: int
    finished: bool
    summary: str = ""
    committed: bool = False
    ledger: dict = field(default_factory=dict)
    control_events: List[dict] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


def _httpx_client(timeout: float):
    import httpx

    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or os.environ.get("HTTP_PROXY")
    if proxy:
        try:
            return httpx.Client(proxy=proxy, timeout=timeout)
        except TypeError:
            return httpx.Client(proxies=proxy, timeout=timeout)
    return httpx.Client(timeout=timeout)


def _anthropic_tools() -> List[dict]:
    converted = []
    for item in TOOLS:
        fn = item["function"]
        converted.append(
            {
                "name": fn["name"],
                "description": fn.get("description") or "",
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return converted


def _to_anthropic_messages(messages: List[dict]) -> tuple[List[dict], List[dict]]:
    system_parts: List[str] = []
    anth: List[dict] = []
    idx = 0
    while idx < len(messages):
        msg = messages[idx]
        role = msg.get("role")
        if role == "system":
            system_parts.append(msg.get("content") or "")
            idx += 1
            continue
        if role == "tool":
            results = []
            while idx < len(messages) and messages[idx].get("role") == "tool":
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": messages[idx].get("tool_call_id"),
                        "content": messages[idx].get("content") or "",
                    }
                )
                idx += 1
            anth.append({"role": "user", "content": results})
            continue
        if role == "assistant":
            blocks: List[dict] = []
            text = msg.get("content") or ""
            if text:
                blocks.append({"type": "text", "text": text})
            for tc in msg.get("tool_calls") or []:
                raw_args = (tc.get("function") or {}).get("arguments") or "{}"
                if isinstance(raw_args, str):
                    try:
                        parsed = json.loads(raw_args)
                    except json.JSONDecodeError:
                        parsed = {}
                else:
                    parsed = raw_args
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id"),
                        "name": (tc.get("function") or {}).get("name"),
                        "input": parsed if isinstance(parsed, dict) else {},
                    }
                )
            anth.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            idx += 1
            continue
        anth.append({"role": "user", "content": msg.get("content") or ""})
        idx += 1
    system_text = "\n\n".join(part for part in system_parts if part).strip() or SYSTEM
    attribution = os.environ.get(
        "AGENTTX_ANTHROPIC_ATTRIBUTION",
        "x-anthropic-billing-header: cc_version=2.1.83.c50; cc_entrypoint=cli; cch=00000;",
    ).strip()
    system = []
    if attribution:
        system.append({"type": "text", "text": attribution})
    system.append({"type": "text", "text": system_text})
    return system, anth


class LLMToolAgent:
    def __init__(self, workdir, model=None, session_dir=None, max_turns=30, api_base=None, api_key=None, provider=None, trace_backend="auto"):
        self.workdir = Path(workdir).resolve()
        load_provider_env()
        self.provider: ProviderProfile = resolve_provider(provider)
        self.model = model or self.provider.model
        self.max_turns = max_turns
        self.api_base = api_base or self.provider.base_url or os.environ.get("OPENAI_API_BASE")
        self.api_key = api_key or self.provider.api_key
        self.trace_backend = trace_backend
        self.harness = CodingAgentHarness(
            workdir=self.workdir,
            session_dir=session_dir,
            policy=CommitPolicy(workdir=self.workdir),
            trace_backend=trace_backend,
        )
        self.control_events: List[dict] = []

    def close(self, destroy=True):
        self.harness.close(destroy=destroy)

    def _openrouter_headers(self) -> Dict[str, str]:
        return {
            "HTTP-Referer": os.environ.get("OPENROUTER_HTTP_REFERER", "https://openrouter.ai"),
            "X-Title": os.environ.get("OPENROUTER_APP_TITLE", "AgentTX"),
        }

    def _timeout(self) -> float:
        return float(os.environ.get("AGENTTX_API_TIMEOUT_S", "180"))

    def _client(self):
        from openai import OpenAI

        kw = {
            "timeout": self._timeout(),
            "max_retries": int(os.environ.get("AGENTTX_API_MAX_RETRIES", "2")),
        }
        if self.api_key:
            kw["api_key"] = self.api_key
        if self.api_base:
            kw["base_url"] = self.api_base
        if self.provider.name == "openrouter":
            kw["default_headers"] = self._openrouter_headers()
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or os.environ.get("HTTP_PROXY")
        if proxy:
            import httpx

            timeout = kw.get("timeout", 180)
            try:
                kw["http_client"] = httpx.Client(proxy=proxy, timeout=timeout)
            except TypeError:
                kw["http_client"] = httpx.Client(proxies=proxy, timeout=timeout)
        return OpenAI(**kw)

    def _completion_kwargs(self, messages):
        kwargs = {
            "model": self.model,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
        }
        if self.provider.name == "openrouter":
            kwargs["extra_headers"] = self._openrouter_headers()
            kwargs["extra_body"] = {
                "provider": {
                    "require_parameters": True,
                    "allow_fallbacks": True,
                }
            }
        return kwargs

    def _anthropic_complete(self, messages: List[dict]) -> dict:
        system, anth_messages = _to_anthropic_messages(messages)
        base = (self.api_base or "https://jojocode.com").rstrip("/")
        body = {
            "model": self.model,
            "max_tokens": int(os.environ.get("AGENTTX_ANTHROPIC_MAX_TOKENS", "8192")),
            "system": system,
            "messages": anth_messages,
            "tools": _anthropic_tools(),
            "tool_choice": {"type": "auto"},
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": os.environ.get("ANTHROPIC_VERSION", "2023-06-01"),
            "Content-Type": "application/json",
        }
        billing = os.environ.get(
            "AGENTTX_ANTHROPIC_BILLING_HEADER",
            "cc_version=2.1.83.c50; cc_entrypoint=cli; cch=00000;",
        ).strip()
        if billing:
            headers["x-anthropic-billing-header"] = billing
        timeout = self._timeout()
        with _httpx_client(timeout) as client:
            resp = client.post(f"{base}/v1/messages", headers=headers, json=body)
        try:
            payload = resp.json()
        except Exception:
            payload = {"error": {"message": resp.text[:400]}}
        if resp.status_code >= 400:
            err = payload.get("error") if isinstance(payload, dict) else payload
            message = err.get("message") if isinstance(err, dict) else str(err)
            raise RuntimeError(f"anthropic HTTP {resp.status_code}: {message}")
        return payload

    def _parse_anthropic_message(self, payload: dict) -> tuple[str, List[dict], dict]:
        text_parts = []
        tool_calls = []
        for block in payload.get("content") or []:
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text") or "")
            elif btype == "tool_use":
                tool_calls.append(
                    {
                        "id": block.get("id"),
                        "type": "function",
                        "function": {
                            "name": block.get("name"),
                            "arguments": json.dumps(block.get("input") or {}),
                        },
                    }
                )
        usage = payload.get("usage") or {}
        return "".join(text_parts), tool_calls, usage

    def _dispatch(self, name, args):
        if name == "finish":
            return json.dumps({"ok": True, **args})
        if name == "inspect_ledger":
            ledger = self.harness.tx.ledger.to_dict()
            steps = [
                {
                    "step_id": step["step_id"],
                    "tool_name": step["tool_name"],
                    "status": step["status"],
                    "parents": step.get("parents", []),
                    "effects": step.get("effects", []),
                }
                for step in ledger.get("steps", [])
            ]
            event = {"tool": name, "step_count": len(steps)}
            self.control_events.append(event)
            return json.dumps(
                {
                    "committed_frontier": ledger.get("committed_frontier", -1),
                    "steps": steps,
                }
            )
        if name == "rollback_causal":
            step_id = int(args["step_id"])
            targets = self.harness.tx.rollback_causal(step_id)
            active = [
                step.step_id
                for step in self.harness.tx.ledger.steps
                if step.status != "rolled_back"
                and step.step_id > self.harness.tx.ledger.committed_frontier
            ]
            event = {
                "tool": name,
                "step_id": step_id,
                "targets": list(targets),
                "active_step_ids": active,
            }
            self.control_events.append(event)
            return json.dumps({"ok": True, **event})
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

    def _one_turn(self, client, messages: List[dict]) -> tuple[str, List[dict], dict]:
        if self.provider.name == "anthropic":
            payload = self._anthropic_complete(messages)
            text, tool_calls, usage = self._parse_anthropic_message(payload)
            return text, tool_calls, {
                "prompt_tokens": int(usage.get("input_tokens") or 0),
                "completion_tokens": int(usage.get("output_tokens") or 0),
            }
        resp = client.chat.completions.create(**self._completion_kwargs(messages))
        usage = getattr(resp, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage is not None else 0
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0) if usage is not None else 0
        msg = resp.choices[0].message
        tool_calls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
            }
            for tc in (msg.tool_calls or [])
        ]
        return msg.content or "", tool_calls, {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }

    def run(self, task, commit=False):
        if not self.api_key:
            raise RuntimeError(
                f"No API key for provider {self.provider.name}. "
                f"Set the corresponding {self.provider.name.upper()}_API_KEY."
            )
        self.control_events = []
        client = None if self.provider.name == "anthropic" else self._client()
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}]
        tool_calls = 0
        finished = False
        summary = ""
        want_commit = commit
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        for _ in range(self.max_turns):
            text, calls, usage = self._one_turn(client, messages)
            prompt_tokens += int(usage.get("prompt_tokens") or 0)
            completion_tokens += int(usage.get("completion_tokens") or 0)
            messages.append(
                {
                    "role": "assistant",
                    "content": text,
                    "tool_calls": calls or None,
                }
            )
            if not calls:
                summary = text
                finished = True
                break
            for tc in calls:
                tool_calls += 1
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = self._dispatch(name, args)
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
                if name == "finish":
                    finished = True
                    summary = args.get("summary", "")
                    if args.get("commit"):
                        want_commit = True
                    break
            if finished:
                break
        total_tokens = prompt_tokens + completion_tokens
        committed = False
        active = [
            step.step_id
            for step in self.harness.tx.ledger.steps
            if step.status != "rolled_back"
            and step.step_id > self.harness.tx.ledger.committed_frontier
        ]
        if want_commit and active:
            up_to = max(active)
            self.harness.policy.assert_committable(self.harness.tx.ledger, up_to)
            self.harness.tx.commit(up_to)
            committed = True
        return AgentRunResult(
            messages=messages,
            tool_calls=tool_calls,
            finished=finished,
            summary=summary,
            committed=committed,
            ledger=self.harness.tx.ledger.to_dict(),
            control_events=list(self.control_events),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
