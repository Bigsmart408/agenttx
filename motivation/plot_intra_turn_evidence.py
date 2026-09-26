#!/usr/bin/env python3
"""Generate the M0/M1/M2/M4 figures specified by agent.md."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

try:
    from motivation.paper_figure_style import (
        configure_paper_style,
        save_paper_figure,
    )
except ModuleNotFoundError:  # Support direct execution from motivation/.
    from paper_figure_style import (  # type: ignore[no-redef]
        configure_paper_style,
        save_paper_figure,
    )


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "experiments" / "results" / "motivation" / "intra_turn"
M3_RESULTS = ROOT / "experiments" / "results" / "motivation" / "m3"
CM = 1 / 2.54

DISPLAY_POLICIES = (
    "no_recovery",
    "root_only",
    "turn_checkpoint_proxy",
    "temporal_step",
    "whole_branch",
    "ideal_oracle",
)
POLICY_LABELS = {
    "no_recovery": "No recovery",
    "root_only": "Root only",
    "turn_checkpoint_proxy": "Turn checkpoint",
    "temporal_step": "Temporal step",
    "whole_branch": "Whole branch",
    "ideal_oracle": "Ideal oracle",
}

POLICY_STYLES = {
    "no_recovery": dict(
        color="#7a5f9a", marker="v", linestyle="-.", linewidth=0.9,
        markersize=3.2, markerfacecolor="none",
    ),
    "root_only": dict(
        color="#4c8c57", marker="^", linestyle="--", linewidth=0.9,
        markersize=3.2, markerfacecolor="none",
    ),
    "ideal_oracle": dict(
        color="#c00000", marker="s", linestyle="-", linewidth=1.0,
        markersize=3.5,
    ),
    "temporal_step": dict(
        color="#e78129", marker="x", linestyle=":", linewidth=0.9,
        markersize=3.8, markeredgewidth=0.9,
    ),
    "turn_checkpoint_proxy": dict(
        color="#111111", marker="o", linestyle="--", linewidth=0.9,
        markersize=3.2, markerfacecolor="none",
    ),
    "whole_branch": dict(
        color="#3f7eab", marker="D", linestyle="-.", linewidth=0.9,
        markersize=3.1, markerfacecolor="none",
    ),
}


def configure_style() -> None:
    configure_paper_style()


def read_csv(name: str) -> list[dict[str, str]]:
    with (RESULTS / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_csv_path(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def save_figure(fig, stem: str) -> tuple[Path, Path]:
    return save_paper_figure(fig, stem, root=ROOT)


def plot_m0_turn_audit() -> tuple[Path, Path]:
    rows = read_csv("motivation_turn_audit_raw.csv")
    cdf = read_csv("motivation_turn_audit_cdf.csv")
    if len(rows) != 638 or len(cdf) != 638:
        raise ValueError("M0 formal figure requires all 638 audited turns")
    values = [float(row["turn_checkpoint_extra_tokens"]) for row in rows]
    independent = sum(
        row["multiple_independent_stateful_components"] == "True" for row in rows
    )
    if independent != 6:
        raise ValueError(f"M0 expected 6 gated turns, found {independent}")

    fig, ax = plt.subplots(dpi=300, figsize=(17.8 * CM, 6.0 * CM))
    x = [float(row["turn_checkpoint_extra_tokens"]) for row in cdf]
    y = [100 * float(row["cdf"]) for row in cdf]
    ax.step(
        x,
        y,
        where="post",
        color="#3f7eab",
        linewidth=1.0,
    )
    quantiles = (
        (0.50, "P50", "#3f7eab", "o"),
        (0.95, "P95", "#e78129", "x"),
        (0.99, "P99", "#c00000", "s"),
    )
    handles = []
    for q, label, color, marker in quantiles:
        value = percentile(values, q)
        display_value = f"{value:,.0f}" if q == 0.50 else f"{value:,.1f}"
        ax.vlines(
            value,
            0,
            q * 100,
            color=color,
            linestyle=":",
            linewidth=0.65,
            zorder=0,
        )
        handle, = ax.plot(
            value,
            q * 100,
            color=color,
            marker=marker,
            linestyle="none",
            markersize=3.8,
            markerfacecolor="none" if marker != "s" else color,
            label=f"{label}: {display_value}",
        )
        handles.append(handle)
    ax.text(
        0.98,
        0.12,
        "Independent stateful components\n6 / 638 turns (0.94%)",
        transform=ax.transAxes,
        ha="right",
        va="bottom",
        fontsize=6.2,
        color="#b56d6d",
    )
    ax.set_xlim(700, 5_600)
    ax.set_ylim(0, 103)
    ax.set_ylabel("Cumulative turns (%)", fontsize=8)
    ax.set_xlabel(
        "Useful token weight unnecessarily discarded by turn checkpoint",
        fontsize=7,
    )
    ax.tick_params(axis="both", labelsize=7)
    fig.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.99),
        ncol=3,
        fontsize=6.2,
        columnspacing=1.2,
        handlelength=1.2,
        handletextpad=0.3,
        borderpad=0.3,
    )
    fig.subplots_adjust(left=0.08, right=0.995, top=0.82, bottom=0.27)
    return save_figure(fig, "FIG-M0-Turn-Audit-CDF")


def m1_policy_means(rows: list[dict[str, str]], metric: str) -> list[float]:
    values = []
    for policy in DISPLAY_POLICIES:
        cells = [
            float(row[metric])
            for row in rows
            if row["policy"] == policy
        ]
        if len(cells) != 3_600:
            raise ValueError(
                f"M1 {policy} has {len(cells)} cells, expected 3600"
            )
        values.append(100 * float(np.mean(cells)))
    return values


def plot_m1_strategy_outcomes() -> tuple[Path, Path]:
    rows = [
        row
        for row in read_csv("motivation_recovery_raw.csv")
        if row["phase"] == "m1"
    ]
    fig, ax = plt.subplots(dpi=300, figsize=(17.8 * CM, 6.0 * CM))
    x = np.arange(len(DISPLAY_POLICIES))
    labels = tuple(
        POLICY_LABELS[policy].replace(" ", "\n", 1)
        for policy in DISPLAY_POLICIES
    )
    retained = m1_policy_means(rows, "valid_retention")
    removed = m1_policy_means(rows, "invalid_removal")
    ax.plot(
        x,
        retained,
        color="#c00000",
        marker="s",
        linestyle="-",
        linewidth=1.0,
        markersize=3.5,
        label="Useful work retained",
    )
    ax.plot(
        x,
        removed,
        color="#3f7eab",
        marker="o",
        linestyle="--",
        linewidth=0.9,
        markersize=3.5,
        markerfacecolor="none",
        label="Invalid work removed",
    )
    for position, value in zip(x, retained):
        ax.annotate(
            f"{value:.0f}",
            (position, value),
            xytext=(0, 6),
            textcoords="offset points",
            ha="center",
            va="bottom",
            color="#9d0000",
            fontsize=5.8,
        )
    for position, value in zip(x, removed):
        vertical_offset = 7 if value <= 5 else -7
        ax.annotate(
            f"{value:.0f}",
            (position, value),
            xytext=(0, vertical_offset),
            textcoords="offset points",
            ha="center",
            va="bottom" if vertical_offset > 0 else "top",
            color="#2f668c",
            fontsize=5.8,
        )
    ax.axhline(100, color="0.72", linestyle=":", linewidth=0.5, zorder=0)
    ax.set_xlim(-0.25, len(DISPLAY_POLICIES) - 0.75)
    ax.set_ylim(-6, 118)
    ax.set_xticks(x, labels, fontsize=6.5)
    ax.set_yticks((0, 25, 50, 75, 100))
    ax.set_ylabel("Recovery outcome (%)", fontsize=8)
    ax.set_xlabel(
        "Recovery policy\nMean across loop widths and controlled workloads",
        fontsize=7,
    )
    ax.tick_params(axis="y", labelsize=7)
    fig.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, 0.99),
        ncol=2,
        fontsize=6.2,
        columnspacing=1.2,
        handlelength=1.7,
        handletextpad=0.3,
        borderpad=0.3,
    )
    fig.subplots_adjust(left=0.08, right=0.995, top=0.82, bottom=0.28)
    return save_figure(fig, "FIG-M1-Strategy-Outcomes")


M2_ORDER = (
    "turn_checkpoint_proxy",
    "temporal_step",
    "whole_branch",
    "ideal_oracle",
)
M2_LABELS = ("Turn\ncheckpoint", "Temporal\nstep", "Whole\nbranch", "Ideal\noracle")


def plot_m2_extreme() -> tuple[Path, Path]:
    rows = [
        row
        for row in read_csv("motivation_recovery_raw.csv")
        if row["phase"] == "m2"
    ]
    by_policy = {row["policy"]: row for row in rows}
    if len(by_policy) != 7 or not set(M2_ORDER).issubset(by_policy):
        raise ValueError("M2 figure requires the complete seven-policy result")
    fig, ax = plt.subplots(dpi=300, figsize=(17.8 * CM, 6.0 * CM))
    x = np.arange(len(M2_ORDER))
    wasted = [int(by_policy[policy]["useful_steps_wasted"]) for policy in M2_ORDER]
    ax.plot(
        x,
        wasted,
        color="#3f7eab",
        marker="o",
        markerfacecolor="none",
        linestyle="--",
        linewidth=1.0,
        markersize=3.8,
    )
    ax.plot(
        x[-1],
        wasted[-1],
        color="#c00000",
        marker="s",
        linestyle="none",
        markersize=3.8,
    )
    for position, value in zip(x, wasted):
        ax.annotate(
            str(value),
            (position, value),
            xytext=(0, 6),
            textcoords="offset points",
            ha="center",
            va="bottom",
            color="#9d0000" if value == 0 else "#2f668c",
            fontsize=6,
        )
    ax.axhline(124, color="0.72", linestyle=":", linewidth=0.55, zorder=0)
    ax.set_xlim(-0.25, len(M2_ORDER) - 0.75)
    ax.set_ylim(-5, 138)
    ax.set_xticks(x, M2_LABELS, fontsize=6.5)
    ax.set_yticks((0, 25, 50, 75, 100, 124))
    ax.set_ylabel("Useful steps unnecessarily discarded", fontsize=8)
    ax.set_xlabel(
        "Recovery policy\nM2 extreme case: 4 invalid and 124 valid steps",
        fontsize=7,
    )
    ax.tick_params(axis="y", labelsize=7)
    fig.tight_layout(pad=0.45)
    return save_figure(fig, "FIG-M2-Extreme-Tradeoff")


M3_ORDER = (
    "clean_recovery",
    "crash_no_rollback",
    "turn_checkpoint_proxy",
    "temporal_step",
    "whole_branch",
    "ideal_oracle",
)
M3_LABELS = (
    "Clean",
    "No\nrollback",
    "Turn\ncheckpoint",
    "Temporal\nstep",
    "Whole\nbranch",
    "Ideal\noracle",
)


def m3_backend_summary(backend: str) -> dict[str, dict[str, float]]:
    legacy_dir = M3_RESULTS / "legacy_v7" / backend
    current_dir = M3_RESULTS / backend
    legacy_config = json.loads(
        (legacy_dir / "motivation_replay_config.json").read_text(encoding="utf-8")
    )
    current_config = json.loads(
        (current_dir / "motivation_replay_config.json").read_text(encoding="utf-8")
    )
    for config, schema in (
        (legacy_config, "agenttx.motivation.replay_config/v7"),
        (current_config, "agenttx.motivation.replay_config/v8"),
    ):
        profile = config.get("codex_gpt_profile", {})
        reasoning = config.get("reasoning_effort", profile.get("reasoning_effort"))
        if (
            config.get("schema") != schema
            or config.get("model") != "gpt-5.6-luna"
            or reasoning != "max"
            or config.get("replay_protocol")
            != "one_artifact_per_completed_write_event_strict_audit"
        ):
            raise ValueError(f"M3 {backend} source configuration is incompatible")
    if current_config.get("policies") != ["crash_no_rollback"]:
        raise ValueError(f"M3 {backend} v8 source must contain only no-rollback")

    rows = []
    for row in read_csv_path(legacy_dir / "motivation_replay_raw.csv"):
        if row["policy"] in M3_ORDER and row["policy"] != "crash_no_rollback":
            rows.append(row)
    for row in read_csv_path(current_dir / "motivation_replay_raw.csv"):
        if row["policy"] == "crash_no_rollback":
            rows.append(row)

    latest: dict[tuple[str, str, int], dict[str, str]] = {}
    attempts: dict[tuple[str, str, int], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        key = (row["trace_id"], row["policy"], int(row["repeat"]))
        latest[key] = row
        attempts[key].append(row)
    policy_cells = {
        policy: {
            (trace_id, repeat)
            for trace_id, candidate, repeat in latest
            if candidate == policy
        }
        for policy in M3_ORDER
    }
    common_cells = set.intersection(*(policy_cells[policy] for policy in M3_ORDER))
    expected_cells = 30 if backend == "codex" else 25
    if len(common_cells) != expected_cells:
        raise ValueError(
            f"M3 {backend} has {len(common_cells)} matched cells, "
            f"expected {expected_cells}"
        )

    summary = {}
    for policy in M3_ORDER:
        cells = [latest[(trace, policy, repeat)] for trace, repeat in common_cells]
        completed = sum(row["completed"] == "True" for row in cells)
        attempt_rows = [
            row
            for trace, repeat in common_cells
            for row in attempts[(trace, policy, repeat)]
        ]
        observed_tokens = sum(int(row["replay_total_tokens"]) for row in attempt_rows)
        summary[policy] = {
            "cells": float(len(cells)),
            "attempts": float(len(attempt_rows)),
            "completed": float(completed),
            "success_rate": completed / len(cells),
            "tokens_per_success": observed_tokens / completed,
        }
    return summary


def token_label(value: float) -> str:
    return f"{value / 1000:.1f}K" if value < 10_000 else f"{value / 1000:.0f}K"


def plot_m3_strategy_comparison() -> tuple[Path, Path]:
    backends = (
        ("codex", "Codex", "#c00000", "s", "-", (30_000, 700_000)),
        ("sweagent", "SWEAgent", "#3f7eab", "o", "--", (3_000, 180_000)),
    )
    fig, axes = plt.subplots(1, 2, dpi=300, figsize=(17.8 * CM, 6.2 * CM))
    x = np.arange(len(M3_ORDER))
    combined_rows = []
    for index, (backend, label, color, marker, linestyle, ylim) in enumerate(backends):
        ax = axes[index]
        summary = m3_backend_summary(backend)
        for policy in M3_ORDER:
            row = summary[policy]
            combined_rows.append(
                {
                    "backend": backend,
                    "policy": policy,
                    "source_protocol": (
                        "v8" if policy == "crash_no_rollback" else "v7"
                    ),
                    "matched_cells": int(row["cells"]),
                    "attempts": int(row["attempts"]),
                    "completed": int(row["completed"]),
                    "success_rate": row["success_rate"],
                    "tokens_per_success": row["tokens_per_success"],
                }
            )
        values = [summary[policy]["tokens_per_success"] for policy in M3_ORDER]
        ax.plot(
            x,
            values,
            color=color,
            marker=marker,
            markerfacecolor="none" if marker == "o" else color,
            linestyle=linestyle,
            linewidth=1.0,
            markersize=3.5,
        )
        for position, value in zip(x, values):
            ax.annotate(
                token_label(value),
                (position, value),
                xytext=(0, 5),
                textcoords="offset points",
                ha="center",
                va="bottom",
                color=color,
                fontsize=5.7,
            )
        ax.set_yscale("log")
        ax.set_ylim(*ylim)
        ax.set_xlim(-0.25, len(M3_ORDER) - 0.75)
        ax.set_xticks(x, M3_LABELS, fontsize=5.8)
        ax.set_ylabel("Tokens per successful cell (log scale)", fontsize=7.5)
        ax.set_xlabel(
            f"Recovery policy\n({chr(97 + index)}) {label}, "
            f"{int(summary[M3_ORDER[0]]['cells'])} matched cells",
            fontsize=7,
        )
        ax.tick_params(axis="y", labelsize=6.5)
    summary_path = M3_RESULTS / "motivation_replay_combined_summary.csv"
    with summary_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(combined_rows[0]))
        writer.writeheader()
        writer.writerows(combined_rows)
    fig.subplots_adjust(left=0.08, right=0.995, top=0.94, bottom=0.27, wspace=0.30)
    return save_figure(fig, "FIG-M3-Strategy-Comparison")


def plot_m4_real_turns() -> tuple[Path, Path]:
    rows = read_csv("motivation_real_counterfactual_turns.csv")
    keys = sorted({(row["trace_id"], int(row["turn_id"])) for row in rows})
    if len(keys) != 6 or len(rows) != 30:
        raise ValueError("M4 figure requires 6 turns and 30 turn-policy rows")
    short = {
        "django__django-12915": "D",
        "sphinx-doc__sphinx-7975": "S",
        "sympy__sympy-16792": "Y",
    }
    labels = [f"{short[trace]}{turn}" for trace, turn in keys]
    lookup = {
        (row["trace_id"], int(row["turn_id"]), row["policy"]): float(
            row["useful_tokens_wasted_mean"]
        )
        for row in rows
    }
    plot_policies = (
        "ideal_oracle",
        "whole_branch",
        "turn_checkpoint_proxy",
        "temporal_step",
    )
    policy_labels = {
        "ideal_oracle": "ideal oracle",
        "turn_checkpoint_proxy": "turn checkpoint",
        "temporal_step": "temporal step",
        "whole_branch": "whole branch",
    }

    fig, axes = plt.subplots(1, 2, dpi=300, figsize=(17.8 * CM, 6.0 * CM))
    ax = axes[0]
    handles = []
    per_policy: dict[str, list[float]] = {}
    for policy in plot_policies:
        values = [lookup[(trace, turn, policy)] for trace, turn in keys]
        per_policy[policy] = values
        style = dict(POLICY_STYLES[policy])
        if policy == "whole_branch":
            style["markevery"] = (1, 2)
        elif policy == "turn_checkpoint_proxy":
            style["markevery"] = (0, 2)
        handle, = ax.plot(
            range(len(keys)),
            values,
            label=policy_labels[policy],
            **style,
        )
        handles.append(handle)
    ax.set_xticks(range(len(keys)), labels)
    ax.set_ylim(-80, 2_450)
    ax.set_ylabel("Useful token weight discarded", fontsize=8)
    ax.set_xlabel(
        "Eligible native turn (D=Django, S=Sphinx, Y=SymPy)\n"
        "(a) Paired turn-level counterfactual",
        fontsize=7,
    )
    ax.tick_params(axis="both", labelsize=7)
    ax.legend(
        handles=handles,
        loc="lower center",
        bbox_to_anchor=(0.5, 1.01),
        ncol=2,
        fontsize=5.4,
        handlelength=1.7,
        handletextpad=0.3,
        borderpad=0.25,
    )

    ax = axes[1]
    summary_policies = (
        "turn_checkpoint_proxy",
        "temporal_step",
        "whole_branch",
        "ideal_oracle",
    )
    summary_labels = ("Turn", "Temporal", "Whole", "Ideal")
    summary_x = np.arange(len(summary_policies))
    quantiles = (
        (0.50, "P50", "#3f7eab", "o"),
        (0.95, "P95", "#e78129", "x"),
        (0.99, "P99", "#c00000", "s"),
    )
    for q, label, color, marker in quantiles:
        values = [percentile(per_policy[policy], q) for policy in summary_policies]
        ax.plot(
            summary_x,
            values,
            label=label,
            color=color,
            marker=marker,
            linewidth=0.9,
            markersize=3.5,
            markerfacecolor="none" if marker != "s" else color,
        )
    ax.set_xticks(summary_x, summary_labels, rotation=20, ha="right", fontsize=6.2)
    ax.set_ylim(-80, 2_450)
    ax.set_ylabel("Useful token weight discarded", fontsize=8)
    ax.set_xlabel("Recovery policy\n(b) Across-turn distribution", fontsize=7)
    ax.tick_params(axis="y", labelsize=7)
    ax.legend(
        loc="lower center",
        bbox_to_anchor=(0.5, 1.01),
        ncol=3,
        fontsize=5.7,
        columnspacing=0.7,
        handlelength=1.5,
        handletextpad=0.3,
        borderpad=0.25,
    )
    ax.text(
        0.48,
        0.12,
        "Ideal oracle: 0 at P50 / P95 / P99",
        transform=ax.transAxes,
        color="#b56d6d",
        ha="left",
        fontsize=6,
    )
    fig.subplots_adjust(left=0.08, right=0.995, top=0.82, bottom=0.27, wspace=0.30)
    return save_figure(fig, "FIG-M4-Real-Turn-Distribution")


def render_all() -> list[tuple[Path, Path]]:
    configure_style()
    return [
        plot_m0_turn_audit(),
        plot_m1_strategy_outcomes(),
        plot_m2_extreme(),
        plot_m3_strategy_comparison(),
        plot_m4_real_turns(),
    ]


def main() -> None:
    for png, pdf in render_all():
        print(f"wrote {png}")
        print(f"wrote {pdf}")


if __name__ == "__main__":
    main()
