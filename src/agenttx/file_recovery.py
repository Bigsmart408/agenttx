"""File-scoped recovery from an explicit historical checkpoint.

A report identifies files, never an injected faulty command. All mutations
of those files after the checkpoint are withdrawn with their file/result
dependents. This intentionally makes no claim to identify which edit was
semantically wrong. Audits use a frozen pre-recovery history, saved file
versions and active conversation spans; no test-success or injection marker
is used as an acceptance oracle. Completeness is relative to recorded edges.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import TYPE_CHECKING, Optional, Sequence

from .ledger import EffectKind

if TYPE_CHECKING:
    from .runtime import AgentTX

POLICIES = ("causal", "selected_effects", "temporal_checkpoint",
            "turn_checkpoint", "whole_branch_abort", "no_rollback")
MUTATIONS = (EffectKind.WRITE, EffectKind.DELETE)


def _signature(tx: AgentTX) -> str:
    data = [tx.ledger.to_dict(), tx.conversation.to_dict()]
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class FileRecoveryPlan:
    paths: tuple[str, ...]
    checkpoint_step: int  # state AFTER this command; -1 means session start
    history_end: int
    policy: str
    root_step_ids: tuple[int, ...]
    required_step_ids: tuple[int, ...]
    rollback_targets: tuple[int, ...]
    history_signature: str
    active_step_ids: tuple[int, ...]
    visible_step_ids: tuple[int, ...]
    edges: tuple[tuple[int, int], ...]

    def user_message(self, workspace: Path) -> str:
        paths = [str(Path(p).relative_to(workspace)) for p in self.paths]
        return ("User report: the following file(s) are incorrect: "
                + json.dumps(paths) + ". Recover their changes and dependent work "
                "since the selected reference checkpoint. Preserve independent work. "
                "No faulty command or step is identified by this report.")


def _closure(roots, edges) -> set[int]:
    selected = set(roots)
    while True:
        expanded = selected | {child for parent, child in edges if parent in selected}
        if expanded == selected:
            return selected
        selected = expanded


def plan_file_recovery(
    tx: AgentTX, paths: Sequence[str], *, checkpoint_step: Optional[int] = None,
    policy: str = "causal",
) -> FileRecoveryPlan:
    if policy not in POLICIES:
        raise ValueError(f"unknown file recovery policy: {policy}")
    if isinstance(paths, (str, bytes)) or not paths:
        raise ValueError("provide a nonempty sequence of problem file paths")
    checkpoint = tx.ledger.committed_frontier if checkpoint_step is None else checkpoint_step
    if (isinstance(checkpoint, bool) or not isinstance(checkpoint, int)
            or checkpoint < tx.ledger.committed_frontier
            or checkpoint >= len(tx.ledger.steps)
            or (checkpoint >= 0 and tx.ledger.steps[checkpoint].status == 'rolled_back')):
        raise ValueError("reference checkpoint must be within the uncommitted history")
    normalized = set()
    for raw in paths:
        if not isinstance(raw, (str, os.PathLike)) or not str(raw).strip():
            raise ValueError("problem file path cannot be empty")
        path = Path(os.path.abspath(tx.workspace / raw))
        if not path.is_relative_to(tx.workspace) or path == tx.workspace:
            raise ValueError("problem file must be inside the workspace")
        for _ in range(40):
            path = Path(tx._resolve_alias_ancestors(str(path)))
            entry = tx._overlay_entry(path) if tx.pool is not None else path
            if entry is None or not entry.is_symlink():
                break
            target = os.readlink(entry)
            path = Path(os.path.abspath(path.parent / target))
            if not path.is_relative_to(tx.workspace):
                raise ValueError('problem file must resolve inside the workspace')
        else:
            raise ValueError('problem file has a symlink cycle')
        if tx.commit_policy and tx.commit_policy.is_ignored(str(path)):
            raise ValueError("problem file is outside the managed recovery state")
        normalized.add(str(path))
    # Include known hard-link aliases, using the catalog/effect identity rather
    # than inferring identity from a possibly superseded host inode.
    object_ids = {effect.object_id for step in tx.ledger.steps for effect in step.effects
                  if effect.path in normalized and effect.object_id is not None}
    for record in tx.object_catalog.records.values():
        if normalized.intersection(record.aliases):
            object_ids.add(record.object_id)
    active = {step.step_id for step in tx.ledger.steps if step.status != "rolled_back"}
    window = {i for i in active if i > checkpoint}
    roots = {step.step_id for step in tx.ledger.steps if step.step_id in window
             and any(effect.kind in MUTATIONS and (
                 any(tx._paths_overlap(effect.path, path) for path in normalized)
                 or effect.object_id in object_ids
             ) for effect in step.effects)}
    edges = {(parent, step.step_id) for step in tx.ledger.steps if step.step_id in active
             for parent in step.parents if parent in active}
    edges.update((parent, span.step_id) for turn in tx.conversation.active_turns()
                 for span in turn.active_calls() if span.step_id in active
                 for parent in span.parents if parent in active)
    required = _closure(roots, edges)
    if not required <= window:
        raise ValueError("file dependencies cross the reference checkpoint")
    if policy == "causal":
        targets = required
    elif policy == "selected_effects":
        targets = roots
    elif policy == "temporal_checkpoint":
        targets = {i for i in window if roots and i >= min(roots)}
    elif policy == "whole_branch_abort":
        targets = window if roots else set()
    elif policy == "turn_checkpoint":
        targets = set()
        for turn in tx.conversation.active_turns():
            if required.intersection(turn.step_ids):
                targets.update(i for i in turn.step_ids if i in active)
        if not required <= targets:
            raise ValueError("required commands are missing from recorded turns")
        if not targets <= window:
            raise ValueError("a selected turn crosses the reference checkpoint")
    else:
        targets = set()
    return FileRecoveryPlan(
        tuple(sorted(normalized)), checkpoint, len(tx.ledger.steps), policy,
        tuple(sorted(roots)), tuple(sorted(required)), tuple(sorted(targets)),
        _signature(tx), tuple(sorted(active)), tuple(tx.conversation.active_step_ids()),
        tuple(sorted(edges)),
    )


def apply_file_recovery_plan(tx: AgentTX, plan: FileRecoveryPlan) -> list[int]:
    """Apply a frozen policy selection. Experiments must audit even incomplete arms.

    Interactive callers should use recover_file(), which requires the isolated
    audit to pass before live application.
    """
    if _signature(tx) != plan.history_signature:
        raise ValueError("file recovery plan is stale; history changed after planning")
    if not plan.rollback_targets:
        return []
    return tx._rollback_selected_targets(
        list(plan.rollback_targets), mode=f"file:{plan.policy}", preserve_prior_mutations=True,
    )


def select_step_hint_targets(
    tx: AgentTX, step_ids: Sequence[int], *, policy: str,
    affected_step_ids: Optional[Sequence[int]] = None,
) -> list[int]:
    """Select control-arm commands from explicit step hints, without a file report.

    Turn checkpoints receive the registered affected calls, as in the existing
    baseline. The ideal oracle receives the registered invalid set. Neither
    selection consults file recovery roots or captured dependency edges.
    """
    active = {s.step_id for s in tx.ledger.active_uncommitted()}
    roots = set(step_ids)
    if (not roots or any(isinstance(i, bool) or not isinstance(i, int) for i in roots)
            or not roots <= active):
        raise ValueError('step controls require explicit active, uncommitted step IDs')
    if policy == 'selected_effects':
        selected = roots
    elif policy == 'temporal_checkpoint':
        selected = {i for i in active if i >= min(roots)}
    elif policy == 'whole_branch_abort':
        selected = active
    elif policy == 'no_rollback':
        selected = set()
    elif policy in {'turn_checkpoint', 'ideal_oracle'}:
        affected = set(affected_step_ids or ())
        if not roots <= affected or not affected <= active:
            raise ValueError('turn/oracle controls require the registered affected step IDs')
        selected = affected
        if policy == 'turn_checkpoint':
            selected = {i for turn in tx.conversation.active_turns()
                        if affected.intersection(turn.step_ids) for i in turn.step_ids
                        if i in active}
            if not affected <= selected:
                raise ValueError('registered affected calls have no recorded turn')
    else:
        raise ValueError(f'unsupported step control policy: {policy}')
    return sorted(selected)


def _entry(path: Path) -> dict:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"kind": "absent"}
    mode = info.st_mode
    data = dict(mode=stat.S_IMODE(mode), uid=info.st_uid, gid=info.st_gid)
    data["xattrs"] = {key: os.getxattr(path, key, follow_symlinks=False).hex()
                      for key in sorted(os.listxattr(path, follow_symlinks=False))}
    if stat.S_ISREG(mode):
        digest = hashlib.sha256()
        with path.open('rb') as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(block)
        data.update(kind="file", sha256=digest.hexdigest())
    elif stat.S_ISLNK(mode):
        data.update(kind="symlink", target=os.readlink(path))
    elif stat.S_ISDIR(mode):
        data.update(kind="directory", entries={p.name: _entry(p) for p in sorted(path.iterdir())})
    else:
        data.update(kind="special", type=stat.S_IFMT(mode), device=info.st_rdev)
    return data


def _upper_entry(root: Path, logical: str) -> dict:
    path = root / logical.lstrip('/')
    return dict(entry=_entry(path), whiteout=_entry(path.parent / f".wh.{path.name}"))


def _groups(paths) -> list[set[str]]:
    from .ledger import Ledger
    remaining = set(paths)
    groups = []
    while remaining:
        group = {remaining.pop()}
        while True:
            additions = {p for p in remaining if any(Ledger._paths_overlap(p, q) for q in group)}
            if not additions:
                break
            group.update(additions)
            remaining.difference_update(additions)
        groups.append(group)
    return groups


@dataclass
class FileRecoveryAudit:
    """Capture expected versions BEFORE any strategy changes the history."""
    plan: FileRecoveryPlan
    expected: dict[str, dict]
    retained: dict[str, dict]
    checkpoint_files: dict[str, dict]
    lower: dict[str, dict]

    @classmethod
    def capture(cls, tx: AgentTX, plan: FileRecoveryPlan) -> FileRecoveryAudit:
        if _signature(tx) != plan.history_signature:
            raise ValueError("file recovery plan is stale")
        tx.start()
        assert tx.pool is not None and tx.pool.layers is not None
        tx.pool._stop_worker()
        mutations = [(step.step_id, effect.path) for step in tx.ledger.steps
                     if step.step_id in plan.active_step_ids for effect in step.effects
                     if effect.kind in MUTATIONS
                     and (tx.commit_policy is None or not tx.commit_policy.is_ignored(effect.path))]
        # Required effects remain part of the expected state even if an
        # incomplete policy fails to select them. This catches physical residue.
        withdrawn = set(plan.required_step_ids) | set(plan.rollback_targets)
        restored = {path for i, path in mutations if i in withdrawn}
        expected = {}
        for group in _groups(restored):
            first = min(i for i, path in mutations if i in withdrawn and path in group)
            for path in group:
                with tx.pool.layers._path_source(first, path) as source:
                    expected[path] = _upper_entry(source, path)
        upper = tx.pool.session_dir / 'upperdir'
        retained = {path: _upper_entry(upper, path) for i, path in mutations
                    if i not in plan.rollback_targets
                    and not any(tx._paths_overlap(path, p) for p in restored)}
        checkpoint_files = {}
        if plan.root_step_ids:
            # Rolled-back calls leave no effects and may have had their saved
            # versions collected. The first still-active call after the
            # boundary has the same pre-state as that boundary.
            reference_before = min(i for i in plan.active_step_ids
                                   if i > plan.checkpoint_step)
            for path in plan.paths:
                with tx.pool.layers._path_source(reference_before, path) as source:
                    checkpoint_files[path] = _upper_entry(source, path)
        lower = {path: _entry(Path(path)) for path in restored | set(plan.paths)}
        return cls(plan, expected, retained, checkpoint_files, lower)

    def check(self, tx: AgentTX) -> dict:
        assert tx.pool is not None
        plan = self.plan
        upper = tx.pool.session_dir / 'upperdir'
        active = {step.step_id for step in tx.ledger.steps if step.status != "rolled_back"}
        visible = set(tx.conversation.active_step_ids())
        withdrawn = set(plan.active_step_ids) - active
        required = set(plan.required_step_ids)
        residue = sorted(required & active)
        context_residue = sorted(required & visible)
        dangling = sorted((parent, child) for parent, child in plan.edges
                          if parent in withdrawn and child in active)
        changed = lambda entries: sorted(p for p, value in entries.items()
                                        if _upper_entry(upper, p) != value)
        state_mismatches = changed(self.expected)
        retained_changed = changed(self.retained)
        checkpoint_mismatches = changed(self.checkpoint_files)
        lower_changed = sorted(p for p, value in self.lower.items() if _entry(Path(p)) != value)
        expected_visible = set(plan.visible_step_ids) - withdrawn
        context_mismatch = sorted(visible ^ expected_visible)
        selection_matches = withdrawn == set(plan.rollback_targets)
        valid = bool(plan.root_step_ids) and not any((
            residue, context_residue, dangling, state_mismatches, retained_changed,
            checkpoint_mismatches, lower_changed, context_mismatch,
            not selection_matches, len(tx.ledger.steps) != plan.history_end,
        ))
        return dict(
            valid=valid, basis='recorded file/result dependencies and saved versions',
            semantic_task_success_checked=False, checkpoint_step=plan.checkpoint_step,
            problem_files=list(plan.paths), policy=plan.policy,
            required_step_ids=list(plan.required_step_ids), withdrawn_step_ids=sorted(withdrawn),
            residual_step_ids=residue, residual_context_step_ids=context_residue,
            dangling_dependency_edges=dangling, file_state_mismatches=state_mismatches,
            checkpoint_file_mismatches=checkpoint_mismatches,
            retained_file_changes=retained_changed, lower_file_changes=lower_changed,
            context_visibility_mismatches=context_mismatch, selection_matches=selection_matches,
            independent_steps_discarded=sorted(withdrawn - required),
            reason='' if plan.root_step_ids else 'no recorded file mutation after the checkpoint',
        )


@dataclass
class FileRecoveryOutcome:
    plan: FileRecoveryPlan
    audit: dict
    applied: bool
    status: str

    def to_dict(self) -> dict:
        return asdict(self)


def recover_file(
    tx: AgentTX, paths: Sequence[str], *, checkpoint_step: Optional[int] = None,
    policy: str = "causal", dry_run: bool = False,
) -> FileRecoveryOutcome:
    plan = plan_file_recovery(tx, paths, checkpoint_step=checkpoint_step, policy=policy)
    try:
        audit = FileRecoveryAudit.capture(tx, plan)
    except (OSError, ValueError) as error:
        return FileRecoveryOutcome(plan, dict(valid=False,
            reason=f'{type(error).__name__}: {error}'), False, 'inconclusive')
    if not plan.root_step_ids:
        return FileRecoveryOutcome(plan, audit.check(tx), False, 'inconclusive')
    try:
        with tx.fork() as trial:
            apply_file_recovery_plan(trial, plan)
            checked = audit.check(trial)
    except (OSError, ValueError) as error:
        checked = dict(valid=False, reason=f'{type(error).__name__}: {error}')
    if not checked['valid'] or dry_run:
        return FileRecoveryOutcome(plan, checked, False,
                                   'verified' if checked['valid'] else 'inconclusive')
    apply_file_recovery_plan(tx, plan)
    checked = audit.check(tx)
    return FileRecoveryOutcome(plan, checked, True,
                               'recovered' if checked['valid'] else 'verification_failed')
