from pathlib import Path
import json


def test_optimization_history_preserves_preimages_and_manifests() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "agenttx" / "optimization_history"
    assert (root / "README.md").exists()
    for name in ("iteration_00_unoptimized", "iteration_01_known_write_trace_bypass"):
        snapshot = root / name
        assert (snapshot / "runtime.py").exists()
        assert (snapshot / "semisolate.py").exists()
        assert (snapshot / "layers.py").exists()
        assert (snapshot / "harness.py").exists()
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["files"] == ["runtime.py", "semisolate.py", "layers.py", "harness.py"]