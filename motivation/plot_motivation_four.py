#!/usr/bin/env python3
"""Generate the four canonical coarse-recovery motivation figures."""

from __future__ import annotations

import csv
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from experiments.motivation_strategies import (
    CORRECT_RECOVERY_POLICIES,
    MICROBENCHMARK_POLICIES,
    MOTIVATION_CORRECT_RECOVERY_POLICIES,
    MOTIVATION_POLICIES,
    POLICY_LABELS,
)


ROOT = Path(__file__).resolve().parents[1]
M0_DIR = ROOT / "experiments" / "results" / "motivation" / "intra_turn"
MICRO_DIR = ROOT / "experiments" / "results" / "motivation" / "microbenchmark"
CONTROLLED_DIR = ROOT / "experiments" / "results" / "motivation" / "controlled_case"
EXTERNAL_DIR = (
    ROOT
    / "experiments"
    / "results"
    / "external_recovery_motivation400_20260922"
)
M4_DIR = ROOT / "experiments" / "results" / "motivation" / "real_trace"
FIGURE_DIR = ROOT / "motivation"
PAPER_DIR = ROOT / "paper" / "img"
CM = 1 / 2.54
WIDTH_CM = 17.8

RAW_CORRECT_POLICIES = CORRECT_RECOVERY_POLICIES
COARSE_POLICIES = MOTIVATION_CORRECT_RECOVERY_POLICIES
MICRO_POLICIES = MICROBENCHMARK_POLICIES
DISPLAY_POLICIES = MOTIVATION_POLICIES
LABELS = POLICY_LABELS
ARTIFACT_LABELS = {
    "session_try": "try",
    "yolofs": "YoloFS",
    "branchfs": "BranchFS",
    "waypoint": "Waypoint",
}
EXTERNAL_SLICE_TO_PHASE = {
    "mb2_task_length": "controlled",
    "mb3_fault_position": "controlled-position",
    "mb4_dependency": "m1",
}
STYLES = {
    "no_recovery": dict(
        color="#6b7280", marker="v", linestyle="none", markerfacecolor="none"
    ),
    "root_only": dict(
        color="#7c5aa6", marker="x", linestyle="none", markeredgewidth=0.9
    ),
    "turn_checkpoint_proxy": dict(
        color="#1b4f72", marker="o", linestyle="--", markerfacecolor="none"
    ),
    "temporal_step": dict(
        color="#d97706", marker="x", linestyle=":", markeredgewidth=0.9
    ),
    "whole_branch": dict(
        color="#16837a", marker="D", linestyle="-.", markerfacecolor="none"
    ),
    "yolofs": dict(
        color="#7c5aa6", marker="^", linestyle=(0, (4, 1, 1, 1)),
        markerfacecolor="none"
    ),
    "waypoint": dict(
        color="#c45a87", marker="P", linestyle=(0, (1, 1)),
        markerfacecolor="none"
    ),
    "branchfs": dict(
        color="#16837a", marker="D", linestyle="-.", markerfacecolor="none"
    ),
    "session_try": dict(
        color="#6b7280", marker="v", linestyle=(0, (2, 1)),
        markerfacecolor="none"
    ),
    "ideal_oracle": dict(color="#b42318", marker="s", linestyle="-"),
}


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
            "legend.frameon": False,
            "lines.linewidth": 0.9,
            "lines.markersize": 3.2,
            "savefig.dpi": 300,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def percentile(values: Sequence[float], q: float) -> float:
    return float(np.quantile(np.asarray(values, dtype=float), q))


def ecdf(values: Iterable[float]) -> tuple[np.ndarray, np.ndarray]:
    x = np.sort(np.asarray(list(values), dtype=float))
    return x, np.arange(1, len(x) + 1, dtype=float) / len(x) * 100


def save_figure(fig, stem: str) -> tuple[Path, Path]:
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    PAPER_DIR.mkdir(parents=True, exist_ok=True)
    png = FIGURE_DIR / f"{stem}.png"
    pdf = FIGURE_DIR / f"{stem}.pdf"
    fig.savefig(pdf, bbox_inches="tight", pad_inches=0.02)
    fig.savefig(png, bbox_inches="tight", pad_inches=0.02)
    shutil.copyfile(pdf, PAPER_DIR / pdf.name)
    plt.close(fig)
    return png, pdf


