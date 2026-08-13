import json
from types import SimpleNamespace

import pytest

from agenttx.agents.llm_agent import LLMToolAgent
from experiments.scripts.bench_token_end_to_end import (
    recovery_task,
    regenerated_documents,
    summarize as summarize_end_to_end,
)
from experiments.scripts.bench_token_recovery import _apply_policy, summarize
from experiments.workloads.token_recovery_agent import (
    inject_token_recovery_trajectory,
    seed_token_recovery_repo,
)


def test_llm_agent_accumulates_api_usage(tmp_path, monkeypatch):
    workdir = tmp_path / "usage-ws"
    workdir.mkdir()
    agent = LLMToolAgent(
        workdir=workdir,
        session_dir=tmp_path / "usage-session",
        api_key="test-key",
        max_turns=1,
    )
    tool_call = SimpleNamespace(
        id="finish-1",
        function=SimpleNamespace(
            name="finish",
            arguments=json.dumps({"summary": "done", "commit": False}),
        ),
    )
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=101,
            completion_tokens=23,
            total_tokens=124,
        ),
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="", tool_calls=[tool_call])
            )
        ],
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: response)
        )
    )
    monkeypatch.setattr(agent, "_client", lambda: client)
    try:
        result = agent.run("finish the task", commit=False)
        assert result.finished
        assert result.prompt_tokens == 101
        assert result.completion_tokens == 23
        assert result.total_tokens == 124
    finally:
        agent.close(destroy=True)


@pytest.mark.parametrize(
    ("mode", "prefix_rolled_back", "independent_rolled_back"),
    [
        ("causal", False, False),
        ("temporal_checkpoint", False, True),
        ("whole_branch_abort", True, True),
    ],
)
def test_token_workload_recovery_granularity(
    tmp_path, mode, prefix_rolled_back, independent_rolled_back
):
    workdir = tmp_path / f"{mode}-ws"
    workdir.mkdir()
    seed_token_recovery_repo(workdir)
    agent = LLMToolAgent(
        workdir=workdir,
        session_dir=tmp_path / f"{mode}-session",
        api_key="test-key",
    )
    try:
        injected = inject_token_recovery_trajectory(agent)
        assert injected["tests_failed"]
        assert injected["root_is_parent_of_derived"]
        assert injected["root_is_parent_of_tests"]
        assert not injected["independent_is_parent_of_derived"]
        targets = set(_apply_policy(agent, mode, injected))
        assert (injected["prefix_step"] in targets) is prefix_rolled_back
        assert (
            injected["independent_step"] in targets
        ) is independent_rolled_back
        assert injected["root_step"] in targets
        assert injected["derived_step"] in targets
        assert injected["test_step"] in targets
    finally:
        agent.close(destroy=True)


def test_token_summary_reports_agenttx_savings():
    rows = []
    for mode, total, completion in [
        ("causal", 1000, 100),
        ("temporal_checkpoint", 1600, 300),
        ("whole_branch_abort", 2500, 600),
    ]:
        rows.append(
            {
                "mode": mode,
                "model": "test",
                "success": True,
                "regenerated_document_count": {
                    "causal": 0,
                    "temporal_checkpoint": 1,
                    "whole_branch_abort": 2,
                }[mode],
                "host_polluted_before_commit": False,
                "prompt_tokens": total - completion,
                "completion_tokens": completion,
                "total_tokens": total,
                "tool_calls": 5,
                "wall_s": 1.0,
            }
        )
    summary = {row["mode"]: row for row in summarize(rows)}
    assert summary["temporal_checkpoint"]["agenttx_total_tokens_saved"] == 600
    assert summary["temporal_checkpoint"]["agenttx_total_tokens_saved_pct"] == 0.375
    assert summary["whole_branch_abort"]["agenttx_completion_tokens_saved"] == 500


def test_end_to_end_recovery_task_fixes_common_contract() -> None:
    task = recovery_task(24)
    assert "exactly" in task
    assert "24 ordered entries" in task
    assert "DESIGN-001:" in task
    assert "DESIGN-024:" in task
    assert "CHANGE-001:" in task
    assert "CHANGE-024:" in task
    assert "Do not call a rollback tool" in task
    assert "commit=false" in task


def test_end_to_end_regeneration_counts_only_write_file_effects() -> None:
    def step(tool_name, path, status="applied"):
        return SimpleNamespace(
            tool_name=tool_name,
            status=status,
            effects=[SimpleNamespace(path=path)],
        )

    steps = [
        step("write_file", "/ws/docs/design.md"),
        step("run_shell", "/ws/docs/changelog.md"),
        step("write_file", "docs/changelog.md"),
        step("write_file", "docs/design.md", status="rolled_back"),
    ]
    assert regenerated_documents(steps, 1) == ["docs/changelog.md"]
    assert regenerated_documents(steps, 0) == [
        "docs/design.md",
        "docs/changelog.md",
    ]


def test_end_to_end_summary_reports_nonzero_causal_savings() -> None:
    rows = []
    for mode, total, completion in [
        ("causal", 900, 100),
        ("temporal_checkpoint", 1500, 350),
        ("whole_branch_abort", 2400, 700),
    ]:
        rows.append(
            {
                "mode": mode,
                "document_lines": 24,
                "model": "test",
                "success": True,
                "host_polluted_before_commit": False,
                "regenerated_document_count": {
                    "causal": 0,
                    "temporal_checkpoint": 1,
                    "whole_branch_abort": 2,
                }[mode],
                "prompt_tokens": total - completion,
                "completion_tokens": completion,
                "total_tokens": total,
                "tool_calls": 5,
                "model_calls": 3,
                "recovery_ledger_steps": 2,
                "policy_ms": 1.0,
                "recovery_wall_s": 2.0,
            }
        )
    summary = {row["mode"]: row for row in summarize_end_to_end(rows)}
    temporal = summary["temporal_checkpoint"]
    whole = summary["whole_branch_abort"]
    assert temporal["agenttx_total_tokens_saved"] == 600
    assert temporal["agenttx_total_tokens_saved_pct"] == 0.4
    assert whole["agenttx_completion_tokens_saved"] == 600
