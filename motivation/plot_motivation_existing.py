#!/usr/bin/env python3
"""Paper motivation figures over existing systems (Background Table).

FAST/USENIX line-plot conventions match motivation/plot_scaling.ipynb:
white panels, boxed top legend, shared BASE/REF palette.
"""
from __future__ import annotations

from pathlib import Path
import shutil

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

STANDARD_WIDTH = 17.8


def cm_to_inch(v: float) -> float:
    return v / 2.54


def style() -> None:
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


# Same family as plot_scaling.ipynb / plot.ipynb (OURS/BASE1/BASE2/REF).
# No AgentTX series here; map existing systems onto that palette.
STYLES = {
    "bare": dict(
        color="black", marker="x", linestyle="--", linewidth=0.9,
        markersize=3.6, markeredgewidth=0.9,
    ),
    "try": dict(
        color="#e78129", marker="x", linestyle=":", linewidth=0.9,
        markersize=3.6, markeredgewidth=0.9,
    ),
    "YoloFS": dict(
        color="#4c8c57", marker="o", linestyle="-.", linewidth=0.9,
        markersize=3.2, markerfacecolor="none",
    ),
    "BranchFS": dict(
        color="#3f7eab", marker="^", linestyle="-.", linewidth=0.9,
        markersize=3.2, markerfacecolor="none",
    ),
    "Crab": dict(
        color="#7a5f9a", marker="s", linestyle="-", linewidth=1.0,
        markersize=3.0,
    ),
    "DeltaBox": dict(
        color="#a35d5d", marker="v", linestyle="-", linewidth=0.9,
        markersize=3.2, markerfacecolor="none",
    ),
}


ORDER = ["bare", "try", "YoloFS", "BranchFS", "Crab", "DeltaBox"]
LABELS = {
    "bare": "bare",
    "try": "try",
    "YoloFS": "YoloFS",
    "BranchFS": "BranchFS",
    "Crab": "Crab",
    "DeltaBox": "DeltaBox",
}


