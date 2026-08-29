import json
from pathlib import Path

from agenttx.agents.llm_agent import LLMToolAgent, SYSTEM
from agenttx.conversation import (
    ConversationLog,
    render_ledger_recovery_notice,
)
from agenttx.ledger import Effect, EffectKind, Ledger
from agenttx.runtime import AgentTX


def _turn(step_id: int, path: str, content: str) -> tuple[list[dict], list[int], list[str]]:
    call_id = f"call-{step_id}"
    assistant = {
        "role": "assistant",
        "content": f"write {path}",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": "write_file",
                    "arguments": json.dumps({"path": path, "content": content}),
                },
            }
        ],
    }
    tool = {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps({"step_id": step_id, "path": path, "ok": True}),
    }
    return [assistant, tool], [step_id], ["write_file"]


def test_causal_rewind_keeps_independent_later_turns():
    log = ConversationLog()
    log.seed("sys", "do work")
    log.append_turn(*_turn(0, "fault.txt", "bad"))
    log.append_turn(*_turn(1, "keep.txt", "keep"))
    log.append_turn(*_turn(2, "derived.txt", "from fault"))
    result = log.rewind([0, 2], "rolled back fault cone", mode="causal")
    assert result["rewound"] is True
    assert result["generation"] == 1
    assert result["retained_step_ids"] == [1]
    messages = log.active_messages()
    blob = json.dumps(messages)
    assert "keep.txt" in blob
    assert '"step_id": 0' not in blob
    assert '"step_id": 2' not in blob
    assert messages[-1]["role"] == "user"
    assert "rolled back fault cone" in messages[-1]["content"]
    assert messages[0]["content"] == "sys"
    assert messages[1]["content"] == "do work"


def test_temporal_rewind_drops_later_independent_turns():
    log = ConversationLog()
    log.seed("sys", "do work")
    log.append_turn(*_turn(0, "fault.txt", "bad"))
    log.append_turn(*_turn(1, "keep.txt", "keep"))
    log.append_turn(*_turn(2, "derived.txt", "from fault"))
    log.rewind([0, 1, 2], "temporal suffix", mode="temporal")
    assert log.active_step_ids() == []
    blob = json.dumps(log.active_messages())
    assert "keep.txt" not in blob
    assert "temporal suffix" in blob


def test_stale_control_turns_are_dropped_on_rewind():
    log = ConversationLog()
    log.seed("sys", "do work")
    log.append_turn(*_turn(0, "fault.txt", "bad"))
    log.append_turn(
        [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "ins", "type": "function", "function": {"name": "inspect_ledger", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "ins", "content": json.dumps({"steps": [0]})},
        ],
        step_ids=[],
        tool_names=["inspect_ledger"],
        kind="control",
    )
    log.rewind([0], "notice")
    blob = json.dumps(log.active_messages())
    assert "inspect_ledger" not in blob
    assert '"steps": [0]' not in blob


def test_empty_log_rewind_is_noop():
    log = ConversationLog()
    result = log.rewind([0], "notice")
    assert result["rewound"] is False
    assert log.active_messages() == []


def test_recovery_notice_lists_retained_and_invalidated_paths():
    ledger = Ledger()
    ledger.add_step("write_file", [Effect("fault.txt", EffectKind.WRITE)])
    ledger.add_step("write_file", [Effect("keep.txt", EffectKind.WRITE)])
    ledger.add_step("run_shell", [Effect("derived.txt", EffectKind.WRITE)])
    ledger.mark_rolled_back([0, 2])
    notice = render_ledger_recovery_notice(ledger, [0, 2], mode="causal")
    assert "`fault.txt`" in notice
    assert "`keep.txt`" in notice
    assert "`derived.txt`" in notice
    assert "conversation recovery (causal)" in notice


def test_runtime_causal_rollback_rewinds_persisted_conversation(tmp_path):
    workdir = tmp_path / "ws"
    workdir.mkdir()
    session = tmp_path / "session"
    tx = AgentTX.begin(workdir=workdir, session_dir=session)
    try:
        tx.conversation.seed(SYSTEM, "build files")
        root = tx.run_tool("write_file", ["bash", "-c", "printf fault > fault.txt"], trace_reads=False)
        independent = tx.run_tool("write_file", ["bash", "-c", "printf keep > keep.txt"], trace_reads=False)
        derived = tx.run_tool("run_shell", ["bash", "-c", "cat fault.txt > derived.txt"])
        tx.conversation.append_turn(*_turn(root.step_id, "fault.txt", "fault"))
        tx.conversation.append_turn(*_turn(independent.step_id, "keep.txt", "keep"))
        tx.conversation.append_turn(*_turn(derived.step_id, "derived.txt", "from fault"))
        tx._persist()

        targets = tx.rollback_causal(root.step_id)
        assert root.step_id in targets
        assert derived.step_id in targets
        assert independent.step_id not in targets
        assert tx.conversation.generation == 1
        assert tx.conversation.active_step_ids() == [independent.step_id]
        blob = json.dumps(tx.conversation.active_messages())
        assert "keep.txt" in blob
        assert '"step_id": %d' % root.step_id not in blob

        conv_path = Path(session) / "conversation.json"
        assert conv_path.exists()
        resumed = AgentTX.load(session)
        assert resumed.conversation.generation == 1
        assert resumed.conversation.active_step_ids() == [independent.step_id]
        resumed.close(destroy=False)
    finally:
        tx.close(destroy=True)


def test_llm_agent_dispatch_rewinds_in_session_messages(tmp_path):
    workdir = tmp_path / "ws"
    workdir.mkdir()
    agent = LLMToolAgent(
        workdir=workdir,
        session_dir=tmp_path / "session",
        api_key="test-key",
    )
    try:
        agent.harness.tx.conversation.seed(SYSTEM, "do work")
        root = agent.harness.call_tool("write_file", {"path": "fault.txt", "content": "fault"})
        independent = agent.harness.call_tool("write_file", {"path": "keep.txt", "content": "keep"})
        derived = agent.harness.call_tool("run_shell", {"cmd": "cat fault.txt > derived.txt"})
        agent.harness.tx.conversation.append_turn(*_turn(root.step_id, "fault.txt", "fault"))
        agent.harness.tx.conversation.append_turn(*_turn(independent.step_id, "keep.txt", "keep"))
        agent.harness.tx.conversation.append_turn(*_turn(derived.step_id, "derived.txt", "from fault"))

        rolled_back = json.loads(agent._dispatch("rollback_causal", {"step_id": root.step_id}))
        assert rolled_back["ok"] is True
        assert rolled_back["conversation"]["rewound"] is True
        messages = agent.harness.tx.conversation.active_messages()
        blob = json.dumps(messages)
        assert "keep.txt" in blob
        assert '"step_id": %d' % root.step_id not in blob
        assert messages[-1]["role"] == "user"
        assert "Invalidated steps" in messages[-1]["content"]
    finally:
        agent.close(destroy=True)
