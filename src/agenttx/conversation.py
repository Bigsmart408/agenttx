"""Conversation log bound to the effect ledger.

Filesystem rollback without conversation rewind leaves the model believing
rolled-back tool results still happened.  This module stores per-tool-call
spans, unions ledger parents (path/object overlap) with copy-from-result
token parents, and rebuilds an OpenAI-format message list that matches the
*active* spans after causal or temporal rollback.  ``rewind`` is seeded with
the ledger closure and then expanded along conversation edges, so a read of a
rolled-back write is dropped even when the write produced empty stdout.

This is not CRIU / process checkpointing, and it does not restore hidden
provider chain-of-thought.  External black-box agents still use a retained-
effects manifest handoff; native tool-calling agents resume from this log.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Iterable, List, Mapping, Optional, Sequence, Set

from .ledger import EffectKind, Ledger

SCHEMA = "agenttx.conversation/v2"
CONTROL_TOOLS = frozenset(
    {"inspect_ledger", "rollback_causal", "rollback", "finish"}
)
ROLLBACK_TOOLS = frozenset({"rollback_causal", "rollback"})
MIN_COPY_LEN = 8
_SKIP_TOKENS = CONTROL_TOOLS | {
    "write_file",
    "append_file",
    "read_file",
    "run_shell",
    "run_tests",
    "delete_file",
    "true",
    "false",
    "null",
}
_TOKEN_SPLIT = re.compile(r"[^\w.:/=+-]+")


def canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_payload(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _walk_strings(value: object, acc: List[str]) -> None:
    if isinstance(value, str):
        acc.append(value)
        return
    if isinstance(value, dict):
        for item in value.values():
            _walk_strings(item, acc)
        return
    if isinstance(value, list):
        for item in value:
            _walk_strings(item, acc)


def copied_tokens(*texts: str) -> List[str]:
    """Distinctive strings copied from a tool result into later arguments."""
    found: List[str] = []
    seen = set()
    for text in texts:
        if not text:
            continue
        candidates = [text]
        try:
            parsed = json.loads(text)
        except (TypeError, json.JSONDecodeError):
            parsed = None
        if parsed is not None:
            _walk_strings(parsed, candidates)
        for raw in candidates:
            pieces = [raw]
            pieces.extend(part for part in _TOKEN_SPLIT.split(raw) if part)
            for piece in pieces:
                token = piece.strip()
                if len(token) < MIN_COPY_LEN or token in _SKIP_TOKENS or token in seen:
                    continue
                seen.add(token)
                found.append(token)
    return found


def render_ledger_recovery_notice(
    ledger: Ledger,
    targets: Sequence[int],
    mode: str = "causal",
) -> str:
    """Authoritative post-rollback notice derived from the effect ledger."""
    target_set = {int(step_id) for step_id in targets}
    retained_lines: List[str] = []
    invalidated_lines: List[str] = []
    for step in ledger.steps:
        paths = [
            effect.path
            for effect in step.effects
            if effect.kind in (EffectKind.WRITE, EffectKind.DELETE)
        ]
        path_text = ", ".join(f"`{path}`" for path in paths) or "(no write/delete)"
        line = f"- step {step.step_id} `{step.tool_name}`: {path_text}"
        if step.step_id in target_set or step.status == "rolled_back":
            if step.step_id in target_set:
                invalidated_lines.append(line)
        elif step.status != "rolled_back" and step.step_id > ledger.committed_frontier:
            retained_lines.append(line)
    retained_block = "\n".join(retained_lines) or "- (none)"
    invalidated_block = "\n".join(invalidated_lines) or "- (none)"
    return (
        f"## AgentTX conversation recovery ({mode})\n"
        "The workspace and effect ledger were rolled back. "
        "Tool results from invalidated steps are no longer true.\n\n"
        "### Invalidated steps\n"
        f"{invalidated_block}\n\n"
        "### Retained steps\n"
        f"{retained_block}\n\n"
        "Continue from the retained workspace. Do not recreate retained files. "
        "Do not use invalidated files."
    )


@dataclass
class CallSpan:
    call_id: str
    tool_name: str
    arguments: str = ""
    result: str = ""
    step_id: Optional[int] = None
    parents: List[int] = field(default_factory=list)
    status: str = "active"

    def to_dict(self) -> dict:
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "result": self.result,
            "step_id": self.step_id,
            "parents": list(self.parents),
            "status": self.status,
        }

    @staticmethod
    def from_dict(data: dict) -> "CallSpan":
        step_id = data.get("step_id")
        return CallSpan(
            call_id=str(data.get("call_id") or ""),
            tool_name=str(data.get("tool_name") or ""),
            arguments=str(data.get("arguments") or ""),
            result=str(data.get("result") or ""),
            step_id=None if step_id is None else int(step_id),
            parents=[int(parent) for parent in data.get("parents") or []],
            status=str(data.get("status") or "active"),
        )


@dataclass
class TurnRecord:
    turn_id: int
    kind: str
    messages: List[dict] = field(default_factory=list)
    assistant: dict = field(default_factory=dict)
    calls: List[CallSpan] = field(default_factory=list)
    status: str = "active"
    request_hash: str = ""
    response_hash: str = ""

    @property
    def step_ids(self) -> List[int]:
        return [call.step_id for call in self.calls if call.step_id is not None]

    @property
    def tool_names(self) -> List[str]:
        return [call.tool_name for call in self.calls]

    def active_calls(self) -> List[CallSpan]:
        return [call for call in self.calls if call.status == "active"]

    def refresh_status(self) -> None:
        if self.kind in {"recovery", "user"}:
            return
        if self.calls:
            if any(call.status == "active" for call in self.calls):
                self.status = "active"
            else:
                self.status = "invalidated"

    def to_dict(self) -> dict:
        return {
            "turn_id": self.turn_id,
            "kind": self.kind,
            "messages": list(self.messages),
            "assistant": dict(self.assistant),
            "calls": [call.to_dict() for call in self.calls],
            "step_ids": list(self.step_ids),
            "tool_names": list(self.tool_names),
            "status": self.status,
            "request_hash": self.request_hash,
            "response_hash": self.response_hash,
        }

    @staticmethod
    def from_dict(data: dict) -> "TurnRecord":
        calls = [CallSpan.from_dict(item) for item in data.get("calls") or []]
        messages = list(data.get("messages") or [])
        assistant = dict(data.get("assistant") or {})
        if not calls and messages:
            calls = _spans_from_messages(
                messages,
                step_ids=data.get("step_ids") or [],
                tool_names=data.get("tool_names") or [],
            )
            assistant = _assistant_from_messages(messages)
        turn = TurnRecord(
            turn_id=int(data["turn_id"]),
            kind=str(data.get("kind") or "effect"),
            messages=messages,
            assistant=assistant,
            calls=calls,
            status=str(data.get("status") or "active"),
            request_hash=str(data.get("request_hash") or ""),
            response_hash=str(data.get("response_hash") or ""),
        )
        if turn.status == "invalidated":
            for call in turn.calls:
                call.status = "invalidated"
        return turn


def _assistant_from_messages(messages: Sequence[dict]) -> dict:
    for message in messages:
        if message.get("role") == "assistant":
            return dict(message)
    return {}


def _spans_from_messages(
    messages: Sequence[dict],
    step_ids: Optional[Sequence[int]] = None,
    tool_names: Optional[Sequence[str]] = None,
) -> List[CallSpan]:
    assistant = _assistant_from_messages(messages)
    results = {
        str(message.get("tool_call_id") or ""): str(message.get("content") or "")
        for message in messages
        if message.get("role") == "tool"
    }
    leftover_ids = [int(step_id) for step_id in (step_ids or [])]
    leftover_names = [str(name) for name in (tool_names or [])]
    spans: List[CallSpan] = []
    for index, tool_call in enumerate(assistant.get("tool_calls") or []):
        function = tool_call.get("function") or {}
        call_id = str(tool_call.get("id") or f"call-{index}")
        result = results.get(call_id, "")
        step_id = _step_id_from_result(result)
        if step_id is None and leftover_ids:
            step_id = leftover_ids.pop(0)
        name = str(function.get("name") or "")
        if not name and leftover_names:
            name = leftover_names.pop(0)
        arguments = function.get("arguments") or ""
        if not isinstance(arguments, str):
            arguments = canonical_json(arguments)
        spans.append(
            CallSpan(
                call_id=call_id,
                tool_name=name,
                arguments=arguments,
                result=result,
                step_id=step_id,
                parents=_parents_from_result(result),
            )
        )
    return spans


def _step_id_from_result(result: str) -> Optional[int]:
    payload = _result_payload(result)
    if payload is not None and "step_id" in payload:
        return int(payload["step_id"])
    return None


def _parents_from_result(result: str) -> List[int]:
    payload = _result_payload(result)
    if payload is None:
        return []
    parents = payload.get("parents") or []
    out: List[int] = []
    seen = set()
    for item in parents:
        value = int(item)
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _result_payload(result: str) -> Optional[dict]:
    try:
        payload = json.loads(result)
    except (TypeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


@dataclass
class ConversationLog:
    """Durable native-agent conversation aligned with ledger step ids."""

    system: str = ""
    task: str = ""
    turns: List[TurnRecord] = field(default_factory=list)
    generation: int = 0
    last_recovery_mode: str = ""

    def has_seed(self) -> bool:
        return bool(self.system or self.task)

    def is_empty(self) -> bool:
        return not self.has_seed() and not self.turns

    def seed(self, system: str, task: str) -> None:
        self.system = system
        self.task = task

    def last_followup_text(self) -> str:
        for turn in reversed(self.turns):
            if turn.status != "active" or turn.kind != "user":
                continue
            if turn.messages:
                return str(turn.messages[0].get("content") or "")
        return ""

    def append_followup(self, task: str) -> TurnRecord:
        turn = TurnRecord(
            turn_id=self._next_turn_id(),
            kind="user",
            messages=[{"role": "user", "content": task}],
            status="active",
        )
        self.turns.append(turn)
        return turn

    def _next_turn_id(self) -> int:
        if not self.turns:
            return 0
        return self.turns[-1].turn_id + 1

    def _prior_spans(self) -> List[CallSpan]:
        spans: List[CallSpan] = []
        for turn in self.turns:
            spans.extend(turn.calls)
        return spans

    def infer_parents(self, arguments: str, extra_spans: Optional[Sequence[CallSpan]] = None) -> List[int]:
        parents: List[int] = []
        seen = set()
        for span in list(self._prior_spans()) + list(extra_spans or []):
            if span.step_id is None or span.step_id in seen:
                continue
            tokens = copied_tokens(span.result)
            if any(token and token in arguments for token in tokens):
                parents.append(span.step_id)
                seen.add(span.step_id)
        return parents

    def append_turn(
        self,
        messages: Sequence[dict],
        step_ids: Optional[Sequence[int]] = None,
        tool_names: Optional[Sequence[str]] = None,
        kind: Optional[str] = None,
    ) -> TurnRecord:
        stored = [dict(message) for message in messages]
        assistant = _assistant_from_messages(stored)
        spans = _spans_from_messages(stored, step_ids=step_ids, tool_names=tool_names)
        assigned: List[CallSpan] = []
        for span in spans:
            copied = self.infer_parents(span.arguments, extra_spans=assigned)
            span.parents = sorted(set(span.parents) | set(copied))
            assigned.append(span)
        if kind is None:
            if any(span.step_id is not None for span in spans):
                kind = "effect"
            elif any(span.tool_name in CONTROL_TOOLS for span in spans):
                kind = "control"
            elif assistant:
                kind = "control"
            else:
                kind = "user"
        tools = [message for message in stored if message.get("role") == "tool"]
        turn = TurnRecord(
            turn_id=self._next_turn_id(),
            kind=kind,
            messages=stored,
            assistant=assistant,
            calls=spans,
            request_hash=hash_payload(assistant),
            response_hash=hash_payload(tools),
        )
        self.turns.append(turn)
        return turn

    def conversation_closure(self, step_ids: Iterable[int]) -> Set[int]:
        frontier = {int(step_id) for step_id in step_ids}
        changed = True
        while changed:
            changed = False
            for turn in self.turns:
                for span in turn.calls:
                    if span.step_id is None or span.step_id in frontier:
                        continue
                    if frontier.intersection(span.parents):
                        frontier.add(span.step_id)
                        changed = True
        return frontier

    def invalidate_steps(self, step_ids: Iterable[int]) -> List[int]:
        wanted = self.conversation_closure(step_ids)
        invalidated: List[int] = []
        for turn in self.turns:
            if turn.status != "active":
                continue
            touched = False
            for span in turn.calls:
                if span.status != "active":
                    continue
                if span.step_id is not None and span.step_id in wanted:
                    span.status = "invalidated"
                    touched = True
            if touched:
                turn.refresh_status()
                if turn.status == "invalidated":
                    invalidated.append(turn.turn_id)
        return invalidated

    def invalidate_stale_control(self) -> List[int]:
        invalidated: List[int] = []
        for turn in self.turns:
            if turn.status != "active" or turn.kind == "user":
                continue
            if turn.kind == "recovery":
                for span in turn.calls:
                    span.status = "invalidated"
                turn.status = "invalidated"
                invalidated.append(turn.turn_id)
                continue
            for span in turn.calls:
                if span.status != "active":
                    continue
                if span.step_id is None and span.tool_name in CONTROL_TOOLS:
                    span.status = "invalidated"
            if turn.kind == "control" or not turn.active_calls():
                for span in turn.calls:
                    span.status = "invalidated"
                if turn.calls or turn.kind == "control":
                    turn.status = "invalidated"
                    invalidated.append(turn.turn_id)
                    continue
            turn.refresh_status()
            if turn.status == "invalidated":
                invalidated.append(turn.turn_id)
        return invalidated

    def append_recovery(self, notice: str, mode: str = "causal") -> TurnRecord:
        turn = TurnRecord(
            turn_id=self._next_turn_id(),
            kind="recovery",
            messages=[{"role": "user", "content": notice}],
            status="active",
        )
        self.turns.append(turn)
        self.last_recovery_mode = mode
        return turn

    def rewind(self, step_ids: Sequence[int], notice: str, mode: str = "causal") -> dict:
        """Drop spans whose effects or copied results were rolled back.

        ``step_ids`` should be the ledger closure (causal dependents or the
        temporal suffix).  Token-copy children that the ledger cannot see are
        then added.  The two sets are unioned; neither graph is used alone.
        """
        ledger_set = {int(step_id) for step_id in step_ids}
        recorded = {
            span.step_id
            for turn in self.turns
            for span in turn.calls
            if span.step_id is not None
        }
        if self.is_empty():
            return {
                "rewound": False,
                "invalidated_turn_ids": [],
                "generation": self.generation,
                "conversation_closure_mismatch": len(ledger_set - recorded),
                "ledger_steps_missing_from_conversation": sorted(ledger_set - recorded),
                "copy_only_step_ids": [],
            }
        token_expanded = self.conversation_closure(ledger_set)
        drop = ledger_set | token_expanded
        effect_ids = self.invalidate_steps(drop)
        control_ids = self.invalidate_stale_control()
        self.append_recovery(notice, mode=mode)
        self.generation += 1
        return {
            "rewound": True,
            "invalidated_turn_ids": sorted(set(effect_ids + control_ids)),
            "generation": self.generation,
            "mode": mode,
            "retained_step_ids": self.active_step_ids(),
            "dropped_step_ids": sorted(drop),
            "conversation_closure_mismatch": len(ledger_set - recorded),
            "ledger_steps_missing_from_conversation": sorted(ledger_set - recorded),
            "copy_only_step_ids": sorted(token_expanded - ledger_set),
        }

    def active_step_ids(self) -> List[int]:
        step_ids: List[int] = []
        for turn in self.turns:
            if turn.status != "active":
                continue
            for span in turn.active_calls():
                if span.step_id is not None:
                    step_ids.append(span.step_id)
        return step_ids

    def active_turns(self) -> List[TurnRecord]:
        return [turn for turn in self.turns if turn.status == "active"]

    def _messages_for_turn(self, turn: TurnRecord) -> List[dict]:
        if turn.kind in {"recovery", "user"}:
            return [dict(message) for message in turn.messages]
        active_calls = turn.active_calls()
        if not turn.calls:
            return [dict(message) for message in turn.messages]
        if not active_calls:
            return []
        assistant = dict(turn.assistant or _assistant_from_messages(turn.messages))
        keep_ids = {call.call_id for call in active_calls}
        original_calls = list(assistant.get("tool_calls") or [])
        kept = [item for item in original_calls if item.get("id") in keep_ids]
        assistant["tool_calls"] = kept or None
        messages = [assistant]
        results = {
            str(message.get("tool_call_id") or ""): message
            for message in turn.messages
            if message.get("role") == "tool"
        }
        for call in active_calls:
            if call.call_id in results:
                messages.append(dict(results[call.call_id]))
            else:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.call_id,
                        "content": call.result,
                    }
                )
        return messages

    def active_messages(self) -> List[dict]:
        messages: List[dict] = []
        if self.system:
            messages.append({"role": "system", "content": self.system})
        if self.task:
            messages.append({"role": "user", "content": self.task})
        for turn in self.active_turns():
            messages.extend(self._messages_for_turn(turn))
        return messages

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA,
            "system": self.system,
            "task": self.task,
            "generation": self.generation,
            "last_recovery_mode": self.last_recovery_mode,
            "turns": [turn.to_dict() for turn in self.turns],
        }

    @staticmethod
    def from_dict(data: Optional[dict]) -> "ConversationLog":
        if not data:
            return ConversationLog()
        log = ConversationLog(
            system=str(data.get("system") or ""),
            task=str(data.get("task") or ""),
            generation=int(data.get("generation") or 0),
            last_recovery_mode=str(data.get("last_recovery_mode") or ""),
        )
        log.turns = [TurnRecord.from_dict(item) for item in data.get("turns") or []]
        return log

def record_tool_record(
    conversation: "ConversationLog",
    record: object,
    args: Optional[Mapping[str, object]] = None,
    *,
    max_result_chars: int = 4000,
) -> "TurnRecord":
    """Bind a harness ToolCallRecord to the native conversation log.

    Injected crash trajectories currently go through ``harness.call_tool`` and
    would otherwise be invisible to rewind.  After this bind, causal/temporal
    rollback can drop invalidated spans so the model no longer believes rolled
    back writes still succeeded.
    """
    step_id = int(getattr(record, "step_id"))
    tool_name = str(getattr(record, "tool_name") or "unknown")
    call_id = f"inject-{step_id}"
    arguments = canonical_json(dict(args or {}))
    stdout = str(getattr(record, "stdout", "") or "")
    stderr = str(getattr(record, "stderr", "") or "")
    if len(stdout) > max_result_chars:
        stdout = stdout[:max_result_chars] + "\n…truncated…"
    if len(stderr) > max_result_chars:
        stderr = stderr[:max_result_chars] + "\n…truncated…"
    parents = []
    seen = set()
    for item in getattr(record, "parents", None) or []:
        value = int(item)
        if value in seen:
            continue
        seen.add(value)
        parents.append(value)
    result = canonical_json(
        {
            "step_id": step_id,
            "returncode": int(getattr(record, "returncode", 0) or 0),
            "stdout": stdout,
            "stderr": stderr,
            "parents": parents,
        }
    )
    assistant = {
        "role": "assistant",
        "content": f"[agenttx-injected] {tool_name} step {step_id}",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": tool_name, "arguments": arguments},
            }
        ],
    }
    tool = {
        "role": "tool",
        "tool_call_id": call_id,
        "content": result,
    }
    return conversation.append_turn(
        [assistant, tool],
        step_ids=[step_id],
        tool_names=[tool_name],
        kind="effect",
    )

