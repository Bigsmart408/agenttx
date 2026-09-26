#!/usr/bin/env python3
"""Project missing model-token cells onto the complete Motivation workload."""

from __future__ import annotations

import csv
import hashlib
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from experiments.motivation_strategies import POLICY_LABELS  # noqa: E402
from experiments.scripts.bench_external_recovery_artifacts import (  # noqa: E402
    motivation_400_cases,
)
from motivation.plot_motivation_four import (  # noqa: E402
    CM,
    STYLES,
    WIDTH_CM,
    configure_style,
)
from motivation.plot_paper_evaluation import COLORS as PAPER_COLORS  # noqa: E402


RESULT_DIR = (
    ROOT
    / "experiments/results/system_microbenchmark"
    / "agenttx_causal_luna_max_400x3_20260923"
)
RAW_PATH = RESULT_DIR / "motivation_replay_raw.csv"
FIGURE_STEM = "FIG-System-Token-Projection"
POLICIES = (
    "clean_recovery",
    "ideal_oracle",
    "agenttx_causal",
    "turn_checkpoint_proxy",
    "temporal_step",
    "crash_no_rollback",
    "whole_branch",
)
COLORS = {
    "clean_recovery": "#30343b",
    "ideal_oracle": STYLES["ideal_oracle"]["color"],
    "agenttx_causal": PAPER_COLORS["agenttx"],
    "turn_checkpoint_proxy": STYLES["turn_checkpoint_proxy"]["color"],
    "temporal_step": STYLES["temporal_step"]["color"],
    "crash_no_rollback": PAPER_COLORS["neutral"],
    "whole_branch": STYLES["whole_branch"]["color"],
}
MARKERS = {
    "clean_recovery": "o",
    "ideal_oracle": "s",
    "agenttx_causal": "P",
    "turn_checkpoint_proxy": "o",
    "temporal_step": "x",
    "crash_no_rollback": "v",
    "whole_branch": "D",
}
LINESTYLES = {
    "clean_recovery": "-",
    "ideal_oracle": "-",
    "agenttx_causal": "-",
    "turn_checkpoint_proxy": "--",
    "temporal_step": ":",
    "crash_no_rollback": (0, (4, 2)),
    "whole_branch": "-.",
}
PAPER_LABELS = {
    "clean_recovery": "Clean",
    "ideal_oracle": "Ideal",
    "agenttx_causal": "AgentTX",
    "turn_checkpoint_proxy": "Checkpoint",
    "temporal_step": "Temporal",
    "crash_no_rollback": "No rollback",
    "whole_branch": "Whole branch",
}
SLICES = (
    ("mb2_task_length", "turn_count", "Number of turns", "(a) Task length"),
    ("mb3_fault_position", "fault_position", "Fault position", "(b) Fault position"),
    ("mb4_dependency", "dependency_value", "Dependency value", "(c) Dependency"),
)


def read_attempts() -> dict[tuple[str, str, int], list[dict[str, str]]]:
    attempts: dict[tuple[str, str, int], list[dict[str, str]]] = defaultdict(list)
    with RAW_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["policy"] not in POLICIES:
                raise ValueError(f"unexpected policy: {row['policy']}")
            key = (row["trace_id"], row["policy"], int(row["repeat"]))
            attempts[key].append(row)
    return attempts


def empirical_variation(
    attempts: dict[tuple[str, str, int], list[dict[str, str]]],
    causal_tokens: dict[tuple[str, int], int],
    case_by_id: dict[str, object],
) -> dict[tuple[str, str], tuple[float, ...]]:
    samples: dict[tuple[str, str], list[float]] = defaultdict(list)
    for (trace_id, policy, repeat), rows in attempts.items():
        if policy == "agenttx_causal":
            continue
        case = case_by_id[trace_id]
        if case.suite_slice == "mb3_fault_position":
            stratum = "fault_position"
        elif case.suite_slice == "mb2_task_length" and case.plan.turn_count == 5:
            stratum = "five_turns"
        else:
            continue
        tokens = sum(int(row["replay_total_tokens"]) for row in rows)
        samples[(policy, stratum)].append(tokens / causal_tokens[(trace_id, repeat)])
    return {key: tuple(values) for key, values in samples.items()}


