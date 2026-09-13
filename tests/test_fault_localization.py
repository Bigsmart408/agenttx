from pathlib import Path

import pytest

from agenttx.faults import FailureObservation
from agenttx.ledger import Effect, EffectKind, Ledger
from agenttx.localizer import FaultLocalizer
from agenttx.runtime import AgentTX


def test_localizer_only_ranks_active_write_ancestors() -> None:
    ledger = Ledger()
    faulty = ledger.add_step(
        "faulty-edit", [Effect("/workspace/pkg.py", EffectKind.WRITE)]
    )
    ledger.add_step(
        "independent-edit", [Effect("/workspace/notes.txt", EffectKind.WRITE)]
    )
    verifier = ledger.add_step(
        "verifier:tests", [Effect("/workspace/pkg.py", EffectKind.READ)]
    )
    observation = FailureObservation(
        verifier_step_id=verifier.step_id,
        command=("pytest",),
        returncode=1,
        stderr="pkg.py failed",
        effects=tuple(verifier.effects),
    )

    candidates = FaultLocalizer().candidates(ledger, observation)

    assert [candidate.root_step_ids for candidate in candidates] == [
        (faulty.step_id,)
    ]
    assert candidates[0].rollback_targets == (
        faulty.step_id,
        verifier.step_id,
    )


def _begin(tmp_path: Path, name: str) -> tuple[Path, AgentTX]:
    workspace = tmp_path / f"{name}-workspace"
    workspace.mkdir()
    return workspace, AgentTX.begin(
        workdir=workspace,
        session_dir=tmp_path / f"{name}-session",
        trace_reads=False,
    )


def test_recovery_plan_counterfactually_verifies_single_root(tmp_path: Path) -> None:
    workspace, tx = _begin(tmp_path, "single")
    value = workspace / "value.txt"
    value.write_text("GOOD\n", encoding="utf-8")
    try:
        faulty = tx.run_tool(
            "faulty-edit",
            ["bash", "-c", "printf 'BAD\\n' > value.txt"],
            trace_reads=False,
        )
        independent = tx.run_tool(
            "independent-edit",
            ["bash", "-c", "printf 'keep\\n' > notes.txt"],
            trace_reads=False,
        )
        failed = tx.run_verifier(
            ["bash", "-c", "test \"$(cat value.txt)\" = GOOD"],
            name="value",
            read_paths=[str(value)],
            trace_reads=False,
        )
        step_count = len(tx.ledger.steps)

        plan = tx.plan_recovery(
            failed,
            allow_multi_root=False,
            allow_fallback=False,
        )

        assert plan.verified
        assert plan.mode == "causal"
        assert plan.root_step_ids == (faulty.step_id,)
        assert independent.step_id not in plan.rollback_targets
        assert len(tx.ledger.steps) == step_count
        assert all(step.status == "speculative" for step in tx.ledger.steps)

        targets = tx.apply_recovery_plan(plan)
        assert faulty.step_id in targets
        assert failed.step_id in targets
        assert independent.step_id not in targets

        checked = tx.run_verifier(
            [
                "bash",
                "-c",
                "test \"$(cat value.txt)\" = GOOD && "
                "test \"$(cat notes.txt)\" = keep",
            ],
            name="post-recovery",
            read_paths=[str(value), str(workspace / "notes.txt")],
            trace_reads=False,
        )
        assert checked.returncode == 0
    finally:
        tx.close(destroy=True)


def test_recovery_plan_can_require_two_fault_roots(tmp_path: Path) -> None:
    workspace, tx = _begin(tmp_path, "multi")
    first_path = workspace / "first.txt"
    second_path = workspace / "second.txt"
    first_path.write_text("GOOD\n", encoding="utf-8")
    second_path.write_text("GOOD\n", encoding="utf-8")
    try:
        first = tx.run_tool(
            "break-first",
            ["bash", "-c", "printf 'BAD\\n' > first.txt"],
            trace_reads=False,
        )
        independent = tx.run_tool(
            "independent-edit",
            ["bash", "-c", "printf 'keep\\n' > notes.txt"],
            trace_reads=False,
        )
        second = tx.run_tool(
            "break-second",
            ["bash", "-c", "printf 'BAD\\n' > second.txt"],
            trace_reads=False,
        )
        failed = tx.run_verifier(
            [
                "bash",
                "-c",
                "test \"$(cat first.txt)\" = GOOD && "
                "test \"$(cat second.txt)\" = GOOD",
            ],
            name="both-values",
            read_paths=[str(first_path), str(second_path)],
            trace_reads=False,
        )

        plan = tx.plan_recovery(
            failed,
            max_candidates=4,
            max_probes=4,
            allow_multi_root=True,
            allow_fallback=False,
        )

        assert plan.verified
        assert plan.mode == "causal"
        assert plan.root_step_ids == (first.step_id, second.step_id)
        assert independent.step_id not in plan.rollback_targets
        assert [attempt.validation for attempt in plan.attempts] == [
            "failed",
            "failed",
            "passed",
        ]

        tx.apply_recovery_plan(plan)
        checked = tx.run_verifier(
            [
                "bash",
                "-c",
                "test \"$(cat first.txt)\" = GOOD && "
                "test \"$(cat second.txt)\" = GOOD && "
                "test \"$(cat notes.txt)\" = keep",
            ],
            name="post-recovery",
            read_paths=[
                str(first_path),
                str(second_path),
                str(workspace / "notes.txt"),
            ],
            trace_reads=False,
        )
        assert checked.returncode == 0
    finally:
        tx.close(destroy=True)


def test_recovery_rejects_an_ordinary_failed_tool(tmp_path: Path) -> None:
    _workspace, tx = _begin(tmp_path, "ordinary-failure")
    try:
        failed = tx.run_tool(
            "shell", ["bash", "-c", "exit 1"], trace_reads=False
        )
        with pytest.raises(ValueError, match="run_verifier"):
            tx.plan_recovery(failed)
    finally:
        tx.close(destroy=True)


def test_passing_verifier_produces_an_applicable_noop_plan(tmp_path: Path) -> None:
    _workspace, tx = _begin(tmp_path, "passing")
    try:
        passed = tx.run_verifier(
            ["bash", "-c", "exit 0"], name="passing", trace_reads=False
        )
        plan = tx.plan_recovery(passed)
        assert plan.verified
        assert plan.mode == "none"
        assert tx.apply_recovery_plan(plan) == []
    finally:
        tx.close(destroy=True)
