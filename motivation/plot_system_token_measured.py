#!/usr/bin/env python3
"""Plot fully measured model-token cost on the 400-manifest replay suite."""

from __future__ import annotations

import csv
import shutil
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from motivation.plot_motivation_four import CM, configure_style
from experiments.scripts.bench_external_recovery_artifacts import motivation_400_cases
from motivation.plot_system_token_projection import (
    COLORS,
    LINESTYLES,
    MARKERS,
    PAPER_LABELS,
    POLICIES,
    RAW_PATH,
    SLICES,
)


ROOT = Path(__file__).resolve().parents[1]


def measured_cells() -> list[dict[str, str]]:
    attempts: dict[tuple[str, str, int], list[dict[str, str]]] = defaultdict(list)
    with RAW_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            attempts[(row["trace_id"], row["policy"], int(row["repeat"]))].append(row)
    if len(attempts) != 400 * len(POLICIES) * 3:
        raise ValueError(f"expected 8400 measured cells, found {len(attempts)}")
    for policy in POLICIES:
        if sum(key[1] == policy for key in attempts) != 1200:
            raise ValueError(f"incomplete policy: {policy}")
    cells = []
    for rows in attempts.values():
        if len({row["agent_prompt_hash"] for row in rows}) != 1:
            raise ValueError("mixed prompt versions in measured cell")
        cell = dict(rows[-1])
        cell["replay_total_tokens"] = str(sum(int(row["replay_total_tokens"]) for row in rows))
        cells.append(cell)
    return cells


def main() -> None:
    rows = measured_cells()
    cases = {case.plan.trace_id: case for case in motivation_400_cases()}
    if len(cases) != 400:
        raise ValueError("expected 400 distinct manifests")
    grouped: dict[tuple[str, str, float], list[int]] = defaultdict(list)
    for row in rows:
        case = cases[row["trace_id"]]
        if row["manifest_hash"] != case.plan.manifest_hash:
            raise ValueError(f"manifest mismatch: {row['trace_id']}")
        for suite_slice, factor, _, _ in SLICES:
            if row["suite_slice"] == suite_slice:
                value = (
                    case.plan.turn_count if factor == "turn_count" else
                    case.plan.requested_fault_position if factor == "fault_position" else
                    case.plan.requested_dependency_value
                )
                grouped[(suite_slice, row["policy"], float(value))].append(
                    int(row["replay_total_tokens"])
                )
                break

    configure_style()
    fig, axes = plt.subplots(1, 3, figsize=(8.6 * CM, 4.7 * CM), dpi=300, sharey=True)
    for axis, (suite_slice, _, xlabel, title) in zip(axes, SLICES):
        for policy in POLICIES:
            points = sorted(
                (factor, values)
                for (group_slice, group_policy, factor), values in grouped.items()
                if group_slice == suite_slice and group_policy == policy
            )
            if not points or any(len(values) != 30 for _, values in points):
                raise ValueError(f"expected 30 measured cells per point: {suite_slice}, {policy}")
            axis.plot(
                [factor for factor, _ in points],
                [sum(values) / len(values) / 1e6 for _, values in points],
                color=COLORS[policy],
                linestyle=LINESTYLES[policy],
                linewidth=1.0 if policy == "agenttx_causal" else 0.65,
                marker=MARKERS[policy],
                markersize=1.7,
                markeredgewidth=0.5,
                label=PAPER_LABELS[policy],
            )
        axis.set_yscale("log")
        axis.set_title(title, loc="left", fontsize=5.9, pad=2)
        axis.set_xlabel(xlabel, fontsize=5.5)
        axis.tick_params(axis="both", labelsize=5.1, pad=1)
    axes[0].set_ylabel("Tokens / cell (M)", fontsize=5.5)
    axes[0].set_xticks((1, 4, 7, 10))
    axes[1].set_xticks((0.1, 0.5, 0.9))
    axes[2].set_xticks((0.0, 0.5, 1.0))
    axes[0].set_ylim(0.02, 30)
    axes[0].set_yticks((0.1, 1, 10))
    axes[0].set_yticklabels(("0.1", "1", "10"))
    for axis in axes[1:]:
        axis.tick_params(axis="y", left=False, labelleft=False)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles, labels, loc="upper center", bbox_to_anchor=(0.5, 0.99),
        ncol=4, fontsize=5.6, frameon=False, columnspacing=0.35,
        handlelength=1.1, handletextpad=0.2,
    )
    fig.subplots_adjust(left=0.15, right=0.995, bottom=0.17, top=0.69, wspace=0.24)
    output = ROOT / "motivation/FIG-System-Token-Measured.pdf"
    png = ROOT / "motivation/FIG-System-Token-Measured.png"
    fig.savefig(output, bbox_inches="tight", pad_inches=0.02)
    fig.savefig(png, bbox_inches="tight", pad_inches=0.02)
    shutil.copyfile(output, ROOT / "paper/img/FIG-System-Token-Measured.pdf")
    plt.close(fig)
    print(output)


if __name__ == "__main__":
    main()
