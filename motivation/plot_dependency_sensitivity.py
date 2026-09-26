#!/usr/bin/env python3
"""Plot one-factor sensitivity sweeps for the dependency microbenchmark."""

from __future__ import annotations

import csv
import json
import shutil
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "experiments" / "results" / "motivation" / "microbenchmark"
OUTPUT = ROOT / "motivation" / "FIG-Motivation-Sensitivity"
PAPER_OUTPUT = ROOT / "paper" / "img" / "FIG-Motivation-Sensitivity"
DEPENDENCIES = tuple(index / 20 for index in range(21))
STANDARD_WIDTH_CM = 17.8
CM_TO_INCH = 1 / 2.54

SWEEPS = (
    ("turn_count", "turn_count", "Turns", "(a) Task length"),
    (
        "fault_position",
        "requested_fault_position",
        "Fault position",
        "(b) Fault position",
    ),
    ("shape", "shape", "Graph shape", "(c) Graph shape"),
    (
        "interleaving",
        "interleaving",
        "Interleaving",
        "(d) Interleaving",
    ),
)


def label(value: str) -> str:
    return value.replace("independent-", "indep. ").replace("fan-out", "fan-out")


def factor_sort_key(value: str) -> tuple[int, float | str]:
    try:
        return (0, float(value))
    except ValueError:
        return (1, value)


def load_sweep(name: str) -> tuple[list[dict[str, str]], dict]:
    directory = RESULTS / "sensitivity" / name
    with (directory / "config.json").open(encoding="utf-8") as handle:
        config = json.load(handle)
    with (directory / "motivation_recovery_raw.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle))
    expected = int(config["planned_m1_traces"]) * len(config["policies"])
    if len(rows) != expected:
        raise ValueError(f"{name}: expected {expected} rows, found {len(rows)}")
    observed = {float(row["requested_dependency_value"]) for row in rows}
    if observed != set(DEPENDENCIES):
        raise ValueError(f"{name}: dependency grid mismatch: {sorted(observed)}")
    return rows, config


def temporal_retention(
    rows: list[dict[str, str]], factor: str
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    paired: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for row in rows:
        paired[row["trace_id"]][row["policy"]] = row

    grouped: dict[tuple[str, float], list[float]] = defaultdict(list)
    for trace_id, policies in paired.items():
        if "temporal_step" not in policies:
            raise ValueError(f"incomplete paired trace: {trace_id}")
        temporal = policies["temporal_step"]
        grouped[
            (temporal[factor], float(temporal["requested_dependency_value"]))
        ].append(100.0 * float(temporal["valid_retention"]))

    output = {}
    factors = sorted({key[0] for key in grouped}, key=factor_sort_key)
    for factor_value in factors:
        means = [
            float(np.mean(grouped[(factor_value, dependency)]))
            for dependency in DEPENDENCIES
        ]
        output[factor_value] = (np.array(DEPENDENCIES), np.array(means))
    return output


def configure_style() -> None:
    plt.rcParams.update(plt.rcParamsDefault)
    plt.rcParams.update(
        {
            "font.family": "Nimbus Roman",
            "font.size": 8,
            "axes.labelsize": 8,
            "axes.titlesize": 8,
            "legend.fontsize": 5.8,
            "xtick.labelsize": 7,
            "ytick.labelsize": 7,
            "axes.grid": False,
            "axes.linewidth": 0.6,
            "xtick.direction": "in",
            "ytick.direction": "in",
            "xtick.major.width": 0.6,
            "ytick.major.width": 0.6,
            "legend.frameon": False,
            "lines.linewidth": 0.9,
            "lines.markersize": 3.0,
            "savefig.dpi": 300,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )


def main() -> int:
    configure_style()
    fig, axes = plt.subplots(
        2,
        2,
        figsize=(STANDARD_WIDTH_CM * CM_TO_INCH, 10.0 * CM_TO_INCH),
        dpi=300,
        sharex=True,
    )
    palette = ("#2d3436", "#d97706", "#2f80b7", "#4c956c", "#7c5aa6")
    markers = ("o", "x", "D", "^", "v")

    for ax, (name, factor, legend_title, title) in zip(axes.flat, SWEEPS):
        rows, _ = load_sweep(name)
        curves = temporal_retention(rows, factor)
        for index, (factor_value, (x, y)) in enumerate(curves.items()):
            ax.plot(
                x,
                y,
                color=palette[index % len(palette)],
                marker=markers[index % len(markers)],
                linestyle=("-", "--", ":", "-.")[index % 4],
                markerfacecolor="white" if markers[index % len(markers)] != "x" else None,
                # Stagger markers so exactly overlapping sensitivity curves
                # remain visibly represented instead of hiding one another.
                markevery=(index, len(curves)),
                label=label(factor_value),
            )
        ax.axhline(
            100,
            color="#b42318",
            linewidth=1.0,
            label="Ideal oracle",
        )
        ax.set_xlim(-0.02, 1.02)
        ax.set_ylim(-3, 103)
        ax.set_xticks([0.0, 0.25, 0.50, 0.75, 1.0])
        ax.tick_params(axis="both", labelsize=7)
        ax.legend(
            title=legend_title,
            title_fontsize=5.8,
            ncol=3 if name in {"turn_count", "fault_position"} else 2,
            loc="lower center",
            bbox_to_anchor=(0.5, 1.01),
            borderaxespad=0,
            columnspacing=0.65,
            handlelength=1.5,
            handletextpad=0.3,
            borderpad=0.25,
        )

    for ax in axes[:, 0]:
        ax.set_ylabel("Valid token weight retained (%)")
    for ax, (_, _, _, title) in zip(axes.flat, SWEEPS):
        ax.set_xlabel(f"Dependency value\n{title}", fontsize=7)

    fig.subplots_adjust(
        left=0.08,
        right=0.995,
        bottom=0.13,
        top=0.84,
        hspace=1.02,
        wspace=0.28,
    )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    PAPER_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    source_pdf = OUTPUT.with_suffix(".pdf")
    source_png = OUTPUT.with_suffix(".png")
    fig.savefig(source_pdf, bbox_inches="tight", pad_inches=0.02)
    fig.savefig(source_png, bbox_inches="tight", pad_inches=0.02)
    shutil.copyfile(source_pdf, PAPER_OUTPUT.with_suffix(".pdf"))
    plt.close(fig)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