def plot_m0() -> tuple[Path, Path]:
    config = json.loads((CONTROLLED_DIR / "config.json").read_text(encoding="utf-8"))
    rows = read_csv(CONTROLLED_DIR / "controlled_recovery_raw.csv")
    position_rows = read_csv(CONTROLLED_DIR / "controlled_position_raw.csv")
    turn_counts = tuple(config.get("turn_counts") or ())
    if (
        config.get("schema") != "agenttx.motivation.controlled-waste/v4"
        or turn_counts != tuple(range(1, 11))
        or config.get("turn_width") != 16
        or config.get("repeats") != 10
        or config.get("fixed_closure_size") != 4
        or tuple(config.get("policies") or ()) != MICRO_POLICIES
        or len(rows) != 600
        or config.get("fault_positions") != [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
        or config.get("position_sweep_turn_count") != 10
        or len(position_rows) != 540
    ):
        raise ValueError("M0 controlled-case configuration is incomplete")
    grouped: dict[tuple[str, int], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row["policy"], int(row["turn_count"]))].append(row)
    if any(
        len(grouped[(policy, turn_count)]) != 10
        for policy in MICRO_POLICIES
        for turn_count in turn_counts
    ):
        raise ValueError("M0 requires 10 paired cases per policy and turn count")
    grouped_positions: dict[tuple[str, float], list[dict[str, str]]] = defaultdict(list)
    for row in position_rows:
        grouped_positions[(row["policy"], float(row["requested_fault_position"]))].append(row)
    positions = tuple(config["fault_positions"])
    if any(
        len(grouped_positions[(policy, position)]) != 10
        for policy in MICRO_POLICIES
        for position in positions
    ):
        raise ValueError("M0 requires 10 paired cases per policy and fault position")
    if any(
        (row["rollback_safe"] == "True")
        != (row["policy"] in RAW_CORRECT_POLICIES)
        for row in rows
    ):
        raise ValueError("M0 correctness gate does not match policy semantics")

    turn = grouped_factor_metric(
        grouped, turn_counts, "turn_checkpoint_proxy", "useful_steps_wasted"
    )
    whole = grouped_factor_metric(
        grouped, turn_counts, "whole_branch", "useful_steps_wasted"
    )
    if not np.isclose(turn[0], whole[0]) or not np.any(turn[1:] < whole[1:]):
        raise ValueError("M0 does not separate turn and whole-branch rollback")
    correct_removal = [
        grouped_factor_metric(grouped, turn_counts, policy, "invalid_removal")
        for policy in COARSE_POLICIES
    ]
    if any(not np.array_equal(series, correct_removal[0]) for series in correct_removal[1:]):
        raise ValueError("M0 correct policies cannot share one correctness line")

    fig, axes = plt.subplots(1, 3, figsize=(8.5 * CM, 4.1 * CM), dpi=300)
    legend_handles = {}
    for index, policy in enumerate(COARSE_POLICIES):
        style = dict(STYLES[policy])
        style["markersize"] = 2.1
        if policy == "turn_checkpoint_proxy":
            style["markevery"] = (0, 2)
        elif policy == "whole_branch":
            style["markevery"] = (1, 2)
        elif policy == "ideal_oracle":
            style["markevery"] = (0, 2)
            style["markersize"] = 1.7
        wasted = grouped_factor_metric(
            grouped, turn_counts, policy, "useful_steps_wasted"
        )
        handle, = axes[0].plot(turn_counts, wasted, label=LABELS[policy], **style)
        legend_handles[policy] = handle
        position_waste = np.array(
            [
                np.mean(
                    [
                        float(row["useful_steps_wasted"])
                        for row in grouped_positions[(policy, position)]
                    ]
                )
                for position in positions
            ]
        )
        axes[1].plot(positions, position_waste, **style)
        rollback_sizes = np.array(
            [
                np.mean(
                    [
                        len(json.loads(row["rollback_set"]))
                        for row in grouped[(policy, turn_count)]
                    ]
                )
                for turn_count in turn_counts
            ]
        )
        axes[2].plot(turn_counts, rollback_sizes / 4.0, **style)

    for ax in (axes[0], axes[2]):
        ax.set_xlim(0.7, 10.3)
        ax.set_xticks((1, 4, 7, 10))
        ax.tick_params(axis="x", labelsize=6)
    axes[0].set_ylim(-10, 270)
    axes[0].set_ylabel("Valid operations discarded", fontsize=6)
    axes[0].set_xlabel("Turns\n(a) Task length", fontsize=6)
    axes[1].set_xlim(0.08, 0.92)
    axes[1].set_xticks((0.1, 0.5, 0.9))
    axes[1].set_ylim(-10, 270)
    axes[1].set_xlabel("Fault position\n(b) Chronology", fontsize=6)
    axes[2].set_yscale("log", base=2)
    axes[2].set_ylim(0.8, 80)
    axes[2].set_yticks(
        (1, 4, 16, 64),
        ("1", "4", "16", "64"),
    )
    axes[2].set_ylabel("Amplification (x)", fontsize=6)
    axes[2].set_xlabel("Turns\n(c) Rollback size", fontsize=6)
    for ax in axes:
        ax.tick_params(axis="both", labelsize=5.5, pad=1)
    fig.legend(
        handles=[legend_handles[policy] for policy in COARSE_POLICIES],
        labels=[LABELS[policy] for policy in COARSE_POLICIES],
        loc="upper center",
        bbox_to_anchor=(0.5, 0.995),
        ncol=4,
        fontsize=5.5,
        columnspacing=0.5,
        handlelength=1.0,
        handletextpad=0.2,
        borderpad=0.1,
    )
    fig.subplots_adjust(
        left=0.10,
        right=0.99,
        bottom=0.30,
        top=0.78,
        wspace=0.60,
    )
    return save_figure(fig, "FIG-Motivation-Coarse-Waste")


