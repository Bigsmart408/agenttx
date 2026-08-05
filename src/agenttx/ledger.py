"""Causal effect ledger for multi-step agent trajectories."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable, List, Optional, Set


class EffectKind(str, Enum):
    READ = "R"
    WRITE = "W"
    DELETE = "D"
    NEGATIVE = "N"


@dataclass(frozen=True)
class Effect:
    path: str
    kind: EffectKind

    def to_dict(self) -> dict:
        return {"path": self.path, "kind": self.kind.value}

    @staticmethod
    def from_dict(d: dict) -> "Effect":
        return Effect(path=d["path"], kind=EffectKind(d["kind"]))


@dataclass
class Step:
    step_id: int
    tool_name: str
    effects: List[Effect] = field(default_factory=list)
    parents: Set[int] = field(default_factory=set)
    status: str = "speculative"

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "tool_name": self.tool_name,
            "effects": [e.to_dict() for e in self.effects],
            "parents": sorted(self.parents),
            "status": self.status,
        }

    @staticmethod
    def from_dict(d: dict) -> "Step":
        return Step(
            step_id=d["step_id"],
            tool_name=d["tool_name"],
            effects=[Effect.from_dict(e) for e in d.get("effects", [])],
            parents=set(d.get("parents", [])),
            status=d.get("status", "speculative"),
        )


@dataclass
class Ledger:
    steps: List[Step] = field(default_factory=list)
    committed_frontier: int = -1

    def _uncommitted(self) -> Iterable[Step]:
        return (
            s
            for s in self.steps
            if s.step_id > self.committed_frontier and s.status != "rolled_back"
        )

    def _writer_index(self) -> Dict[str, int]:
        written: Dict[str, int] = {}
        for prev in self._uncommitted():
            for e in prev.effects:
                if e.kind in (EffectKind.WRITE, EffectKind.DELETE):
                    written[e.path] = prev.step_id
        return written

    def add_step(self, tool_name: str, effects: Optional[List[Effect]] = None) -> Step:
        effects = effects or []
        step = Step(step_id=len(self.steps), tool_name=tool_name, effects=list(effects))
        written = self._writer_index()
        for e in step.effects:
            if e.kind == EffectKind.READ and e.path in written:
                step.parents.add(written[e.path])
            if e.kind in (EffectKind.WRITE, EffectKind.DELETE) and e.path in written:
                step.parents.add(written[e.path])
            if e.kind == EffectKind.WRITE:
                for prev in self._uncommitted():
                    for pe in prev.effects:
                        if pe.kind == EffectKind.NEGATIVE and pe.path == e.path:
                            step.parents.add(prev.step_id)
        self.steps.append(step)
        return step

    def causal_dependents(self, failed_step_id: int) -> List[int]:
        """Graph closure: failed step plus steps that transitively depend via parents."""
        if failed_step_id < 0 or failed_step_id >= len(self.steps):
            raise ValueError(f"invalid step id {failed_step_id}")
        dependents = {failed_step_id}
        changed = True
        while changed:
            changed = False
            for s in self.steps:
                if s.step_id in dependents or s.status == "rolled_back":
                    continue
                if s.parents & dependents:
                    dependents.add(s.step_id)
                    changed = True
        return sorted(dependents)

    def cascade_rollback_targets(self, failed_step_id: int) -> List[int]:
        """Temporal rollback: failed step and all later uncommitted steps."""
        if failed_step_id <= self.committed_frontier:
            raise ValueError("cannot roll back a committed step")
        if failed_step_id < 0 or failed_step_id >= len(self.steps):
            raise ValueError(f"invalid step id {failed_step_id}")
        targets: List[int] = []
        for s in self.steps:
            if s.step_id < failed_step_id:
                continue
            if s.step_id <= self.committed_frontier:
                continue
            if s.status == "rolled_back":
                continue
            targets.append(s.step_id)
        return targets

    def mark_rolled_back(self, step_ids: Iterable[int]) -> None:
        wanted = set(step_ids)
        for s in self.steps:
            if s.step_id in wanted:
                s.status = "rolled_back"

    def advance_frontier(self, step_id: int) -> None:
        if step_id < self.committed_frontier:
            raise ValueError("frontier can only move forward")
        for s in self.steps:
            if self.committed_frontier < s.step_id <= step_id:
                if s.status == "rolled_back":
                    raise ValueError(f"step {s.step_id} is rolled_back; cannot commit past it")
                s.status = "committed"
        self.committed_frontier = step_id

    def to_dict(self) -> dict:
        return {
            "committed_frontier": self.committed_frontier,
            "steps": [s.to_dict() for s in self.steps],
        }

    @staticmethod
    def from_dict(d: dict) -> "Ledger":
        led = Ledger(committed_frontier=d.get("committed_frontier", -1))
        led.steps = [Step.from_dict(s) for s in d.get("steps", [])]
        return led