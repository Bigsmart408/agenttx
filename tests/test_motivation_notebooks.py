"""Structural checks for the paper-facing AgentTX motivation notebooks."""

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _code(nb_path: Path) -> str:
    notebook = json.loads(nb_path.read_text(encoding="utf-8"))
    assert notebook["nbformat"] == 4
    assert notebook["metadata"]["kernelspec"]["name"] == "python3"
    sources = []
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            source = "".join(cell["source"])
            ast.parse(source, filename=str(nb_path))
            sources.append(source)
    return "\n".join(sources)


def test_motivation_notebooks_are_valid_and_use_agenttx_results() -> None:
    plot = _code(ROOT / "motivation" / "plot.ipynb")
    tail = _code(ROOT / "motivation" / "plot_tail.ipynb")
    report = _code(ROOT / "motivation" / "report.ipynb")

    for source in (plot, tail, report):
        assert "experiments" in source
        assert "results" in source
        assert "Path.cwd()" in source

    assert "motivation_optimization_history.csv" in plot
    assert "motivation_runtime_comparison.csv" in plot
    assert "robustness.json" in tail
    assert "real_agent_robustness.json" in tail
    assert "Optimization chain" in plot
    assert "Real-agent refactor" in tail


def test_report_notebook_has_narrative_cells() -> None:
    notebook = json.loads(
        (ROOT / "motivation" / "report.ipynb").read_text(encoding="utf-8")
    )
    markdown = "\n".join(
        "".join(cell["source"])
        for cell in notebook["cells"]
        if cell["cell_type"] == "markdown"
    )
    assert "Problem" in markdown
    assert "Motivation" in markdown
    assert "Correctness" in markdown
