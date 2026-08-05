"""Causal effect ledger (stub)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Set


class EffectKind(str, Enum):
    READ = "R"
    WRITE = "W"
    DELETE = "D"
    NEGATIVE = "N"


@dataclass(frozen=True)
class Effect:
    path: str
    kind: EffectKind


@dataclass
class Step:
    step_id: int
    tool_name: str
    effects: List[Effect] = field(default_factory=list)
    parents: Set[int] = field(default_factory=set)


@dataclass
class Ledger:
    steps: List[Step] = field(default_factory=list)
    committed_frontier: int = -1

    def add_step(self, tool_name: str, effects: Optional[List[Effect]] = None) -> Step:
        step = Step(step_id=len(self.steps), tool_name=tool_name, effects=effects or [])
        written = {}
        for prev in self.steps[self.committed_frontier + 1 :]:
            for e in prev.effects:
                if e.kind in (EffectKind.WRITE, EffectKind.DELETE):
                    written[e.path] = prev.step_id
        for e in step.effects:
            if e.kind == EffectKind.READ and e.path in written:
                step.parents.add(written[e.path])
        self.steps.append(step)
        return step

    def cascade_rollback_targets(self, failed_step_id: int) -> List[int]:
        dependents = {failed_step_id}
        changed = True
        while changed:
            changed = False
            for s in self.steps:
                if s.step_id in dependents:
                    continue
                if s.parents & dependents:
                    dependents.add(s.step_id)
                    changed = True
        return sorted(dependents)
