"""Conversation log bound to the effect ledger.

Filesystem rollback without conversation rewind leaves the model believing
rolled-back tool results still happened.  This module stores TurnRecords at
each LLM/tool boundary and rebuilds an OpenAI-format message list that matches
the *active* ledger after causal or temporal rollback.

This is not CRIU / process checkpointing, and it does not restore hidden
provider chain-of-thought.  External black-box agents still use a retained-
effects manifest handoff; native tool-calling agents resume from this log.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence

from .ledger import EffectKind, Ledger

SCHEMA = "agenttx.conversation/v1"
CONTROL_TOOLS = frozenset(
    {"inspect_ledger", "rollback_causal", "rollback", "finish"}
)
ROLLBACK_TOOLS = frozenset({"rollback_causal", "rollback"})


def canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_payload(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


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
class TurnRecord:
    turn_id: int
    kind: str
    messages: List[dict] = field(default_factory=list)
    step_ids: List[int] = field(default_factory=list)
    tool_names: List[str] = field(default_factory=list)
    status: str = "active"
    request_hash: str = ""
    response_hash: str = ""

    def to_dict(self) -> dict:
        return {
            "turn_id": self.turn_id,
            "kind": self.kind,
            "messages": list(self.messages),
            "step_ids": list(self.step_ids),
            "tool_names": list(self.tool_names),
            "status": self.status,
            "request_hash": self.request_hash,
            "response_hash": self.response_hash,
        }

    @staticmethod
    def from_dict(data: dict) -> "TurnRecord":
        return TurnRecord(
            turn_id=int(data["turn_id"]),
            kind=str(data.get("kind") or "effect"),
            messages=list(data.get("messages") or []),
            step_ids=[int(step_id) for step_id in data.get("step_ids") or []],
            tool_names=[str(name) for name in data.get("tool_names") or []],
            status=str(data.get("status") or "active"),
            request_hash=str(data.get("request_hash") or ""),
            response_hash=str(data.get("response_hash") or ""),
        )


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

    def _next_turn_id(self) -> int:
        if not self.turns:
            return 0
        return self.turns[-1].turn_id + 1

    def append_turn(
        self,
        messages: Sequence[dict],
        step_ids: Optional[Sequence[int]] = None,
        tool_names: Optional[Sequence[str]] = None,
        kind: Optional[str] = None,
    ) -> TurnRecord:
        step_ids = [int(step_id) for step_id in (step_ids or [])]
        tool_names = [str(name) for name in (tool_names or [])]
        stored = [dict(message) for message in messages]
        if kind is None:
            if step_ids:
                kind = "effect"
            elif any(name in CONTROL_TOOLS for name in tool_names):
                kind = "control"
            else:
                kind = "control"
        assistant = next((msg for msg in stored if msg.get("role") == "assistant"), {})
        tools = [msg for msg in stored if msg.get("role") == "tool"]
        turn = TurnRecord(
            turn_id=self._next_turn_id(),
            kind=kind,
            messages=stored,
            step_ids=step_ids,
            tool_names=tool_names,
            request_hash=hash_payload(assistant),
            response_hash=hash_payload(tools),
        )
        self.turns.append(turn)
        return turn

    def invalidate_steps(self, step_ids: Iterable[int]) -> List[int]:
        wanted = {int(step_id) for step_id in step_ids}
        invalidated: List[int] = []
        for turn in self.turns:
            if turn.status != "active":
                continue
            if wanted.intersection(turn.step_ids):
                turn.status = "invalidated"
                invalidated.append(turn.turn_id)
        return invalidated

    def invalidate_stale_control(self) -> List[int]:
        invalidated: List[int] = []
        for turn in self.turns:
            if turn.status != "active":
                continue
            if turn.kind in {"control", "recovery"}:
                turn.status = "invalidated"
                invalidated.append(turn.turn_id)
        return invalidated

    def append_recovery(self, notice: str, mode: str = "causal") -> TurnRecord:
        turn = self.append_turn(
            [{"role": "user", "content": notice}],
            step_ids=[],
            tool_names=[],
            kind="recovery",
        )
        self.last_recovery_mode = mode
        return turn

    def rewind(self, step_ids: Sequence[int], notice: str, mode: str = "causal") -> dict:
        """Drop turns whose effects were rolled back; keep independent later work."""
        if self.is_empty():
            return {
                "rewound": False,
                "invalidated_turn_ids": [],
                "generation": self.generation,
            }
        effect_ids = self.invalidate_steps(step_ids)
        control_ids = self.invalidate_stale_control()
        self.append_recovery(notice, mode=mode)
        self.generation += 1
        return {
            "rewound": True,
            "invalidated_turn_ids": sorted(set(effect_ids + control_ids)),
            "generation": self.generation,
            "mode": mode,
            "retained_step_ids": self.active_step_ids(),
        }

    def active_step_ids(self) -> List[int]:
        step_ids: List[int] = []
        for turn in self.turns:
            if turn.status != "active":
                continue
            step_ids.extend(turn.step_ids)
        return step_ids

    def active_turns(self) -> List[TurnRecord]:
        return [turn for turn in self.turns if turn.status == "active"]

    def active_messages(self) -> List[dict]:
        messages: List[dict] = []
        if self.system:
            messages.append({"role": "system", "content": self.system})
        if self.task:
            messages.append({"role": "user", "content": self.task})
        for turn in self.active_turns():
            messages.extend(dict(message) for message in turn.messages)
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
