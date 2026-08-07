from pathlib import Path
import json

from agenttx.semisolate import SharedSemisolate


def test_optimization_history_preserves_preimages_and_manifests() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "agenttx" / "optimization_history"
    assert (root / "README.md").exists()
    for name in (
        "iteration_00_unoptimized",
        "iteration_01_known_write_trace_bypass",
        "iteration_02_known_read_effect_bypass",
        "iteration_03_persistent_command_script",
    ):
        snapshot = root / name
        assert (snapshot / "runtime.py").exists()
        assert (snapshot / "semisolate.py").exists()
        assert (snapshot / "layers.py").exists()
        assert (snapshot / "harness.py").exists()
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["files"] == ["runtime.py", "semisolate.py", "layers.py", "harness.py"]


def test_shared_command_script_is_reused_and_cleaned(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    tx = SharedSemisolate(workspace=workspace, trace_reads=False)
    first = tx._write_cmd_script(["bash", "-c", "echo one"])
    second = tx._write_cmd_script(["bash", "-c", "echo two"])
    parent = first.parent
    try:
        assert first == second
        assert second.read_text(encoding="utf-8").endswith("echo two\n")
    finally:
        tx.close()
    assert not parent.exists()
