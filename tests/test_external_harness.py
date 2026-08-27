from pathlib import Path

from agenttx.agents.external import (
    CodexHarness,
    DeepSeekHarness,
    _parse_jsonl,
    _parse_dsh_sessions,
    _token_usage,
)


def test_parse_jsonl_usage_and_tool_events():
    events, usage, tool_calls = _parse_jsonl(
        '{"type":"tool_call","name":"bash","usage":{"prompt_tokens":3,"completion_tokens":2}}\n'
        '{"type":"message","usage":{"prompt_tokens":1,"completion_tokens":4}}\n'
    )
    assert len(events) == 2
    assert usage == (4, 6, 10)
    assert tool_calls == 1


def test_deepseek_usage_includes_cache_and_deduplicates_final_message(tmp_path):
    assert _token_usage({
        "usage": {
            "inputTokens": 10,
            "cacheReadTokens": 90,
            "cacheWriteTokens": 5,
            "outputTokens": 7,
        }
    }) == (105, 7, 112)
    session = tmp_path / ".sessions" / "s" / "session.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text(
        "\n".join(
            [
                '{"type":"assistant/chunk","data":{"turn":1,"step":1,"chunk":{"type":"usage","usage":{"inputTokens":10,"cacheReadTokens":90,"outputTokens":7}}}}',
                '{"type":"tool/call","data":{"turn":1,"step":1}}',
                '{"type":"assistant/message","data":{"turn":1,"step":1,"usage":{"inputTokens":11,"cacheReadTokens":89,"outputTokens":8}}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    events, usage, tool_calls = _parse_dsh_sessions(tmp_path)
    assert len(events) == 3
    assert usage == (100, 8, 108)
    assert tool_calls == 1


def test_deepseek_usage_reads_transaction_upperdir(tmp_path):
    upper = tmp_path / "overlay" / "tmp" / "repo" / ".dsh" / "sessions" / "s"
    upper.mkdir(parents=True)
    (upper / "session.jsonl").write_text(
        '{"type":"assistant/message","data":{"turn":2,"step":1,'
        '"usage":{"inputTokens":4,"cacheReadTokens":6,"outputTokens":3}}}\n',
        encoding="utf-8",
    )
    events, usage, tool_calls = _parse_dsh_sessions(
        tmp_path / "host-workdir", extra_roots=[tmp_path / "overlay"]
    )
    assert len(events) == 1
    assert usage == (10, 3, 13)
    assert tool_calls == 0


def test_external_commands_are_configurable_without_running_a_model(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_HARNESS_BIN", "/bin/echo")
    monkeypatch.setenv("CODEX_BIN", "/bin/echo")
    monkeypatch.setenv("AGENTTX_CLASH_COMMAND", "")
    deepseek = DeepSeekHarness(root=tmp_path, model="deepseek-v4-flash")
    codex = CodexHarness(model="gpt-test")
    deepseek_cmd = deepseek.command("repair tests", Path("/workspace"))
    codex_cmd = codex.command("repair tests", Path("/workspace"))
    assert "/bin/echo" in deepseek_cmd[-1]
    assert "/bin/echo" in codex_cmd[-1]
    assert "repair tests" in deepseek_cmd[-1]
    assert "repair tests" in codex_cmd[-1]
    assert "CODEX_HOME=/workspace/.codex" in codex_cmd[-1]
    assert "PIP_CACHE_DIR=/workspace/.cache/pip" in codex_cmd[-1]
    assert "XDG_CACHE_HOME=/workspace/.cache" in codex_cmd[-1]
    assert "TMPDIR=/workspace/.tmp" in codex_cmd[-1]
    assert '.venv/bin:$PATH' in codex_cmd[-1]
    assert "PYTHONNOUSERSITE=1" in codex_cmd[-1]
    assert "PIP_USER=0" in codex_cmd[-1]
    assert "PYTHONDONTWRITEBYTECODE=1" in codex_cmd[-1]
    assert "MPLCONFIGDIR=/workspace/.cache/matplotlib" in codex_cmd[-1]
    assert "PYTHONPYCACHEPREFIX=/workspace/.cache/pyc" in codex_cmd[-1]
    assert "FONTCONFIG_FILE=/workspace/.cache/fonts.conf" in codex_cmd[-1]
