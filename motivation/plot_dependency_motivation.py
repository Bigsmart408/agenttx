#!/usr/bin/env python3
"""Plot the headline motivation story from the completed microbenchmark."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "experiments" / "results" / "motivation" / "microbenchmark"
PRIMARY = RESULTS / "motivation_recovery_raw.csv"
WIDTH = RESULTS / "sensitivity" / "width" / "motivation_recovery_raw.csv"
OUTPUT = ROOT / "motivation" / "FIG-Motivation-Dependency"
PAPER_OUTPUT = ROOT / "paper" / "img" / "FIG-Motivation-Dependency"
STANDARD_WIDTH_CM = 17.8
CM_TO_INCH = 1 / 2.54
WIDTH_DEPENDENCY = 0.25

POLICIES = ("turn_checkpoint_proxy", "temporal_step", "ideal_oracle")
LABELS = {
    "turn_checkpoint_proxy": "Checkpoint",
    "temporal_step": "Temporal suffix",
    "ideal_oracle": "Ideal oracle",
}
STYLES = {
    "turn_checkpoint_proxy": dict(
        color="#111111",
        marker="o",
        linestyle="--",
        markerfacecolor="none",
    ),
    "temporal_step": dict(
        color="#e78129",
        marker="x",
        linestyle=":",
        markeredgewidth=0.9,
    ),
    "ideal_oracle": dict(
        color="#c00000",
        marker="s",
        linestyle="-",
    ),
}


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def assert_paired_baselines(rows: list[dict[str, str]]) -> None:
    paired: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for row in rows:
        paired[row["trace_id"]][row["policy"]] = row
    for trace_id, policies in paired.items():
        turn = policies.get("turn_checkpoint_proxy")
        whole = policies.get("whole_branch")
        if turn is None or whole is None:
            raise ValueError(f"{trace_id}: missing turn or whole-branch baseline")
        for metric in ("useful_tokens_wasted", "valid_retention"):
            if float(turn[metric]) != float(whole[metric]):
                raise ValueError(
                    f"{trace_id}: turn and whole branch differ for {metric}"
                )


def dependency_series(
    rows: list[dict[str, str]], policy: str, metric: str
) -> tuple[np.ndarray, np.ndarray]:
    grouped: dict[float, list[float]] = defaultdict(list)
    for row in rows:
        if row["policy"] == policy:
            grouped[float(row["requested_dependency_value"])].append(
                float(row[metric])
            )
    x = np.array(sorted(grouped))
    return x, np.array([float(np.mean(grouped[value])) for value in x])


def width_series(
    rows: list[dict[str, str]], policy: str
) -> tuple[np.ndarray, np.ndarray]:
    grouped: dict[int, list[float]] = defaultdict(list)
    for row in rows:
        if (
            row["policy"] == policy
            and float(row["requested_dependency_value"]) == WIDTH_DEPENDENCY
        ):
            grouped[int(row["loop_width"])].append(
                float(row["useful_tokens_wasted"])
            )
    x = np.array(sorted(grouped))
    return x, np.array([float(np.mean(grouped[value])) for value in x])


def configure_style() -> None:
    plt.rcParams.update(plt.rcParamsDefault)
    plt.rcParams.update(
        {
            "font.family": "Nimbus Roman",
            "font.size": 8,
            "axes.labelsize": 8,
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
    primary = read_rows(PRIMARY)
    width = read_rows(WIDTH)
    assert_paired_baselines(primary)
    assert_paired_baselines(width)
    configure_style()

    fig, axes = plt.subplots(
        1,
        3,
        figsize=(STANDARD_WIDTH_CM * CM_TO_INCH, 5.7 * CM_TO_INCH),
        dpi=300,
    )
    handles = []
    for policy in POLICIES:
        x, waste = dependency_series(
            primary, policy, "useful_tokens_wasted"
        )
        handle, = axes[0].plot(x, waste, label=LABELS[policy], **STYLES[policy])
        handles.append(handle)

        x, retention = dependency_series(primary, policy, "valid_retention")
        axes[1].plot(x, 100 * retention, **STYLES[policy])

        x, width_waste = width_series(width, policy)
        axes[2].plot(x, width_waste, **STYLES[policy])

    for ax in axes[:2]:
        ax.set_xlim(-0.02, 1.02)
        ax.set_xticks((0.0, 0.25, 0.5, 0.75, 1.0))
    axes[0].set_ylim(bottom=-45)
    axes[0].set_ylabel("Useful token weight discarded")
    axes[0].set_xlabel("Dependency value\n(a) Coarse rollback waste", fontsize=7)

    axes[1].set_ylim(-3, 103)
    axes[1].set_yticks((0, 25, 50, 75, 100))
    axes[1].set_ylabel("Valid work retained (%)")
    axes[1].set_xlabel("Dependency value\n(b) Selective recovery opportunity", fontsize=7)

    axes[2].set_xlim(4, 132)
    axes[2].set_ylim(bottom=-90)
    axes[2].set_xticks((8, 32, 64, 128))
    axes[2].set_ylabel("Useful token weight discarded")
    axes[2].set_xlabel(
        "Turn width (# operations)\n(c) Waste grows with turn size",
        fontsize=7,
    )
    for ax in axes:
        ax.tick_params(axis="both", labelsize=7)

    fig.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.995),
        ncol=3,
        fontsize=6.5,
        columnspacing=0.9,
        handlelength=1.8,
        handletextpad=0.35,
        borderpad=0.3,
    )
    fig.subplots_adjust(
        left=0.065,
        right=0.995,
        bottom=0.27,
        top=0.76,
        wspace=0.42,
    )
    for stem in (OUTPUT, PAPER_OUTPUT):
        stem.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(stem.with_suffix(".pdf"), bbox_inches="tight", pad_inches=0.02)
        fig.savefig(stem.with_suffix(".png"), bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
