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
    assert "chain:" in plot
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


def test_fast_style_scaling_notebooks_use_line_plots() -> None:
    scaling = _code(ROOT / "motivation" / "plot_scaling.ipynb")
    tail = _code(ROOT / "motivation" / "plot_tail_scaling.ipynb")
    assert "motivation_scaling.csv" in scaling
    assert "motivation_tail_scaling.csv" in tail
    assert "ax.plot" in scaling
    assert "ax.plot" in tail
    assert "Trajectory length (# calls)" in scaling
    assert "Trajectory length (# calls)" in tail
    assert "ax.bar" not in scaling
    assert "ax.bar" not in tail


def test_causal_retention_notebook_uses_controlled_dag_results() -> None:
    source = _code(ROOT / "motivation" / "plot_causal_retention.ipynb")
    assert "causal_retention.csv" in source
    assert "independent_retention_mean" in source
    assert "target_removed_mean" in source
    assert "rollback_ms_p95" in source
    assert "ax.plot" in source
    assert "ax.bar" not in source


def test_new_recovery_notebooks_use_their_source_results() -> None:
    token = _code(ROOT / "motivation" / "plot_token_recovery.ipynb")
    real_agent = _code(ROOT / "motivation" / "plot_real_agent_recovery.ipynb")
    robustness = _code(ROOT / "motivation" / "plot_robustness.ipynb")

    assert "token_recovery.csv" in token
    assert "total_tokens_mean" in token
    assert "regenerated_documents_mean" in token
    assert "FIG-Token-Recovery.pdf" in token

    assert "real_agent_recovery.csv" in real_agent
    assert "causal_targets_correct" in real_agent
    assert "independent_retained" in real_agent
    assert "FIG-Real-Agent-Recovery.pdf" in real_agent

    assert "robustness.json" in robustness
    assert "worker_crash" in robustness
    assert "long_session" in robustness
    assert "concurrent_agents" in robustness
    assert "FIG-Robustness.pdf" in robustness

    for source in (token, real_agent, robustness):
        assert "Path.cwd()" in source
        assert "ax.plot" in source
        assert "ax.bar" not in source
