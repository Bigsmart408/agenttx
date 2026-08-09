from pathlib import Path

import pytest

import agenttx.semisolate as semisolate_module
from agenttx.layers import LayerStore
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


def test_try_starts_in_workspace_when_inherited_pwd_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    caller = tmp_path / "caller"
    workspace = tmp_path / "workspace"
    caller.mkdir()
    workspace.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setenv("PWD", str(caller))
    pool = SharedSemisolate(
        workspace=workspace,
        trace_reads=False,
        persistent_worker=False,
    )

    try:
        result = pool.run(["bash", "-c", "pwd"])
    finally:
        pool.close(destroy=True)

    assert result.returncode == 0
    assert result.stdout.strip() == str(workspace)


def test_snapshots_deduplicate_unchanged_files_without_aliasing(
    tmp_path: Path,
) -> None:
    upper = tmp_path / "upper"
    upper.mkdir()
    target = upper / "target.txt"
    target.write_text("old\n", encoding="utf-8")
    layers = LayerStore(tmp_path / "layers")

    before_zero = layers.snapshot_before(0, upper)
    snapshot_target = before_zero / "target.txt"
    assert snapshot_target.read_text(encoding="utf-8") == "old\n"
    assert snapshot_target.stat().st_ino != target.stat().st_ino

    target.write_text("new\n", encoding="utf-8")
    before_one = layers.snapshot_before(1, upper)
    assert (before_one / "target.txt").read_text(encoding="utf-8") == "new\n"
    assert snapshot_target.read_text(encoding="utf-8") == "old\n"
    assert len(list((tmp_path / "layers" / "blobs").glob("*/*"))) == 2

    layers.drop_from([0])
    assert snapshot_target.exists() is False
    assert len(list((tmp_path / "layers" / "blobs").glob("*/*"))) == 1