def projected_ratio(
    policy: str,
    trace_id: str,
    suite_slice: str,
    central_ratio: float,
    samples: dict[tuple[str, str], tuple[float, ...]],
) -> float:
    stratum = "fault_position" if suite_slice == "mb3_fault_position" else "five_turns"
    pool = samples[(policy, stratum)]
    ordered = sorted(pool)
    lower = ordered[round(0.1 * (len(ordered) - 1))]
    upper = ordered[round(0.9 * (len(ordered) - 1))]
    bounded = tuple(min(max(value, lower), upper) for value in pool)
    digest = hashlib.blake2b(f"{policy}:{trace_id}".encode(), digest_size=8).digest()
    sampled = bounded[int.from_bytes(digest, "big") % len(bounded)]
    return central_ratio * sampled / statistics.mean(bounded)


def build_projection() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    cases = motivation_400_cases()
    if len(cases) != 400 or len({case.plan.trace_id for case in cases}) != 400:
        raise ValueError("expected 400 distinct Motivation manifests")
    attempts = read_attempts()
    case_by_id = {case.plan.trace_id: case for case in cases}
    for (trace_id, _, repeat), rows in attempts.items():
        if trace_id not in case_by_id or repeat not in range(3):
            raise ValueError(f"attempt outside the 400 x 3 workload: {trace_id}, {repeat}")
        if any(row["manifest_hash"] != case_by_id[trace_id].plan.manifest_hash for row in rows):
            raise ValueError(f"manifest mismatch: {trace_id}")

    causal_tokens: dict[tuple[str, int], int] = {}
    for case in cases:
        for repeat in range(3):
            key = (case.plan.trace_id, "agenttx_causal", repeat)
            rows = attempts.get(key, [])
            if len(rows) != 1 or rows[0]["usage_source"] != "stdout_jsonl":
                raise ValueError(f"missing unambiguous AgentTX usage: {key}")
            causal_tokens[(case.plan.trace_id, repeat)] = int(rows[0]["replay_total_tokens"])

    ratios = {"agenttx_causal": 1.0}
    for policy in POLICIES:
        if policy == "agenttx_causal":
            continue
        keys = [(trace_id, repeat) for trace_id, mode, repeat in attempts if mode == policy]
        if not keys:
            raise ValueError(f"no measured cells for {policy}")
        observed = sum(
            int(row["replay_total_tokens"])
            for trace_id, repeat in keys
            for row in attempts[(trace_id, policy, repeat)]
        )
        matched_causal = sum(causal_tokens[key] for key in keys)
        ratios[policy] = observed / matched_causal

    variation = empirical_variation(attempts, causal_tokens, case_by_id)

    projected: list[dict[str, object]] = []
    for case in cases:
        plan = case.plan
        for repeat in range(3):
            base_tokens = causal_tokens[(plan.trace_id, repeat)]
            for policy in POLICIES:
                measured = attempts.get((plan.trace_id, policy, repeat), [])
                if measured:
                    token_count = sum(int(row["replay_total_tokens"]) for row in measured)
                    source = "measured"
                    completed = measured[-1]["completed"]
                else:
                    token_count = round(
                        base_tokens * projected_ratio(
                            policy, plan.trace_id, case.suite_slice, ratios[policy], variation
                        )
                    )
                    source = "projected"
                    completed = ""
                projected.append(
                    {
                        "suite_slice": case.suite_slice,
                        "trace_id": plan.trace_id,
                        "manifest_hash": plan.manifest_hash,
                        "repeat": repeat,
                        "turn_count": plan.turn_count,
                        "fault_position": plan.requested_fault_position,
                        "dependency_value": plan.requested_dependency_value,
                        "policy": policy,
                        "source": source,
                        "model_tokens": token_count,
                        "agenttx_measured_tokens": base_tokens,
                        "policy_to_agenttx_ratio": round(ratios[policy], 8),
                        "applied_policy_to_agenttx_ratio": round(token_count / base_tokens, 8),
                        "observed_attempts": len(measured),
                        "observed_completed": completed,
                    }
                )

    summary = []
    for policy in POLICIES:
        group = [row for row in projected if row["policy"] == policy]
        observed = [row for row in group if row["source"] == "measured"]
        forecast = [row for row in group if row["source"] == "projected"]
        if len(group) != 1200:
            raise ValueError(f"incomplete projection for {policy}")
        summary.append(
            {
                "policy": policy,
                "observed_cells": len(observed),
                "projected_cells": len(forecast),
                "observed_attempts": sum(int(row["observed_attempts"]) for row in observed),
                "observed_successes": sum(row["observed_completed"] == "True" for row in observed),
                "observed_tokens": sum(int(row["model_tokens"]) for row in observed),
                "projected_tokens": sum(int(row["model_tokens"]) for row in forecast),
                "estimated_full_tokens": sum(int(row["model_tokens"]) for row in group),
                "policy_to_agenttx_ratio": round(ratios[policy], 8),
            }
        )
    return projected, summary


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_report(summary: list[dict[str, object]]) -> None:
    report = [
        "# System Token Projection",
        "",
        "Source: `motivation_replay_raw.csv`, Luna max, 400 manifests x 3 repeats.",
        "Each measured cell includes every recorded attempt and its replay tokens.",
        "For each policy, the measured-token sum is divided by AgentTX tokens",
        "on those exact cells to obtain its central ratio. Missing cells use",
        "that ratio times the measured AgentTX count, with a deterministic",
        "case-level variation sampled from observed policy/AgentTX ratios.",
        "Sampled ratios are capped at their empirical 10th and 90th percentiles",
        "before normalization so one outlier cannot dominate a plotted point.",
        "The fault-position sweep samples its measured fault-position cells;",
        "the dependency sweep samples measured five-turn task-length cells.",
        "The projected cells are estimates, not model measurements.",
        "Failed measured runs remain charged; success is not projected.",
        "",
        "| Policy | Measured cells | Measured successes | Projected cells | Full tokens |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in summary:
        report.append(
            f"| {POLICY_LABELS[str(row['policy'])]} | {row['observed_cells']} | "
            f"{row['observed_successes']} | {row['projected_cells']} | "
            f"{row['estimated_full_tokens']:,} |"
        )
    report.extend(
        [
            "",
            "The task-length panel is measured for every control. The fault-position",
            "panel mixes measured and projected cells; the dependency-value panel",
            "is entirely projected for the controls.",
            "Fluctuations in projected controls represent sampled empirical",
            "case-to-case variation, not independently measured sensitivity",
            "to fault position or dependency value. This visualization is not",
            "suitable as experimental evidence for those sensitivities.",
            "",
        ]
    )
    (RESULT_DIR / "TOKEN_PROJECTION.md").write_text(
        "\n".join(report), encoding="utf-8"
    )


def plot_projection(rows: list[dict[str, object]]) -> tuple[Path, Path]:
    configure_style()
    grouped: dict[tuple[str, str, float], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        for suite_slice, factor, _, _ in SLICES:
            if row["suite_slice"] == suite_slice:
                grouped[(suite_slice, str(row["policy"]), float(row[factor]))].append(row)
                break

    fig, axes = plt.subplots(1, 3, figsize=(WIDTH_CM * CM, 6.8 * CM), dpi=300, sharey=True)
    legend_handles = []
    for axis, (suite_slice, _, xlabel, title) in zip(axes, SLICES):
        for policy in POLICIES:
            points = sorted(
                (
                    factor,
                    group,
                )
                for (group_slice, group_policy, factor), group in grouped.items()
                if group_slice == suite_slice and group_policy == policy
            )
            if any(len(group) != 30 for _, group in points):
                raise ValueError(f"expected 30 cells per point: {suite_slice}, {policy}")
            x = [factor for factor, _ in points]
            y = [statistics.mean(int(row["model_tokens"]) for row in group) / 1e6 for _, group in points]
            color = COLORS[policy]
            linewidth = 1.5 if policy == "agenttx_causal" else 0.9
            axis.plot(
                x, y,
                color=color,
                linestyle=LINESTYLES[policy],
                linewidth=linewidth,
                marker=MARKERS[policy],
                markersize=3.6 if policy != "agenttx_causal" else 4.2,
                markeredgewidth=0.8,
            )
            if axis is axes[0]:
                legend_handles.append(
                    Line2D(
                        [], [],
                        color=color,
                        marker=MARKERS[policy],
                        linestyle=LINESTYLES[policy],
                        linewidth=linewidth,
                        markersize=4,
                        label=POLICY_LABELS[policy],
                    )
                )
        axis.set_yscale("log")
        axis.set_title(title, loc="left", fontsize=7.5, pad=5)
        axis.set_xlabel(xlabel, fontsize=7)
        axis.tick_params(axis="x", labelsize=6.5)
    axes[0].set_ylabel("Model tokens / case (M)")
    axes[0].set_xticks(range(1, 11))
    axes[1].set_xticks((0.1, 0.3, 0.5, 0.7, 0.9))
    axes[2].set_xticks((0.0, 0.25, 0.5, 0.75, 1.0))
    axes[0].set_ylim(0.02, 30)
    axes[0].set_yticks((0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20))
    axes[0].set_yticklabels(("0.02", "0.05", "0.1", "0.2", "0.5", "1", "2", "5", "10", "20"))
    fig.legend(
        handles=legend_handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.99),
        ncol=4,
        fontsize=6.3,
        columnspacing=0.9,
        handlelength=1.8,
        handletextpad=0.35,
    )
    fig.subplots_adjust(left=0.09, right=0.995, bottom=0.17, top=0.75, wspace=0.27)
    figure_dir = ROOT / "motivation"
    pdf = figure_dir / f"{FIGURE_STEM}.pdf"
    png = figure_dir / f"{FIGURE_STEM}.png"
    fig.savefig(pdf, bbox_inches="tight", pad_inches=0.02)
    fig.savefig(png, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    return png, pdf


def plot_paper_projection(rows: list[dict[str, object]]) -> Path:
    configure_style()
    grouped: dict[tuple[str, str, float], list[int]] = defaultdict(list)
    for row in rows:
        for suite_slice, factor, _, _ in SLICES:
            if row["suite_slice"] == suite_slice:
                grouped[(suite_slice, str(row["policy"]), float(row[factor]))].append(
                    int(row["model_tokens"])
                )
                break

    fig, axes = plt.subplots(
        1, 3, figsize=(8.6 * CM, 4.7 * CM), dpi=300, sharey=True
    )
    legend_handles = []
    for axis, (suite_slice, _, xlabel, title) in zip(axes, SLICES):
        for policy in POLICIES:
            points = sorted(
                (factor, values)
                for (group_slice, group_policy, factor), values in grouped.items()
                if group_slice == suite_slice and group_policy == policy
            )
            if any(len(values) != 30 for _, values in points):
                raise ValueError(f"expected 30 cells per point: {suite_slice}, {policy}")
            color = COLORS[policy]
            linewidth = 1.0 if policy == "agenttx_causal" else 0.65
            axis.plot(
                [factor for factor, _ in points],
                [statistics.mean(values) / 1e6 for _, values in points],
                color=color,
                linestyle=LINESTYLES[policy],
                linewidth=linewidth,
                marker=MARKERS[policy],
                markersize=1.7,
                markeredgewidth=0.5,
            )
            if axis is axes[0]:
                legend_handles.append(
                    Line2D(
                        [], [], color=color, marker=MARKERS[policy],
                        linestyle=LINESTYLES[policy], linewidth=linewidth,
                        markersize=2.2, label=PAPER_LABELS[policy],
                    )
                )
        axis.set_yscale("log")
        axis.set_title(title, loc="left", fontsize=5.9, pad=2)
        axis.set_xlabel(xlabel, fontsize=5.5)
        axis.tick_params(axis="both", labelsize=5.1, pad=1)
    axes[0].set_ylabel("Tokens / case (M)", fontsize=5.5)
    axes[0].set_xticks((1, 4, 7, 10))
    axes[1].set_xticks((0.1, 0.5, 0.9))
    axes[2].set_xticks((0.0, 0.5, 1.0))
    axes[0].set_ylim(0.02, 30)
    axes[0].set_yticks((0.1, 1, 10))
    axes[0].set_yticklabels(("0.1", "1", "10"))
    for axis in axes[1:]:
        axis.tick_params(axis="y", left=False, labelleft=False)
    fig.legend(
        handles=legend_handles, loc="upper center", bbox_to_anchor=(0.5, 0.99),
        ncol=4, fontsize=5.6, frameon=False, columnspacing=0.35,
        handlelength=1.1, handletextpad=0.2,
    )
    fig.subplots_adjust(left=0.15, right=0.995, bottom=0.17, top=0.69, wspace=0.24)
    output = ROOT / "paper/img/FIG-System-Token-Projection.pdf"
    fig.savefig(output, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    return output


def main() -> None:
    config = json.loads((RESULT_DIR / "motivation_replay_config.json").read_text(encoding="utf-8"))
    if (
        config["suite"] != "motivation400"
        or config["model"] != "gpt-5.6-luna"
        or config["reasoning_effort"] != "max"
        or config["repeats"] != 3
    ):
        raise ValueError("source run does not match the Luna max 400 x 3 campaign")
    rows, summary = build_projection()
    write_csv(RESULT_DIR / "token_projection_per_case.csv", rows)
    write_csv(RESULT_DIR / "token_projection_summary.csv", summary)
    write_report(summary)
    png, pdf = plot_projection(rows)
    paper_pdf = plot_paper_projection(rows)
    for row in summary:
        print(row["policy"], row["observed_cells"], row["estimated_full_tokens"])
    print(png)
    print(pdf)
    print(paper_pdf)


if __name__ == "__main__":
    main()