def grouped_factor_metric(
    grouped: dict[tuple[str, int], list[dict[str, str]]],
    factor_values: Sequence[int],
    policy: str,
    metric: str,
) -> np.ndarray:
    return np.array(
        [
            np.mean([float(row[metric]) for row in grouped[(policy, value)]])
            for value in factor_values
        ]
    )


def load_primary() -> tuple[list[dict[str, str]], dict]:
    config = json.loads((MICRO_DIR / "config.json").read_text(encoding="utf-8"))
    rows = read_csv(MICRO_DIR / "motivation_recovery_raw.csv")
    if config.get("schema") != "agenttx.motivation.microbenchmark/multiturn-v3":
        raise ValueError("M1 requires the multi-turn microbenchmark schema")
    if tuple(config.get("policies") or ()) != MICRO_POLICIES:
        raise ValueError("M1 policy manifest is not the six-policy non-causal set")
    if len(rows) != 1260 or any(row["policy"] == "agenttx_causal" for row in rows):
        raise ValueError("M1 requires 1260 non-causal policy rows")
    if config.get("turn_counts") != [5] or config.get("turn_widths") != [16]:
        raise ValueError("M1 requires five 16-operation turns")
    dependencies = sorted({float(row["requested_dependency_value"]) for row in rows})
    if len(dependencies) != 21:
        raise ValueError("M1 requires all 21 dependency values")
    return rows, config


def grouped_metric(
    rows: list[dict[str, str]], policy: str, metric: str
) -> tuple[np.ndarray, np.ndarray]:
    grouped: dict[float, list[float]] = defaultdict(list)
    for row in rows:
        if row["policy"] == policy:
            grouped[float(row["requested_dependency_value"])].append(
                float(row[metric])
            )
    x = np.array(sorted(grouped))
    if any(len(grouped[value]) != 10 for value in x):
        raise ValueError(f"{policy}/{metric} does not have 10 paired cases")
    return x, np.array([float(np.mean(grouped[value])) for value in x])


def grouped_metric_samples(
    rows: list[dict[str, str]], policy: str, metric: str
) -> tuple[np.ndarray, list[np.ndarray]]:
    grouped: dict[float, list[float]] = defaultdict(list)
    for row in rows:
        if row["policy"] == policy:
            grouped[float(row["requested_dependency_value"])].append(
                float(row[metric])
            )
    x = np.array(sorted(grouped))
    samples = [np.asarray(grouped[value], dtype=float) for value in x]
    if any(len(values) != 10 for values in samples):
        raise ValueError(f"{policy}/{metric} does not have 10 paired cases")
    return x, samples


