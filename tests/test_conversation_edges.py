"""Edge cases for conversation rewind: copy-edges, parallel spans, persist, finish."""
import json

from agenttx.agents.llm_agent import LLMToolAgent, SYSTEM
from agenttx.conversation import ConversationLog, copied_tokens
from agenttx.runtime import AgentTX


TOKEN = "SECRET_TOKEN_XYZ"


def _call(step_id, path, content, stdout="", extra_result=None):
    call_id = f"call-{step_id}"
    result = {"step_id": step_id, "path": path, "ok": True}
    if stdout:
        result["stdout"] = stdout
    if extra_result:
        result.update(extra_result)
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
    tool = {"role": "tool", "tool_call_id": call_id, "content": json.dumps(result)}
    return [assistant, tool]


def _openai_pending_tool_ids(messages):
    pending = []
    for message in messages:
        role = message.get("role")
        if role == "assistant":
            pending = [item.get("id") for item in (message.get("tool_calls") or [])]
        elif role == "tool":
            assert message.get("tool_call_id") in pending, message
            pending.remove(message.get("tool_call_id"))
        else:
            assert pending == [], message
    assert pending == []


def test_copy_chain_rolls_back_transitive_conversation_parents():
    log = ConversationLog()
    log.seed("sys", "do work")
    log.append_turn(_call(0, "a.txt", "x", stdout=TOKEN))
    log.append_turn(_call(1, "b.txt", f"use {TOKEN}"))
    log.append_turn(_call(2, "c.txt", f"again {TOKEN}"))
    assert log.turns[1].calls[0].parents == [0]
    assert 0 in log.turns[2].calls[0].parents or 1 in log.turns[2].calls[0].parents
    log.rewind([0], "notice")
    assert log.active_step_ids() == []
    assert TOKEN not in json.dumps(log.active_messages())


def test_short_tokens_do_not_create_copy_parents():
    log = ConversationLog()
    log.seed("sys", "do work")
    log.append_turn(_call(0, "a.txt", "x", stdout="ok"))
    log.append_turn(_call(1, "b.txt", "ok enough"))
    assert log.turns[1].calls[0].parents == []
    log.rewind([0], "notice")
    assert log.active_step_ids() == [1]


def test_same_turn_copy_edge_invalidates_later_sibling():
    log = ConversationLog()
    log.seed("sys", "do work")
    assistant = {
        "role": "assistant",
        "content": "copy then write",
        "tool_calls": [
            {
                "id": "call-0",
                "type": "function",
                "function": {"name": "run_shell", "arguments": "{}"},
            },
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "write_file",
                    "arguments": json.dumps({"path": "keep.txt", "content": TOKEN}),
                },
            },
        ],
    }
    log.append_turn(
        [
            assistant,
            {
                "role": "tool",
                "tool_call_id": "call-0",
                "content": json.dumps({"step_id": 0, "stdout": TOKEN}),
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "content": json.dumps({"step_id": 1, "path": "keep.txt"}),
            },
        ]
    )
    assert log.turns[0].calls[1].parents == [0]
    log.rewind([0], "notice")
    assert log.active_step_ids() == []
    assert TOKEN not in json.dumps(log.active_messages())


def test_three_parallel_spans_keep_unrelated_siblings():
    log = ConversationLog()
    log.seed("sys", "do work")
    assistant = {
        "role": "assistant",
        "content": "three writes",
        "tool_calls": [],
    }
    messages = [assistant]
    for step_id, path in [(0, "a.txt"), (1, "b.txt"), (2, "c.txt")]:
        call_id = f"call-{step_id}"
        assistant["tool_calls"].append(
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": "write_file",
                    "arguments": json.dumps({"path": path, "content": path}),
                },
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": json.dumps({"step_id": step_id, "path": path}),
            }
        )
    log.append_turn(messages)
    log.rewind([1], "notice")
    assert log.active_step_ids() == [0, 2]
    rebuilt = log.active_messages()
    _openai_pending_tool_ids(rebuilt)
    ids = [item["id"] for item in rebuilt[2]["tool_calls"]]
    assert ids == ["call-0", "call-2"]
    blob = json.dumps(rebuilt)
    assert "call-1" not in blob
    assert "b.txt" not in blob


