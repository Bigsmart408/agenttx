#!/usr/bin/env python3
"""FAST/USENIX-style figure for SWE-Bench + Terminal-Bench recovery."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "experiments" / "results" / "official" / "official_tasks_raw.csv"
OUT = ROOT / "paper" / "img" / "FIG-Official-Tasks.pdf"

STANDARD_WIDTH = 17.8  # cm
RED = "#c00000"
ORANGE = "#e78129"
BLACK = "#000000"
BLUE = "#2f5496"
GRAY = "#7f7f7f"
MODES = [
    ("causal", "Causal", RED, "s"),
    ("temporal_checkpoint", "Temporal", ORANGE, "x"),
    ("whole_branch_abort", "Whole abort", BLACK, "o"),
    ("chat_only", "Chat-only", GRAY, "^"),
    ("chat_fs", "Chat+FS", BLUE, "D"),
]


def cm_to_in(cm: float) -> float:
    return cm / 2.54


def load_rows(path: Path):
    if not path.exists():
        raise SystemExit(f"missing {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def rate(rows, field) -> float:
    if not rows:
        return 0.0
    return sum(str(r.get(field, "")).lower() in {"true", "1", "yes"} for r in rows) / len(rows)


def main() -> int:
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.serif": ["Nimbus Roman", "Times New Roman", "Times"],
            "font.size": 9,
            "axes.labelsize": 9,
            "axes.titlesize": 9,
            "legend.fontsize": 8,
            "pdf.fonttype": 42,
        }
    )
    rows = load_rows(CSV)
    keep_tasks = {
        "pytest-dev__pytest-8906",
        "pallets__flask-4992",
        "pylint-dev__pylint-5859",
        "hello-world",
        "csv-to-parquet",
        "log-summary",
    }
    labels_map = {
        "swe:pytest-dev__pytest-8906": "SWE pytest",
        "swe:pallets__flask-4992": "SWE flask",
        "swe:pylint-dev__pylint-5859": "SWE pylint",
        "tb:hello-world": "TB hello",
        "tb:csv-to-parquet": "TB csv",
        "tb:log-summary": "TB logs",
    }
    grouped = defaultdict(lambda: defaultdict(list))
    labels = []
    seen = set()
    for row in rows:
        if row.get("task") not in keep_tasks:
            continue
        label = labels_map.get(f"{row.get('suite')}:{row.get('task')}", f"{row.get('suite')}:{row.get('task')}")
        grouped[label][row["mode"]].append(row)
        if label not in seen:
            labels.append(label)
            seen.add(label)
    if not labels:
        raise SystemExit("no official-task rows")

    fig, axes = plt.subplots(
        1,
        2,
        figsize=(cm_to_in(STANDARD_WIDTH), cm_to_in(7.2)),
        constrained_layout=True,
    )
    x = list(range(len(labels)))
    for ax, field, title, ylabel in (
        (axes[0], "success", "(a) Official verifier + retention", "Success rate"),
        (axes[1], "independent_retained", "(b) Independent work retained", "Retention rate"),
    ):
        for idx, (mode, name, color, marker) in enumerate(MODES):
            ys = [rate(grouped[label].get(mode, []), field) for label in labels]
            xs = [i + (idx - 1) * 0.18 for i in x]
            ax.plot(
                xs,
                ys,
                marker=marker,
                color=color,
                linestyle="none",
                markersize=7,
                markerfacecolor="none" if marker == "o" else color,
                markeredgecolor=color,
                markeredgewidth=1.2,
                label=name,
            )
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        ax.set_ylim(-0.05, 1.05)
        ax.yaxis.set_major_locator(MultipleLocator(0.25))
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=20, ha="right")
        ax.grid(True, axis="y", linestyle=":", linewidth=0.4)
        ax.set_axisbelow(True)
    axes[0].legend(frameon=False, loc="lower left")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, bbox_inches="tight")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
