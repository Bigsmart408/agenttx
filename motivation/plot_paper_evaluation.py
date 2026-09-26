#!/usr/bin/env python3
"""Regenerate the cited evaluation plots from their original result CSVs."""

from __future__ import annotations

import ast
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from motivation.plot_motivation_four import STYLES, configure_style


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "experiments" / "results"
FIGURES = ROOT / "motivation"
PAPER = ROOT / "paper" / "img"
CM = 1 / 2.54
COLORS = {
    "agenttx": "#694391",
    "checkpoint": STYLES["turn_checkpoint_proxy"]["color"],
    "temporal": STYLES["temporal_step"]["color"],
    "whole": STYLES["whole_branch"]["color"],
    "ideal": STYLES["ideal_oracle"]["color"],
    "neutral": STYLES["no_recovery"]["color"],
}
LINES = {
    "agenttx": dict(color=COLORS["agenttx"], marker="P", linestyle="-"),
    "checkpoint": dict(color=COLORS["checkpoint"], marker="o", linestyle="--", markerfacecolor="none"),
    "temporal": dict(color=COLORS["temporal"], marker="x", linestyle=":"),
    "whole": dict(color=COLORS["whole"], marker="D", linestyle="-.", markerfacecolor="none"),
    "neutral": dict(color=COLORS["neutral"], marker="^", linestyle="--", markerfacecolor="none"),
}


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def save(fig: plt.Figure, name: str) -> None:
    for directory in (FIGURES, PAPER):
        directory.mkdir(parents=True, exist_ok=True)
        fig.savefig(directory / f"{name}.pdf", bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


def finish_panels(fig: plt.Figure, axes: np.ndarray, handles: list, columns: int) -> None:
    for ax in axes.flat:
        ax.tick_params(axis="both", labelsize=7)
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 1.02),
               ncol=columns, fontsize=6.7, columnspacing=0.9, handlelength=1.8)
    fig.tight_layout(pad=0.7, h_pad=1.5, w_pad=1.0, rect=(0, 0, 1, 0.91))


def causal_retention() -> None:
    data = rows(RESULTS / "causal_retention.csv")
    styles = {
        "causal": ("AgentTX causal", LINES["agenttx"]),
        "temporal": ("Temporal step", LINES["temporal"]),
        "whole_session": ("Whole branch", LINES["whole"]),
        "causal_without_dependencies": ("No dependencies", LINES["neutral"]),
    }
    panels = (
        ("size", "independent_retention_mean", "Useful work retained (%)", "DAG size (# calls)\n(a) Independent work"),
        ("size", "target_removed_mean", "Invalid work removed (%)", "DAG size (# calls)\n(b) Recovery completeness"),
        ("fault_position", "utility", "Recovery utility (%)", "Requested fault position (%)\n(c) Retention x removal"),
        ("size", "rollback_ms_p95", "Rollback p95 (ms)", "DAG size (# calls)\n(d) Recovery latency"),
    )
    fig, axes = plt.subplots(2, 2, figsize=(17.8 * CM, 7.0 * CM))
    handles = []
    for ax, (sweep, metric, ylabel, xlabel) in zip(axes.flat, panels):
        for mode, (label, style) in styles.items():
            group = sorted((r for r in data if r["sweep"] == sweep and r["mode"] == mode),
                           key=lambda r: float(r["x_value"]))
            x = [float(r["x_value"]) * (100 if sweep == "fault_position" else 1) for r in group]
            if metric == "utility":
                y = [100 * float(r["independent_retention_mean"]) * float(r["target_removed_mean"]) for r in group]
            else:
                scale = 100 if metric.endswith("_mean") else 1
                y = [scale * float(r[metric]) for r in group]
            line, = ax.plot(x, y, label=label, **style)
            if ax is axes[0, 0]:
                handles.append(line)
        ax.set_ylabel(ylabel)
        ax.set_xlabel(xlabel)
        if metric != "rollback_ms_p95":
            ax.set_ylim(-5, 105)
    finish_panels(fig, axes, handles, 4)
    save(fig, "FIG-Causal-Retention")


