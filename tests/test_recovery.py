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