def paired_bootstrap_summary(
    samples: Sequence[np.ndarray], scale: float = 1.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    means = []
    lows = []
    highs = []
    for index, values in enumerate(samples):
        # Reusing the seed for a dependency point gives every policy the same
        # resampled case indices, preserving the paired-manifest design.
        rng = np.random.default_rng(20260921 + index)
        indices = rng.integers(0, len(values), size=(2000, len(values)))
        boot = values[indices].mean(axis=1) * scale
        means.append(float(values.mean() * scale))
        lows.append(float(np.quantile(boot, 0.025)))
        highs.append(float(np.quantile(boot, 0.975)))
    return np.asarray(means), np.asarray(lows), np.asarray(highs)


def amplification_series(
    rows: list[dict[str, str]], policy: str
) -> tuple[np.ndarray, np.ndarray]:
    grouped: dict[float, list[float]] = defaultdict(list)
    for row in rows:
        if row["policy"] == policy:
            rollback_size = len(json.loads(row["rollback_set"]))
            closure_size = int(row["closure_size"])
            grouped[float(row["requested_dependency_value"])].append(
                rollback_size / closure_size
            )
    x = np.array(sorted(grouped))
    return x, np.array([float(np.mean(grouped[value])) for value in x])


def amplification_samples(
    rows: list[dict[str, str]], policy: str
) -> tuple[np.ndarray, list[np.ndarray]]:
    grouped: dict[float, list[float]] = defaultdict(list)
    for row in rows:
        if row["policy"] == policy:
            rollback_size = len(json.loads(row["rollback_set"]))
            closure_size = int(row["closure_size"])
            grouped[float(row["requested_dependency_value"])].append(
                rollback_size / closure_size
            )
    x = np.array(sorted(grouped))
    samples = [np.asarray(grouped[value], dtype=float) for value in x]
    if any(len(values) != 10 for values in samples):
        raise ValueError(f"{policy}/amplification does not have 10 paired cases")
    return x, samples


def exact_target_is_temporal_suffix(row: dict[str, str]) -> bool:
    targets = set(json.loads(row["rollback_set"]))
    if not targets:
        return True
    return targets == set(range(min(targets), int(row["loop_width"])))


def plot_m1() -> tuple[Path, Path]:
    rows, _ = load_primary()
    artifacts = load_artifact_recovery_suite(rows, suite_slice="mb4_dependency")
    coincident_pairs = (
        ("yolofs", "waypoint"),
        ("branchfs", "session_try"),
    )
    for left_backend, right_backend in coincident_pairs:
        if any(
            left["present_ids"] != right["present_ids"]
            for left, right in zip(
                artifacts[left_backend], artifacts[right_backend]
            )
        ):
            raise ValueError(
                f"{left_backend} and {right_backend} dependency states diverged"
            )
    comparison_rows = rows + [
        row for backend_rows in artifacts.values() for row in backend_rows
    ]
    ideal_rows = [row for row in rows if row["policy"] == "ideal_oracle"]
    nonprefix_cases = sum(
        not exact_target_is_temporal_suffix(row) for row in ideal_rows
    )
    if len(ideal_rows) != 210 or nonprefix_cases != 200:
        raise ValueError("M1 non-prefix target count changed")
    turn = [row for row in rows if row["policy"] == "turn_checkpoint_proxy"]
    whole = [row for row in rows if row["policy"] == "whole_branch"]
    paired_turn = {row["trace_id"]: row for row in turn}
    paired_whole = {row["trace_id"]: row for row in whole}
    if paired_turn.keys() != paired_whole.keys() or not any(
        paired_turn[key]["rollback_set"] != paired_whole[key]["rollback_set"]
        for key in paired_turn
    ):
        raise ValueError("turn and whole-branch rollback must differ")

    policies = (
        "turn_checkpoint_proxy",
        "temporal_step",
        "yolofs",
        "waypoint",
        "branchfs",
        "session_try",
        "ideal_oracle",
    )
    fig, axes = plt.subplots(
        1, 3, figsize=(WIDTH_CM * CM, 6.4 * CM), dpi=300
    )
    handles = []
    policy_offsets = np.linspace(-0.006, 0.006, len(policies))
    for policy_index, policy in enumerate(policies):
        style = dict(STYLES[policy])
        if policy == "turn_checkpoint_proxy":
            style["markevery"] = (0, 2)
        elif policy in {"waypoint", "session_try"}:
            style["markevery"] = (1, 2)
        elif policy in {"yolofs", "branchfs"}:
            style["markevery"] = (0, 2)
        color = style["color"]
        x, samples = grouped_metric_samples(
            comparison_rows, policy, "useful_tokens_wasted"
        )
        y, low, high = paired_bootstrap_summary(samples)
        axes[0].fill_between(x, low, high, color=color, alpha=0.10, linewidth=0)
        for dependency, values in zip(x, samples):
            jitter = np.linspace(-0.004, 0.004, len(values))
            axes[0].scatter(
                dependency + policy_offsets[policy_index] + jitter,
                values,
                s=3.0,
                color=color,
                alpha=0.12,
                linewidths=0,
                zorder=1,
            )
        label = LABELS.get(policy) or ARTIFACT_LABELS[policy]
        handle, = axes[0].plot(x, y, label=label, zorder=3, **style)
        handles.append(handle)
        x, samples = grouped_metric_samples(
            comparison_rows, policy, "valid_retention"
        )
        y, low, high = paired_bootstrap_summary(samples, scale=100.0)
        axes[1].fill_between(x, low, high, color=color, alpha=0.10, linewidth=0)
        for dependency, values in zip(x, samples):
            jitter = np.linspace(-0.004, 0.004, len(values))
            axes[1].scatter(
                dependency + policy_offsets[policy_index] + jitter,
                100.0 * values,
                s=3.0,
                color=color,
                alpha=0.12,
                linewidths=0,
                zorder=1,
            )
        axes[1].plot(x, y, zorder=3, **style)
        x, samples = amplification_samples(comparison_rows, policy)
        y, low, high = paired_bootstrap_summary(samples)
        axes[2].fill_between(x, low, high, color=color, alpha=0.10, linewidth=0)
        for dependency, values in zip(x, samples):
            jitter = np.linspace(-0.004, 0.004, len(values))
            axes[2].scatter(
                dependency + policy_offsets[policy_index] + jitter,
                values,
                s=3.0,
                color=color,
                alpha=0.12,
                linewidths=0,
                zorder=1,
            )
        axes[2].plot(x, y, zorder=3, **style)

    for ax in axes:
        ax.set_xlim(-0.02, 1.02)
        ax.set_xticks((0.0, 0.25, 0.5, 0.75, 1.0))
    axes[0].set_ylim(bottom=-45)
    axes[0].set_ylabel("Useful token weight discarded")
    axes[0].set_xlabel("Dependency value\n(a) Avoidable recovery waste", fontsize=7)
    axes[1].set_ylim(-3, 103)
    axes[1].set_yticks((0, 25, 50, 75, 100))
    axes[1].set_ylabel("Valid work retained (%)")
    axes[1].set_xlabel("Dependency value\n(b) Independent work preserved", fontsize=7)
    axes[2].set_yscale("log")
    axes[2].set_ylim(0.8, 80)
    axes[2].set_ylabel("Rollback amplification (x)")
    axes[2].set_xlabel("Dependency value\n(c) Work removed / invalid work", fontsize=7)
    fig.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.995),
        ncol=4,
        fontsize=5.2,
        columnspacing=0.8,
        handlelength=1.8,
        handletextpad=0.35,
        borderpad=0.3,
    )
    fig.subplots_adjust(left=0.065, right=0.995, bottom=0.25, top=0.69, wspace=0.43)
    return save_figure(fig, "FIG-Motivation-Dependency")


