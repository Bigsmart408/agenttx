"""Mid-trajectory crash injection: the three recovery policies must diverge."""

from pathlib import Path

from agenttx.harness import CodingAgentHarness
from experiments.scripts.bench_official_tasks import _apply_policy
from experiments.workloads.recovery_inject import (
    DocSpec,
    all_midcrash_docs,
    dag_is_valid,
    document_content,
    document_valid,
    independent_work_discarded,
    inject_recovery_dag,
    midcrash_docs,
    missing_independent_docs,
)


def test_midcrash_docs_splits_two_and_pads_one():
    one = (DocSpec("recovery_notes/design.md", "design", 16),)
    before, after = midcrash_docs(one)
    assert [s.path for s in before] == ["recovery_notes/design.md"]
    assert [s.path for s in after] == ["recovery_notes/post_crash.md"]
    two = (
        DocSpec("recovery_notes/design.md", "design", 32),
        DocSpec("recovery_notes/changelog.md", "change", 32),
    )
    before, after = midcrash_docs(two)
    assert [s.path for s in before] == ["recovery_notes/design.md"]
    assert [s.path for s in after] == ["recovery_notes/changelog.md"]
    three = two + (DocSpec("recovery_notes/validation.md", "validation", 64),)
    before, after = midcrash_docs(three)
    assert [s.path for s in before] == ["recovery_notes/design.md"]
    assert [s.path for s in after] == [
        "recovery_notes/changelog.md",
        "recovery_notes/validation.md",
    ]


def _inject(workdir: Path, session: Path, docs):
    workdir.mkdir(parents=True, exist_ok=True)
    session.mkdir(parents=True, exist_ok=True)
    harness = CodingAgentHarness(workdir=workdir, session_dir=session)
    agent = type("A", (), {"harness": harness})()
    injected = inject_recovery_dag(
        agent,
        docs=docs,
        task_name="midcrash",
        prefix_writes=(),
        faulty_path="pkg/fault.py",
        faulty_content="BROKEN\n",
        derived_cmd="mkdir -p recovery_build && cat pkg/fault.py > recovery_build/derived.txt",
        test_cmd="python -c \"print(open('pkg/fault.py').read()); raise SystemExit(1)\"",
    )
    return agent, injected


def _commit_active(tx):
    active = [
        step.step_id
        for step in tx.ledger.steps
        if step.status != "rolled_back" and step.step_id > tx.ledger.committed_frontier
    ]
    if active:
        tx.commit(max(active))


def test_injected_order_is_prefix_fault_suffix(tmp_path):
    docs = (
        DocSpec("recovery_notes/design.md", "design", 4),
        DocSpec("recovery_notes/changelog.md", "change", 4),
    )
    agent, injected = _inject(tmp_path / "ws", tmp_path / "sess", docs)
    try:
        (tmp_path / "ws").mkdir(exist_ok=True)
        assert dag_is_valid(injected)
        assert injected["docs_before"] == ["recovery_notes/design.md"]
        assert injected["docs_after"] == ["recovery_notes/changelog.md"]
        assert max(injected["independent_before_steps"]) < injected["root_step"]
        assert min(injected["independent_after_steps"]) > injected["root_step"]
        assert injected["root_is_parent_of_derived"]
        assert not injected["independent_is_parent_of_derived"]
    finally:
        agent.harness.close(destroy=True)


def test_three_policies_keep_different_independent_notes(tmp_path):
    docs = (
        DocSpec("recovery_notes/design.md", "design", 4),
        DocSpec("recovery_notes/changelog.md", "change", 4),
    )
    expected = {
        "causal": (True, True, False),
        "temporal_checkpoint": (True, False, True),
        "whole_branch_abort": (False, False, True),
    }
    for mode, (keep_before, keep_after, discarded) in expected.items():
        workdir = tmp_path / mode / "ws"
        session = tmp_path / mode / "sess"
        workdir.mkdir(parents=True)
        session.mkdir(parents=True)
        agent, injected = _inject(workdir, session, docs)
        try:
            assert dag_is_valid(injected)
            _apply_policy(agent, mode, injected["root_step"])
            assert independent_work_discarded(injected, agent.harness.tx.ledger) is discarded
            _commit_active(agent.harness.tx)
            before = workdir / "recovery_notes" / "design.md"
            after = workdir / "recovery_notes" / "changelog.md"
            derived = workdir / "recovery_build" / "derived.txt"
            if keep_before:
                assert document_valid(before, "design", 4)
            else:
                assert not before.exists()
            if keep_after:
                assert document_valid(after, "change", 4)
            else:
                assert not after.exists()
            assert not derived.exists()
            missing = missing_independent_docs(workdir, all_midcrash_docs(docs))
            missing_paths = {spec.path for spec in missing}
            if keep_before:
                assert "recovery_notes/design.md" not in missing_paths
            else:
                assert "recovery_notes/design.md" in missing_paths
            if keep_after:
                assert "recovery_notes/changelog.md" not in missing_paths
            else:
                assert "recovery_notes/changelog.md" in missing_paths
        finally:
            agent.harness.close(destroy=True)


def test_single_doc_gets_post_crash_sibling(tmp_path):
    docs = (DocSpec("recovery_notes/design.md", "design", 4),)
    workdir = tmp_path / "ws"
    workdir.mkdir()
    agent, injected = _inject(workdir, tmp_path / "sess", docs)
    try:
        assert injected["docs_after"] == ["recovery_notes/post_crash.md"]
        _apply_policy(agent, "temporal_checkpoint", injected["root_step"])
        _commit_active(agent.harness.tx)
        assert (workdir / "recovery_notes" / "design.md").exists()
        assert not (workdir / "recovery_notes" / "post_crash.md").exists()
    finally:
        agent.harness.close(destroy=True)


def test_temporal_missing_docs_are_only_post_crash(tmp_path):
    docs = (
        DocSpec("recovery_notes/design.md", "design", 4),
        DocSpec("recovery_notes/changelog.md", "change", 4),
    )
    workdir = tmp_path / "ws"
    agent, injected = _inject(workdir, tmp_path / "sess", docs)
    try:
        _apply_policy(agent, "temporal_checkpoint", injected["root_step"])
        missing = missing_independent_docs(
            workdir, all_midcrash_docs(docs), agent=agent
        )
        assert [spec.path for spec in missing] == ["recovery_notes/changelog.md"]
        _apply_policy(agent, "whole_branch_abort", injected["root_step"])
        missing = missing_independent_docs(
            workdir, all_midcrash_docs(docs), agent=agent
        )
        assert {spec.path for spec in missing} == {
            "recovery_notes/design.md",
            "recovery_notes/changelog.md",
        }
    finally:
        agent.harness.close(destroy=True)
