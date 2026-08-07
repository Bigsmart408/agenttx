from pathlib import Path
import json

from agenttx.layers import LayerStore
from agenttx.semisolate import SharedSemisolate


def test_optimization_history_preserves_preimages_and_manifests() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "agenttx" / "optimization_history"
    assert (root / "README.md").exists()
    for name in (
        "iteration_00_unoptimized",
        "iteration_01_known_write_trace_bypass",
        "iteration_02_known_read_effect_bypass",
        "iteration_03_persistent_command_script",
        "iteration_04_deferred_blob_gc",
        "iteration_05_direct_executable_script",
        "iteration_06_persistent_try_worker",
        "iteration_07_robustness_evaluation",
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


def test_persistent_try_worker_reuses_session(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    tx = SharedSemisolate(workspace=workspace, trace_reads=False)
    worker = None
    try:
        first = tx.run(["bash", "-c", "echo one > one.txt"])
        worker = tx._worker_process
        second = tx.run(["bash", "-c", "echo two > two.txt"])
        assert first.returncode == 0
        assert second.returncode == 0
        assert worker is not None
        assert tx._worker_process is worker
    finally:
        tx.close()
    assert worker is not None and worker.poll() is not None


def test_incremental_snapshot_replays_changed_paths(tmp_path: Path) -> None:
    upper = tmp_path / "upper"
    upper.mkdir()
    (upper / "stable.txt").write_text("stable\n", encoding="utf-8")
    (upper / "changed.txt").write_text("before\n", encoding="utf-8")
    store = LayerStore(tmp_path / "layers")
    store.snapshot_before(0, upper)

    (upper / "changed.txt").write_text("after\n", encoding="utf-8")
    (upper / "new.txt").write_text("new\n", encoding="utf-8")
    store.snapshot_before(1, upper, changed_paths=["/changed.txt", "/new.txt"])

    assert (store.root / "before_0001/changed.txt").read_text(encoding="utf-8") == "after\n"
    assert (store.root / "before_0001/new.txt").read_text(encoding="utf-8") == "new\n"
    assert (store.root / "before_0001/stable.txt").read_text(encoding="utf-8") == "stable\n"
    assert (store.root / "before_0000/stable.txt").stat().st_ino == (
        store.root / "before_0001/stable.txt"
    ).stat().st_ino