def load_controlled_suite() -> list[dict[str, str]]:
    paths = (
        CONTROLLED_DIR / "controlled_recovery_raw.csv",
        CONTROLLED_DIR / "controlled_position_raw.csv",
        MICRO_DIR / "motivation_recovery_raw.csv",
    )
    rows: list[dict[str, str]] = []
    for path in paths:
        rows.extend(read_csv(path))

    by_trace: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_trace[(row["phase"], row["trace_id"])].append(row)
    if len(by_trace) != 400 or len(rows) != 400 * len(MICRO_POLICIES):
        raise ValueError("controlled suite requires 400 paired manifests")
    if any(
        {row["policy"] for row in trace_rows} != set(MICRO_POLICIES)
        or len({row["manifest_hash"] for row in trace_rows}) != 1
        for trace_rows in by_trace.values()
    ):
        raise ValueError("controlled suite has an incomplete paired-policy cell")
    ideal_rows = [row for row in rows if row["policy"] == "ideal_oracle"]
    nonprefix_cases = sum(
        not exact_target_is_temporal_suffix(row) for row in ideal_rows
    )
    if len(ideal_rows) != 400 or nonprefix_cases != 390:
        raise ValueError("controlled suite non-prefix target count changed")
    return rows


def load_artifact_recovery_suite(
    controlled_rows: list[dict[str, str]],
    suite_slice: str | None = None,
) -> dict[str, list[dict[str, str]]]:
    rows = read_csv(EXTERNAL_DIR / "external_recovery_raw.csv")
    if suite_slice is not None:
        rows = [row for row in rows if row["suite_slice"] == suite_slice]
    expected_cases = {
        (row["trace_id"], row["manifest_hash"]) for row in controlled_rows
    }
    source_by_case: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in controlled_rows:
        source_by_case[(row["trace_id"], row["manifest_hash"])].append(row)
    artifacts: dict[str, list[dict[str, str]]] = {}
    for backend in ARTIFACT_LABELS:
        backend_rows = [row for row in rows if row["backend"] == backend]
        cases = {
            (row["trace_id"], row["manifest_hash"]) for row in backend_rows
        }
        if (
            len(backend_rows) != len(expected_cases)
            or cases != expected_cases
            or any(row["status"] != "ok" for row in backend_rows)
            or any(row["recovery_safe"] != "True" for row in backend_rows)
            or any(float(row["invalid_removal"]) != 1.0 for row in backend_rows)
            or any(row["suite_slice"] not in EXTERNAL_SLICE_TO_PHASE for row in backend_rows)
        ):
            raise ValueError(f"{backend} artifact suite is incomplete or unsafe")
        normalized_rows = []
        for row in backend_rows:
            normalized = dict(row)
            case = (row["trace_id"], row["manifest_hash"])
            whole = next(
                source
                for source in source_by_case[case]
                if source["policy"] == "whole_branch"
            )
            unit_weight = float(whole["useful_tokens_wasted"]) / float(
                whole["useful_steps_wasted"]
            )
            normalized["policy"] = backend
            normalized["phase"] = EXTERNAL_SLICE_TO_PHASE[row["suite_slice"]]
            normalized["rollback_set"] = row["removed_ids"]
            normalized["useful_tokens_wasted"] = str(
                float(row["useful_steps_wasted"]) * unit_weight
            )
            normalized_rows.append(normalized)
        artifacts[backend] = sorted(
            normalized_rows,
            key=lambda row: (float(row["requested_dependency_value"]), row["trace_id"]),
        )
    return artifacts


