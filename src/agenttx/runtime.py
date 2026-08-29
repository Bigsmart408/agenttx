"""AgentTX runtime: tool-boundary interception + shared semisolate + ledger."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

from .commit_wal import CommitWAL
from .conversation import ConversationLog, render_ledger_recovery_notice
from .effects import effects_from_paths
from .ledger import Effect, EffectKind, Ledger
from .object_identity import (
    HardlinkCatalog,
    HardlinkGroup,
    expand_hardlink_paths,
)
from .policy import CommitPolicy
from .semisolate import SharedSemisolate


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Durably replace a JSON metadata file without exposing partial content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_fd = os.open(str(path.parent), directory_flags)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _load_conversation(session_dir: Path) -> ConversationLog:
    path = Path(session_dir) / "conversation.json"
    if not path.exists():
        return ConversationLog()
    return ConversationLog.from_dict(json.loads(path.read_text(encoding="utf-8")))


@dataclass
class ToolCallRecord:
    step_id: int
    tool_name: str
    argv: List[str]
    returncode: int
    duration_s: float
    effects: List[Effect] = field(default_factory=list)
    parents: List[int] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""

    @property
    def exit_code(self) -> int:
        return self.returncode

    @property
    def effect_count(self) -> int:
        return len(self.effects)


@dataclass
class AgentTX:
    """Session-oriented API used by CLI and integration tests."""

    workspace: Path
    ledger: Ledger = field(default_factory=Ledger)
    history: List[ToolCallRecord] = field(default_factory=list)
    pool: Optional[SharedSemisolate] = None
    hide_network: bool = False
    trace_reads: bool = True
    trace_backend: str = "auto"
    commit_policy: Optional[CommitPolicy] = None
    object_catalog: HardlinkCatalog = field(default_factory=HardlinkCatalog)
    conversation: ConversationLog = field(default_factory=ConversationLog)
    _catalog_initialized: bool = False
    _meta_path: Optional[Path] = None

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace).resolve()
        if self.commit_policy is None:
            self.commit_policy = CommitPolicy(workdir=self.workspace)

    @classmethod
    def begin(
        cls,
        workdir: Optional[Path] = None,
        session_dir: Optional[Path] = None,
        hide_network: bool = False,
        trace_reads: bool = True,
        trace_backend: str = "auto",
        commit_policy: Optional[CommitPolicy] = None,
    ) -> "AgentTX":
        workdir = Path(workdir).resolve() if workdir else Path.cwd()
        pool = SharedSemisolate(
            workspace=workdir,
            sandbox_dir=session_dir,
            hide_network=hide_network,
            trace_reads=trace_reads,
            trace_backend=trace_backend,
        )
        # if caller passed session_dir, SharedSemisolate should not destroy foreign dirs? 
        # mark ownership: if session_dir provided, still allow destroy on close(destroy=True)
        if session_dir is not None:
            pool._owns_sandbox = True
        tx = cls(
            workspace=workdir,
            pool=pool,
            hide_network=hide_network,
            trace_reads=trace_reads,
            trace_backend=trace_backend,
            commit_policy=commit_policy,
        )
        tx.object_catalog.refresh(workdir)
        tx._catalog_initialized = True
        tx._recover_commit_wal()
        tx._persist()
        return tx

    @classmethod
    def load(
        cls,
        session_dir: Path,
        commit_policy: Optional[CommitPolicy] = None,
    ) -> "AgentTX":
        session_dir = Path(session_dir)
        meta = session_dir / "agenttx.json"
        if not meta.exists():
            raise FileNotFoundError(f"no agenttx.json in {session_dir}")
        data = json.loads(meta.read_text(encoding="utf-8"))
        policy_data = data.get("commit_policy", {})
        if commit_policy is None and policy_data:
            commit_policy = CommitPolicy(
                workdir=Path(data["workspace"]),
                allow_globs=list(policy_data.get("allow_globs", ["**/*"])),
                deny_globs=list(policy_data.get("deny_globs", [])),
            )
        tx = cls(
            workspace=Path(data["workspace"]),
            ledger=Ledger.from_dict(data.get("ledger", {})),
            hide_network=bool(data.get("hide_network", False)),
            trace_reads=bool(data.get("trace_reads", True)),
            trace_backend=str(data.get("trace_backend", "auto")),
            commit_policy=commit_policy,
            object_catalog=HardlinkCatalog.from_dict(data.get("object_catalog")),
            conversation=_load_conversation(session_dir),
        )
        tx.pool = SharedSemisolate(
            workspace=tx.workspace,
            sandbox_dir=session_dir,
            hide_network=tx.hide_network,
            trace_reads=tx.trace_reads,
            trace_backend=tx.trace_backend,
        )
        tx.pool._owns_sandbox = True
        tx._recover_commit_wal()
        tx.object_catalog.refresh(tx.workspace)
        tx._catalog_initialized = True
        # Layer snapshots are keyed by ledger step id. Resume at the next id so
        # a loaded session cannot overwrite an earlier before_NNNN snapshot.
        tx.pool._step_count = len(tx.ledger.steps)
        # restore cached summary if overlay already has state
        tx.pool.refresh_summary()
        tx.pool._cached_digests = tx.pool.upperdir_digests()
        tx._meta_path = meta
        return tx

    def _recover_commit_wal(self) -> None:
        """Resolve an interrupted host commit before exposing the session."""
        assert self.pool is not None
        wal = CommitWAL.load(self.pool.session_dir)
        if wal is None:
            return
        finalize = self.ledger.committed_frontier >= wal.up_to and wal.phase in {
            "materialized",
            "committed",
        }
        if not finalize:
            wal.restore(self.workspace, self.pool.session_dir / "upperdir")
            if self.ledger.committed_frontier >= wal.up_to:
                self.ledger = Ledger.from_dict(wal.payload["ledger_before"])
                self._persist()
        try:
            wal.cleanup()
        except OSError:
            # The WAL intent was removed before its backup; an orphaned backup
            # is harmless and will be reclaimed by the next prepare.
            pass
        self.pool._cached_summary = {}
        self.pool._cached_digests = self.pool.upperdir_digests()

    def _persist(self) -> None:
        assert self.pool is not None
        assert self.commit_policy is not None
        meta = self.pool.session_dir / "agenttx.json"
        payload = {
            "workspace": str(self.workspace),
            "hide_network": self.hide_network,
            "trace_reads": self.trace_reads,
            "trace_backend": self.trace_backend,
            "commit_policy": {
                "allow_globs": list(self.commit_policy.allow_globs),
                "deny_globs": list(self.commit_policy.deny_globs),
            },
            "ledger": self.ledger.to_dict(),
            "object_catalog": self.object_catalog.to_dict(),
        }
        _atomic_write_json(meta, payload)
        self._persist_conversation()
        self._meta_path = meta

    def start(self) -> None:
        if self.pool is None:
            self.pool = SharedSemisolate(
                workspace=self.workspace,
                hide_network=self.hide_network,
                trace_reads=self.trace_reads,
                trace_backend=self.trace_backend,
            )

    def _overlay_entry(self, logical: Path) -> Optional[Path]:
        assert self.pool is not None
        upper = self.pool.session_dir / "upperdir"
        try:
            relative = logical.relative_to(Path("/"))
        except ValueError:
            return None
        upper_entry = upper.joinpath(*relative.parts)
        if os.path.lexists(str(upper_entry)):
            return upper_entry
        if os.path.lexists(str(logical)):
            return logical
        return None

    def _resolve_alias_ancestors(self, path: str) -> str:
        """Resolve lexical symlink ancestors in the merged workspace view."""
        candidate = Path(path)
        try:
            relative = candidate.relative_to(self.workspace)
        except ValueError:
            return path
        if not relative.parts:
            return path
        current = self.workspace
        for part in relative.parts[:-1]:
            current = Path(os.path.normpath(str(current / part)))
            for _ in range(40):
                entry = self._overlay_entry(current)
                if entry is None or not stat.S_ISLNK(entry.lstat().st_mode):
                    break
                target = os.readlink(str(entry))
                current = Path(
                    os.path.normpath(
                        target if os.path.isabs(target) else str(current.parent / target)
                    )
                )
            try:
                current.relative_to(self.workspace)
            except ValueError:
                return path
        resolved = Path(os.path.normpath(str(current / relative.parts[-1])))
        try:
            resolved.relative_to(self.workspace)
        except ValueError:
            return path
        return str(resolved)

    def _canonicalize_effects(self, effects: List[Effect]) -> List[Effect]:
        out = list(effects)
        seen = {(effect.path, effect.kind) for effect in out}
        for effect in effects:
            canonical = self._resolve_alias_ancestors(effect.path)
            if canonical == effect.path:
                continue
            alias_effect = Effect(canonical, effect.kind)
            if (alias_effect.path, alias_effect.kind) not in seen:
                out.append(alias_effect)
                seen.add((alias_effect.path, alias_effect.kind))
        return out

    def path_exists(self, path: Path) -> bool:
        """Check a path in the merged host/upperdir view for trusted tools."""
        logical = Path(path).resolve()
        if self.pool is not None:
            upper = self.pool.session_dir / "upperdir"
            relative = logical.relative_to(Path("/"))
            direct = upper.joinpath(*relative.parts)
            whiteout = direct.parent / f".wh.{direct.name}"
            if os.path.lexists(str(whiteout)):
                return False
            if os.path.lexists(str(direct)):
                try:
                    entry_stat = direct.lstat()
                except FileNotFoundError:
                    return False
                if stat.S_ISCHR(entry_stat.st_mode) and entry_stat.st_rdev == os.makedev(0, 0):
                    return False
        entry = self._overlay_entry(logical)
        return entry is not None

    def run_tool(
        self,
        tool_name: str,
        argv: Sequence[str],
        extra_reads: Optional[Sequence[str]] = None,
        extra_effects: Optional[Sequence[Effect]] = None,
        trace_reads: Optional[bool] = None,
        timeout_s: Optional[float] = None,
    ) -> ToolCallRecord:
        self.start()
        assert self.pool is not None
        # Host identity is stable while the overlay is mounted.  The catalog
        # is initialized once per session (and refreshed at commit/rebase
        # boundaries), avoiding a full workspace walk on every short tool.
        if not self._catalog_initialized:
            self.object_catalog.refresh(self.workspace)
            self._catalog_initialized = True
        result = self.pool.run(list(argv), trace_reads=trace_reads, timeout_s=timeout_s)
        effects = list(result.effects)
        if extra_reads:
            effects.extend(effects_from_paths(reads=extra_reads))
        if extra_effects:
            effects.extend(extra_effects)
        effects = self._canonicalize_effects(effects)
        effects = self.object_catalog.annotate(effects)
        step = self.ledger.add_step(tool_name, effects)
        rec = ToolCallRecord(
            step_id=step.step_id,
            tool_name=tool_name,
            argv=list(argv),
            returncode=result.returncode,
            duration_s=result.duration_s,
            effects=effects,
            parents=sorted(step.parents),
            stdout=result.stdout,
            stderr=result.stderr,
        )
        self.history.append(rec)
        self._persist()
        return rec

    def _conversation_path(self) -> Path:
        assert self.pool is not None
        return self.pool.session_dir / "conversation.json"

    def _persist_conversation(self) -> None:
        if self.conversation.is_empty():
            return
        _atomic_write_json(self._conversation_path(), self.conversation.to_dict())

    def _rewind_conversation(self, targets: Sequence[int], mode: str) -> dict:
        notice = render_ledger_recovery_notice(self.ledger, targets, mode=mode)
        return self.conversation.rewind(targets, notice, mode=mode)

    def rollback_from(self, step_id: Optional[int] = None) -> List[int]:
        if not self.ledger.steps:
            return []
        active = [
            s.step_id
            for s in self.ledger.steps
            if s.step_id > self.ledger.committed_frontier and s.status != "rolled_back"
        ]
        if not active:
            return []
        if step_id is None:
            step_id = max(active)
        targets = self.ledger.cascade_rollback_targets(step_id)
        self.ledger.mark_rolled_back(targets)
        assert self.pool is not None
        self.pool.rollback_steps(targets)
        self._rewind_conversation(targets, mode="temporal")
        self._persist()
        return targets

    def rollback(self, step_id: Optional[int] = None) -> List[int]:
        return self.rollback_from(step_id)

    def rollback_causal_from(self, step_id: Optional[int] = None) -> List[int]:
        """Rollback a failed step and graph dependents, retaining independent work."""
        if not self.ledger.steps:
            return []
        active = [
            s.step_id
            for s in self.ledger.steps
            if s.step_id > self.ledger.committed_frontier
            and s.status != "rolled_back"
        ]
        if not active:
            return []
        if step_id is None:
            step_id = max(active)
        if step_id <= self.ledger.committed_frontier:
            raise ValueError("cannot roll back a committed step")

        targets = self.ledger.causal_dependents(step_id)
        if any(
            target <= self.ledger.committed_frontier for target in targets
        ):
            raise ValueError("causal rollback crosses the committed frontier")
        target_set = set(targets)
        target_paths = {
            effect.path
            for step in self.ledger.steps
            if step.step_id in target_set
            for effect in step.effects
            if effect.kind in (EffectKind.WRITE, EffectKind.DELETE)
        }
        retained_effects = [
            effect
            for step in self.ledger.steps
            if step.step_id > self.ledger.committed_frontier
            and step.status != "rolled_back"
            and step.step_id not in target_set
            for effect in step.effects
        ]
        conflicts = sorted(
            (target_path, effect.path)
            for target_path in target_paths
            for effect in retained_effects
            if self._paths_overlap(target_path, effect.path)
        )
        if conflicts:
            details = ", ".join(
                f"{left} <> {right}" for left, right in conflicts[:8]
            )
            raise ValueError(
                "causal rollback overlaps retained effects; "
                f"cannot reconstruct safely: {details}"
            )

        assert self.pool is not None
        self.pool.rollback_causal(targets, sorted(target_paths))
        self.ledger.mark_rolled_back(targets)
        self._rewind_conversation(targets, mode="causal")
        self._persist()
        return targets

    def rollback_causal(self, step_id: Optional[int] = None) -> List[int]:
        return self.rollback_causal_from(step_id)

    @staticmethod
    def _paths_overlap(left: str, right: str) -> bool:
        left = left.rstrip("/") or "/"
        right = right.rstrip("/") or "/"
        return (
            left == right
            or left.startswith(right + "/")
            or right.startswith(left + "/")
        )

    def _commit_path_plan(self, up_to: int) -> tuple[List[str], List[tuple[str, str]]]:
        selected = set()
        later = set()
        for step in self.ledger.steps:
            if step.status == "rolled_back" or step.step_id <= self.ledger.committed_frontier:
                continue
            target = selected if step.step_id <= up_to else later
            for effect in step.effects:
                if effect.kind in (EffectKind.WRITE, EffectKind.DELETE):
                    if self.commit_policy is not None and self.commit_policy.is_ignored(
                        effect.path
                    ):
                        continue
                    target.add(effect.path)

        # A path-only frontier is unsound for an existing hard-link group:
        # committing one name by rename creates a fresh inode and leaves every
        # untouched alias stale.  Expand both sides before conflict checking so
        # an alias written by a later step is treated as the same object.
        selected_paths, _ = expand_hardlink_paths(selected, self.workspace)
        later_paths, _ = expand_hardlink_paths(later, self.workspace)

        conflicts = sorted(
            (path, later_path)
            for path in selected_paths
            for later_path in later_paths
            if self._paths_overlap(path, later_path)
        )
        return selected_paths, conflicts

    def _commit_paths(self, up_to: int) -> List[str]:
        selected, conflicts = self._commit_path_plan(up_to)
        if conflicts:
            details = ", ".join(f"{a} <> {b}" for a, b in conflicts[:8])
            raise ValueError(
                "partial commit crosses later writes to the same path; "
                f"roll back or include those steps first: {details}"
            )
        return selected

    def _historical_snapshot_step(self, up_to: int) -> int:
        assert self.pool is not None and self.pool.layers is not None
        candidates = [
            step.step_id
            for step in self.ledger.steps
            if step.step_id > up_to
            and step.step_id > self.ledger.committed_frontier
            and step.status != "rolled_back"
        ]
        if not candidates:
            raise ValueError("historical commit has no retained later snapshot")
        snapshot_step = min(candidates)
        snapshot = self.pool.layers.root / f"before_{snapshot_step:04d}"
        if not snapshot.exists():
            raise ValueError(
                "historical commit snapshot is unavailable; "
                f"expected {snapshot}"
            )
        return snapshot_step

    def commit_frontier(self, up_to: Optional[int] = None) -> int:
        self._recover_commit_wal()
        if up_to is not None and (up_to < 0 or up_to >= len(self.ledger.steps)):
            raise ValueError(f"invalid commit frontier {up_to}")
        if not self.ledger.steps:
            return self.ledger.committed_frontier
        active = [
            s.step_id
            for s in self.ledger.steps
            if s.step_id > self.ledger.committed_frontier and s.status != "rolled_back"
        ]
        if not active:
            return self.ledger.committed_frontier
        if up_to is None:
            up_to = max(active)
        if up_to <= self.ledger.committed_frontier:
            return self.ledger.committed_frontier
        assert self.commit_policy is not None
        self.commit_policy.assert_committable(self.ledger, up_to)
        paths, conflicts = self._commit_path_plan(up_to)
        # The ledger policy sees the originally named effect paths.  Alias
        # closure can add more directory entries, so enforce the same policy
        # on every path that will actually be materialized.
        denied_aliases = [
            decision
            for path in paths
            if not (decision := self.commit_policy.check_path(path)).allowed
        ]
        if denied_aliases:
            details = ", ".join(
                f"{decision.path} ({decision.reason})"
                for decision in denied_aliases[:8]
            )
            raise PermissionError(
                "commit blocked by policy for a hard-link alias: " + details
            )
        historical_step = self._historical_snapshot_step(up_to) if conflicts else None
        retained_suffix = any(
            step.step_id > up_to
            and step.step_id > self.ledger.committed_frontier
            and step.status != "rolled_back"
            for step in self.ledger.steps
        )
        assert self.pool is not None
        if not paths:
            self.ledger.advance_frontier(up_to)
            self._persist()
            return self.ledger.committed_frontier

        # Keep the complete existing host hard-link groups in the durable WAL,
        # then route those groups through in-place object materialization.
        paths, hardlink_groups = expand_hardlink_paths(paths, self.workspace)

        ledger_before = self.ledger.to_dict()
        wal = CommitWAL.prepare(
            self.pool.session_dir,
            self.workspace,
            self.pool.session_dir / "upperdir",
            paths,
            up_to,
            ledger_before,
        )
        try:
            wal.mark("applying")
            if historical_step is None:
                try:
                    cp = self.pool.commit(paths=paths, hardlink_groups=hardlink_groups)
                except TypeError as exc:
                    # Keep older test doubles and third-party semisolate
                    # backends source-compatible while the production pool
                    # uses the hard-link-aware keyword.
                    if "hardlink_groups" not in str(exc):
                        raise
                    cp = self.pool.commit(paths=paths)
            else:
                cp = self.pool.commit_from_snapshot(
                    historical_step, paths, hardlink_groups=hardlink_groups
                )
            if cp.returncode != 0:
                raise RuntimeError(f"try commit failed: {cp.stderr}")
            if retained_suffix:
                # The host lower generation changed while later speculative
                # effects remain.  Normalize the retained upper before WAL
                # publication so a remount cannot reuse stale index origins.
                self.pool.rebase_upper_generation()
            wal.mark("materialized")
            self.ledger.advance_frontier(up_to)
            self.object_catalog.generation += 1
            self.object_catalog.refresh(self.workspace)
            self._catalog_initialized = True
            self._persist()
        except Exception:
            if wal.phase == "materialized":
                # Metadata persistence may have succeeded before raising (for
                # example, after a directory fsync failure). Leave the WAL for
                # reload-time reconciliation instead of guessing.
                self.ledger = Ledger.from_dict(ledger_before)
                raise
            self.ledger = Ledger.from_dict(ledger_before)
            wal.restore(self.workspace, self.pool.session_dir / "upperdir")
            wal.cleanup()
            self.pool._cached_summary = {}
            self.pool._cached_digests = self.pool.upperdir_digests()
            raise

        try:
            wal.mark("committed")
            wal.cleanup()
        except OSError:
            # The durable frontier is already persisted; a later load will
            # finalize and remove this WAL.
            pass
        return self.ledger.committed_frontier

    def commit(self, up_to: Optional[int] = None) -> int:
        return self.commit_frontier(up_to)

    def status(self) -> dict:
        return {
            "workspace": str(self.workspace),
            "session_dir": str(self.pool.session_dir) if self.pool else None,
            "hide_network": self.hide_network,
            "trace_reads": self.trace_reads,
            "trace_backend": self.trace_backend,
            "steps": len(self.ledger.steps),
            "committed_frontier": self.ledger.committed_frontier,
        }

    def close(self, destroy: bool = False) -> None:
        if self.pool is not None:
            self._persist()
            self.pool.close(destroy=destroy)
            if destroy:
                self.pool = None

    def dump_ledger(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.ledger.to_dict(), indent=2) + "\n", encoding="utf-8")

    def __enter__(self) -> "AgentTX":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close(destroy=True)


# Back-compat alias used by earlier benches/demos
AgentTXRuntime = AgentTX
