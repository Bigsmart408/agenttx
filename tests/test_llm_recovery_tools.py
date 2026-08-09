import json

from agenttx.agents.llm_agent import LLMToolAgent, TOOLS
from experiments.workloads.recovery_agent import (
    CORRECT_PIPELINE,
    INDEPENDENT_NOTE,
    inject_recovery_failure,
    seed_recovery_repo,
)


def test_llm_agent_exposes_ledger_inspection_and_causal_rollback(tmp_path):
    workdir = tmp_path / "ws"
    workdir.mkdir()
    agent = LLMToolAgent(
        workdir=workdir,
        session_dir=tmp_path / "session",
        api_key="test-key",
    )
    try:
        root = agent.harness.call_tool(
            "write_file", {"path": "fault.txt", "content": "fault"}
        )
        independent = agent.harness.call_tool(
            "write_file", {"path": "keep.txt", "content": "keep"}
        )
        derived = agent.harness.call_tool(
            "run_shell", {"cmd": "cat fault.txt > derived.txt"}
        )
        assert root.step_id in derived.parents
        assert independent.step_id not in derived.parents

        inspected = json.loads(agent._dispatch("inspect_ledger", {}))
        assert [step["step_id"] for step in inspected["steps"]] == [0, 1, 2]
        assert inspected["steps"][2]["parents"] == [root.step_id]

        rolled_back = json.loads(
            agent._dispatch("rollback_causal", {"step_id": root.step_id})
        )
        assert rolled_back["targets"] == [root.step_id, derived.step_id]
        assert rolled_back["active_step_ids"] == [independent.step_id]

        agent.harness.tx.commit(independent.step_id)
        assert not (workdir / "fault.txt").exists()
        assert not (workdir / "derived.txt").exists()
        assert (workdir / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    finally:
        agent.close(destroy=True)


def test_recovery_control_tools_are_advertised():
    names = {tool["function"]["name"] for tool in TOOLS}
    assert {"inspect_ledger", "rollback_causal"} <= names


def test_seeded_real_agent_failure_has_expected_causal_graph(tmp_path):
    workdir = tmp_path / "ws"
    workdir.mkdir()
    seed_recovery_repo(workdir)
    agent = LLMToolAgent(
        workdir=workdir,
        session_dir=tmp_path / "session",
        api_key="test-key",
    )
    try:
        injected = inject_recovery_failure(agent)
        assert injected["tests_failed"]
        assert injected["root_is_parent_of_derived"]
        assert injected["root_is_parent_of_tests"]
        assert not injected["independent_is_parent_of_derived"]

        targets = agent.harness.tx.rollback_causal(injected["root_step"])
        assert injected["derived_step"] in targets
        assert injected["test_step"] in targets
        assert injected["independent_step"] not in targets
        agent.harness.tx.commit(injected["independent_step"])

        assert (workdir / "src" / "pipeline.py").read_text(
            encoding="utf-8"
        ) == CORRECT_PIPELINE
        assert (workdir / "notes" / "independent.md").read_text(
            encoding="utf-8"
        ) == INDEPENDENT_NOTE
        assert not (workdir / "artifacts" / "rendered.txt").exists()
    finally:
        agent.close(destroy=True)
