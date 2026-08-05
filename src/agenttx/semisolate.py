"""Shared/incremental semisolate pool backed by binpash/try -N DIR."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .effects import SummaryEntry, diff_summaries, parse_try_summary
from .ledger import Effect, EffectKind


def _default_try_bin() -> Path:
    root = Path(__file__).resolve().parents[2]
    wrapper = root / "scripts" / "try-wrapper.sh"
    if wrapper.exists():
        return wrapper
    raise FileNotFoundError("scripts/try-wrapper.sh not found")


@dataclass
class StepResult:
    step_index: int
    returncode: int
    stdout: str
    stderr: str
    summary_before: Dict[str, SummaryEntry]
    summary_after: Dict[str, SummaryEntry]
    duration_s: float
    effects: List[Effect] = field(default_factory=list)


@dataclass
class SharedSemisolate:
    """One overlay sandbox reused across many tool calls.

    Optimization vs naive per-call try:
    - pay sandbox dir creation once
    - cache last summary in-memory; only one `try summary` per step
    - detect content mutations via upperdir digests (append to existing file)
    """

    workspace: Path
    try_bin: Path = field(default_factory=_default_try_bin)
    sandbox_dir: Optional[Path] = None
    hide_network: bool = False
    _owns_sandbox: bool = False
    _step_count: int = 0
    _closed: bool = False
    _cached_summary: Dict[str, SummaryEntry] = field(default_factory=dict)
    _cached_digests: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace).resolve()
        self.try_bin = Path(self.try_bin)
        if self.sandbox_dir is None:
            cache_root = Path.home() / ".cache" / "agenttx"
            cache_root.mkdir(parents=True, exist_ok=True)
            self.sandbox_dir = Path(
                tempfile.mkdtemp(prefix="agenttx-sandbox-", dir=str(cache_root))
            )
            self._owns_sandbox = True
        else:
            self.sandbox_dir = Path(self.sandbox_dir)
            self.sandbox_dir.mkdir(parents=True, exist_ok=True)

    @property
    def session_dir(self) -> Path:
        assert self.sandbox_dir is not None
        return self.sandbox_dir

    def _run_try(self, args: Sequence[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
        cmd = [str(self.try_bin), *args]
        return subprocess.run(
            cmd,
            cwd=str(cwd or self.workspace),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def refresh_summary(self) -> Dict[str, SummaryEntry]:
        assert self.sandbox_dir is not None
        try:
            if not any(self.sandbox_dir.iterdir()):
                self._cached_summary = {}
                return self._cached_summary
        except FileNotFoundError:
            self._cached_summary = {}
            return self._cached_summary
        cp = self._run_try(["summary", str(self.sandbox_dir)])
        self._cached_summary = parse_try_summary(cp.stdout)
        return self._cached_summary

    def upperdir_digests(self) -> Dict[str, str]:
        assert self.sandbox_dir is not None
        upper = self.sandbox_dir / "upperdir"
        out: Dict[str, str] = {}
        if not upper.exists():
            return out
        for f in upper.rglob("*"):
            if not f.is_file() or f.name.startswith(".wh."):
                continue
            abs_path = "/" + f.relative_to(upper).as_posix()
            try:
                out[abs_path] = hashlib.sha256(f.read_bytes()).hexdigest()
            except OSError:
                continue
        return out

    def run(self, argv: Sequence[str]) -> StepResult:
        if self._closed:
            raise RuntimeError("SharedSemisolate is closed")
        assert self.sandbox_dir is not None
        before = dict(self._cached_summary)
        dig_before = dict(self._cached_digests)
        flags = ["-N", str(self.sandbox_dir)]
        if self.hide_network:
            flags.insert(0, "-x")
        t0 = time.perf_counter()
        cp = self._run_try([*flags, "--", *argv], cwd=self.workspace)
        duration = time.perf_counter() - t0
        after = self.refresh_summary()
        dig_after = self.upperdir_digests()
        self._cached_digests = dig_after

        effects = diff_summaries(before, after)
        # content mutations that stay "added" in summary
        seen = {e.path for e in effects}
        for path, h in dig_after.items():
            if dig_before.get(path) != h and path not in seen:
                effects.append(Effect(path=path, kind=EffectKind.WRITE))
                seen.add(path)
        for path in dig_before:
            if path not in dig_after and path not in seen:
                effects.append(Effect(path=path, kind=EffectKind.DELETE))

        idx = self._step_count
        self._step_count += 1
        return StepResult(
            step_index=idx,
            returncode=cp.returncode,
            stdout=cp.stdout,
            stderr=cp.stderr,
            summary_before=before,
            summary_after=after,
            duration_s=duration,
            effects=effects,
        )

    def step_effects(self, result: StepResult) -> List[Effect]:
        return list(result.effects)

    def commit(self) -> subprocess.CompletedProcess:
        assert self.sandbox_dir is not None
        return self._run_try(["commit", str(self.sandbox_dir)])

    def reset(self) -> None:
        """Drop overlay state but keep the same session directory path."""
        assert self.sandbox_dir is not None
        subprocess.run(["chmod", "-R", "u+rwX", str(self.sandbox_dir)], check=False)
        for child in list(self.sandbox_dir.iterdir()):
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        self._cached_summary = {}
        self._cached_digests = {}

    def close(self, destroy: bool = True) -> None:
        if self._closed:
            return
        self._closed = True
        if destroy and self._owns_sandbox and self.sandbox_dir is not None:
            subprocess.run(["chmod", "-R", "u+rwX", str(self.sandbox_dir)], check=False)
            shutil.rmtree(self.sandbox_dir, ignore_errors=True)
            self.sandbox_dir = None

    def __enter__(self) -> "SharedSemisolate":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
