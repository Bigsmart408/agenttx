"""Coding-agent harness: drive multi-step tool trajectories through AgentTX."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from .ledger import Effect, EffectKind
from .policy import CommitPolicy
from .runtime import AgentTX, ToolCallRecord


ToolFn = Callable[[AgentTX, dict], ToolCallRecord]


@dataclass
class TrajectoryStep:
    tool: str
    args: dict = field(default_factory=dict)


@dataclass
class TrajectoryResult:
    records: List[ToolCallRecord]
    wall_s: float
    committed: bool
    ledger_path: Optional[Path] = None


class CodingAgentHarness:
    """Minimal coding-agent loop with tool-boundary interception."""

    def __init__(
        self,
        workdir: Path,
        session_dir: Optional[Path] = None,
        policy: Optional[CommitPolicy] = None,
        auto_commit: bool = False,
        trace_reads: bool = True,
    ) -> None:
        self.workdir = Path(workdir).resolve()
        self.policy = policy or CommitPolicy(workdir=self.workdir)
        self.auto_commit = auto_commit
        self.trace_reads = trace_reads
        self.tx = AgentTX.begin(
            workdir=self.workdir,
            session_dir=session_dir,
            trace_reads=trace_reads,
            commit_policy=self.policy,
        )
        self.tools: Dict[str, ToolFn] = {
            "write_file": self._write_file,
            "append_file": self._append_file,
            "read_file": self._read_file,
            "run_shell": self._run_shell,
            "run_tests": self._run_tests,
            "delete_file": self._delete_file,
        }

    def close(self, destroy: bool = True) -> None:
        self.tx.close(destroy=destroy)

    def _rel(self, path: str) -> Path:
        p = Path(path)
        if not p.is_absolute():
            p = self.workdir / p
        return p.resolve()

    def _write_file(self, tx: AgentTX, args: dict) -> ToolCallRecord:
        path = self._rel(args["path"])
        content = args.get("content", "")
        parent = str(path.parent)
        cmd = (
            f"mkdir -p '{parent}' && cat > '{path}' <<'AGENTTX_EOF'\n"
            f"{content}\nAGENTTX_EOF"
        )
        return tx.run_tool("write_file", ["bash", "-c", cmd], trace_reads=False)

    def _append_file(self, tx: AgentTX, args: dict) -> ToolCallRecord:
        path = self._rel(args["path"])
        content = args.get("content", "")
        cmd = (
            f"mkdir -p '{path.parent}' && cat >> '{path}' <<'AGENTTX_EOF'\n"
            f"{content}\nAGENTTX_EOF"
        )
        return tx.run_tool("append_file", ["bash", "-c", cmd], trace_reads=False)

    def _read_file(self, tx: AgentTX, args: dict) -> ToolCallRecord:
        path = self._rel(args["path"])
        effect_kind = EffectKind.READ if tx.path_exists(path) else EffectKind.NEGATIVE
        return tx.run_tool(
            "read_file",
            ["bash", "-c", f"cat '{path}'"],
            extra_effects=[Effect(str(path), effect_kind)],
            trace_reads=False,
        )

    def _run_shell(self, tx: AgentTX, args: dict) -> ToolCallRecord:
        return tx.run_tool("run_shell", ["bash", "-c", args["cmd"]])

    def _run_tests(self, tx: AgentTX, args: dict) -> ToolCallRecord:
        cmd = args.get("cmd", "python3 -m pytest -q")
        return tx.run_tool("run_tests", ["bash", "-c", cmd])

    def _delete_file(self, tx: AgentTX, args: dict) -> ToolCallRecord:
        path = self._rel(args["path"])
        return tx.run_tool(
            "delete_file",
            ["bash", "-c", f"rm -f '{path}'"],
            trace_reads=False,
        )

    def call_tool(self, name: str, args: Optional[dict] = None) -> ToolCallRecord:
        if name not in self.tools:
            raise KeyError(f"unknown tool: {name}")
        return self.tools[name](self.tx, args or {})

    def run_trajectory(
        self, steps: Sequence[TrajectoryStep], commit: Optional[bool] = None
    ) -> TrajectoryResult:
        do_commit = self.auto_commit if commit is None else commit
        records: List[ToolCallRecord] = []
        t0 = time.perf_counter()
        for step in steps:
            records.append(self.call_tool(step.tool, step.args))
        wall = time.perf_counter() - t0
        committed = False
        if do_commit:
            up_to = max((r.step_id for r in records), default=-1)
            self.tx.commit(up_to)
            committed = True
        return TrajectoryResult(records=records, wall_s=wall, committed=committed)

    def dump_ledger(self, path: Path) -> None:
        self.tx.dump_ledger(path)
