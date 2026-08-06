#!/usr/bin/env python3
"""Integration tests against the real binpash/try runtime."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agenttx.ledger import EffectKind
from agenttx.runtime import AgentTX


def _begin(tmp_path: Path, name: str = "session") -> tuple[Path, AgentTX]:
    ws = tmp_path / (name + "-ws")
    ws.mkdir()
    return ws, AgentTX.begin(workdir=ws, session_dir=tmp_path / name)


def test_selective_commit_materializes_only_frontier_paths(tmp_path: Path) -> None:
    ws, tx = _begin(tmp_path)
    try:
        first = tx.run_tool("write-a", ["bash", "-c", "echo one > a.txt"])
        second = tx.run_tool("write-b", ["bash", "-c", "echo two > b.txt"])
        assert first.returncode == second.returncode == 0
        assert not (ws / "a.txt").exists()
        assert not (ws / "b.txt").exists()

        assert tx.commit(0) == 0
        assert (ws / "a.txt").read_text(encoding="utf-8") == "one\n"
        assert not (ws / "b.txt").exists()
        assert tx.ledger.steps[0].status == "committed"
        assert tx.ledger.steps[1].status == "speculative"

        assert tx.commit() == 1
        assert (ws / "b.txt").read_text(encoding="utf-8") == "two\n"
    finally:
        tx.close(destroy=True)


def test_selective_commit_materializes_new_parent_directories(tmp_path: Path) -> None:
    ws, tx = _begin(tmp_path, "nested")
    try:
        tx.run_tool("nested", ["bash", "-c", "mkdir -p pkg/sub && echo data > pkg/sub/data.txt"])
        tx.run_tool("later", ["bash", "-c", "echo later > later.txt"])
        tx.commit(0)
        assert (ws / "pkg/sub/data.txt").read_text(encoding="utf-8") == "data\n"
        assert not (ws / "later.txt").exists()
    finally:
        tx.close(destroy=True)


def test_direct_host_file_deletion_is_recorded_and_committed(tmp_path: Path) -> None:
    ws, tx = _begin(tmp_path, "delete")
    seed = ws / "seed.txt"
    seed.write_text("seed\n", encoding="utf-8")
    try:
        record = tx.run_tool("delete", ["bash", "-c", "rm seed.txt"])
        assert record.returncode == 0
        assert any(
            effect.path == str(seed) and effect.kind == EffectKind.DELETE
            for effect in record.effects
        )
        assert seed.exists(), "host must remain unchanged before commit"
        tx.commit()
        assert not seed.exists()
    finally:
        tx.close(destroy=True)


def test_partial_commit_rejects_later_write_to_same_path(tmp_path: Path) -> None:
    ws, tx = _begin(tmp_path, "conflict")
    try:
        tx.run_tool("first", ["bash", "-c", "echo old > same.txt"])
        tx.run_tool("second", ["bash", "-c", "echo new > same.txt"])
        with pytest.raises(ValueError, match="partial commit crosses later writes"):
            tx.commit(0)
        assert not (ws / "same.txt").exists()

        tx.commit()
        assert (ws / "same.txt").read_text(encoding="utf-8") == "new\n"
    finally:
        tx.close(destroy=True)


def test_temporal_rollback_keeps_host_clean_then_allows_new_commit(tmp_path: Path) -> None:
    ws, tx = _begin(tmp_path, "rollback")
    try:
        tx.run_tool("first", ["bash", "-c", "echo one > a.txt"])
        tx.run_tool("second", ["bash", "-c", "echo two > b.txt"])
        assert tx.rollback(0) == [0, 1]
        assert not (ws / "a.txt").exists()
        assert not (ws / "b.txt").exists()

        replacement = tx.run_tool("replacement", ["bash", "-c", "echo three > c.txt"])
        assert replacement.returncode == 0
        tx.commit()
        assert (ws / "c.txt").read_text(encoding="utf-8") == "three\n"
        assert tx.rollback() == []
    finally:
        tx.close(destroy=True)
