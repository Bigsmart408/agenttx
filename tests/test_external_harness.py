import fcntl
import subprocess
from pathlib import Path

from agenttx.agents.external import (
    CodexHarness,
    DeepSeekHarness,
    _external_request_slot,
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


def test_parse_jsonl_counts_completed_codex_tool_items_once():
    events, usage, tool_calls = _parse_jsonl(
        '{"type":"item.started","item":{"id":"1","type":"command_execution"}}\n'
        '{"type":"item.completed","item":{"id":"1","type":"command_execution",'
        '"command":"pwd","exit_code":0}}\n'
        '{"type":"item.completed","item":{"id":"2","type":"file_change"}}\n'
        '{"type":"item.completed","item":{"id":"3","type":"agent_message"}}\n'
    )
    assert len(events) == 4
    assert usage == (0, 0, 0)
    assert tool_calls == 2


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


def test_external_request_slot_uses_configured_lock(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTTX_EXTERNAL_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("AGENTTX_EXTERNAL_SLOT_DIR", str(tmp_path))

    with _external_request_slot():
        contender = (tmp_path / "slot-00.lock").open("a+", encoding="utf-8")
        try:
            try:
                fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                raise AssertionError("request slot was not held")
        finally:
            contender.close()


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
    assert "features.plugins=false" in codex_cmd[-1]
    assert "CODEX_HOME=/workspace/.agenttx-codex-" in codex_cmd[-1]
    assert 'CODEX_CONFIG_SOURCE="${CODEX_CONFIG_FILE:-$HOME/.codex/config.toml}"' in codex_cmd[-1]
    assert 'install -m 600 "$CODEX_CONFIG_SOURCE" /workspace/.agenttx-codex-' in codex_cmd[-1]
    assert 'install -m 600 "$CODEX_AUTH_SOURCE" /workspace/.agenttx-codex-' in codex_cmd[-1]
    assert 'ln -s "$CODEX_CONFIG_SOURCE"' not in codex_cmd[-1]
    assert 'ln -s "$CODEX_AUTH_SOURCE"' not in codex_cmd[-1]
    assert "trap 'rm -rf -- /workspace/.agenttx-codex-" in codex_cmd[-1]
    assert "miniconda3/envs/agenttx/bin" in codex_cmd[-1]
    assert "PIP_CACHE_DIR=/workspace/.agenttx-codex-" in codex_cmd[-1]
    assert "XDG_CACHE_HOME=/workspace/.agenttx-codex-" in codex_cmd[-1]
    assert "TMPDIR=/workspace/.agenttx-codex-" in codex_cmd[-1]
    assert '.venv/bin:$PATH' in codex_cmd[-1]
    assert "PYTHONNOUSERSITE=1" in codex_cmd[-1]
    assert "PIP_USER=0" in codex_cmd[-1]
    assert "PYTHONDONTWRITEBYTECODE=1" in codex_cmd[-1]
    assert "MPLCONFIGDIR=/workspace/.agenttx-codex-" in codex_cmd[-1]
    assert "PYTHONPYCACHEPREFIX=/workspace/.agenttx-codex-" in codex_cmd[-1]
    assert "FONTCONFIG_FILE=/workspace/.agenttx-codex-" in codex_cmd[-1]


def test_codex_harness_respects_direct_network_mode(monkeypatch):
    monkeypatch.setenv("CODEX_BIN", "/bin/echo")
    monkeypatch.setenv("AGENTTX_CLASH_COMMAND", "/bin/false")
    monkeypatch.setenv("AGENTTX_NO_PROXY", "1")

    harness = CodexHarness(model="gpt-test")

    assert harness.proxy_command == []
    assert harness.command("repair tests", Path("/workspace"))[0] != "/bin/false"
    assert "unset HTTP_PROXY HTTPS_PROXY ALL_PROXY" in harness.command(
        "repair tests", Path("/workspace")
    )[-1]


def test_codex_command_resolves_host_binary_before_isolated_login_shell(
    monkeypatch, tmp_path
):
    bin_dir = tmp_path / "host-bin"
    bin_dir.mkdir()
    binary = bin_dir / "codex"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.delenv("CODEX_BIN", raising=False)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("AGENTTX_CLASH_COMMAND", "")

    harness = CodexHarness(model="gpt-test")
    command = harness.command("repair tests", Path("/workspace"))[-1]

    assert harness.binary == str(binary)
    assert f"{binary} exec --model gpt-test" in command


def test_codex_command_pins_reasoning_effort(monkeypatch):
    monkeypatch.setenv("CODEX_BIN", "/bin/echo")
    monkeypatch.setenv("AGENTTX_CLASH_COMMAND", "")

    command = CodexHarness(
        model="gpt-5.6-luna", reasoning_effort="max"
    ).command("repair tests", Path("/workspace"))[-1]

    assert "--model gpt-5.6-luna" in command
    assert "model_reasoning_effort=" in command
    assert "max" in command


def test_external_timeout_preserves_byte_stream_usage(monkeypatch, tmp_path):
    harness = CodexHarness(command="/bin/true", model="gpt-test", timeout_s=1)
    partial = (
        b'{"type":"message","usage":{"prompt_tokens":11,'
        b'"completion_tokens":7}}\n'
    )

    def raise_timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(
            cmd=["codex"], timeout=1, output=partial, stderr=b"partial stderr"
        )

    monkeypatch.setattr(subprocess, "run", raise_timeout)
    result = harness.run("repair tests", tmp_path)

    assert result.returncode == 124
    assert result.total_tokens == 18
    assert "partial stderr" in result.stderr
    assert "external harness timeout" in result.stderr


def test_codex_discovers_updated_vscode_extension_when_path_is_stale(
    monkeypatch, tmp_path
):
    binary = (
        tmp_path
        / ".vscode-server/extensions/openai.chatgpt-26.901-linux-x64"
        / "bin/linux-x86_64/codex"
    )
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.delenv("CODEX_BIN", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("agenttx.agents.external.shutil.which", lambda _command: None)

    harness = CodexHarness(model="gpt-test")

    assert harness.binary == str(binary.resolve())