def bootstrap_mean(values: np.ndarray, seed: int) -> tuple[float, float, float]:
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(values), size=(4000, len(values)))
    means = values[indices].mean(axis=1)
    return (
        float(values.mean()),
        float(np.quantile(means, 0.025)),
        float(np.quantile(means, 0.975)),
    )


def plot_m2() -> tuple[Path, Path]:
    rows = load_controlled_suite()
    artifacts = load_artifact_recovery_suite(rows)
    by_policy = {
        policy: [row for row in rows if row["policy"] == policy]
        for policy in MICRO_POLICIES
    }
    plot_rows = {**by_policy, **artifacts}
    series = (
        ("Turn-count sweep", "controlled", "#1b4f72", "o", "--", -0.045),
        ("Fault-position sweep", "controlled-position", "#d97706", "x", ":", 0.0),
        ("Dependency sweep", "m1", "#16837a", "D", "-.", 0.045),
        ("All 400 cases", None, "#b42318", "s", "-", 0.0),
    )
    panel_specs = (
        (
            DISPLAY_POLICIES,
            lambda row: 100.0 * float(row["invalid_removal"]),
            "Affected steps removed (%)",
            "Recovery completeness",
        ),
        (
            (
                "turn_checkpoint_proxy",
                "temporal_step",
                "yolofs",
                "waypoint",
                "branchfs",
                "session_try",
                "ideal_oracle",
            ),
            lambda row: 100.0 * (1.0 - float(row["valid_retention"])),
            "Valid operations discarded (%)",
            "Boundary bounds and named systems",
        ),
    )
    fig, axes = plt.subplots(1, 2, figsize=(WIDTH_CM * CM, 6.0 * CM), dpi=300)
    legend_handles = []
    for panel_index, (policies, metric, ylabel, xlabel) in enumerate(panel_specs):
        x = np.arange(len(policies), dtype=float)
        for series_index, (label, phase, color, marker, linestyle, offset) in enumerate(series):
            means = []
            lows = []
            highs = []
            for policy in policies:
                values = np.asarray(
                    [
                        metric(row)
                        for row in plot_rows[policy]
                        if phase is None
                        or row.get("phase") == phase
                        or EXTERNAL_SLICE_TO_PHASE.get(row.get("suite_slice", ""))
                        == phase
                    ],
                    dtype=float,
                )
                mean, low, high = bootstrap_mean(
                    values, 20261300 + panel_index * 10 + series_index
                )
                means.append(mean)
                lows.append(low)
                highs.append(high)
            means_array = np.asarray(means)
            handle, = axes[panel_index].plot(
                x + offset,
                means_array,
                color=color,
                marker=marker,
                linestyle=linestyle,
                linewidth=1.25 if phase is None else 0.9,
                markersize=4.2 if phase is None else 3.8,
                markerfacecolor=color if phase is None else "none",
                markeredgewidth=0.8,
                label=label,
                zorder=5 if phase is None else 3,
            )
            axes[panel_index].errorbar(
                x + offset,
                means_array,
                yerr=np.vstack(
                    (
                        means_array - np.asarray(lows),
                        np.asarray(highs) - means_array,
                    )
                ),
                fmt="none",
                ecolor=color,
                elinewidth=0.6,
                capsize=1.8,
                alpha=0.8,
                zorder=2,
            )
            if panel_index == 0:
                legend_handles.append(handle)
        axes[panel_index].set_ylim(-4, 106)
        axes[panel_index].set_yticks((0, 25, 50, 75, 100))
        axes[panel_index].set_ylabel(ylabel)
        axes[panel_index].set_xlim(-0.55, len(policies) - 0.45)
        axes[panel_index].set_xticks(
            x,
            [
                (LABELS.get(policy) or ARTIFACT_LABELS[policy]).replace(" ", "\n", 1)
                for policy in policies
            ],
            fontsize=5.8 if panel_index == 1 else 6.5,
        )
        axes[panel_index].set_xlabel("Recovery policy", fontsize=7)
        axes[panel_index].set_title(
            f"({'a' if panel_index == 0 else 'b'}) {xlabel}",
            loc="left",
            fontsize=7.5,
            pad=5,
        )
        axes[panel_index].axhline(100, color="#777777", linewidth=0.45, linestyle=":", zorder=1)
    fig.legend(
        handles=legend_handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.995),
        ncol=4,
        fontsize=6.2,
        columnspacing=1.0,
        handlelength=1.8,
        handletextpad=0.35,
    )
    fig.subplots_adjust(left=0.075, right=0.995, bottom=0.22, top=0.78, wspace=0.30)
    return save_figure(fig, "FIG-Motivation-Suite")


