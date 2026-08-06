import json
from pathlib import Path

import pytest

import agenttx.runtime as runtime_module
from agenttx.runtime import AgentTX


def test_load_preserves_layer_sequence_and_prior_speculation(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    session = tmp_path / "session"
    ws.mkdir()

    tx = AgentTX.begin(workdir=ws, session_dir=session)
    first = tx.run_tool("first", ["bash", "-c", "echo first > first.txt"])
    assert first.step_id == 0
    tx.close(destroy=False)

    resumed = AgentTX.load(session)
    try:
        second = resumed.run_tool("second", ["bash", "-c", "echo second > second.txt"])
        assert second.step_id == 1
        assert (session / "layers" / "before_0001").is_dir()

        assert resumed.rollback(second.step_id) == [1]
        assert resumed.ledger.steps[0].status == "speculative"
        assert resumed.ledger.steps[1].status == "rolled_back"

        resumed.commit(first.step_id)
        assert (ws / "first.txt").read_text(encoding="utf-8") == "first\n"
        assert not (ws / "second.txt").exists()
    finally:
        resumed.close(destroy=True)


def test_failed_metadata_replace_preserves_previous_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "ws"
    session = tmp_path / "session"
    ws.mkdir()

    tx = AgentTX.begin(workdir=ws, session_dir=session)
    meta = session / "agenttx.json"
    before = meta.read_bytes()
    tx.ledger.add_step("unpersisted")

    def fail_replace(source: str, destination: str) -> None:
        raise OSError("injected replace failure")

    with monkeypatch.context() as patch:
        patch.setattr(runtime_module.os, "replace", fail_replace)
        with pytest.raises(OSError, match="injected replace failure"):
            tx._persist()

    assert meta.read_bytes() == before
    assert json.loads(meta.read_text(encoding="utf-8"))["ledger"]["steps"] == []
    assert list(session.glob(".agenttx.json.*.tmp")) == []
    tx.close(destroy=True)


def test_trace_mode_survives_session_reload(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    session = tmp_path / "session"
    workspace.mkdir()

    tx = AgentTX.begin(
        workdir=workspace,
        session_dir=session,
        trace_reads=False,
    )
    tx.close(destroy=False)

    resumed = AgentTX.load(session)
    try:
        assert resumed.trace_reads is False
        assert resumed.pool is not None
        assert resumed.pool.trace_reads is False
    finally:
        resumed.close(destroy=True)


def test_commit_wal_recovers_partial_materialization_on_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "ws"
    session = tmp_path / "session"
    ws.mkdir()
    target = ws / "target.txt"
    target.write_text("old\n", encoding="utf-8")

    tx = AgentTX.begin(workdir=ws, session_dir=session)
    tx.run_tool("rewrite", ["bash", "-c", "echo new > target.txt"])
    assert target.read_text(encoding="utf-8") == "old\n"

    assert tx.pool is not None

    def crash_after_partial_host_write(paths):
        target.write_text("new\n", encoding="utf-8")
        raise KeyboardInterrupt("simulated process loss")

    monkeypatch.setattr(tx.pool, "commit", crash_after_partial_host_write)
    with pytest.raises(KeyboardInterrupt, match="simulated process loss"):
        tx.commit()
    assert (session / "commit_wal.json").exists()
    tx.close(destroy=False)

    resumed = AgentTX.load(session)
    try:
        assert target.read_text(encoding="utf-8") == "old\n"
        assert not (session / "commit_wal.json").exists()
        assert resumed.commit() == 0
        assert target.read_text(encoding="utf-8") == "new\n"
        assert not (session / "commit_wal.json").exists()
    finally:
        resumed.close(destroy=True)
