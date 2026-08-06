import stat
from pathlib import Path

from agenttx.ledger import EffectKind
from agenttx.runtime import AgentTX


def _effects(record):
    return {(effect.path, effect.kind) for effect in record.effects}


def test_empty_directory_is_recorded_and_committed(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    tx = AgentTX.begin(workdir=ws, session_dir=tmp_path / "session")
    try:
        record = tx.run_tool("mkdir", ["bash", "-c", "mkdir empty-dir"])
        assert (str(ws / "empty-dir"), EffectKind.WRITE) in _effects(record)
        assert not (ws / "empty-dir").exists()
        tx.commit()
        assert (ws / "empty-dir").is_dir()
    finally:
        tx.close(destroy=True)


def test_repeated_metadata_changes_are_each_recorded(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    target = ws / "mode.txt"
    target.write_text("same-content\n", encoding="utf-8")
    target.chmod(0o644)
    tx = AgentTX.begin(workdir=ws, session_dir=tmp_path / "session")
    try:
        first = tx.run_tool("chmod-600", ["bash", "-c", "chmod 600 mode.txt"])
        second = tx.run_tool("chmod-640", ["bash", "-c", "chmod 640 mode.txt"])
        expected = (str(target), EffectKind.WRITE)
        assert expected in _effects(first)
        assert expected in _effects(second)
        tx.commit()
        assert stat.S_IMODE(target.stat().st_mode) == 0o640
    finally:
        tx.close(destroy=True)


def test_rename_records_source_delete_and_destination_write(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    source = ws / "source.txt"
    destination = ws / "destination.txt"
    source.write_text("payload\n", encoding="utf-8")
    tx = AgentTX.begin(workdir=ws, session_dir=tmp_path / "session")
    try:
        record = tx.run_tool("rename", ["bash", "-c", "mv source.txt destination.txt"])
        effects = _effects(record)
        assert (str(source), EffectKind.DELETE) in effects
        assert (str(destination), EffectKind.WRITE) in effects
        tx.commit()
        assert not source.exists()
        assert destination.read_text(encoding="utf-8") == "payload\n"
    finally:
        tx.close(destroy=True)