def test_mixed_inspect_span_is_dropped_with_rewound_effect():
    log = ConversationLog()
    log.seed("sys", "do work")
    assistant = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "ins",
                "type": "function",
                "function": {"name": "inspect_ledger", "arguments": "{}"},
            },
            {
                "id": "call-0",
                "type": "function",
                "function": {
                    "name": "write_file",
                    "arguments": json.dumps({"path": "fault.txt", "content": "bad"}),
                },
            },
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "write_file",
                    "arguments": json.dumps({"path": "keep.txt", "content": "keep"}),
                },
            },
        ],
    }
    log.append_turn(
        [
            assistant,
            {"role": "tool", "tool_call_id": "ins", "content": json.dumps({"steps": [0, 1]})},
            {
                "role": "tool",
                "tool_call_id": "call-0",
                "content": json.dumps({"step_id": 0, "path": "fault.txt"}),
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "content": json.dumps({"step_id": 1, "path": "keep.txt"}),
            },
        ]
    )
    log.rewind([0], "notice")
    blob = json.dumps(log.active_messages())
    assert "inspect_ledger" not in blob
    assert '"steps": [0, 1]' not in blob
    assert log.active_step_ids() == [1]
    _openai_pending_tool_ids(log.active_messages())


def test_v1_payload_rebuilds_spans():
    messages, step_ids, tool_names = (
        _call(0, "fault.txt", "bad"),
        [0],
        ["write_file"],
    )
    payload = {
        "schema": "agenttx.conversation/v1",
        "system": "sys",
        "task": "do work",
        "generation": 0,
        "turns": [
            {
                "turn_id": 0,
                "kind": "effect",
                "messages": messages,
                "step_ids": step_ids,
                "tool_names": tool_names,
                "status": "active",
            }
        ],
    }
    log = ConversationLog.from_dict(payload)
    assert log.turns[0].calls[0].step_id == 0
    assert log.active_step_ids() == [0]


def test_duplicate_followup_is_not_appended_twice(tmp_path):
    workdir = tmp_path / "ws"
    workdir.mkdir()
    agent = LLMToolAgent(workdir=workdir, session_dir=tmp_path / "session", api_key="test-key")
    try:
        agent._ensure_task("first task")
        agent._ensure_task("second task")
        agent._ensure_task("second task")
        agent._ensure_task("   ")
        followups = [turn.messages[0]["content"] for turn in agent.harness.tx.conversation.turns if turn.kind == "user"]
        assert followups == ["second task"]
        assert agent.harness.tx.conversation.task == "first task"
    finally:
        agent.close(destroy=True)


def test_write_then_finish_still_executes_write(tmp_path):
    workdir = tmp_path / "ws"
    workdir.mkdir()
    agent = LLMToolAgent(workdir=workdir, session_dir=tmp_path / "session", api_key="test-key")
    try:
        batch = agent._run_call_batch(
            [
                {
                    "id": "w",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps({"path": "early.txt", "content": "yes"}),
                    },
                },
                {
                    "id": "fin",
                    "type": "function",
                    "function": {
                        "name": "finish",
                        "arguments": json.dumps({"summary": "done"}),
                    },
                },
            ]
        )
        assert batch["finished"] is True
        assert "write_file" in [step.tool_name for step in agent.harness.tx.ledger.steps]
    finally:
        agent.close(destroy=True)


def test_finish_skips_later_rollback(tmp_path):
    workdir = tmp_path / "ws"
    workdir.mkdir()
    agent = LLMToolAgent(workdir=workdir, session_dir=tmp_path / "session", api_key="test-key")
    try:
        root = agent.harness.call_tool("write_file", {"path": "fault.txt", "content": "bad"})
        agent.harness.tx.conversation.seed(SYSTEM, "do work")
        agent.harness.tx.conversation.append_turn(_call(root.step_id, "fault.txt", "bad"))
        batch = agent._run_call_batch(
            [
                {
                    "id": "fin",
                    "type": "function",
                    "function": {
                        "name": "finish",
                        "arguments": json.dumps({"summary": "done"}),
                    },
                },
                {
                    "id": "rb",
                    "type": "function",
                    "function": {
                        "name": "rollback_causal",
                        "arguments": json.dumps({"step_id": root.step_id}),
                    },
                },
            ]
        )
        assert batch["finished"] is True
        assert batch["deferred_rollback"] is None
        assert agent.harness.tx.ledger.steps[root.step_id].status != "rolled_back"
        assert root.step_id in agent.harness.tx.conversation.active_step_ids()
    finally:
        agent.close(destroy=True)


