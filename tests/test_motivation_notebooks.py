"""Structural checks for the paper-facing AgentTX motivation notebooks."""

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_paper_motivation_reports_all_multiturn_boundaries() -> None:
    from experiments.motivation_strategies import (
        MOTIVATION_CORRECT_RECOVERY_POLICIES,
        MOTIVATION_POLICIES,
        POLICY_LABELS,
    )

    assert "turn_checkpoint_proxy" in MOTIVATION_POLICIES
    assert "whole_branch" in MOTIVATION_POLICIES
    assert "whole_branch" in MOTIVATION_CORRECT_RECOVERY_POLICIES
    assert POLICY_LABELS["turn_checkpoint_proxy"] == "Turn checkpoint"


def test_paper_motivation_names_artifact_backends() -> None:
    source = (ROOT / "motivation" / "plot_motivation_four.py").read_text(
        encoding="utf-8"
    )
    assert '"session_try": "try"' in source
    assert '"yolofs": "YoloFS"' in source
    assert '"branchfs": "BranchFS"' in source
    assert '"waypoint": "Waypoint"' in source
    artifact_labels = source.split("ARTIFACT_LABELS = {", 1)[1].split("}", 1)[0]
    assert "agenttx" not in artifact_labels
    assert "external_recovery_raw.csv" in source
    assert 'suite_slice="mb4_dependency"' in source
    assert '"artifact_checkpoint"' not in source
    assert '"YoloFS / Waypoint"' not in source


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


def test_intra_turn_motivation_notebook_uses_canonical_bundle() -> None:
    source = _code(ROOT / "motivation" / "plot_intra_turn_evidence.ipynb")
    assert "motivation.plot_intra_turn_evidence import render_all" in source
    assert "render_all()" in source

    module = (ROOT / "motivation" / "plot_intra_turn_evidence.py").read_text(
        encoding="utf-8"
    )
    assert "experiments" in module
    assert "results" in module
    assert "motivation_turn_audit" in module
    assert "motivation_recovery_raw.csv" in module
    assert "motivation_real_counterfactual_turns.csv" in module
    assert "FIG-M0-Turn-Audit-CDF" in module
    assert "FIG-M1-Strategy-Outcomes" in module
    assert "add_gridspec" not in module
    assert "FIG-M2-Extreme-Tradeoff" in module
    assert "FIG-M3-Strategy-Comparison" in module
    assert "FIG-M4-Real-Turn-Distribution" in module
    assert '"legacy_v7"' in module
    assert "common_cells" in module
    assert "motivation_replay_combined_summary.csv" in module
    assert ".imshow(" not in module
    assert ".bar(" not in module
    assert "fill_between" not in module
    assert "AgentTX" not in module
    assert "(ours)" not in module
    assert '"Causal"' not in module
    for label in (
        "No recovery",
        "Root only",
        "Turn checkpoint",
        "Temporal step",
        "Whole branch",
        "Ideal oracle",
    ):
        assert label in module


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
    token_end_to_end = _code(
        ROOT / "motivation" / "plot_token_end_to_end.ipynb"
    )
    real_agent = _code(ROOT / "motivation" / "plot_real_agent_recovery.ipynb")
    robustness = _code(ROOT / "motivation" / "plot_robustness.ipynb")

    assert "token_recovery.csv" in token
    assert "total_tokens_mean" in token
    assert "regenerated_documents_mean" in token
    assert "FIG-Token-Recovery.pdf" in token

    assert "token_end_to_end.csv" in token_end_to_end
    assert "total_tokens_mean" in token_end_to_end
    assert "prompt_tokens_mean" in token_end_to_end
    assert "completion_tokens_mean" in token_end_to_end
    assert "FIG-Token-End-to-End.pdf" in token_end_to_end

    assert "real_agent_recovery.csv" in real_agent
    assert "causal_targets_correct" in real_agent
    assert "independent_retained" in real_agent
    assert "FIG-Real-Agent-Recovery.pdf" in real_agent

    assert "robustness.json" in robustness
    assert "worker_crash" in robustness
    assert "long_session" in robustness
    assert "concurrent_agents" in robustness
    assert "FIG-Robustness.pdf" in robustness

    for source in (token, token_end_to_end, real_agent, robustness):
        assert "Path.cwd()" in source
        assert "ax.plot" in source
        assert "ax.bar" not in source


def test_bpf_trace_notebook_uses_overhead_results() -> None:
    source = _code(ROOT / "motivation" / "plot_bpf_trace.ipynb")
    assert "bpf_trace_overhead.csv" in source
    assert "per_step_ms_mean" in source
    assert "per_step_ms_p95" in source
    assert "reads_captured" in source
    assert "negatives_captured" in source
    assert "FIG-Bpf-Trace.pdf" in source
    assert "Path.cwd()" in source
    assert "ax.plot" in source
    assert "ax.bar" not in source
    assert "bench_bpf_trace.py" in source
