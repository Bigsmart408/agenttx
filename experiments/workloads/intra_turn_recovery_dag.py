"""Deterministic multi-turn DAGs for the paper motivation experiments."""

from __future__ import annotations

import hashlib
import json
import math
import random
import shlex
from dataclasses import dataclass, replace
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
    turn_index: int = 0
    turn_step_index: int = 0

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
    fault_step_ids: tuple[int, ...] = ()
    requested_dependency_value: float | None = None

    @property
    def fault_roots(self) -> tuple[int, ...]:
        return self.fault_step_ids or (self.fault_step_id,)

    @property
    def turn_ids(self) -> tuple[int, ...]:
        return tuple(sorted({step.turn_index for step in self.steps}))

    @property
    def turn_count(self) -> int:
        return len(self.turn_ids)

    @property
    def valid_ids(self) -> tuple[int, ...]:
        invalid = set(self.oracle_closure)
        return tuple(step.index for step in self.steps if step.index not in invalid)

    @property
    def actual_dependency_value(self) -> float:
        """Fraction of post-root operations causally dependent on the root."""
        suffix = {
            step.index for step in self.steps if step.index > self.fault_step_id
        }
        if not suffix:
            return 0.0
        descendants = (set(self.oracle_closure) - set(self.fault_roots)) & suffix
        return len(descendants) / len(suffix)

    @property
    def manifest_hash(self) -> str:
        payload = {
            "steps": [
                {
                    "index": step.index,
                    "path": step.relative_path,
                    "parents": step.parent_indices,
                    "role": step.role,
                    "turn": step.turn_index,
                    "turn_step": step.turn_step_index,
                }
                for step in self.steps
            ],
            "fault": self.fault_roots,
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


def _dependent_slots(
    candidates: list[int],
    count: int,
    interleaving: str,
    rng: random.Random,
) -> set[int]:
    if count <= 0:
        return set()
    if interleaving == "independent-before":
        return set(candidates[-count:])
    if interleaving == "independent-after":
        return set(candidates[:count])
    if count >= len(candidates):
        return set(candidates)
    # Stratified placement keeps dependent and independent operations mixed
    # across the suffix while allowing seeds to represent distinct DAGs.
    selected = []
    for stratum in range(count):
        lower = math.floor(stratum * len(candidates) / count)
        upper = math.floor((stratum + 1) * len(candidates) / count) - 1
        selected.append(candidates[rng.randint(lower, max(lower, upper))])
    return set(selected)


def build_intra_turn_plan(
    width: int,
    *,
    fault_position: float,
    closure_ratio: float | None = None,
    dependency_value: float | None = None,
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
    if (closure_ratio is None) == (dependency_value is None):
        raise ValueError("provide exactly one of closure_ratio or dependency_value")
    if closure_ratio is not None and not 0.0 < closure_ratio <= 1.0:
        raise ValueError("closure_ratio must be in (0, 1]")
    if dependency_value is not None and not 0.0 <= dependency_value <= 1.0:
        raise ValueError("dependency_value must be in [0, 1]")

    if dependency_value is not None:
        # Fix time position first. Dependency density must not move the fault.
        root = int(round((width - 1) * fault_position))
        suffix_size = width - root - 1
        dependent_count = int(round(suffix_size * dependency_value))
        requested_closure = 1 + dependent_count
        requested_closure_ratio = requested_closure / width
    else:
        assert closure_ratio is not None
        requested_closure = max(1, int(round(width * closure_ratio)))
        # Legacy behavior: interpret position within the feasible root range.
        latest_root = width - requested_closure
        root = int(round(latest_root * fault_position))
        requested_closure_ratio = closure_ratio
    rng = random.Random(seed)
    suffix = list(range(root + 1, width))
    dependent_slots = _dependent_slots(
        suffix, requested_closure - 1, interleaving, rng
    )

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
                recent = dependent_ids[-layer_width:]
                parent_count = 1 if len(recent) == 1 else rng.randint(1, 2)
                parents = tuple(sorted(rng.sample(recent, parent_count)))
            dependent_ids.append(index)
        steps.append(
            IntraTurnStep(
                index=index,
                name=f"call-{index:03d}",
                relative_path=f"step_{index:03d}.txt",
                parent_indices=parents,
                role=role,
                turn_index=0,
                turn_step_index=index,
            )
        )
    closure = causal_closure(steps, [root])
    if len(closure) != requested_closure:
        raise AssertionError((len(closure), requested_closure, root, dependent_slots))
    density_key = (
        f"d{dependency_value:g}"
        if dependency_value is not None
        else f"c{closure_ratio:g}"
    )
    trace_key = (
        f"w{width}-f{fault_position:g}-{density_key}-"
        f"{shape}-{interleaving}-s{seed}"
    )
    return IntraTurnPlan(
        steps=tuple(steps),
        fault_step_id=root,
        oracle_closure=closure,
        requested_fault_position=fault_position,
        requested_closure_ratio=requested_closure_ratio,
        shape=shape,
        interleaving=interleaving,
        seed=seed,
        trace_id=trace_key,
        requested_dependency_value=dependency_value,
    )


def build_multi_turn_plan(
    turn_count: int,
    turn_width: int,
    *,
    fault_position: float,
    closure_ratio: float | None = None,
    dependency_value: float | None = None,
    shape: str,
    interleaving: str,
    seed: int = 0,
) -> IntraTurnPlan:
    """Build one branch containing a configurable sequence of agent turns."""

    if not 1 <= turn_count <= 10:
        raise ValueError("turn_count must be in [1, 10]")
    if turn_width < 8:
        raise ValueError("turn_width must be at least 8")
    base = build_intra_turn_plan(
        turn_count * turn_width,
        fault_position=fault_position,
        closure_ratio=closure_ratio,
        dependency_value=dependency_value,
        shape=shape,
        interleaving=interleaving,
        seed=seed,
    )
    steps = tuple(
        replace(
            step,
            name=(
                f"turn-{step.index // turn_width:02d}-"
                f"call-{step.index % turn_width:03d}"
            ),
            turn_index=step.index // turn_width,
            turn_step_index=step.index % turn_width,
        )
        for step in base.steps
    )
    return replace(
        base,
        steps=steps,
        trace_id=f"t{turn_count}x{turn_width}-{base.trace_id}",
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
            turn_index=0,
            turn_step_index=index,
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


def build_controlled_waste_plan(
    width: int, seed: int = 0, fault_position: float = 0.25
) -> IntraTurnPlan:
    """Build a turn with a fixed four-operation fault closure.

    Width changes only the amount of independent valid work. This isolates the
    recovery-boundary cost from fault size for the headline motivation case.
    """
    if width < 16:
        raise ValueError("controlled-case width must be at least 16")
    if not 0.0 <= fault_position < 0.95:
        raise ValueError("controlled-case fault position must be in [0, 0.95)")
    root = int(round((width - 1) * fault_position))
    candidates = list(range(root + 1, width))
    rng = random.Random(seed)
    dependent_slots = sorted(
        rng.choice(candidates[start:end])
        for start, end in (
            (0, max(1, len(candidates) // 3)),
            (max(1, len(candidates) // 3), max(2, 2 * len(candidates) // 3)),
            (max(2, 2 * len(candidates) // 3), len(candidates)),
        )
    )
    parents = {
        dependent_slots[0]: (root,),
        dependent_slots[1]: (dependent_slots[0],),
        dependent_slots[2]: (dependent_slots[1],),
    }
    steps = tuple(
        IntraTurnStep(
            index=index,
            name=f"call-{index:03d}",
            relative_path=f"step_{index:03d}.txt",
            parent_indices=parents.get(index, ()),
            role=(
                "fault"
                if index == root
                else "dependent"
                if index in parents
                else "independent"
            ),
            turn_index=0,
            turn_step_index=index,
        )
        for index in range(width)
    )
    closure = causal_closure(steps, [root])
    if len(closure) != 4:
        raise AssertionError(closure)
    return IntraTurnPlan(
        steps=steps,
        fault_step_id=root,
        oracle_closure=closure,
        requested_fault_position=fault_position,
        requested_closure_ratio=4 / width,
        requested_dependency_value=3 / len(candidates),
        shape="chain",
        interleaving="alternating",
        seed=seed,
        trace_id=f"controlled-w{width}-f{fault_position:g}-s{seed}",
    )


def build_multiturn_controlled_waste_plan(
    turn_count: int,
    *,
    turn_width: int = 16,
    seed: int = 0,
    fault_position: float = 0.25,
) -> IntraTurnPlan:
    """Build a multi-turn branch with a fixed four-operation fault closure."""

    if not 1 <= turn_count <= 10:
        raise ValueError("turn_count must be in [1, 10]")
    if turn_width < 16:
        raise ValueError("controlled-case turn_width must be at least 16")
    base = build_controlled_waste_plan(
        turn_count * turn_width,
        seed=seed,
        fault_position=fault_position,
    )
    steps = tuple(
        replace(
            step,
            name=(
                f"turn-{step.index // turn_width:02d}-"
                f"call-{step.index % turn_width:03d}"
            ),
            turn_index=step.index // turn_width,
            turn_step_index=step.index % turn_width,
        )
        for step in base.steps
    )
    return replace(
        base,
        steps=steps,
        trace_id=f"controlled-t{turn_count}x{turn_width}-f{fault_position:g}-s{seed}",
    )


def build_multiturn_extreme_plan(seed: int = 0) -> IntraTurnPlan:
    """Build the M3 stress case with faults distributed across execution turns."""
    turn_width = 16
    turn_count = 8
    roots = (1, 34, 83, 116)
    parents = {
        17: (1,),
        51: (34,),
        95: (83,),
        127: (116,),
    }
    steps = tuple(
        IntraTurnStep(
            index=index,
            name=f"turn-{index // turn_width:02d}-call-{index % turn_width:02d}",
            relative_path=(
                "effects/"
                + hashlib.sha256(
                    f"agenttx-m3-effect:{seed}:{index}".encode("utf-8")
                ).hexdigest()[:20]
                + ".state"
            ),
            parent_indices=parents.get(index, ()),
            role=(
                "fault"
                if index in roots
                else "dependent"
                if index in parents
                else "independent"
            ),
            turn_index=index // turn_width,
            turn_step_index=index % turn_width,
        )
        for index in range(turn_width * turn_count)
    )
    closure = causal_closure(steps, roots)
    expected = tuple(sorted((*roots, *parents)))
    if closure != expected:
        raise AssertionError((closure, expected))
    return IntraTurnPlan(
        steps=steps,
        fault_step_id=roots[0],
        fault_step_ids=roots,
        oracle_closure=closure,
        requested_fault_position=roots[0] / (len(steps) - 1),
        requested_closure_ratio=len(closure) / len(steps),
        shape="multi-turn-layered",
        interleaving="alternating",
        seed=seed,
        trace_id=f"multiturn-extreme-{turn_count}x{turn_width}-s{seed}",
    )
