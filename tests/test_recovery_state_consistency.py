"""Recovery must rewind logical state so the agent knows what survived.

Filesystem rollback alone is not enough: if the conversation still contains
rolled-back tool results, the model will re-read or rewrite retained work.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from agenttx.conversation import ConversationLog
from agenttx.harness import CodingAgentHarness
from experiments.scripts.bench_official_tasks import _apply_policy
from experiments.workloads.swe_bench_suite import (
    SHORT as SWE_SHORT,
    direct_task_prompt as direct_swe_task_prompt,
)
from experiments.workloads.terminal_bench_suite import (
    SHORT as TB_SHORT,
    direct_task_prompt as direct_tb_task_prompt,
    test_command as tb_test_command,
)
from experiments.workloads.recovery_inject import (
    DocSpec,
    all_midcrash_docs,
    build_recovery_manifest,
    dag_is_valid,
    inject_recovery_dag,
    missing_independent_docs,
    read_recovery_documents,
    render_recovery_manifest_prompt,
    recovery_context_variant,
    recovery_prompt,
)


DOCS = (
    DocSpec("recovery_notes/design.md", "design", 4),
    DocSpec("recovery_notes/changelog.md", "change", 4),
)


def test_oracle_truth_restores_workspace_despite_incorrect_captured_edges(tmp_path):
    agent, injected = _inject(tmp_path / "ws", tmp_path / "sess")
    tx = agent.harness.tx
    try:
        tx.ledger.steps[injected["independent_after_steps"][0]].parents.add(injected["root_step"])
        tx.ledger.steps[injected["derived_step"]].parents.clear()
        def forbidden(*args):
            raise AssertionError("oracle must not query captured closure")
        tx._unified_causal_targets = forbidden
        for turn in tx.conversation.turns:
            for span in turn.calls:
                if span.step_id == injected["independent_after_steps"][0]:
                    span.parents = (injected["root_step"],)
        tx.conversation.conversation_closure = forbidden
        targets = _apply_policy(agent, "ideal_oracle", injected["root_step"],
                                oracle_targets=injected["oracle_invalid_steps"])
        assert set(targets) == set(injected["oracle_invalid_steps"])
        assert set(injected["independent_steps"]) <= set(tx.conversation.active_step_ids())
        assert not set(targets).intersection(tx.conversation.active_step_ids())
        assert not tx.path_exists(agent.harness.workdir / "pkg/fault.py")
        assert not tx.path_exists(agent.harness.workdir / "recovery_build/derived.txt")
        for path in injected["docs_before"] + injected["docs_after"]:
            assert tx.path_exists(agent.harness.workdir / path)
        before = tx.ledger.to_dict(), tx.conversation.to_dict()
        with pytest.raises(ValueError, match="inactive step"):
            tx.rollback_exact(injected["oracle_invalid_steps"])
        assert (tx.ledger.to_dict(), tx.conversation.to_dict()) == before
    finally:
        agent.harness.close(destroy=True)


def _inject(
    workdir: Path,
    session: Path,
    *,
    bind: bool = True,
    docs=DOCS,
    fault_after_docs=None,
):
    workdir.mkdir(parents=True, exist_ok=True)
    session.mkdir(parents=True, exist_ok=True)
    harness = CodingAgentHarness(workdir=workdir, session_dir=session)
    agent = type("A", (), {"harness": harness})()
    injected = inject_recovery_dag(
        agent,
        docs=docs,
        task_name="state-consistency",
        prefix_writes=(),
        faulty_path="pkg/fault.py",
        faulty_content="BROKEN\n",
        derived_cmd="mkdir -p recovery_build && cat pkg/fault.py > recovery_build/derived.txt",
        test_cmd="python -c \"print(open('pkg/fault.py').read()); raise SystemExit(1)\"",
        bind_conversation=bind,
        fault_after_docs=fault_after_docs,
    )
    return agent, injected


def test_inject_places_fault_at_requested_independent_step(tmp_path):
    docs = tuple(
        DocSpec(f"recovery_notes/random-{index}.md", f"random{index}", 2)
        for index in range(5)
    )
    agent, injected = _inject(
        tmp_path / "ws",
        tmp_path / "sess",
        docs=docs,
        fault_after_docs=3,
    )
    try:
        assert dag_is_valid(injected)
        assert injected["root_step"] == 3
        assert len(injected["independent_before_steps"]) == 3
        assert len(injected["independent_after_steps"]) == 2
    finally:
        agent.harness.close(destroy=True)


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
        oracle_fault_step_id=(
            injected["root_step"] if mode == "ideal_oracle" else None
        ),
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


def test_temporal_rem_allows_expected_discard_and_lists_recreate_path(tmp_path):
    agent, injected = _inject(tmp_path / "ws", tmp_path / "sess")
    try:
        _apply_policy(agent, "temporal_checkpoint", injected["root_step"])
        manifest = _manifest_after_policy(agent, injected, "temporal_checkpoint")
        assert manifest["authoritative"] is True
        assert [item["path"] for item in manifest["retained"]] == [
            "recovery_notes/design.md"
        ]
        assert [item["path"] for item in manifest["recreate_required"]] == [
            "recovery_notes/changelog.md"
        ]
        prompt = render_recovery_manifest_prompt(manifest)
        assert "RECREATE-REQUIRED" in prompt
        assert "recovery_notes/changelog.md" in prompt
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


def test_no_fault_prompt_is_clean_baseline():
    prompt = recovery_prompt(
        title="clean-baseline",
        context="Suite: test",
        instruction="Implement the requested change.",
        docs=DOCS,
        test_cmd="pytest -q tests/test_example.py",
        mode="no_fault",
    )
    assert "A previous attempt" not in prompt
    assert "Recovery protocol" not in prompt
    assert "recovery_notes" not in prompt
    assert "recovery_build" not in prompt
    assert "rollback" not in prompt.lower()
    assert "Execution rules" in prompt


def test_direct_baseline_prompt_is_only_the_benchmark_instruction():
    assert direct_swe_task_prompt(
        SWE_SHORT, {"problem_statement": "  Original SWE issue.\n"}
    ) == "Original SWE issue."
    assert direct_tb_task_prompt(TB_SHORT) == (
        'Create a file called hello.txt. Write "Hello, world!" to it.'
    )


def test_terminal_verifier_uses_selected_python_runtime_libraries():
    command = tb_test_command("/opt/conda/envs/agenttx/bin/python", TB_SHORT)
    assert "PATH=/opt/conda/envs/agenttx/bin:$PATH" in command
    assert (
        "LD_LIBRARY_PATH=/opt/conda/envs/agenttx/lib"
        "${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" in command
    )


def test_swe_agent_verifier_runs_fail_to_pass_only_without_verified_prefix():
    from experiments.workloads.swe_bench_suite import agent_test_command

    instance = {
        "repo": "astropy/astropy",
        "FAIL_TO_PASS": ["astropy/modeling/tests/test_separable.py::test_cstack"],
    }
    unseeded = agent_test_command(SWE_SHORT, instance, "/venv/bin/python")
    seeded = agent_test_command(
        SWE_SHORT,
        instance,
        "/venv/bin/python",
        reference_progress_verified=True,
    )

    assert "verify_swe_recovery.py" in unseeded
    assert "test_separable.py::test_cstack" in unseeded
    assert " && " in unseeded
    assert "verify_swe_recovery.py" in seeded
    assert "test_separable.py::test_cstack" not in seeded


def test_control_prompts_distinguish_clean_recovery_and_no_rollback():
    from experiments.workloads.recovery_inject import recovery_prompt

    clean = recovery_prompt(
        title="control",
        context="control",
        instruction="Do the task.",
        docs=(),
        test_cmd="pytest -q",
        focus_path="pkg/core.py",
        mode="clean_recovery",
    )
    no_rollback = recovery_prompt(
        title="control",
        context="control",
        instruction="Do the task.",
        docs=(),
        test_cmd="pytest -q",
        focus_path="pkg/core.py",
        mode="crash_no_rollback",
    )
    assert "No crash was injected" in clean
    assert "faulty producer" not in clean
    assert "intentionally performs no rollback" in no_rollback
    assert "remove invalid fault-dependent artifacts" in no_rollback
    assert "Ensure `recovery_build/derived.txt` is absent" in no_rollback
    assert "preserves the injected workspace state" not in no_rollback
    assert "Recovery protocol:" in clean
    assert "Recovery protocol:" in no_rollback
    assert "clean unsolved benchmark checkout" in clean
    assert "reference solution is already materialized" not in clean
    assert "Run the exact named verifier before making any edit" in clean
    assert "Start with the verifier's concrete diagnostics" in clean
    assert "No faulty step, source path, or recovery certificate is available" in clean
    assert "disclosed invalidated source" not in clean
    assert "step and path are not disclosed" in no_rollback
    assert "named verifier" in clean
    assert "named verifier" in no_rollback
    assert "`pytest -q`" in clean
    assert "`pytest -q`" in no_rollback


def test_compact_manifest_handoff_omits_hashes(tmp_path, monkeypatch):
    agent, injected = _inject(tmp_path / "ws", tmp_path / "sess")
    try:
        _apply_policy(agent, "causal", injected["root_step"])
        manifest = _manifest_after_policy(agent, injected, "causal")
        monkeypatch.setenv("AGENTTX_RECOVERY_CONTEXT", "compact")
        prompt = render_recovery_manifest_prompt(manifest)
        assert "compact; authoritative" in prompt
        assert "sha256=" not in prompt
        assert "complete-protected" in prompt
        assert "recovery_build/derived.txt" in prompt
        assert "each rerun follows a concrete issue-relevant edit" in prompt
        assert "environment failure before test execution does not count" in prompt
        assert "rerun that verifier once" not in prompt
        assert "one additional focused edit" not in prompt
        assert "only task paths to inspect or edit" not in prompt
        assert "follow only issue-relevant symbols or tests" in prompt
    finally:
        agent.harness.close(destroy=True)


def test_compact_manifest_handoff_hides_location_from_coarse_policy(tmp_path, monkeypatch):
    agent, injected = _inject(tmp_path / "ws", tmp_path / "sess")
    try:
        _apply_policy(agent, "temporal_checkpoint", injected["root_step"])
        manifest = _manifest_after_policy(agent, injected, "temporal_checkpoint")
        monkeypatch.setenv("AGENTTX_RECOVERY_CONTEXT", "compact")
        prompt = render_recovery_manifest_prompt(manifest)
        assert recovery_context_variant("temporal_checkpoint") == "compact"
        assert "compact; authoritative" in prompt
        assert "sha256=" not in prompt
        assert "pkg/fault.py" not in prompt
        assert "no fault step or source path is disclosed" in prompt
    finally:
        agent.harness.close(destroy=True)


def test_compact_handoff_information_is_policy_scoped(monkeypatch):
    monkeypatch.setenv("AGENTTX_RECOVERY_CONTEXT", "compact")
    base = {
        "schema": "agenttx.recovery_manifest/v1",
        "authoritative": True,
        "state_id": "state",
        "generation": {"ledger_steps": 9, "committed_frontier": 1},
        "retained": [],
        "recreate_required": [],
        "invalidated": [
            {
                "path": "secret/fault.py",
                "reason": "fault_producer_rolled_back",
                "current_state": "absent",
                "must_stay_absent": False,
            }
        ],
        "pending": ["run verifier"],
        "fault_location_source": "hidden",
        "localized_fault_step_ids": [],
        "oracle_post_recovery_verified": False,
    }

    for policy in (
        "crash_no_rollback",
        "temporal_checkpoint",
        "whole_branch_abort",
    ):
        prompt = render_recovery_manifest_prompt({**base, "policy": policy})
        assert "secret/fault.py" not in prompt
        assert "no fault step or source path is disclosed" in prompt

    causal = render_recovery_manifest_prompt(
        {
            **base,
            "policy": "causal",
            "fault_location_source": "agenttx_localizer",
            "localized_fault_step_ids": [4],
        }
    )
    assert "secret/fault.py" in causal
    assert "AgentTX-localized root step(s): 4" in causal

    ideal = render_recovery_manifest_prompt(
        {**base, "policy": "ideal_oracle", "oracle_fault_step_id": 4}
    )
    assert "secret/fault.py" in ideal
    assert "Oracle fault step: 4" in ideal


def test_ideal_oracle_compact_handoff_discloses_exact_step(tmp_path, monkeypatch):
    agent, injected = _inject(tmp_path / "ws", tmp_path / "sess")
    try:
        _apply_policy(agent, "ideal_oracle", injected["root_step"],
                      oracle_targets=injected["oracle_invalid_steps"])
        manifest = _manifest_after_policy(agent, injected, "ideal_oracle")
        monkeypatch.setenv("AGENTTX_RECOVERY_CONTEXT", "compact")
        prompt = render_recovery_manifest_prompt(manifest)
        assert recovery_context_variant("ideal_oracle") == "compact"
        assert f"Oracle fault step: {injected['root_step']}" in prompt
        assert manifest["oracle_fault_step_id"] == injected["root_step"]
    finally:
        agent.harness.close(destroy=True)


def test_clean_recovery_can_use_compact_execution_constraints(monkeypatch):
    monkeypatch.setenv("AGENTTX_RECOVERY_CONTEXT", "compact")
    prompt = recovery_prompt(
        title="control",
        context="control",
        instruction="Do the task.",
        docs=(),
        test_cmd="pytest -q",
        focus_path="pkg/core.py",
        mode="clean_recovery",
    )
    assert recovery_context_variant("clean_recovery") == "compact"
    assert "unsolved clean checkout" in prompt
    assert "verifier-guided task protocol" in prompt
    assert "Run the exact named verifier before making any edit" in prompt
    assert "No faulty step, source path, or recovery certificate is available" in prompt
    assert "Make no tool calls" not in prompt
    assert "compact recovery certificate is authoritative" not in prompt
    assert "No crash was injected" in prompt


def test_verified_clean_recovery_uses_compact_no_tool_fast_path(monkeypatch):
    monkeypatch.setenv("AGENTTX_RECOVERY_CONTEXT", "compact")
    prompt = recovery_prompt(
        title="control",
        context="control",
        instruction="Do the task.",
        docs=(),
        test_cmd="pytest -q",
        focus_path="pkg/core.py",
        mode="clean_recovery",
        reference_progress_verified=True,
    )
    assert "clean compact recovery baseline" in prompt
    assert "Make no tool calls" in prompt
    assert "reference solution was materialized" in prompt
