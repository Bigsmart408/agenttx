"""Commit / effect policy for AgentTX trajectories."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Sequence

from .ledger import Effect, EffectKind, Ledger


IGNORE_COMMIT_GLOBS = (
    "*/__pycache__/*",
    "*.pyc",
    "*.pyo",
    "*/.pytest_cache/*",
    "*/.git/*",
)

DEFAULT_DENY = (
    "/etc/*",
    "/usr/*",
    "/bin/*",
    "/sbin/*",
    "/boot/*",
    "/dev/*",
    "/proc/*",
    "/sys/*",
    "*/.ssh/*",
    "*/.gnupg/*",
    "*/id_rsa*",
    "*/id_ed25519*",
    "*.pem",
)


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str
    path: str = ""


@dataclass
class CommitPolicy:
    """Path allow/deny policy checked before advancing the commit frontier."""

    workdir: Path
    allow_globs: Sequence[str] = field(default_factory=lambda: ["**/*"])
    deny_globs: Sequence[str] = field(default_factory=lambda: list(DEFAULT_DENY))

    def __post_init__(self) -> None:
        self.workdir = Path(self.workdir).resolve()

    def _match(self, path: str, patterns: Iterable[str]) -> bool:
        p = path
        for pat in patterns:
            if fnmatch.fnmatch(p, pat):
                return True
            try:
                rel = str(Path(p).resolve().relative_to(self.workdir))
            except Exception:
                rel = p
            if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch("./" + rel, pat):
                return True
        return False

    def check_path(self, path: str) -> PolicyDecision:
        if self._match(path, self.deny_globs):
            return PolicyDecision(False, "denied by deny_globs", path)
        if any(g in ("**/*", "*", "/**") for g in self.allow_globs):
            try:
                Path(path).resolve().relative_to(self.workdir)
                return PolicyDecision(True, "under workdir", path)
            except Exception:
                return PolicyDecision(False, "outside workdir", path)
        if self._match(path, self.allow_globs):
            return PolicyDecision(True, "allowed by allow_globs", path)
        return PolicyDecision(False, "not in allow_globs", path)

    def check_effects(self, effects: Sequence[Effect]) -> List[PolicyDecision]:
        out: List[PolicyDecision] = []
        for e in effects:
            if e.kind in (EffectKind.WRITE, EffectKind.DELETE):
                if self._match(e.path, IGNORE_COMMIT_GLOBS):
                    continue  # ephemeral tooling artifacts
                out.append(self.check_path(e.path))
        return out

    def check_ledger(self, ledger: Ledger, up_to: int) -> List[PolicyDecision]:
        decisions: List[PolicyDecision] = []
        for step in ledger.steps:
            if step.step_id > up_to or step.status == "rolled_back":
                continue
            if step.step_id <= ledger.committed_frontier:
                continue
            decisions.extend(self.check_effects(step.effects))
        return decisions

    def assert_committable(self, ledger: Ledger, up_to: int) -> None:
        bad = [d for d in self.check_ledger(ledger, up_to) if not d.allowed]
        if bad:
            msgs = ", ".join(f"{d.path} ({d.reason})" for d in bad[:8])
            raise PermissionError(f"commit blocked by policy: {msgs}")
