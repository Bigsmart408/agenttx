"""Typed failure observations and recovery plans for AgentTX."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Sequence, Tuple

from .ledger import Effect


class FailureKind(str, Enum):
    """Failures that must remain distinct at the recovery boundary."""

    VERIFIER_FAILURE = "verifier_failure"
    AGENT_CRASH = "agent_crash"
    SANDBOX_CRASH = "sandbox_crash"
    TOOL_FAILURE = "tool_failure"
    MODEL_IO_FAILURE = "model_io_failure"
    SUSPECTED_HANG = "suspected_hang"
    FORCED_TIMEOUT = "forced_timeout"
    CANCELLED = "cancelled"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class FailureObservation:
    """Trusted evidence that a verifier observed a bad transaction state."""

    verifier_step_id: int
    command: Tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    effects: Tuple[Effect, ...] = ()
    last_passing_step: Optional[int] = None
    kind: FailureKind = FailureKind.VERIFIER_FAILURE

    @classmethod
    def from_record(
        cls,
        record: object,
        *,
        last_passing_step: Optional[int] = None,
    ) -> "FailureObservation":
        return cls(
            verifier_step_id=int(getattr(record, "step_id")),
            command=tuple(str(item) for item in getattr(record, "argv", ())),
            returncode=int(getattr(record, "returncode")),
            stdout=str(getattr(record, "stdout", "") or ""),
            stderr=str(getattr(record, "stderr", "") or ""),
            effects=tuple(getattr(record, "effects", ()) or ()),
            last_passing_step=last_passing_step,
        )


@dataclass(frozen=True)
class FaultCandidate:
    """One possible fault root and the causal work it would invalidate."""

    root_step_ids: Tuple[int, ...]
    rollback_targets: Tuple[int, ...]
    suspicion_score: float
    retained_steps: int
    discarded_steps: int


@dataclass
class RecoveryAttempt:
    """Counterfactual validation result for one candidate plan."""

    root_step_ids: Tuple[int, ...]
    rollback_targets: Tuple[int, ...]
    validation: str
    mode: str = "causal"
    verifier_returncode: Optional[int] = None
    error: str = ""


@dataclass
class RecoveryPlan:
    """A recovery decision that is separate from executing the rollback."""

    mode: str
    root_step_ids: Tuple[int, ...]
    rollback_targets: Tuple[int, ...]
    validation: str
    confidence: str
    attempts: List[RecoveryAttempt] = field(default_factory=list)
    reason: str = ""

    @property
    def verified(self) -> bool:
        return (
            self.validation in {"passed", "not_needed"}
            and self.confidence == "verified"
        )


@dataclass
class VerifierSpec:
    """A caller-approved check that can be rerun after a failure signal."""

    command: Tuple[str, ...]
    read_paths: Tuple[str, ...] = ()
    trace_reads: Optional[bool] = None
    timeout_s: Optional[float] = None
    last_passing_step: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "command": list(self.command),
            "read_paths": list(self.read_paths),
            "trace_reads": self.trace_reads,
            "timeout_s": self.timeout_s,
            "last_passing_step": self.last_passing_step,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "VerifierSpec":
        return cls(
            command=tuple(str(item) for item in data["command"]),
            read_paths=tuple(str(item) for item in data.get("read_paths", ())),
            trace_reads=data.get("trace_reads"),
            timeout_s=data.get("timeout_s"),
            last_passing_step=data.get("last_passing_step"),
        )


@dataclass
class RecoveryOutcome:
    """Result of checking a failure signal and applying a verified plan."""

    plan: RecoveryPlan
    verifier_step: int
    rollback_targets: Tuple[int, ...] = ()
    post_verifier_step: Optional[int] = None
    status: str = "inconclusive"


def normalize_roots(step_ids: Sequence[int]) -> Tuple[int, ...]:
    return tuple(sorted({int(step_id) for step_id in step_ids}))
