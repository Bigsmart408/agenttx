"""AgentTX runtime: tool-boundary interception + shared semisolate + ledger."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

from .effects import effects_from_paths
from .ledger import Effect, EffectKind, Ledger
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
    _meta_path: Optional[Path] = None

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace).resolve()

    @classmethod
    def begin(
        cls,
        workdir: Optional[Path] = None,
        session_dir: Optional[Path] = None,
        hide_network: bool = False,
    ) -> "AgentTX":
        workdir = Path(workdir).resolve() if workdir else Path.cwd()
        pool = SharedSemisolate(
            workspace=workdir,
            sandbox_dir=session_dir,
            hide_network=hide_network,
        )
        # if caller passed session_dir, SharedSemisolate should not destroy foreign dirs? 
        # mark ownership: if session_dir provided, still allow destroy on close(destroy=True)
        if session_dir is not None:
            pool._owns_sandbox = True
        tx = cls(workspace=workdir, pool=pool, hide_network=hide_network)
        tx._persist()
        return tx

    @classmethod
    def load(cls, session_dir: Path) -> "AgentTX":
        session_dir = Path(session_dir)
        meta = session_dir / "agenttx.json"
        if not meta.exists():
            raise FileNotFoundError(f"no agenttx.json in {session_dir}")
        data = json.loads(meta.read_text(encoding="utf-8"))
        tx = cls(
            workspace=Path(data["workspace"]),
            ledger=Ledger.from_dict(data.get("ledger", {})),
            hide_network=bool(data.get("hide_network", False)),
        )
        tx.pool = SharedSemisolate(
            workspace=tx.workspace,
            sandbox_dir=session_dir,
            hide_network=tx.hide_network,
        )
        tx.pool._owns_sandbox = True
        # Layer snapshots are keyed by ledger step id. Resume at the next id so
        # a loaded session cannot overwrite an earlier before_NNNN snapshot.
        tx.pool._step_count = len(tx.ledger.steps)
        # restore cached summary if overlay already has state
        tx.pool.refresh_summary()
        tx.pool._cached_digests = tx.pool.upperdir_digests()
        tx._meta_path = meta
        return tx

    def _persist(self) -> None:
        assert self.pool is not None
        meta = self.pool.session_dir / "agenttx.json"
        payload = {
            "workspace": str(self.workspace),
            "hide_network": self.hide_network,
            "ledger": self.ledger.to_dict(),
        }
        _atomic_write_json(meta, payload)
        self._meta_path = meta

    def start(self) -> None:
        if self.pool is None:
            self.pool = SharedSemisolate(workspace=self.workspace, hide_network=self.hide_network)

    def run_tool(
        self,
        tool_name: str,
        argv: Sequence[str],
        extra_reads: Optional[Sequence[str]] = None,
    ) -> ToolCallRecord:
        self.start()
        assert self.pool is not None
        result = self.pool.run(list(argv))
        effects = list(result.effects)
        if extra_reads:
            effects.extend(effects_from_paths(reads=extra_reads))
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
        self._persist()
        return targets

    def rollback(self, step_id: Optional[int] = None) -> List[int]:
        return self.rollback_from(step_id)

    @staticmethod
    def _paths_overlap(left: str, right: str) -> bool:
        left = left.rstrip("/") or "/"
        right = right.rstrip("/") or "/"
        return (
            left == right
            or left.startswith(right + "/")
            or right.startswith(left + "/")
        )

    def _commit_paths(self, up_to: int) -> List[str]:
        selected = set()
        later = set()
        for step in self.ledger.steps:
            if step.status == "rolled_back" or step.step_id <= self.ledger.committed_frontier:
                continue
            target = selected if step.step_id <= up_to else later
            for effect in step.effects:
                if effect.kind in (EffectKind.WRITE, EffectKind.DELETE):
                    target.add(effect.path)

        conflicts = sorted(
            (path, later_path)
            for path in selected
            for later_path in later
            if self._paths_overlap(path, later_path)
        )
        if conflicts:
            details = ", ".join(f"{a} <> {b}" for a, b in conflicts[:8])
            raise ValueError(
                "partial commit crosses later writes to the same path; "
                f"roll back or include those steps first: {details}"
            )
        return sorted(selected)

    def commit_frontier(self, up_to: Optional[int] = None) -> int:
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
        paths = self._commit_paths(up_to)
        assert self.pool is not None
        if paths:
            cp = self.pool.commit(paths=paths)
            if cp.returncode != 0:
                raise RuntimeError(f"try commit failed: {cp.stderr}")
        self.ledger.advance_frontier(up_to)
        self._persist()
        return self.ledger.committed_frontier

    def commit(self, up_to: Optional[int] = None) -> int:
        return self.commit_frontier(up_to)

    def status(self) -> dict:
        return {
            "workspace": str(self.workspace),
            "session_dir": str(self.pool.session_dir) if self.pool else None,
            "hide_network": self.hide_network,
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