def plot_boundary_crossover() -> tuple[Path, Path]:
    rows = load_controlled_suite()
    paired: dict[tuple[str, str], dict[str, dict[str, str]]] = defaultdict(dict)
    for row in rows:
        paired[(row["phase"], row["trace_id"])][row["policy"]] = row

    slices = (
        ("Turn count", "controlled"),
        ("Fault position", "controlled-position"),
        ("Dependency", "m1"),
        ("All 400", None),
    )
    checkpoint_win_rate = []
    temporal_win_rate = []
    tie_rate = []
    checkpoint_waste = []
    temporal_waste = []
    per_case_best = []
    counts = []
    for _, phase in slices:
        cells = [
            policies
            for (cell_phase, _), policies in paired.items()
            if phase is None or cell_phase == phase
        ]
        counts.append(len(cells))
        checkpoint = np.asarray(
            [
                float(cell["turn_checkpoint_proxy"]["useful_tokens_wasted"])
                for cell in cells
            ]
        )
        temporal = np.asarray(
            [
                float(cell["temporal_step"]["useful_tokens_wasted"])
                for cell in cells
            ]
        )
        checkpoint_win_rate.append(100.0 * np.mean(checkpoint < temporal))
        temporal_win_rate.append(100.0 * np.mean(temporal < checkpoint))
        tie_rate.append(100.0 * np.mean(checkpoint == temporal))
        checkpoint_waste.append(float(checkpoint.mean()))
        temporal_waste.append(float(temporal.mean()))
        per_case_best.append(float(np.minimum(checkpoint, temporal).mean()))

    if counts != [100, 90, 210, 400]:
        raise ValueError(f"unexpected crossover slice sizes: {counts}")
    all_cells = counts[-1]
    if (
        round(checkpoint_win_rate[-1] * all_cells / 100) != 139
        or round(temporal_win_rate[-1] * all_cells / 100) != 251
        or round(tie_rate[-1] * all_cells / 100) != 10
    ):
        raise ValueError("coarse-boundary crossover counts changed")

    fig, axes = plt.subplots(1, 2, figsize=(WIDTH_CM * CM, 6.0 * CM), dpi=300)
    x = np.arange(len(slices), dtype=float)
    checkpoint_style = dict(STYLES["turn_checkpoint_proxy"])
    temporal_style = dict(STYLES["temporal_step"])
    checkpoint_line = axes[0].plot(
        x,
        checkpoint_win_rate,
        label="Turn checkpoint",
        **checkpoint_style,
    )[0]
    temporal_line = axes[0].plot(
        x, temporal_win_rate, label="Temporal step", **temporal_style
    )[0]
    tie_line = axes[0].plot(
        x,
        tie_rate,
        label="Tie",
        color="#6b7280",
        marker="v",
        linestyle="-.",
        markerfacecolor="none",
    )[0]
    axes[0].set_ylim(-4, 104)
    axes[0].set_yticks((0, 25, 50, 75, 100))
    axes[0].set_ylabel("Cases with lower\nwaste (%)")
    axes[0].set_title("(a) Boundary preference", loc="left", fontsize=7.5)

    axes[1].plot(x, checkpoint_waste, **checkpoint_style)
    axes[1].plot(x, temporal_waste, **temporal_style)
    best_line = axes[1].plot(
        x,
        per_case_best,
        color="#b42318",
        marker="s",
        linestyle="-",
        label="Per-case better coarse boundary",
    )[0]
    axes[1].set_ylim(0, max(temporal_waste) * 1.08)
    axes[1].set_ylabel("Mean useful token\nweight discarded")
    axes[1].set_title("(b) Fixed-boundary cost", loc="left", fontsize=7.5)
    for ax in axes:
        ax.set_xlim(-0.2, len(slices) - 0.8)
        ax.set_xticks(
            x,
            [label.replace(" ", "\n", 1) for label, _ in slices],
            fontsize=6.1,
        )
    for ax in axes:
        ax.set_xlabel("Microbenchmark slice")
    fig.legend(
        handles=(checkpoint_line, temporal_line, tie_line, best_line),
        loc="upper center",
        bbox_to_anchor=(0.5, 0.995),
        ncol=4,
        fontsize=6.0,
        columnspacing=0.9,
        handlelength=1.8,
        handletextpad=0.35,
    )
    fig.subplots_adjust(left=0.075, right=0.995, bottom=0.22, top=0.78, wspace=0.30)
    return save_figure(fig, "FIG-Motivation-Boundary-Crossover")


