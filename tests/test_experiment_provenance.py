from experiments.scripts.experiment_provenance import source_manifest


def test_provenance_tracks_uncommitted_sources_but_not_results(tmp_path):
    source = tmp_path / "src/agenttx/runtime.py"
    source.parent.mkdir(parents=True)
    source.write_text("first version\n")
    first = source_manifest(tmp_path)
    results = tmp_path / "experiments/results/run"
    results.mkdir(parents=True)
    (results / "data.csv").write_text("new results\n")
    (source.parent / "__pycache__").mkdir()
    (source.parent / "__pycache__/cached.py").touch()
    assert source_manifest(tmp_path) == first
    source.write_text("second version\n")
    assert source_manifest(tmp_path)["sha256"] != first["sha256"]
    assert first["files"][0]["path"] == "src/agenttx/runtime.py"