def main() -> None:
    style()
    root = Path(__file__).resolve().parents[1]
    results = root / "experiments" / "results"
    figdir = root / "motivation"
    paper = root / "paper" / "img"
    figdir.mkdir(exist_ok=True)
    paper.mkdir(parents=True, exist_ok=True)

    scale = pd.read_csv(results / "motivation_existing_scaling.csv")
    tail = pd.read_csv(results / "motivation_existing_tail.csv")
    lengths = sorted(int(x) for x in scale["length"].unique())
    L64 = 64 if 64 in lengths else lengths[len(lengths) // 2]

    def series(df, metric, mode):
        return np.asarray(
            [
                float(df[(df["length"] == l) & (df["mode"] == mode)][metric].iloc[0])
                for l in lengths
            ]
        )

    # FIG-Motivation-Optimization omitted: fixed-length bars duplicate
    # the multi-length scaling figure without adding length trends.

    # ---- FIG-Motivation-Scaling ----
    fig = plt.figure(dpi=300, figsize=(cm_to_inch(STANDARD_WIDTH), cm_to_inch(7.0)))
    panels = [
        ("per_step_mean_ms", "Per-call latency (ms)", "(a) Per-call overhead"),
        ("wall_mean_s", "End-to-end wall time (s)", "(b) Trajectory wall time"),
    ]
    handles, labels = [], []
    for i, (metric, ylabel, sub) in enumerate(panels):
        ax = plt.subplot(2, 2, i + 1)
        for m in ORDER:
            if m not in set(scale["mode"]):
                continue
            h, = ax.plot(lengths, series(scale, metric, m), **STYLES[m], label=LABELS[m])
            if i == 0:
                handles.append(h)
                labels.append(LABELS[m])
        ax.set_ylabel(ylabel, fontsize=8)
        ax.set_xlabel(f"Trajectory length (# calls)\n{sub}", fontsize=7)
        ax.set_xticks(lengths)
        ax.tick_params(axis="both", labelsize=7)

    ax = plt.subplot(2, 2, 3)
    bare_s = series(scale, "per_step_mean_ms", "bare")
    ax.axhline(1.0, color="black", linestyle="--", linewidth=0.8)
    for m in ORDER:
        if m == "bare" or m not in set(scale["mode"]):
            continue
        ax.plot(lengths, series(scale, "per_step_mean_ms", m) / bare_s, **STYLES[m], label=LABELS[m])
    ax.set_ylabel("Overhead vs bare (×)", fontsize=8)
    ax.set_xlabel("Trajectory length (# calls)\n(c) Isolation tax", fontsize=7)
    ax.set_xticks(lengths)
    ax.tick_params(axis="both", labelsize=7)

    ax = plt.subplot(2, 2, 4)
    try_s = series(scale, "per_step_mean_ms", "try")
    for m in ("YoloFS", "BranchFS", "Crab", "DeltaBox"):
        if m not in set(scale["mode"]):
            continue
        ax.plot(lengths, try_s / series(scale, "per_step_mean_ms", m), **STYLES[m], label=LABELS[m])
    ax.set_ylabel("Speedup vs try (×)", fontsize=8)
    ax.set_xlabel("Trajectory length (# calls)\n(d) Amortization gain", fontsize=7)
    ax.set_xticks(lengths)
    ax.tick_params(axis="both", labelsize=7)

    fig.legend(handles, labels, loc="upper center", ncol=6, fontsize=6.5, bbox_to_anchor=(0.5, 1.02))
    fig.tight_layout(rect=[0, 0, 1, 0.93], w_pad=1.2, h_pad=1.4)
    for out in (figdir / "FIG-Motivation-Scaling.pdf", paper / "FIG-Motivation-Scaling.pdf"):
        fig.savefig(out, bbox_inches="tight")
        fig.savefig(out.with_suffix(".png"), bbox_inches="tight", dpi=300)
    plt.close(fig)

    # ---- FIG-Motivation-Tail-Scaling ----
    fig = plt.figure(dpi=300, figsize=(cm_to_inch(STANDARD_WIDTH), cm_to_inch(7.0)))
    panels = [
        ("step_p50_ms", "Per-call p50 (ms)", "(a) Median call latency"),
        ("step_p95_ms", "Per-call p95 (ms)", "(b) Tail call latency"),
        ("run_p50_ms", "Trajectory p50 (ms)", "(c) Median trajectory latency"),
        ("run_p95_ms", "Trajectory p95 (ms)", "(d) Tail trajectory latency"),
    ]
    handles, labels = [], []
    for i, (metric, ylabel, sub) in enumerate(panels):
        ax = plt.subplot(2, 2, i + 1)
        for m in ORDER:
            if m not in set(tail["mode"]):
                continue
            h, = ax.plot(lengths, series(tail, metric, m), **STYLES[m], label=LABELS[m])
            if i == 0:
                handles.append(h)
                labels.append(LABELS[m])
        ax.set_ylabel(ylabel, fontsize=8)
        ax.set_xlabel(f"Trajectory length (# calls)\n{sub}", fontsize=7)
        ax.set_xticks(lengths)
        ax.tick_params(axis="both", labelsize=7)
    fig.legend(handles, labels, loc="upper center", ncol=6, fontsize=6.5, bbox_to_anchor=(0.5, 1.02))
    fig.tight_layout(rect=[0, 0, 1, 0.93], w_pad=1.2, h_pad=1.4)
    for out in (
        figdir / "FIG-Motivation-Tail-Scaling.pdf",
        paper / "FIG-Motivation-Tail-Scaling.pdf",
    ):
        fig.savefig(out, bbox_inches="tight")
        fig.savefig(out.with_suffix(".png"), bbox_inches="tight", dpi=300)
    plt.close(fig)

    src = figdir / "FIG-Motivation-Tail-Scaling.pdf"
    if src.exists():
        shutil.copy2(src, figdir / "FIG-Motivation-Tail.pdf")

    print("wrote motivation existing-system figures (classic palette)")
    print("=== L64 ms/step ===")
    snap = scale[scale["length"] == L64].set_index("mode")
    for m in ORDER:
        if m in snap.index:
            print(m, float(snap.loc[m, "per_step_mean_ms"]))


if __name__ == "__main__":
    main()
