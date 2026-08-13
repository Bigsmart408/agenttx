#!/usr/bin/env python3
"""Plot full autonomous-recovery token usage for three rollback policies."""
from __future__ import annotations

import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

STANDARD_WIDTH = 17.8
STYLES = {
    "causal": dict(color="#c00000", marker="s", linestyle="-", linewidth=1.0, markersize=3.2),
    "temporal_checkpoint": dict(color="#e78129", marker="x", linestyle=":", linewidth=0.9, markersize=3.6, markeredgewidth=0.9),
    "whole_branch_abort": dict(color="black", marker="o", linestyle="--", linewidth=0.8, markersize=2.8, markerfacecolor="none"),
}
LABELS = {
    "causal": "AgentTX causal (ours)",
    "temporal_checkpoint": "optimistic checkpoint",
    "whole_branch_abort": "whole-branch abort",
}
MODES = list(STYLES)


def cm_to_inch(value: float) -> float:
    return value / 2.54


def configure_style() -> None:
    plt.rcParams.update(plt.rcParamsDefault)
    matplotlib.rcParams["text.usetex"] = False
    plt.rcParams["font.family"] = "Nimbus Roman"
    plt.rcParams["axes.grid"] = False
    plt.rcParams["axes.linewidth"] = 0.6
    plt.rcParams["xtick.direction"] = "in"
    plt.rcParams["ytick.direction"] = "in"
    plt.rcParams["legend.frameon"] = True
    plt.rcParams["legend.edgecolor"] = "0.55"
    plt.rcParams["legend.framealpha"] = 1.0
    plt.rcParams["legend.fancybox"] = False


def repository_root() -> Path:
    cwd = Path.cwd()
    if cwd.name == "motivation":
        return cwd.parent
    if (cwd / "experiments").is_dir() and (cwd / "motivation").is_dir():
        return cwd
    return Path(__file__).resolve().parents[1]


def provider_result(root: Path, name: str) -> Path:
    """Prefer the default provider's result dir, then any provider dir,
    then the legacy top-level result file."""
    results = root / "experiments" / "results"
    provider = os.environ.get("AGENTTX_PROVIDER", "deepseek")
    preferred = results / provider / name
    if preferred.exists():
        return preferred
    directories = sorted(
        path for path in results.iterdir()
        if path.is_dir() and (path / name).exists()
    )
    if directories:
        return directories[0] / name
    return results / name


def load_results(root: Path) -> pd.DataFrame:
    result = provider_result(root, "token_end_to_end.csv")
    if not result.exists():
        raise FileNotFoundError(
            f"missing {result}; run experiments/scripts/bench_token_end_to_end.py first"
        )
    frame = pd.read_csv(result)
    numeric = [
        "document_lines",
        "total_tokens_mean",
        "prompt_tokens_mean",
        "completion_tokens_mean",
        "recovery_wall_s_p95",
        "success_rate",
        "host_leak_rate",
    ]
    frame[numeric] = frame[numeric].apply(pd.to_numeric)
    return frame


def plot(frame: pd.DataFrame, output: Path) -> None:
    configure_style()

    def series(mode: str, metric: str):
        rows = frame[frame["mode"] == mode].sort_values("document_lines")
        return rows["document_lines"].to_numpy(), rows[metric].to_numpy(dtype=float)

    panels = [
        ("total_tokens_mean", "Total API tokens", "(a) Complete recovery cost"),
        ("prompt_tokens_mean", "Prompt tokens", "(b) Diagnosis and context"),
        ("completion_tokens_mean", "Completion tokens", "(c) Planning and repair"),
        ("recovery_wall_s_p95", "Recovery p95 (s)", "(d) End-to-end latency"),
    ]
    fig = plt.figure(dpi=300, figsize=(cm_to_inch(STANDARD_WIDTH), cm_to_inch(7.0)))
    handles = []
    for index, (metric, ylabel, subtitle) in enumerate(panels, start=1):
        ax = plt.subplot(2, 2, index)
        for mode in MODES:
            x, y = series(mode, metric)
            handle, = ax.plot(x, y, **STYLES[mode], label=LABELS[mode])
            if index == 1:
                handles.append(handle)
        ax.set_ylabel(ylabel, fontsize=8)
        ax.set_xlabel(f"Document size (# entries)\n{subtitle}", fontsize=7)
        ax.set_xticks(sorted(frame["document_lines"].unique()))
        ax.tick_params(axis="both", labelsize=7)
        ax.set_ylim(bottom=-0.03 * max(1.0, ax.get_ylim()[1]))

    ax_tokens = fig.axes[0]
    x_abort, y_abort = series("whole_branch_abort", "total_tokens_mean")
    x_causal, y_causal = series("causal", "total_tokens_mean")
    saved = y_abort[-1] - y_causal[-1]
    ax_tokens.annotate(
        f"{saved:,.0f} tokens saved",
        xy=(x_abort[-1], y_abort[-1]),
        xytext=(-74, -15),
        textcoords="offset points",
        fontsize=6.5,
        color="#c00000",
        arrowprops=dict(arrowstyle="-", color="#c00000", linewidth=0.6),
    )

    fig.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.035),
        ncol=3,
        fontsize=7,
        columnspacing=1.0,
        handlelength=1.8,
        handletextpad=0.35,
        borderpad=0.3,
    )
    plt.tight_layout(pad=0.6, h_pad=1.6, w_pad=1.2, rect=[0.0, 0.0, 1.0, 0.91])
    output.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        output / "FIG-Token-End-to-End.pdf",
        bbox_inches="tight",
        pad_inches=0.02,
        metadata={"CreationDate": None, "ModDate": None},
    )
    fig.savefig(
        output / "FIG-Token-End-to-End.png",
        dpi=300,
        bbox_inches="tight",
        pad_inches=0.02,
    )
    plt.close(fig)


def main() -> None:
    root = repository_root()
    frame = load_results(root)
    plot(frame, root / "motivation")
    print(f"wrote {root / 'motivation' / 'FIG-Token-End-to-End.png'}")


if __name__ == "__main__":
    main()
