#!/usr/bin/env python3
"""Motivation recovery figure over existing policies only (no AgentTX).

Uses the causal_retention CSV but drops AgentTX causal / ours series.
Maps measured policies to Background-table recovery styles:
  temporal      -> checkpoint-style (Crab / DeltaBox)
  whole_session -> session/branch abort (YoloFS / BranchFS)
  causal_without_dependencies -> command undo without dependency tracking
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

STANDARD_WIDTH = 17.8


def cm_to_inch(v: float) -> float:
    return v / 2.54


def main() -> None:
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

    # Distinct colors; no AgentTX red (#c00000).
    STYLES = {
        "temporal": dict(
            color="#e78129", marker="x", linestyle=":", linewidth=0.9,
            markersize=3.6, markeredgewidth=0.9,
        ),
        "whole_session": dict(
            color="#3f7eab", marker="o", linestyle="--", linewidth=0.9,
            markersize=3.0, markerfacecolor="none",
        ),
        "causal_without_dependencies": dict(
            color="#7a5f9a", marker="^", linestyle="-.", linewidth=0.9,
            markersize=3.2, markerfacecolor="none",
        ),
    }
    LABELS = {
        "temporal": "checkpoint-style (Crab/DeltaBox)",
        "whole_session": "session/branch abort (YoloFS/BranchFS)",
        "causal_without_dependencies": "command undo (no deps)",
    }
    MODES = list(STYLES)

    root = Path(__file__).resolve().parents[1]
    results = root / "experiments" / "results"
    figdir = root / "motivation"
    paper = root / "paper" / "img"
    figdir.mkdir(exist_ok=True)
    paper.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(results / "causal_retention.csv")

    def sweep_series(sweep, metric, mode):
        rows = df[(df["sweep"] == sweep) & (df["mode"] == mode)].copy()
        rows["x_num"] = pd.to_numeric(rows["x_value"])
        rows = rows.sort_values("x_num")
        return rows["x_num"].to_numpy(), rows[metric].to_numpy(dtype=float)

    fig = plt.figure(dpi=300, figsize=(cm_to_inch(STANDARD_WIDTH), cm_to_inch(7.0)))
    handles = []

    ax = plt.subplot(2, 2, 1)
    for mode in MODES:
        x, y = sweep_series("size", "independent_retention_mean", mode)
        h, = ax.plot(x, 100 * y, **STYLES[mode], label=LABELS[mode])
        handles.append(h)
    ax.set_ylabel("Useful work retained (%)", fontsize=8)
    ax.set_xlabel("DAG size (# calls)\n(a) Independent work", fontsize=7)
    ax.set_ylim(-5, 105)
    ax.set_xticks([16, 32, 64])
    ax.tick_params(axis="both", labelsize=7)

    ax = plt.subplot(2, 2, 2)
    for mode in MODES:
        x, y = sweep_series("size", "target_removed_mean", mode)
        ax.plot(x, 100 * y, **STYLES[mode])
    ax.set_ylabel("Invalid work removed (%)", fontsize=8)
    ax.set_xlabel("DAG size (# calls)\n(b) Recovery completeness", fontsize=7)
    ax.set_ylim(-5, 105)
    ax.set_xticks([16, 32, 64])
    ax.tick_params(axis="both", labelsize=7)

    ax = plt.subplot(2, 2, 3)
    for mode in MODES:
        rows = df[(df["sweep"] == "fault_position") & (df["mode"] == mode)].copy()
        rows["x_num"] = pd.to_numeric(rows["x_value"]) * 100
        rows = rows.sort_values("x_num")
        utility = (
            rows["independent_retention_mean"].to_numpy(dtype=float)
            * rows["target_removed_mean"].to_numpy(dtype=float)
        )
        ax.plot(rows["x_num"], 100 * utility, **STYLES[mode])
    ax.set_ylabel("Recovery utility (%)", fontsize=8)
    ax.set_xlabel("Requested fault position (%)\n(c) Retention × removal", fontsize=7)
    ax.set_ylim(-5, 105)
    ax.tick_params(axis="both", labelsize=7)

    ax = plt.subplot(2, 2, 4)
    for mode in MODES:
        x, y = sweep_series("size", "rollback_ms_p95", mode)
        ax.plot(x, y, **STYLES[mode])
    ax.set_ylabel("Rollback p95 (ms)", fontsize=8)
    ax.set_xlabel("DAG size (# calls)\n(d) Recovery latency", fontsize=7)
    ax.set_xticks([16, 32, 64])
    ax.tick_params(axis="both", labelsize=7)

    fig.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.035),
        ncol=3,
        fontsize=6.5,
        columnspacing=0.8,
        handlelength=1.8,
        handletextpad=0.35,
        borderpad=0.3,
    )
    fig.tight_layout(pad=0.6, h_pad=1.6, w_pad=1.2, rect=[0.0, 0.0, 1.0, 0.91])

    for out in (
        figdir / "FIG-Motivation-Recovery.pdf",
        paper / "FIG-Motivation-Recovery.pdf",
    ):
        fig.savefig(out, bbox_inches="tight", pad_inches=0.02)
        fig.savefig(out.with_suffix(".png"), dpi=300, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)

    print("wrote FIG-Motivation-Recovery (no AgentTX)")
    size64 = df[(df["sweep"] == "size")].copy()
    size64 = size64[pd.to_numeric(size64["x_value"], errors="coerce") == 64].set_index("mode")
    for mode in MODES:
        row = size64.loc[mode]
        print(
            f"{LABELS[mode]:40s} retain={row['independent_retention_mean']:.1%} "
            f"remove={row['target_removed_mean']:.1%}"
        )


if __name__ == "__main__":
    main()