def token_recovery() -> None:
    data = rows(RESULTS / "deepseek" / "token_recovery.csv")
    modes = {
        "causal": ("AgentTX causal", LINES["agenttx"]),
        "temporal_checkpoint": ("Temporal checkpoint", LINES["temporal"]),
        "whole_branch_abort": ("Whole branch", LINES["whole"]),
    }
    panels = (
        ("total_tokens_mean", "Replay tokens", "(a) Total LLM work"),
        ("completion_tokens_mean", "Completion tokens", "(b) Regenerated content"),
        ("regenerated_documents_mean", "Documents regenerated", "(c) Valid work lost"),
        ("wall_s_p95", "Recovery p95 (s)", "(d) Replay latency"),
    )
    fig, axes = plt.subplots(2, 2, figsize=(17.8 * CM, 7.0 * CM))
    handles = []
    for ax, (metric, ylabel, subtitle) in zip(axes.flat, panels):
        for mode, (label, style) in modes.items():
            group = sorted((r for r in data if r["mode"] == mode), key=lambda r: int(r["document_lines"]))
            line, = ax.plot([int(r["document_lines"]) for r in group],
                            [float(r[metric]) for r in group], label=label, **style)
            if ax is axes[0, 0]:
                handles.append(line)
        ax.set_ylabel(ylabel)
        ax.set_xlabel(f"Document size (# entries)\n{subtitle}")
        ax.set_xticks((12, 24, 48))
        ax.set_ylim(bottom=-0.03 * max(1, ax.get_ylim()[1]))
    finish_panels(fig, axes, handles, 3)
    save(fig, "FIG-Token-Recovery")


def real_agent_recovery() -> None:
    data = sorted(rows(RESULTS / "deepseek" / "real_agent_recovery.csv"), key=lambda r: int(r["repeat"]))
    x = [int(r["repeat"]) + 1 for r in data]
    fig, axes = plt.subplots(2, 2, figsize=(17.8 * CM, 7.0 * CM))
    handles = []

    def draw(ax, values, label, style):
        line, = ax.plot(x, values, label=label, **style)
        handles.append(line)

    draw(axes[0, 0], [float(r["wall_s"]) for r in data], "End-to-end latency", LINES["agenttx"])
    axes[0, 0].axhline(np.median([float(r["wall_s"]) for r in data]),
                       color=COLORS["neutral"], linestyle=":", linewidth=0.7)
    axes[0, 0].set_ylabel("Task latency (s)")
    axes[0, 0].set_xlabel("Agent run\n(a) Live recovery latency")
    draw(axes[0, 1], [int(r["tool_calls"]) for r in data], "Tool calls", LINES["checkpoint"])
    draw(axes[0, 1], [int(r["ledger_steps"]) for r in data], "Ledger steps", LINES["whole"])
    axes[0, 1].set_ylabel("Count")
    axes[0, 1].set_xlabel("Agent run\n(b) Trajectory size")
    draw(axes[1, 0], [len(ast.literal_eval(r["rollback_targets"])) for r in data],
         "Invalidated steps", LINES["agenttx"])
    draw(axes[1, 0], [int(r["inspect_calls"]) for r in data], "Ledger inspections", LINES["checkpoint"])
    axes[1, 0].set_ylabel("Count")
    axes[1, 0].set_xlabel("Agent run\n(c) Recovery decision")
    checks = (("Root", "selected_root"), ("Targets", "causal_targets_correct"),
              ("Retain", "independent_retained"), ("Remove", "derived_removed"),
              ("Tests", "success"), ("No leak", "host_polluted_before_commit"))
    rates = [100 * sum((r[key].lower() not in ("true", "1")) if label == "No leak"
                       else (r[key].lower() in ("true", "1")) for r in data) / len(data)
             for label, key in checks]
    semantic, = axes[1, 1].plot(range(len(checks)), rates, label="Semantic checks", **LINES["agenttx"])
    handles.append(semantic)
    axes[1, 1].set_ylabel("Runs satisfying invariant (%)")
    axes[1, 1].set_xlabel("Recovery property\n(d) Semantic correctness")
    axes[1, 1].set_xticks(range(len(checks)), [label for label, _ in checks], rotation=20, ha="right")
    axes[1, 1].set_ylim(-5, 105)
    for ax in axes.flat[:3]:
        ax.set_xticks(x)
    finish_panels(fig, axes, handles, 3)
    save(fig, "FIG-Real-Agent-Recovery")


def main() -> None:
    configure_style()
    causal_retention()
    token_recovery()
    real_agent_recovery()


if __name__ == "__main__":
    main()
