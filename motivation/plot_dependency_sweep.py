#!/usr/bin/env python3
"""Plot the preregistered dependency-value motivation sweep."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
RAW = (
    ROOT
    / "experiments"
    / "results"
    / "motivation"
    / "microbenchmark"
    / "motivation_recovery_raw.csv"
)
CONFIG = RAW.parent / "config.json"
OUTPUT = ROOT / "motivation" / "FIG-Dependency-Sweep"
PAPER_OUTPUT = ROOT / "paper" / "img" / "FIG-Dependency-Sweep"
STANDARD_WIDTH_CM = 17.8
CM_TO_INCH = 1 / 2.54

POLICIES = (
    "turn_checkpoint_proxy",
    "temporal_step",
    "whole_branch",
    "ideal_oracle",
)
LABELS = {
    "turn_checkpoint_proxy": "Turn checkpoint",
    "temporal_step": "Temporal suffix",
    "whole_branch": "Whole branch",
    "ideal_oracle": "Ideal oracle",
}
STYLES = {
    "turn_checkpoint_proxy": dict(
        color="#111111", marker="o", linestyle="--", markerfacecolor="none",
        markevery=(0, 2), zorder=3,
    ),
    "temporal_step": dict(
        color="#e78129", marker="x", linestyle=":", markeredgewidth=0.9,
        zorder=4,
    ),
    "whole_branch": dict(
        color="#3f7eab", marker="D", linestyle="-.", markerfacecolor="none",
        markevery=(1, 2), zorder=2,
    ),
    "ideal_oracle": dict(
        color="#c00000", marker="s", linestyle="-", zorder=5,
    ),
}


def bootstrap_mean_ci(values: list[float], seed: int) -> tuple[float, float]:
    if len(values) == 1:
        return values[0], values[0]
    rng = np.random.default_rng(seed)
    samples = rng.choice(values, size=(4000, len(values)), replace=True).mean(axis=1)
    return tuple(float(value) for value in np.quantile(samples, [0.025, 0.975]))


def load_rows() -> tuple[list[dict[str, str]], dict]:
    with CONFIG.open(encoding="utf-8") as handle:
        config = json.load(handle)
    with RAW.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    expected_rows = int(config["planned_m1_traces"]) * len(config["policies"])
    if len(rows) != expected_rows:
        raise ValueError(
            f"expected {expected_rows} primary-sweep rows, found {len(rows)}"
        )
    expected = {float(value) for value in config["dependency_values"]}
    observed = {float(row["requested_dependency_value"]) for row in rows}
    if observed != expected:
        raise ValueError(f"dependency grid mismatch: {sorted(observed)}")
    return rows, config


def series(
    rows: list[dict[str, str]], policy: str, metric: str, repeats: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    grouped: dict[float, list[float]] = defaultdict(list)
    for row in rows:
        if row["policy"] == policy:
            grouped[float(row["actual_dependency_value"])].append(float(row[metric]))
    x = np.array(sorted(grouped))
    means = []
    lows = []
    highs = []
    for index, value in enumerate(x):
        cells = grouped[float(value)]
        if len(cells) != repeats:
            raise ValueError(
                f"{policy} at {value} has {len(cells)} rows, expected {repeats}"
            )
        means.append(float(np.mean(cells)))
        low, high = bootstrap_mean_ci(cells, seed=20260920 + index)
        lows.append(low)
        highs.append(high)
    return x, np.array(means), np.array(lows), np.array(highs)


def configure_style() -> None:
    plt.rcParams.update(plt.rcParamsDefault)
    plt.rcParams.update(
        {
            "font.family": "Nimbus Roman",
            "font.size": 8,
            "axes.labelsize": 8,
            "axes.titlesize": 8,
            "legend.fontsize": 6.5,
            "xtick.labelsize": 7,
            "ytick.labelsize": 7,
            "axes.grid": False,
            "axes.linewidth": 0.6,
            "xtick.direction": "in",
            "ytick.direction": "in",
            "xtick.major.width": 0.6,
            "ytick.major.width": 0.6,
            "legend.frameon": True,
            "legend.edgecolor": "0.55",
            "legend.framealpha": 1.0,
            "legend.fancybox": False,
            "lines.linewidth": 0.9,
            "lines.markersize": 3.2,
            "savefig.dpi": 300,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )


def main() -> int:
    rows, config = load_rows()
    repeats = int(config["repeats"])
    configure_style()
    fig, axes = plt.subplots(
        1,
        2,
        figsize=(STANDARD_WIDTH_CM * CM_TO_INCH, 6.4 * CM_TO_INCH),
        dpi=300,
    )

    for policy in POLICIES:
        x, mean, low, high = series(
            rows, policy, "useful_tokens_wasted", repeats
        )
        style = STYLES[policy]
        axes[0].plot(x, mean, label=LABELS[policy], **style)
        if np.any(high > low):
            axes[0].fill_between(
                x,
                low,
                high,
                color=style["color"],
                alpha=0.10,
                linewidth=0,
            )

        x, mean, low, high = series(rows, policy, "valid_retention", repeats)
        axes[1].plot(x, 100 * mean, **style)
        if np.any(high > low):
            axes[1].fill_between(
                x,
                100 * low,
                100 * high,
                color=style["color"],
                alpha=0.10,
                linewidth=0,
            )

    for ax in axes:
        ax.set_xlim(-0.02, 1.02)
        ax.set_xticks([0.0, 0.25, 0.5, 0.75, 1.0])
        ax.tick_params(axis="both", labelsize=7)

    axes[0].set_ylabel("Useful token weight discarded")
    axes[0].set_xlabel("Dependency value\n(a) Recovery waste", fontsize=7)
    axes[0].set_ylim(bottom=-40)
    axes[1].set_ylabel("Valid work retained (%)")
    axes[1].set_xlabel("Dependency value\n(b) State retention", fontsize=7)
    axes[1].set_ylim(-3, 103)

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.995),
        ncol=4,
        columnspacing=0.8,
        handlelength=1.8,
        handletextpad=0.35,
        borderpad=0.3,
    )
    fig.subplots_adjust(left=0.08, right=0.995, bottom=0.24, top=0.78, wspace=0.28)
    for stem in (OUTPUT, PAPER_OUTPUT):
        stem.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(stem.with_suffix(".pdf"), bbox_inches="tight", pad_inches=0.02)
        fig.savefig(stem.with_suffix(".png"), bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
