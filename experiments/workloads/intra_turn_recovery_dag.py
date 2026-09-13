"""Deterministic single-turn DAGs for the paper motivation experiments."""

from __future__ import annotations

import hashlib
import json
import math
import shlex
from dataclasses import dataclass
from typing import Iterable, Sequence


SHAPES = ("chain", "fan-out", "layered")
INTERLEAVINGS = ("independent-before", "independent-after", "alternating")


@dataclass(frozen=True)
class IntraTurnStep:
    index: int
    name: str
    relative_path: str
    parent_indices: tuple[int, ...] = ()
    role: str = "independent"

    def command(self, steps: Sequence["IntraTurnStep"]) -> list[str]:
        output = shlex.quote(self.relative_path)
        payload = f"artifact-{self.index:03d}-{self.role}"
        if not self.parent_indices:
            body = f"printf '%s\\n' {shlex.quote(payload)} > {output}"
        else:
            inputs = " ".join(
                shlex.quote(steps[parent].relative_path) for parent in self.parent_indices
            )
            body = (
                f"cat {inputs} > {output} && "
                f"printf '%s\\n' {shlex.quote(payload)} >> {output}"
            )
        return ["bash", "-c", body]

    def token_payload(self, steps: Sequence["IntraTurnStep"]) -> str:
        return json.dumps(
            {
                "name": self.name,
                "arguments": {"cmd": self.command(steps)},
                "artifact_payload": f"artifact-{self.index:03d}-{self.role}",
            },
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True)
class IntraTurnPlan:
    steps: tuple[IntraTurnStep, ...]
    fault_step_id: int
    oracle_closure: tuple[int, ...]
    requested_fault_position: float
    requested_closure_ratio: float
    shape: str
    interleaving: str
    seed: int
    trace_id: str

    @property
    def valid_ids(self) -> tuple[int, ...]:
        invalid = set(self.oracle_closure)
        return tuple(step.index for step in self.steps if step.index not in invalid)

    @property
    def manifest_hash(self) -> str:
        payload = {
            "steps": [
                {
                    "index": step.index,
                    "path": step.relative_path,
                    "parents": step.parent_indices,
                    "role": step.role,
                }
                for step in self.steps
            ],
            "fault": self.fault_step_id,
            "closure": self.oracle_closure,
            "seed": self.seed,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()


def causal_closure(steps: Sequence[IntraTurnStep], roots: Iterable[int]) -> tuple[int, ...]:
    closure = {int(root) for root in roots}
    changed = True
    while changed:
        changed = False
        for step in steps:
            if step.index in closure:
                continue
            if closure.intersection(step.parent_indices):
                closure.add(step.index)
                changed = True
    return tuple(sorted(closure))


def _dependent_slots(candidates: list[int], count: int, interleaving: str) -> set[int]:
    if count <= 0:
        return set()
    if interleaving == "independent-before":
        return set(candidates[-count:])
    if interleaving == "independent-after":
        return set(candidates[:count])
    selected: list[int] = []
    for offset in range(0, len(candidates), 2):
        selected.append(candidates[offset])
        if len(selected) == count:
            return set(selected)
    for offset in range(1, len(candidates), 2):
        selected.append(candidates[offset])
        if len(selected) == count:
            break
    return set(selected)


def build_intra_turn_plan(
    width: int,
    *,
    fault_position: float,
    closure_ratio: float,
    shape: str,
    interleaving: str,
    seed: int = 0,
) -> IntraTurnPlan:
    if width < 8:
        raise ValueError("width must be at least 8")
    if shape not in SHAPES:
        raise ValueError(f"unsupported shape: {shape}")
    if interleaving not in INTERLEAVINGS:
        raise ValueError(f"unsupported interleaving: {interleaving}")
    if not 0.0 <= fault_position < 1.0:
        raise ValueError("fault_position must be in [0, 1)")
    if not 0.0 < closure_ratio <= 1.0:
        raise ValueError("closure_ratio must be in (0, 1]")

    requested_closure = max(1, int(round(width * closure_ratio)))
    # Interpret position within the feasible root range so every requested
    # closure ratio is realizable; raw output also records the actual index.
    latest_root = width - requested_closure
    root = int(round(latest_root * fault_position))
    suffix = list(range(root + 1, width))
    dependent_slots = _dependent_slots(suffix, requested_closure - 1, interleaving)

    dependent_ids: list[int] = []
    steps: list[IntraTurnStep] = []
    layer_width = max(2, int(math.sqrt(max(requested_closure - 1, 1))))
    for index in range(width):
        role = "fault" if index == root else "independent"
        parents: tuple[int, ...] = ()
        if index in dependent_slots:
            role = "dependent"
            if shape == "fan-out" or not dependent_ids:
                parents = (root,)
            elif shape == "chain":
                parents = (dependent_ids[-1],)
            else:
                parents = (dependent_ids[max(0, len(dependent_ids) - layer_width)],)
                if len(dependent_ids) % 2 and dependent_ids[-1] not in parents:
                    parents = (*parents, dependent_ids[-1])
            dependent_ids.append(index)
        steps.append(
            IntraTurnStep(
                index=index,
                name=f"call-{index:03d}",
                relative_path=f"step_{index:03d}.txt",
                parent_indices=parents,
                role=role,
            )
        )
    closure = causal_closure(steps, [root])
    if len(closure) != requested_closure:
        raise AssertionError((len(closure), requested_closure, root, dependent_slots))
    trace_key = f"w{width}-f{fault_position:g}-c{closure_ratio:g}-{shape}-{interleaving}-s{seed}"
    return IntraTurnPlan(
        steps=tuple(steps),
        fault_step_id=root,
        oracle_closure=closure,
        requested_fault_position=fault_position,
        requested_closure_ratio=closure_ratio,
        shape=shape,
        interleaving=interleaving,
        seed=seed,
        trace_id=trace_key,
    )


def build_extreme_plan(seed: int = 0) -> IntraTurnPlan:
    parents = {32: (1,), 64: (32,), 127: (64,)}
    steps = tuple(
        IntraTurnStep(
            index=index,
            name=f"call-{index:03d}",
            relative_path=f"step_{index:03d}.txt",
            parent_indices=parents.get(index, ()),
            role="fault" if index == 1 else ("dependent" if index in parents else "independent"),
        )
        for index in range(128)
    )
    closure = causal_closure(steps, [1])
    if closure != (1, 32, 64, 127):
        raise AssertionError(closure)
    return IntraTurnPlan(
        steps=steps,
        fault_step_id=1,
        oracle_closure=closure,
        requested_fault_position=1 / 127,
        requested_closure_ratio=4 / 128,
        shape="layered",
        interleaving="alternating",
        seed=seed,
        trace_id=f"extreme-128-s{seed}",
    )
