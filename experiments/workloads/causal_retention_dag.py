"""Synthetic effect-DAG workloads for quantitative causal-retention tests."""

from __future__ import annotations

import math
import shlex
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple


SHAPES = ("chain", "fanout", "layered")


@dataclass(frozen=True)
class DAGStep:
    """One topologically ordered file-producing tool call."""

    name: str
    relative_path: str
    parents: Tuple[str, ...] = ()
    role: str = "independent"

    def command(self) -> List[str]:
        output = shlex.quote(self.relative_path)
        if self.parents:
            inputs = " ".join(shlex.quote(path) for path in self.parents)
            body = (
                f"cat {inputs} > {output} && "
                f"printf '%s\\n' {shlex.quote(self.name)} >> {output}"
            )
        else:
            body = f"printf '%s\\n' {shlex.quote(self.name)} > {output}"
        return ["bash", "-c", body]


@dataclass(frozen=True)
class RetentionPlan:
    steps: Tuple[DAGStep, ...]
    fault_step_id: int
    expected_rollback_ids: Tuple[int, ...]
    independent_ids: Tuple[int, ...]
    shape: str
    requested_fault_fraction: float
    requested_independent_fraction: float

    @property
    def total_steps(self) -> int:
        return len(self.steps)

    @property
    def actual_fault_fraction(self) -> float:
        return self.fault_step_id / max(self.total_steps - 1, 1)

    @property
    def actual_independent_fraction(self) -> float:
        return len(self.independent_ids) / max(self.total_steps, 1)


def _closure(steps: Sequence[DAGStep], root_name: str) -> Tuple[int, ...]:
    targets = {root_name}
    changed = True
    while changed:
        changed = False
        for step in steps:
            if step.name in targets:
                continue
            if targets.intersection(step.parents):
                targets.add(step.name)
                changed = True
    return tuple(index for index, step in enumerate(steps) if step.name in targets)


def build_causal_retention_plan(
    total_steps: int,
    *,
    shape: str = "layered",
    fault_fraction: float = 0.25,
    independent_fraction: float = 0.5,
) -> RetentionPlan:
    """Build a deterministic DAG with one faulty root and useful independent work.

    ``fault_fraction`` controls how much independent work occurs before the
    faulty producer. ``independent_fraction`` controls the approximate share of
    post-fault nodes that do not depend on the producer.
    """

    if total_steps < 8:
        raise ValueError("total_steps must be >= 8")
    if shape not in SHAPES:
        raise ValueError(f"unsupported shape: {shape}")
    if not 0.0 < fault_fraction < 0.9:
        raise ValueError("fault_fraction must be between 0 and 0.9")
    if not 0.0 <= independent_fraction < 1.0:
        raise ValueError("independent_fraction must be between 0 and 1")

    prefix_count = max(1, int(round(total_steps * fault_fraction)))
    prefix_count = min(prefix_count, total_steps - 3)
    remaining = total_steps - prefix_count - 1
    dependent_count = max(1, int(round(remaining * (1.0 - independent_fraction))))
    dependent_count = min(dependent_count, remaining)
    independent_after_count = remaining - dependent_count

    prefix = [
        DAGStep(
            name=f"prefix-{index:03d}",
            relative_path=f"prefix_{index:03d}.txt",
            role="independent",
        )
        for index in range(prefix_count)
    ]
    root = DAGStep(
        name="fault-root",
        relative_path="fault_root.txt",
        role="fault",
    )

    dependent_names: List[str] = []
    dependent_paths: Dict[str, str] = {root.name: root.relative_path}
    dependents: List[DAGStep] = []
    width = max(2, int(math.sqrt(dependent_count)))
    for index in range(dependent_count):
        name = f"dependent-{index:03d}"
        path = f"dependent_{index:03d}.txt"
        if shape == "chain":
            parent_names = (root.name if index == 0 else dependent_names[-1],)
        elif shape == "fanout":
            parent_names = (root.name,)
        else:
            if index < width:
                parent_names = (root.name,)
            else:
                candidates = [dependent_names[index - width]]
                if index % 2 and dependent_names[index - 1] not in candidates:
                    candidates.append(dependent_names[index - 1])
                parent_names = tuple(candidates)
        parent_paths = tuple(dependent_paths[parent] for parent in parent_names)
        dependents.append(
            DAGStep(name=name, relative_path=path, parents=parent_paths, role="dependent")
        )
        dependent_names.append(name)
        dependent_paths[name] = path

    independent_after = [
        DAGStep(
            name=f"independent-{index:03d}",
            relative_path=f"independent_{index:03d}.txt",
            role="independent",
        )
        for index in range(independent_after_count)
    ]

    suffix: List[DAGStep] = []
    for index in range(max(len(dependents), len(independent_after))):
        if index < len(dependents):
            suffix.append(dependents[index])
        if index < len(independent_after):
            suffix.append(independent_after[index])
    steps = tuple(prefix + [root] + suffix)

    # DAGStep.parents stores paths because those are the runtime read effects.
    # Reconstruct symbolic ancestry from the unique path-to-name mapping.
    path_to_name = {step.relative_path: step.name for step in steps}
    symbolic_steps = tuple(
        DAGStep(
            name=step.name,
            relative_path=step.relative_path,
            parents=tuple(path_to_name[parent] for parent in step.parents),
            role=step.role,
        )
        for step in steps
    )
    expected = _closure(symbolic_steps, root.name)
    expected_set = set(expected)
    independent = tuple(index for index in range(len(steps)) if index not in expected_set)
    fault_step_id = next(index for index, step in enumerate(steps) if step.role == "fault")
    return RetentionPlan(
        steps=steps,
        fault_step_id=fault_step_id,
        expected_rollback_ids=expected,
        independent_ids=independent,
        shape=shape,
        requested_fault_fraction=fault_fraction,
        requested_independent_fraction=independent_fraction,
    )
