"""Recovery must rewind logical state so the agent knows what survived.

Filesystem rollback alone is not enough: if the conversation still contains
rolled-back tool results, the model will re-read or rewrite retained work.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from agenttx.conversation import ConversationLog
from agenttx.harness import CodingAgentHarness
from experiments.scripts.bench_official_tasks import _apply_policy
from experiments.workloads.recovery_inject import (
    DocSpec,
    all_midcrash_docs,
    build_recovery_manifest,
    dag_is_valid,
    inject_recovery_dag,
    missing_independent_docs,
    read_recovery_documents,
    render_recovery_manifest_prompt,
)


DOCS = (
    DocSpec("recovery_notes/design.md", "design", 4),
    DocSpec("recovery_notes/changelog.md", "change", 4),
)


def _inject(workdir: Path, session: Path, *, bind: bool = True):
    workdir.mkdir(parents=True, exist_ok=True)
    session.mkdir(parents=True, exist_ok=True)
    harness = CodingAgentHarness(workdir=workdir, session_dir=session)
    agent = type("A", (), {"harness": harness})()
    injected = inject_recovery_dag(
        agent,
        docs=DOCS,
        task_name="state-consistency",
        prefix_writes=(),
        faulty_path="pkg/fault.py",
        faulty_content="BROKEN\n",
        derived_cmd="mkdir -p recovery_build && cat pkg/fault.py > recovery_build/derived.txt",
        test_cmd="python -c \"print(open('pkg/fault.py').read()); raise SystemExit(1)\"",
        bind_conversation=bind,
    )
    return agent, injected


def _manifest_after_policy(agent, injected, mode: str):
    crash_docs = all_midcrash_docs(DOCS)
    document_contents = read_recovery_documents(agent, crash_docs)
    workdir = agent.harness.workdir
    state_paths = {
        str(injected.get("faulty_path") or "").lstrip("./"),
        *[str(path).lstrip("./") for path in injected.get("derived_paths") or ()],
        *[spec.path for spec in crash_docs],
    }
    state_paths.discard("")
    path_exists = {
        path: agent.harness.tx.path_exists(workdir / path) for path in state_paths
    }
    return build_recovery_manifest(
        policy=mode,
        ledger=agent.harness.tx.ledger,
        injected=injected,
        docs=crash_docs,
        document_contents=document_contents,
        workdir=workdir,
        rollback_targets=_last_targets(agent),
        path_exists=path_exists,
    )


def _last_targets(agent):
    return [
        step.step_id
        for step in agent.harness.tx.ledger.steps
        if getattr(step, "status", "") == "rolled_back"
    ]


def test_bound_inject_records_every_ledger_step(tmp_path):
    agent, injected = _inject(tmp_path / "ws", tmp_path / "sess")
    try:
        assert dag_is_valid(injected)
        conv = agent.harness.tx.conversation
        active = set(conv.active_step_ids())
        for key in (
            "independent_before_steps",
            "independent_after_steps",
        ):
            for step_id in injected[key]:
                assert step_id in active
        assert injected["root_step"] in active
        assert injected["derived_step"] in active
        blob = json.dumps(conv.active_messages())
        assert "recovery_notes/design.md" in blob
        assert "recovery_notes/changelog.md" in blob
        assert "pkg/fault.py" in blob
    finally:
        agent.harness.close(destroy=True)


def test_unbound_inject_rewind_is_noop(tmp_path):
    agent, injected = _inject(tmp_path / "ws", tmp_path / "sess", bind=False)
    try:
        conv = agent.harness.tx.conversation
        assert conv.is_empty()
        _apply_policy(agent, "causal", injected["root_step"])
        assert conv.is_empty()
        assert conv.active_step_ids() == []
    finally:
        agent.harness.close(destroy=True)


def test_causal_rewind_keeps_both_notes_and_drops_fault_cone(tmp_path):
    agent, injected = _inject(tmp_path / "ws", tmp_path / "sess")
    try:
        _apply_policy(agent, "causal", injected["root_step"])
        conv = agent.harness.tx.conversation
        active = set(conv.active_step_ids())
        for step_id in injected["independent_steps"]:
            assert step_id in active
        assert injected["root_step"] not in active
        assert injected["derived_step"] not in active
        assert injected["test_run_step"] not in active
        notice = "\n".join(
            str(turn.messages[0].get("content") or "")
            for turn in conv.turns
            if turn.kind == "recovery"
        )
        assert "Invalidated steps" in notice
        assert "Retained steps" in notice
        blob = json.dumps(conv.active_messages())
        assert "recovery_notes/design.md" in blob
        assert "recovery_notes/changelog.md" in blob
        assert "Do not recreate retained files" in notice
        missing = missing_independent_docs(
            agent.harness.workdir, all_midcrash_docs(DOCS), agent=agent
        )
        assert missing == []
        assert not agent.harness.tx.path_exists(
            agent.harness.workdir / "recovery_build" / "derived.txt"
        )
    finally:
        agent.harness.close(destroy=True)


def test_temporal_rewind_drops_post_crash_conversation_spans(tmp_path):
    agent, injected = _inject(tmp_path / "ws", tmp_path / "sess")
    try:
        _apply_policy(agent, "temporal_checkpoint", injected["root_step"])
        conv = agent.harness.tx.conversation
        active = set(conv.active_step_ids())
        for step_id in injected["independent_before_steps"]:
            assert step_id in active
        for step_id in injected["independent_after_steps"]:
            assert step_id not in active
        assert injected["root_step"] not in active
        missing = {
            spec.path
            for spec in missing_independent_docs(
                agent.harness.workdir, all_midcrash_docs(DOCS), agent=agent
            )
        }
        assert missing == {"recovery_notes/changelog.md"}
    finally:
        agent.harness.close(destroy=True)


def test_whole_abort_drops_all_independent_conversation_spans(tmp_path):
    agent, injected = _inject(tmp_path / "ws", tmp_path / "sess")
    try:
        _apply_policy(agent, "whole_branch_abort", injected["root_step"])
        conv = agent.harness.tx.conversation
        active = set(conv.active_step_ids())
        for step_id in injected["independent_steps"]:
            assert step_id not in active
        assert injected["root_step"] not in active
        missing = {
            spec.path
            for spec in missing_independent_docs(
                agent.harness.workdir, all_midcrash_docs(DOCS), agent=agent
            )
        }
        assert missing == {"recovery_notes/design.md", "recovery_notes/changelog.md"}
    finally:
        agent.harness.close(destroy=True)


def test_causal_rem_matches_retained_overlay(tmp_path):
    agent, injected = _inject(tmp_path / "ws", tmp_path / "sess")
    try:
        _apply_policy(agent, "causal", injected["root_step"])
        manifest = _manifest_after_policy(agent, injected, "causal")
        assert manifest["authoritative"] is True
        retained = {item["path"]: item for item in manifest["retained"]}
        assert set(retained) == {
            "recovery_notes/design.md",
            "recovery_notes/changelog.md",
        }
        for item in retained.values():
            assert item["state"] == "complete-protected"
            assert item["origin"] == "retained_by_causal_recovery"
            assert item["contract_valid"] is True
        invalidated = {item["path"]: item for item in manifest["invalidated"]}
        assert "recovery_build/derived.txt" in invalidated
        assert invalidated["recovery_build/derived.txt"]["current_state"] == "absent"
        prompt = render_recovery_manifest_prompt(manifest)
        assert "COMPLETE-PROTECTED" in prompt
        assert "recovery_notes/design.md" in prompt
        assert "recovery_notes/changelog.md" in prompt
        assert "Do not read, validate, rewrite, or recreate COMPLETE-PROTECTED paths." in prompt
        assert "INVALIDATED BY RECOVERY" in prompt
        assert "recovery_build/derived.txt" in prompt
    finally:
        agent.harness.close(destroy=True)


def test_stale_conversation_disagrees_with_rolled_back_overlay(tmp_path):
    """The bug this experiment measures: FS recovered, chat still believes the crash."""
    agent, injected = _inject(tmp_path / "ws", tmp_path / "sess")
    try:
        snap = deepcopy(agent.harness.tx.conversation.to_dict())
        _apply_policy(agent, "causal", injected["root_step"])
        agent.harness.tx.conversation = ConversationLog.from_dict(snap)
        conv = agent.harness.tx.conversation
        active = set(conv.active_step_ids())
        assert injected["root_step"] in active
        assert injected["derived_step"] in active
        blob = json.dumps(conv.active_messages())
        assert "pkg/fault.py" in blob
        assert not agent.harness.tx.path_exists(
            agent.harness.workdir / "recovery_build" / "derived.txt"
        )
        assert not any(turn.kind == "recovery" for turn in conv.turns)
    finally:
        agent.harness.close(destroy=True)
