from pathlib import Path

import pytest

import agenttx.semisolate as semisolate_module
from agenttx.semisolate import SharedSemisolate


def test_empty_filtered_commit_is_a_noop(tmp_path: Path) -> None:
    pool = SharedSemisolate(workspace=tmp_path, sandbox_dir=tmp_path / "session")
    result = pool.commit(paths=[])
    assert result.returncode == 0
    assert list(tmp_path.iterdir()) == [tmp_path / "session"]


def test_selective_commit_rejects_filesystem_root(tmp_path: Path) -> None:
    pool = SharedSemisolate(workspace=tmp_path, sandbox_dir=tmp_path / "session")
    with pytest.raises(ValueError, match="filesystem root"):
        pool.commit(paths=["/"])


def test_include_patterns_are_exact_suffixes_and_include_parents(tmp_path: Path) -> None:
    pool = SharedSemisolate(workspace=tmp_path, sandbox_dir=tmp_path / "session")
    target = tmp_path / "pkg" / "sub" / "data.txt"
    patterns = pool._include_patterns([str(target)])
    assert patterns
    assert all(pattern.endswith("$") for pattern in patterns)
    assert any(r"data\.txt$" in pattern for pattern in patterns)
    assert not any("sibling" in pattern for pattern in patterns)


def test_read_tracing_fails_closed_when_strace_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(semisolate_module.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="requires strace"):
        SharedSemisolate(
            workspace=tmp_path,
            sandbox_dir=tmp_path / "traced-session",
        )

    pool = SharedSemisolate(
        workspace=tmp_path,
        sandbox_dir=tmp_path / "untraced-session",
        trace_reads=False,
    )
    pool.close(destroy=True)


def test_try_commit_error_text_is_not_treated_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = SharedSemisolate(
        workspace=tmp_path,
        sandbox_dir=tmp_path / "session",
        trace_reads=False,
    )
    monkeypatch.setattr(
        semisolate_module.subprocess,
        "run",
        lambda *args, **kwargs: semisolate_module.subprocess.CompletedProcess(
            args[0],
            0,
            "",
            "try: couldn't commit /tmp/example",
        ),
    )

    result = pool.commit()

    assert result.returncode == 1
