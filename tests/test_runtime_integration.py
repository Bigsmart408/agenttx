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


def test_partial_commit_reconstructs_historical_same_path_version(
    tmp_path: Path,
) -> None:
    ws, tx = _begin(tmp_path, "historical")
    try:
        first = tx.run_tool("first", ["bash", "-c", "echo old > same.txt"])
        second = tx.run_tool("second", ["bash", "-c", "echo new > same.txt"])
        later = tx.run_tool("later", ["bash", "-c", "echo later > later.txt"])

        assert tx.commit(first.step_id) == first.step_id
        assert (ws / "same.txt").read_text(encoding="utf-8") == "old\n"
        assert not (ws / "later.txt").exists()
        assert tx.ledger.steps[second.step_id].status == "speculative"
        assert tx.ledger.steps[later.step_id].status == "speculative"

        tx.commit()
        assert (ws / "same.txt").read_text(encoding="utf-8") == "new\n"
        assert (ws / "later.txt").read_text(encoding="utf-8") == "later\n"
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


def test_causal_rollback_preserves_independent_later_step(tmp_path: Path) -> None:
    ws, tx = _begin(tmp_path, "causal")
    try:
        producer = tx.run_tool("producer", ["bash", "-c", "echo one > a.txt"])
        independent = tx.run_tool(
            "independent", ["bash", "-c", "echo two > b.txt"]
        )
        consumer = tx.run_tool(
            "consumer", ["bash", "-c", "cat a.txt >/dev/null"]
        )

        assert consumer.parents == [producer.step_id]
        assert tx.rollback_causal(producer.step_id) == [
            producer.step_id,
            consumer.step_id,
        ]
        assert tx.ledger.steps[independent.step_id].status == "speculative"
        assert tx.commit(independent.step_id) == independent.step_id
        assert not (ws / "a.txt").exists()
        assert (ws / "b.txt").read_text(encoding="utf-8") == "two\n"
    finally:
        tx.close(destroy=True)


def test_causal_rollback_rejects_retained_descendant_effects(
    tmp_path: Path,
) -> None:
    ws, tx = _begin(tmp_path, "causal-overlap")
    try:
        first = tx.run_tool(
            "parent", ["bash", "-c", "mkdir pkg; echo one > pkg/a.txt"]
        )
        retained = tx.run_tool(
            "child", ["bash", "-c", "echo two > pkg/b.txt"]
        )

        with pytest.raises(ValueError, match="overlaps retained effects"):
            tx.rollback_causal(first.step_id)
        assert tx.ledger.steps[first.step_id].status == "speculative"
        assert tx.ledger.steps[retained.step_id].status == "speculative"
    finally:
        tx.close(destroy=True)


def test_causal_rollback_restores_lower_delete_and_keeps_independent(
    tmp_path: Path,
) -> None:
    ws, tx = _begin(tmp_path, "causal-delete")
    seed = ws / "seed.txt"
    seed.write_text("seed\n", encoding="utf-8")
    try:
        deleted = tx.run_tool("delete", ["bash", "-c", "rm seed.txt"])
        independent = tx.run_tool(
            "independent", ["bash", "-c", "echo keep > keep.txt"]
        )

        assert tx.rollback_causal(deleted.step_id) == [deleted.step_id]
        tx.commit(independent.step_id)
        assert seed.read_text(encoding="utf-8") == "seed\n"
        assert (ws / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    finally:
        tx.close(destroy=True)
