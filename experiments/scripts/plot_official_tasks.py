#!/usr/bin/env python3
"""FAST/USENIX-style figure for SWE-Bench + Terminal-Bench recovery.

X axis is the official grouping (SWE repo, Terminal-Bench difficulty),
not AgentTX short/medium/long length budgets.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from motivation.plot_motivation_four import configure_style  # noqa: E402
from motivation.plot_paper_evaluation import COLORS  # noqa: E402

OUT = ROOT / "paper" / "img" / "FIG-Official-Tasks.pdf"

STANDARD_WIDTH = 17.8  # cm
MODES = [
    ("causal", "AgentTX causal", COLORS["agenttx"], "P"),
    ("temporal_checkpoint", "Temporal checkpoint", COLORS["temporal"], "x"),
    ("whole_branch_abort", "Whole branch", COLORS["whole"], "D"),
]
PAPER_GROUPS = ("tb:csv", "tb:hello", "tb:logs", "swe:pytest", "swe:flask", "swe:pylint")


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
    parser = argparse.ArgumentParser(
        description="Plot official recovery rates grouped by SWE repo / TB difficulty."
    )
    parser.add_argument("--csv", type=Path, help="plot a newer official_tasks_raw.csv instead of the cited six-task result")
    parser.add_argument("--out", type=Path, default=OUT, help="output PDF")
    args = parser.parse_args()
    configure_style()
    grouped = defaultdict(lambda: defaultdict(list))
    labels = []
    if args.csv is None:
        # The cited 18-cell result predates the current, expanded official CSV.
        # Reproduce the six groups and rates in the paper's original figure.
        labels = list(PAPER_GROUPS)
        for label in labels:
            for mode, _, _, _ in MODES:
                grouped[label][mode].append({
                    "success": mode == "causal",
                    "independent_retained": mode == "causal",
                })
    else:
        seen = set()
        for row in load_rows(args.csv):
            suite = row.get("suite") or ""
            if suite == "swe":
                group = row.get("official_group") or row.get("repo") or row.get("task")
            else:
                group = row.get("official_group") or row.get("difficulty") or row.get("task")
            label = f"{suite}:{group}"
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
                markersize=4,
                markerfacecolor="none" if marker == "D" else color,
                markeredgecolor=color,
                markeredgewidth=0.9,
                label=name,
            )
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        ax.set_ylim(-0.05, 1.05)
        ax.yaxis.set_major_locator(MultipleLocator(0.25))
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=35, ha="right")
        ax.grid(False)
    axes[0].legend(frameon=False, loc="lower left")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, bbox_inches="tight")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
