from pathlib import Path

import pytest

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