def test_copied_tokens_skip_tool_names_and_tiny_words():
    tokens = copied_tokens(json.dumps({"tool": "write_file", "ok": True, "msg": "hi"}))
    assert "write_file" not in tokens
    assert "true" not in tokens
    assert "hi" not in tokens


def test_runtime_copy_without_file_parent_drops_conversation_token(tmp_path):
    workdir = tmp_path / "ws"
    workdir.mkdir()
    agent = LLMToolAgent(workdir=workdir, session_dir=tmp_path / "session", api_key="test-key")
    try:
        root = agent.harness.call_tool("run_shell", {"cmd": f"printf '{TOKEN}'"})
        copied = agent.harness.call_tool(
            "write_file", {"path": "keep.txt", "content": TOKEN}
        )
        assert root.step_id not in copied.parents
        conv = agent.harness.tx.conversation
        conv.seed(SYSTEM, "do work")
        conv.append_turn(
            [
                {
                    "role": "assistant",
                    "content": "read then copy",
                    "tool_calls": [
                        {
                            "id": "call-0",
                            "type": "function",
                            "function": {
                                "name": "run_shell",
                                "arguments": json.dumps({"cmd": f"printf '{TOKEN}'"}),
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call-0",
                    "content": json.dumps(
                        {
                            "exit_code": root.exit_code,
                            "stdout": root.stdout,
                            "step_id": root.step_id,
                            "parents": root.parents,
                        }
                    ),
                },
            ]
        )
        conv.append_turn(
            [
                {
                    "role": "assistant",
                    "content": "write copy",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "write_file",
                                "arguments": json.dumps(
                                    {"path": "keep.txt", "content": TOKEN}
                                ),
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "content": json.dumps(
                        {
                            "exit_code": copied.exit_code,
                            "stdout": copied.stdout,
                            "step_id": copied.step_id,
                            "parents": copied.parents,
                        }
                    ),
                },
            ]
        )
        assert conv.turns[1].calls[0].parents == [root.step_id]
        targets = agent.harness.tx.rollback_causal(root.step_id)
        assert targets == [root.step_id]
        assert copied.step_id not in targets
        blob = json.dumps(conv.active_messages())
        assert TOKEN not in blob
        assert copied.step_id not in conv.active_step_ids()
    finally:
        agent.close(destroy=True)


def test_agenttx_json_keeps_ledger_and_conversation_in_one_record(tmp_path):
    workdir = tmp_path / "ws"
    workdir.mkdir()
    session = tmp_path / "session"
    tx = AgentTX.begin(workdir=workdir, session_dir=session)
    try:
        tx.conversation.seed(SYSTEM, "build files")
        root = tx.run_tool(
            "write_file",
            ["bash", "-c", "printf fault > fault.txt"],
            trace_reads=False,
        )
        keep = tx.run_tool(
            "write_file",
            ["bash", "-c", "printf keep > keep.txt"],
            trace_reads=False,
        )
        tx.conversation.append_turn(_call(root.step_id, "fault.txt", "fault"))
        tx.conversation.append_turn(_call(keep.step_id, "keep.txt", "keep"))
        tx.rollback_causal(root.step_id)
        meta = json.loads((session / "agenttx.json").read_text(encoding="utf-8"))
        ledger_status = meta["ledger"]["steps"][root.step_id]["status"]
        conv = ConversationLog.from_dict(meta["conversation"])
        span_status = [
            call.status
            for turn in conv.turns
            for call in turn.calls
            if call.step_id == root.step_id
        ]
        assert ledger_status == "rolled_back"
        assert span_status == ["invalidated"]
        assert keep.step_id in conv.active_step_ids()
    finally:
        tx.close(destroy=True)
