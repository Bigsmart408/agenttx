"""Verifier-guided causal fault localization."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from .faults import FailureObservation, FaultCandidate, normalize_roots
from .ledger import EffectKind, Ledger, Step


def _active(step: Step, committed_frontier: int) -> bool:
    return step.step_id > committed_frontier and step.status != "rolled_back"


def _writes(step: Step) -> bool:
    return any(
        effect.kind in (EffectKind.WRITE, EffectKind.DELETE)
        for effect in step.effects
    )


def _distances_to(ledger: Ledger, step_id: int) -> dict[int, int]:
    distances: dict[int, int] = {}
    queue = deque((parent, 1) for parent in ledger.steps[step_id].parents)
    while queue:
        current, distance = queue.popleft()
        previous = distances.get(current)
        if previous is not None and previous <= distance:
            continue
        distances[current] = distance
        queue.extend((parent, distance + 1) for parent in ledger.steps[current].parents)
    return distances


def _diagnostic_mentions(step: Step, observation: FailureObservation) -> bool:
    diagnostic = f"{observation.stdout}\n{observation.stderr}".strip()
    if not diagnostic:
        return False
    for effect in step.effects:
        path = str(effect.path)
        if path in diagnostic or Path(path).name in diagnostic:
            return True
    return False


class FaultLocalizer:
    """Produce ranked, filesystem-backed roots for a failed verifier step."""

    def __init__(self, *, max_candidates: Optional[int] = 8) -> None:
        if max_candidates is not None and max_candidates <= 0:
            raise ValueError("max_candidates must be positive")
        self.max_candidates = None if max_candidates is None else int(max_candidates)

    def candidates(
        self,
        ledger: Ledger,
        observation: FailureObservation,
    ) -> List[FaultCandidate]:
        verifier_id = observation.verifier_step_id
        if observation.returncode == 0:
            return []
        ancestors = set(ledger.ancestors(verifier_id))
        distances = _distances_to(ledger, verifier_id)
        lower_bound = ledger.committed_frontier
        if observation.last_passing_step is not None:
            lower_bound = max(lower_bound, int(observation.last_passing_step))

        candidates: List[FaultCandidate] = []
        active_count = sum(
            _active(step, ledger.committed_frontier) for step in ledger.steps
        )
        for step_id in ancestors:
            step = ledger.steps[step_id]
            if step_id <= lower_bound or not _active(step, ledger.committed_frontier):
                continue
            if not _writes(step):
                continue
            targets = tuple(ledger.causal_dependents(step_id))
            distance = distances.get(step_id, len(ledger.steps) + 1)
            score = 100.0 / max(distance, 1)
            if step_id in ledger.steps[verifier_id].parents:
                score += 100.0
            if _diagnostic_mentions(step, observation):
                score += 50.0
            score += step_id / max(len(ledger.steps), 1)
            discarded = sum(
                1
                for target in targets
                if target != verifier_id
                and _active(ledger.steps[target], ledger.committed_frontier)
            )
            candidates.append(
                FaultCandidate(
                    root_step_ids=(step_id,),
                    rollback_targets=targets,
                    suspicion_score=score,
                    retained_steps=max(active_count - len(targets), 0),
                    discarded_steps=discarded,
                )
            )
        candidates.sort(
            key=lambda item: (
                -item.suspicion_score,
                item.discarded_steps,
                -item.root_step_ids[0],
            )
        )
        return candidates[: self.max_candidates]

    @staticmethod
    def combined_candidate(
        ledger: Ledger,
        root_step_ids: Sequence[int],
    ) -> FaultCandidate:
        roots = normalize_roots(root_step_ids)
        targets = tuple(ledger.causal_dependents_many(roots))
        active_count = sum(
            _active(step, ledger.committed_frontier) for step in ledger.steps
        )
        return FaultCandidate(
            root_step_ids=roots,
            rollback_targets=targets,
            suspicion_score=0.0,
            retained_steps=max(active_count - len(targets), 0),
            discarded_steps=len(targets),
        )


def candidate_root_sets(
    candidates: Sequence[FaultCandidate],
    *,
    include_pairs: bool = False,
) -> Iterable[tuple[int, ...]]:
    for candidate in candidates:
        yield candidate.root_step_ids
    if not include_pairs:
        return
    roots = [candidate.root_step_ids[0] for candidate in candidates]
    for left_index, left in enumerate(roots):
        for right in roots[left_index + 1 :]:
            yield normalize_roots((left, right))