def weighted_ecdf(
    rows: list[dict[str, str]], metric
) -> tuple[np.ndarray, np.ndarray]:
    root_counts = Counter((row["trace_id"], row["turn_id"]) for row in rows)
    turn_count = len(root_counts)
    points = sorted(
        (
            float(metric(row)),
            1.0 / (turn_count * root_counts[(row["trace_id"], row["turn_id"])]),
        )
        for row in rows
    )
    return (
        np.array([value for value, _ in points]),
        100 * np.cumsum([weight for _, weight in points]),
    )


def load_m4() -> list[dict[str, str]]:
    config = json.loads((M4_DIR / "config.json").read_text(encoding="utf-8"))
    rows = read_csv(M4_DIR / "motivation_real_counterfactual_raw.csv")
    if (
        config.get("schema") != "agenttx.motivation.real_counterfactual/noncausal-v1"
        or tuple(config.get("policies") or ()) != RAW_CORRECT_POLICIES
        or config.get("trace_count") != 20
    ):
        raise ValueError("M4 configuration is not the non-causal provenance set")
    if len(rows) != 72 or any(row["policy"] == "agenttx_causal" for row in rows):
        raise ValueError("M4 requires 72 non-causal policy rows")
    keys = {(row["trace_id"], row["turn_id"], row["fault_step_id"]) for row in rows}
    if len(keys) != 18 or any(
        {row["policy"] for row in rows if (row["trace_id"], row["turn_id"], row["fault_step_id"]) == key}
        != set(RAW_CORRECT_POLICIES)
        for key in keys
    ):
        raise ValueError("M4 paired fault-root manifest is incomplete")
    return rows


def plot_m4() -> tuple[Path, Path]:
    rows = load_m4()
    paired = defaultdict(dict)
    for row in rows:
        key = (row["trace_id"], row["turn_id"], row["fault_step_id"])
        paired[key][row["policy"]] = row
    for key, policies in paired.items():
        turn = policies["turn_checkpoint_proxy"]
        whole = policies["whole_branch"]
        for metric in ("useful_tokens_wasted", "valid_retention"):
            if not np.isclose(float(turn[metric]), float(whole[metric])):
                raise ValueError(f"M4 turn/whole mismatch for {key}: {metric}")

    fig, axes = plt.subplots(1, 2, figsize=(WIDTH_CM * CM, 6.0 * CM), dpi=300)
    handles = []
    plotted_policies = COARSE_POLICIES
    for index, policy in enumerate(plotted_policies):
        policy_rows = [row for row in rows if row["policy"] == policy]
        x, y = weighted_ecdf(policy_rows, lambda row: row["useful_tokens_wasted"])
        style = dict(STYLES[policy])
        style["markevery"] = (index % 2, 3)
        if policy == "whole_branch":
            style["linestyle"] = "none"
        handle, = axes[0].plot(
            x,
            y,
            drawstyle="steps-post",
            label=LABELS[policy],
            **style,
        )
        handles.append(handle)
        x, y = weighted_ecdf(
            policy_rows, lambda row: 100 * (1 - float(row["valid_retention"]))
        )
        axes[1].plot(x, y, drawstyle="steps-post", **style)

    for ax in axes:
        ax.set_ylim(0, 103)
        ax.set_ylabel("Weighted cumulative fault roots (%)")
    axes[0].set_xlabel("Useful token weight discarded\n(a) Recovery waste", fontsize=7)
    axes[1].set_xlim(-3, 103)
    axes[1].set_xlabel("Valid work discarded (%)\n(b) Recovery opportunity", fontsize=7)
    axes[1].text(
        0.45,
        0.90,
        "18 counterfactual fault roots",
        transform=axes[1].transAxes,
        fontsize=6.2,
        color="#b42318",
    )
    fig.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.995),
        ncol=4,
        fontsize=5.8,
        columnspacing=0.8,
        handlelength=1.7,
        handletextpad=0.3,
        borderpad=0.3,
    )
    fig.subplots_adjust(left=0.08, right=0.995, bottom=0.25, top=0.78, wspace=0.28)
    return save_figure(fig, "FIG-Motivation-Real-Counterfactual")


def main() -> int:
    configure_style()
    outputs = (plot_m0(), plot_m2(), plot_m1(), plot_boundary_crossover())
    for png, pdf in outputs:
        print(f"wrote {png}")
        print(f"wrote {pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
