#!/usr/bin/env python3
"""Integration tests against the real binpash/try runtime."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agenttx.ledger import Effect, EffectKind
from agenttx.policy import CommitPolicy
from agenttx.runtime import AgentTX


def _begin(tmp_path: Path, name: str = "session") -> tuple[Path, AgentTX]:
    ws = tmp_path / (name + "-ws")
    ws.mkdir()
    return ws, AgentTX.begin(workdir=ws, session_dir=tmp_path / name)


@pytest.mark.skipif(shutil.which("strace") is None, reason="strace is unavailable")
def test_strace_metadata_is_not_exposed_as_command_stderr(tmp_path: Path) -> None:
    ws = tmp_path / "trace-output-ws"
    ws.mkdir()
    (ws / "input.txt").write_text("input\n", encoding="utf-8")
    tx = AgentTX.begin(
        workdir=ws,
        session_dir=tmp_path / "trace-output-session",
        trace_backend="strace",
    )
    try:
        record = tx.run_tool(
            "inspect",
            ["bash", "-c", "ls -A; cat input.txt; echo visible-error >&2"],
        )

        assert record.stdout == "input.txt\ninput\n"
        assert record.stderr == "visible-error\n"
        assert "execve(" not in record.stderr
        assert all(".agenttx-strace-" not in effect.path for effect in record.effects)
    finally:
        tx.close(destroy=True)


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


def test_direct_runtime_commit_enforces_default_policy(tmp_path: Path) -> None:
    ws, tx = _begin(tmp_path, "policy-default")
    try:
        record = tx.run_tool(
            "write-secret", ["bash", "-c", "echo secret > private.pem"]
        )
        assert record.returncode == 0
        with pytest.raises(PermissionError, match="commit blocked by policy"):
            tx.commit(record.step_id)
        assert tx.ledger.committed_frontier == -1
        assert tx.ledger.steps[record.step_id].status == "speculative"
        assert not (ws / "private.pem").exists()
    finally:
        tx.close(destroy=True)


def test_commit_ignores_workspace_dependency_environment(tmp_path: Path) -> None:
    ws, tx = _begin(tmp_path, "dependency-environment")
    try:
        record = tx.run_tool(
            "build-with-dependency",
            [
                "bash",
                "-c",
                "mkdir -p .eggs/certifi.egg/certifi; "
                "echo public-ca > .eggs/certifi.egg/certifi/cacert.pem; "
                "echo result > result.txt",
            ],
        )
        assert record.returncode == 0

        tx.commit(record.step_id)

        assert (ws / "result.txt").read_text(encoding="utf-8") == "result\n"
        assert not (ws / ".eggs").exists()
    finally:
        tx.close(destroy=True)


def test_commit_discards_external_private_tmp_but_materializes_workspace(
    tmp_path: Path,
) -> None:
    ws, tx = _begin(tmp_path, "private-tmp")
    external = tmp_path / "astropy-test-venv" / "bin" / "python"
    try:
        record = tx.run_tool(
            "mixed-temp",
            [
                "bash",
                "-c",
                f"mkdir -p {external.parent}; echo temp > {external}; echo result > result.txt",
            ],
        )
        assert record.returncode == 0
        assert any(effect.path == str(external) for effect in record.effects)
        tx.commit(record.step_id)
        assert (ws / "result.txt").read_text(encoding="utf-8") == "result\n"
        assert not external.exists()
    finally:
        tx.close(destroy=True)


def test_cli_commit_cannot_bypass_default_policy(tmp_path: Path) -> None:
    ws, tx = _begin(tmp_path, "policy-cli")
    assert tx.pool is not None
    session = tx.pool.session_dir
    tx.run_tool("write-secret", ["bash", "-c", "echo secret > private.pem"])
    tx.close(destroy=False)

    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agenttx",
                "commit",
                "--session",
                str(session),
            ],
            cwd=str(ROOT),
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=30,
        )
        assert result.returncode != 0
        assert "commit blocked by policy" in result.stderr
        assert not (ws / "private.pem").exists()
    finally:
        resumed = AgentTX.load(session)
        resumed.close(destroy=True)


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


def test_historical_commit_uses_shared_read_only_snapshot(tmp_path: Path) -> None:
    ws, tx = _begin(tmp_path, "historical-shared")
    try:
        first = tx.run_tool("first", ["bash", "-c", "echo old > same.txt"])
        inspected = tx.run_tool("inspect", ["bash", "-c", "cat same.txt"])
        tx.run_tool("second", ["bash", "-c", "echo new > same.txt"])

        snapshot = tx.pool.layers.root / "before_0002"
        assert snapshot.is_symlink()
        assert tx.commit(inspected.step_id) == inspected.step_id
        assert (ws / "same.txt").read_text(encoding="utf-8") == "old\n"
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


def test_causal_rollback_keeps_later_shared_snapshot_valid(tmp_path: Path) -> None:
    _ws, tx = _begin(tmp_path, "causal-shared")
    try:
        tx.run_tool("producer", ["bash", "-c", "echo old > a.txt"])
        discard = tx.run_tool("no-op", ["bash", "-c", "true"])
        later = tx.run_tool("later-no-op", ["bash", "-c", "true"])
        independent = tx.run_tool("independent", ["bash", "-c", "echo keep > b.txt"])

        before_later = tx.pool.layers.root / "before_0002"
        before_independent = tx.pool.layers.root / "before_0003"
        assert before_later.is_symlink()
        assert before_independent.is_symlink()

        assert tx.rollback_causal(discard.step_id) == [discard.step_id]
        assert before_later.is_symlink()
        assert before_independent.resolve() == tx.pool.layers.root / "before_0001"
        assert tx.rollback_causal(independent.step_id) == [independent.step_id]
        assert tx.path_exists(tx.workspace / "a.txt")
        assert not tx.path_exists(tx.workspace / "b.txt")
        assert tx.rollback_causal(later.step_id) == [later.step_id]
        assert not (tx.pool.layers.root / "before_0001").exists()
    finally:
        tx.close(destroy=True)


def test_transient_write_shares_next_filesystem_snapshot(tmp_path: Path) -> None:
    _ws, tx = _begin(tmp_path, "transient-shared")
    try:
        tx.run_tool(
            "transient",
            ["bash", "-c", "echo temporary > scratch.txt; rm scratch.txt"],
        )
        assert tx.pool._pending_snapshot_changes == []
        tx.run_tool("next", ["bash", "-c", "true"])
        assert (tx.pool.layers.root / "before_0001").is_symlink()
    finally:
        tx.close(destroy=True)


def test_causal_rollback_allows_retained_read_of_replaced_path(tmp_path: Path) -> None:
    ws, tx = _begin(tmp_path, "causal-retained-read")
    source = ws / "source.txt"
    source.write_text("committed\n", encoding="utf-8")
    try:
        inspected = tx.run_tool("inspect", ["bash", "-c", "cat source.txt"])
        producer = tx.run_tool(
            "producer", ["bash", "-c", "echo corrupted > source.txt"]
        )

        assert inspected.step_id not in producer.parents
        assert tx.rollback_causal(producer.step_id) == [producer.step_id]
        assert tx.ledger.steps[inspected.step_id].status == "speculative"
        restored = tx.run_tool("read-restored", ["bash", "-c", "cat source.txt"])
        assert restored.stdout == "committed\n"
    finally:
        tx.close(destroy=True)


def test_causal_rollback_ignores_explicit_ephemeral_root_conflicts(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "ephemeral-causal"
    ws.mkdir()
    ephemeral = ws / ".tmp"
    ephemeral.mkdir()
    tx = AgentTX.begin(
        workdir=ws,
        session_dir=tmp_path / "ephemeral-causal-session",
        trace_backend="strace",
        commit_policy=CommitPolicy(
            workdir=ws,
            ephemeral_roots=(ephemeral,),
        ),
    )
    try:
        producer = tx.run_tool("producer", ["bash", "-c", "echo bad > source.txt"])
        independent = tx.run_tool("independent", ["bash", "-c", "echo keep > keep.txt"])
        consumer = tx.run_tool(
            "consumer",
            ["bash", "-c", "cat source.txt >/dev/null; mkdir -p .tmp/pytest/run"],
        )
        tx.ledger.steps[independent.step_id].effects.append(
            Effect(str(ephemeral), EffectKind.WRITE)
        )

        assert tx.rollback_causal(producer.step_id) == [
            producer.step_id,
            consumer.step_id,
        ]
        tx.commit(independent.step_id)
        assert (ws / "keep.txt").read_text(encoding="utf-8") == "keep\n"
        assert not (ws / "source.txt").exists()
    finally:
        tx.close(destroy=True)


def test_causal_rollback_includes_retained_descendant_writer(
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

        assert tx.rollback_causal(first.step_id) == [
            first.step_id,
            retained.step_id,
        ]
        assert not (ws / "pkg").exists()
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


def test_causal_rollback_tracks_parent_directory_read(tmp_path: Path) -> None:
    ws, tx = _begin(tmp_path, "causal-hierarchy")
    try:
        producer = tx.run_tool(
            "producer",
            ["bash", "-c", "mkdir pkg; echo one > pkg/data.txt"],
        )
        independent = tx.run_tool(
            "independent", ["bash", "-c", "echo keep > keep.txt"]
        )
        consumer = tx.run_tool(
            "consumer", ["bash", "-c", "cat pkg/data.txt >/dev/null"]
        )

        assert consumer.parents == [producer.step_id]
        assert tx.rollback_causal(producer.step_id) == [
            producer.step_id,
            consumer.step_id,
        ]
        tx.commit(independent.step_id)
        assert not (ws / "pkg").exists()
        assert (ws / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    finally:
        tx.close(destroy=True)


def test_causal_rollback_tracks_lower_symlink_alias(tmp_path: Path) -> None:
    ws, tx = _begin(tmp_path, "causal-symlink")
    (ws / "real").mkdir()
    (ws / "alias").symlink_to("real", target_is_directory=True)
    try:
        producer = tx.run_tool(
            "producer", ["bash", "-c", "echo one > real/data.txt"]
        )
        consumer = tx.run_tool(
            "consumer", ["bash", "-c", "cat alias/data.txt >/dev/null"]
        )

        assert consumer.parents == [producer.step_id]
        assert tx.rollback_causal(producer.step_id) == [
            producer.step_id,
            consumer.step_id,
        ]
        assert (ws / "alias").is_symlink()
        assert not (ws / "real" / "data.txt").exists()
    finally:
        tx.close(destroy=True)


def test_causal_rollback_tracks_upper_symlink_alias(tmp_path: Path) -> None:
    ws, tx = _begin(tmp_path, "causal-upper-symlink")
    (ws / "real").mkdir()
    try:
        link = tx.run_tool(
            "link", ["bash", "-c", "ln -s real alias"]
        )
        producer = tx.run_tool(
            "producer", ["bash", "-c", "echo one > real/data.txt"]
        )
        consumer = tx.run_tool(
            "consumer", ["bash", "-c", "cat alias/data.txt >/dev/null"]
        )

        assert consumer.parents == [link.step_id, producer.step_id]
        assert tx.rollback_causal(producer.step_id) == [
            producer.step_id,
            consumer.step_id,
        ]
        assert (ws / "alias").is_symlink() is False
    finally:
        tx.close(destroy=True)
