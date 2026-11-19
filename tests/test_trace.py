from pathlib import Path

import pytest

from agenttx.cli import build_parser
from agenttx.ledger import Effect, EffectKind
from agenttx.runtime import AgentTX
from agenttx.trace import parse_strace_effects


@pytest.mark.parametrize(('token','expected'), [
    ('"a,b.txt" trailing annotation', 'a,b.txt'),
    (r'"a\\b"', 'a\\b'),
    (r'"quote\"name"', 'quote"name'),
    (r'"octal\040name"', 'octal name'),
    (r'"hex\x20name"', 'hex name'),
    ('"中文文件"', '中文文件'),
    ('"line\\\ncontinuation"', 'linecontinuation'),
    ('"unclosed', None),
    ('"raw\nnewline"', None),
    ('"raw\x00nul"', None),
    ('NULL', None),
])
def test_path_decoding_preserves_escaped_and_invalid_trace_strings(token, expected):
    from agenttx.trace import _decode_string
    assert _decode_string(token) == expected


def test_argument_prefix_preserves_nested_and_quoted_fields():
    from agenttx.trace import _split_args
    text = 'AT_FDCWD, "comma,name", {flags=O_RDONLY, resolve=0}, ["large", "array"]'
    for count in (1, 2, 3, 4):
        assert _split_args(text, limit=count) == _split_args(text)[:count]


def test_cached_paths_keep_cwd_and_workspace_in_the_key(tmp_path):
    from agenttx.trace import _resolve_path, _workspace_path
    left, right = tmp_path/'left', tmp_path/'right'
    a = _resolve_path('"same"', left)
    b = _resolve_path('"same"', right)
    assert a == left/'same' and b == right/'same'
    assert _workspace_path(a, right) is None
    assert _workspace_path(a, left) == str(a)


def test_long_execve_suffix_and_resumed_stat_keep_dependency_effects(tmp_path):
    state = {}
    assert parse_strace_effects('100 chdir("sub") = 0\n100 clone(flags=SIGCHLD) = 101\n', tmp_path, state=state)
    text = ('101 execve("tool", [' + ','.join('"unused"' for _ in range(10000)) + '], 0x0) = 0\n'
            '101 newfstatat(AT_FDCWD, "missing", <unfinished ...>\n')
    effects = parse_strace_effects(text, tmp_path, state=state)
    effects += parse_strace_effects('101 <... newfstatat resumed>0x0, 0) = -1 ENOENT (No such file)\n', tmp_path, state=state)
    assert Effect(str(tmp_path/'sub/tool'), EffectKind.READ) in effects
    assert Effect(str(tmp_path/'sub/missing'), EffectKind.NEGATIVE) in effects


def test_parse_strace_distinguishes_reads_negatives_and_writes(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    raw = (
        f'100 openat(AT_FDCWD, "input.txt", O_RDONLY|O_CLOEXEC) '
        f'= 3<{workspace}/input.txt>\n'
        f'100 newfstatat(AT_FDCWD, "missing.txt", 0x0, 0) '
        f'= -1 ENOENT (No such file or directory)\n'
        f'100 openat(AT_FDCWD, "output.txt", O_WRONLY|O_CREAT|O_TRUNC, 0666) '
        f'= 4<{workspace}/output.txt>\n'
    )

    effects = set(parse_strace_effects(raw, workspace))

    assert Effect(str(workspace / "input.txt"), EffectKind.READ) in effects
    assert Effect(str(workspace / "missing.txt"), EffectKind.NEGATIVE) in effects
    assert Effect(str(workspace / "output.txt"), EffectKind.READ) not in effects


def test_parse_strace_preserves_symlink_alias(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    raw = (
        f'100 openat(AT_FDCWD, "alias/data.txt", O_RDONLY|O_CLOEXEC) '
        f'= 3<{workspace}/real/data.txt>\n'
    )

    effects = set(parse_strace_effects(raw, workspace))

    assert Effect(str(workspace / "alias/data.txt"), EffectKind.READ) in effects
    assert Effect(str(workspace / "real/data.txt"), EffectKind.READ) in effects


def test_parse_strace_tracks_chdir_across_child_processes(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    raw = (
        '100 chdir("sub") = 0\n'
        "100 clone(child_stack=NULL, flags=SIGCHLD) = 101\n"
        '101 newfstatat(AT_FDCWD, "missing.txt", 0x0, 0) '
        '= -1 ENOENT (No such file or directory)\n'
    )

    effects = parse_strace_effects(raw, workspace)

    assert Effect(
        str(workspace / "sub" / "missing.txt"), EffectKind.NEGATIVE
    ) in effects


def test_runtime_traces_causal_reads_and_negative_lookups(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    tx = AgentTX.begin(workdir=workspace, session_dir=tmp_path / "session")
    try:
        producer = tx.run_tool(
            "producer", ["bash", "-c", "printf 'seed\n' > input.txt"]
        )
        consumer = tx.run_tool(
            "consumer",
            [
                "bash",
                "-c",
                "cat input.txt > derived.txt; test -e missing.txt || true",
            ],
        )
        creator = tx.run_tool(
            "creator", ["bash", "-c", "printf 'later\n' > missing.txt"]
        )

        assert Effect(str(workspace / "input.txt"), EffectKind.READ) in consumer.effects
        assert (
            Effect(str(workspace / "missing.txt"), EffectKind.NEGATIVE)
            in consumer.effects
        )
        assert consumer.parents == [producer.step_id]
        assert creator.parents == [consumer.step_id]
        assert all(
            ".agenttx-strace-" not in effect.path
            for record in (producer, consumer, creator)
            for effect in record.effects
        )
    finally:
        tx.close(destroy=True)


def test_cli_exposes_explicit_trace_opt_out() -> None:
    args = build_parser().parse_args(["begin", "--no-trace-reads"])
    assert args.no_trace_reads is True


def test_cli_exposes_causal_rollback() -> None:
    args = build_parser().parse_args(
        ["rollback", "--session", "/tmp/session", "--causal"]
    )
    assert args.causal is True
