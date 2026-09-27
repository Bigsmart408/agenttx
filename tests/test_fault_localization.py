from pathlib import Path
from types import SimpleNamespace

import pytest

from agenttx.faults import FailureObservation
from agenttx.ledger import Effect, EffectKind, Ledger
from agenttx.localizer import FaultLocalizer
from agenttx.runtime import AgentTX, ToolCallRecord


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


def test_failure_signal_recovers_without_a_supplied_root_after_reload(
    tmp_path: Path,
) -> None:
    workspace, tx = _begin(tmp_path, "signal")
    session = tx.pool.session_dir
    value = workspace / "value.txt"
    value.write_text("GOOD\n", encoding="utf-8")
    command = ["bash", "-c", 'test "$(cat value.txt)" = GOOD']
    tx.register_verifier(command, read_paths=[str(value)], trace_reads=False)
    baseline = tx.run_verifier(
        command, read_paths=[str(value)], trace_reads=False
    )
    faulty = tx.run_tool(
        "faulty-edit", ["bash", "-c", "printf 'BAD\\n' > value.txt"],
        trace_reads=False,
    )
    independent = tx.run_tool(
        "independent-edit", ["bash", "-c", "printf 'keep\\n' > notes.txt"],
        trace_reads=False,
    )
    tx.close(destroy=False)

    resumed = AgentTX.load(session)
    try:
        assert resumed.verifiers["default"].last_passing_step == baseline.step_id
        result = resumed.recover_from_failure_signal(allow_fallback=False)
        assert result.status == "recovered"
        assert result.plan.root_step_ids == (faulty.step_id,)
        assert faulty.step_id in result.rollback_targets
        assert independent.step_id not in result.rollback_targets
        assert resumed.ledger.steps[independent.step_id].status == "speculative"
    finally:
        resumed.close(destroy=True)


def test_failure_signal_requires_a_registered_verifier(tmp_path: Path) -> None:
    _workspace, tx = _begin(tmp_path, "unregistered")
    try:
        with pytest.raises(ValueError, match="no trusted verifier"):
            tx.recover_from_failure_signal()
        assert tx.ledger.steps == []
    finally:
        tx.close(destroy=True)


def test_failure_signal_does_not_mutate_state_when_localization_is_inconclusive(
    tmp_path: Path,
) -> None:
    _workspace, tx = _begin(tmp_path, "inconclusive")
    try:
        tx.register_verifier(["bash", "-c", "exit 1"], trace_reads=False)
        edit = tx.run_tool(
            "independent-edit", ["bash", "-c", "printf 'keep\\n' > notes.txt"],
            trace_reads=False,
        )
        result = tx.recover_from_failure_signal(allow_fallback=False)
        assert result.status == "inconclusive"
        assert result.rollback_targets == ()
        assert tx.ledger.steps[edit.step_id].status == "speculative"
    finally:
        tx.close(destroy=True)


def test_group_probing_reaches_fault_beyond_single_candidate_budget(
    tmp_path: Path,
) -> None:
    workspace, tx = _begin(tmp_path, "long-history")
    paths = [workspace / f"artifact-{index:02d}.txt" for index in range(18)]
    paths[0].write_text("GOOD\n", encoding="utf-8")
    try:
        edits = [
            tx.run_tool(
                f"edit-{index}",
                ["bash", "-c", f"printf 'BAD\\n' > {path.name}"],
                trace_reads=False,
            )
            for index, path in enumerate(paths)
        ]
        failed = tx.run_verifier(
            ["bash", "-c", 'test "$(cat artifact-00.txt)" = GOOD'],
            read_paths=[str(path) for path in paths],
            trace_reads=False,
        )
        plan = tx.plan_recovery(
            failed, max_probes=10, allow_fallback=False,
            verifier_trace_reads=False,
        )
        assert plan.verified
        assert plan.root_step_ids == (edits[0].step_id,)
        assert len(plan.attempts) <= 10
        assert edits[-1].step_id not in plan.rollback_targets
    finally:
        tx.close(destroy=True)


@pytest.mark.parametrize("budget", [3, 24])
def test_group_probe_handles_three_independent_fault_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, budget: int,
) -> None:
    ledger = Ledger()
    roots = [
        ledger.add_step(f"bad-{index}", [Effect(f"/work/{index}", EffectKind.WRITE)])
        for index in range(3)
    ]
    verifier = ledger.add_step(
        "verifier:check",
        [Effect(f"/work/{index}", EffectKind.READ) for index in range(3)],
    )
    tx = AgentTX(workspace=tmp_path, ledger=ledger)

    class Branch:
        selected: tuple[int, ...] = ()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def rollback_causal_many(self, step_ids):
            self.selected = tuple(step_ids)
            return list(step_ids)

        def run_verifier(self, *_args, **_kwargs):
            return SimpleNamespace(returncode=int(len(self.selected) != 3))

    monkeypatch.setattr(tx, "fork", lambda: Branch())
    failed = ToolCallRecord(
        step_id=verifier.step_id,
        tool_name=verifier.tool_name,
        argv=["check"],
        returncode=1,
        duration_s=0.0,
        effects=verifier.effects,
    )
    plan = tx.plan_recovery(failed, max_probes=budget, allow_fallback=False)

    assert plan.verified
    assert plan.root_step_ids == tuple(step.step_id for step in roots)
    assert len(plan.attempts) <= budget


def test_plan_prefers_actual_rollback_closure_over_candidate_score(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = Ledger()
    first = ledger.add_step("first", [Effect("/work/first", EffectKind.WRITE)])
    second = ledger.add_step("second", [Effect("/work/second", EffectKind.WRITE)])
    copied = ledger.add_step("copy", [Effect("/work/copy", EffectKind.WRITE)])
    verifier = ledger.add_step(
        "verifier:check",
        [
            Effect("/work/first", EffectKind.READ),
            Effect("/work/second", EffectKind.READ),
        ],
    )
    tx = AgentTX(workspace=tmp_path, ledger=ledger)

    class Branch:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def rollback_causal_many(self, step_ids):
            root = step_ids[0]
            return [root, copied.step_id] if root == first.step_id else [root]

        def run_verifier(self, *_args, **_kwargs):
            return SimpleNamespace(returncode=0)

    monkeypatch.setattr(tx, "fork", lambda: Branch())
    failed = ToolCallRecord(
        step_id=verifier.step_id,
        tool_name=verifier.tool_name,
        argv=["check"],
        returncode=1,
        duration_s=0.0,
        effects=verifier.effects,
        stderr="first",
    )
    plan = tx.plan_recovery(failed, allow_multi_root=False, allow_fallback=False)

    assert plan.root_step_ids == (second.step_id,)
    assert plan.rollback_targets == (second.step_id,)
