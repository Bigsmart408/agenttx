"""Causal effect ledger for multi-step agent trajectories."""

from __future__ import annotations

from dataclasses import dataclass, field
from bisect import bisect_left
from enum import Enum
from typing import Iterable, List, Optional, Set


class EffectKind(str, Enum):
    READ = "R"
    WRITE = "W"
    DELETE = "D"
    NEGATIVE = "N"


@dataclass(frozen=True)
class Effect:
    path: str
    kind: EffectKind
    object_id: Optional[str] = None
    object_version: Optional[int] = None
    topology_op: Optional[str] = None

    def to_dict(self) -> dict:
        payload = {"path": self.path, "kind": self.kind.value}
        if self.object_id is not None:
            payload["object_id"] = self.object_id
        if self.object_version is not None:
            payload["object_version"] = self.object_version
        if self.topology_op is not None:
            payload["topology_op"] = self.topology_op
        return payload

    @staticmethod
    def from_dict(d: dict) -> "Effect":
        return Effect(
            path=d["path"],
            kind=EffectKind(d["kind"]),
            object_id=d.get("object_id"),
            object_version=d.get("object_version"),
            topology_op=d.get("topology_op"),
        )


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


class _EffectIndex:
    """Index the existing overlap relation without changing path semantics."""

    def __init__(self, effects: Iterable[tuple[Effect, int]]) -> None:
        self.paths: dict[str, Set[int]] = {}
        self.objects: dict[str, Set[int]] = {}
        self.cache: dict[str, Set[int]] = {}
        for effect, step_id in effects:
            path = effect.path.rstrip('/') or '/'
            self.paths.setdefault(path, set()).add(step_id)
            if effect.object_id is not None:
                self.objects.setdefault(effect.object_id, set()).add(step_id)
        self.ordered = sorted(self.paths)

    def related(self, effect: Effect) -> Set[int]:
        path = effect.path.rstrip('/') or '/'
        if path not in self.cache:
            parents = set(self.paths.get(path, ()))
            # Keep literal separators, including repeated slashes and the
            # existing special behaviour of '/', exactly as _paths_overlap.
            for index, char in enumerate(path):
                if char == '/' and index:
                    parents.update(self.paths.get(path[:index], ()))
            start = bisect_left(self.ordered, path + '/')
            end = bisect_left(self.ordered, path + '0')
            for index in range(start, end):
                parents.update(self.paths[self.ordered[index]])
            self.cache[path] = parents
        return self.cache[path] | self.objects.get(effect.object_id, set())


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

    def active_uncommitted(self) -> List[Step]:
        """Return active steps after the committed frontier in ledger order."""
        return list(self._uncommitted())

    @staticmethod
    def _paths_overlap(left: str, right: str) -> bool:
        left = left.rstrip("/") or "/"
        right = right.rstrip("/") or "/"
        return (
            left == right
            or left.startswith(right + "/")
            or right.startswith(left + "/")
        )

    @staticmethod
    def _objects_overlap(left: Effect, right: Effect) -> bool:
        return (
            left.object_id is not None
            and right.object_id is not None
            and left.object_id == right.object_id
        )

    def _writers(self) -> List[tuple[Effect, int]]:
        return [
            (effect, step.step_id)
            for step in self._uncommitted()
            for effect in step.effects
            if effect.kind in (EffectKind.WRITE, EffectKind.DELETE)
        ]

    def _negative_lookups(self) -> List[tuple[Effect, int]]:
        return [
            (effect, step.step_id)
            for step in self._uncommitted()
            for effect in step.effects
            if effect.kind == EffectKind.NEGATIVE
        ]

    def add_step(self, tool_name: str, effects: Optional[List[Effect]] = None) -> Step:
        effects = effects or []
        step = Step(step_id=len(self.steps), tool_name=tool_name, effects=list(effects))
        writers = _EffectIndex(self._writers())
        negatives = _EffectIndex(self._negative_lookups())
        for effect in step.effects:
            if effect.kind in (EffectKind.READ, EffectKind.NEGATIVE, EffectKind.WRITE, EffectKind.DELETE):
                step.parents.update(writers.related(effect))
            if effect.kind == EffectKind.WRITE:
                step.parents.update(negatives.related(effect))
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

    def causal_dependents_many(self, failed_step_ids: Iterable[int]) -> List[int]:
        """Return the union of the causal cones rooted at ``failed_step_ids``."""
        roots = {int(step_id) for step_id in failed_step_ids}
        if not roots:
            return []
        for step_id in roots:
            if step_id < 0 or step_id >= len(self.steps):
                raise ValueError(f"invalid step id {step_id}")
        dependents = set(roots)
        changed = True
        while changed:
            changed = False
            for step in self.steps:
                if step.step_id in dependents or step.status == "rolled_back":
                    continue
                if step.parents & dependents:
                    dependents.add(step.step_id)
                    changed = True
        return sorted(dependents)

    def ancestors(self, step_id: int) -> List[int]:
        """Return every transitive dependency of ``step_id`` in step order."""
        if step_id < 0 or step_id >= len(self.steps):
            raise ValueError(f"invalid step id {step_id}")
        ancestors: Set[int] = set()
        frontier = list(self.steps[step_id].parents)
        while frontier:
            parent = frontier.pop()
            if parent in ancestors:
                continue
            ancestors.add(parent)
            frontier.extend(self.steps[parent].parents)
        return sorted(ancestors)

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
                    continue  # holes from cascade rollback are allowed
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
