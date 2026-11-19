#!/usr/bin/env python3
"""Plot the matched RQ5 Ext4, OverlayFS, DeltaFS and AgentTX comparison."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from motivation.plot_motivation_four import CM, configure_style
from motivation.paper_palette import BAR_PALETTE

CHECKPOINT_SOURCE = ROOT / "review/rq5-deltafs-comparison-20261117/summary.json"
COMMIT_SOURCE = ROOT / "review/rq5-deltafs-commit-20261117/summary.json"
OUTPUT = ROOT / "paper/figures/rq5-backends"
VARIANTS = {
    "ext4": ("Ext4", BAR_PALETTE["teal"], ""),
    "overlay-metacopy": ("OverlayFS", BAR_PALETTE["purple"], ""),
    "agenttx-metacopy": ("AgentTX", BAR_PALETTE["red"], "//"),
    "deltafs": ("DeltaFS", BAR_PALETTE["orange"], ""),
}


def interval(metric: dict, scale: float) -> tuple[float, float, float]:
    samples = np.asarray(metric["samples"], dtype=float) / scale
    assert len(samples) == metric["n"] and len(samples) in (10, 20)
    assert np.isclose(samples.mean(), metric["mean"] / scale)
    rng = np.random.default_rng(20261116)
    means = rng.choice(samples, size=(10000, len(samples)), replace=True).mean(axis=1)
    low, high = np.quantile(means, [0.025, 0.975])
    return float(samples.mean()), float(low), float(high)


@matplotlib.rc_context({'hatch.linewidth': 0.45})
def draw(checkpoints: dict, commits: dict) -> dict:
    baseline, agenttx = "overlay-metacopy", "agenttx-metacopy"
    commit_variants = ("ext4", baseline, "deltafs", agenttx)
    checkpoint_variants = (baseline, "deltafs", agenttx)
    panels = (
        ([f"commit/{op}" for op in ("create", "overwrite", "rename", "unlink")],
         ["Create", "Overwrite", "Rename", "Delete"], commit_variants, 1e9,
         "Time (s)", "(a) File commit"),
        ([f"snapshot/{depth}" for depth in (1, 10, 30)],
         ["1", "10", "30"], checkpoint_variants, 1e6,
         "ms / checkpoint", "(b) Checkpoint creation"),
    )
    fig, axes = plt.subplots(1, 2, figsize=(8.5 * CM, 3.8 * CM))
    evidence = {}
    handles = {}
    for ax, (cases, ticks, variants, scale, ylabel, xlabel) in zip(axes, panels):
        x = np.arange(len(cases))
        width = 0.68 / len(variants)
        ceiling = 0
        for index, variant in enumerate(variants):
            label, color, hatch = VARIANTS[variant]
            measurements = []
            for case in cases:
                if case.startswith("snapshot/"):
                    source_variant = {baseline: "overlay-sync", agenttx: "agenttx-overlay",
                                      "deltafs": "deltafs"}[variant]
                    metric = checkpoints["latency"][source_variant][case.split("/")[1]]["snapshot_ms"]
                    assert metric["n"] == 20
                    measurements.append((metric, 1, CHECKPOINT_SOURCE,
                                         source_variant, "snapshot_ms"))
                else:
                    metric = commits["groups"][case][variant]["lifecycle_ns"]
                    assert metric["n"] == 10
                    measurements.append((metric, scale, COMMIT_SOURCE, variant, "lifecycle_ns"))
            values = [interval(metric, divisor)
                      for metric, divisor, *_ in measurements]
            means, lows, highs = np.asarray(values).T
            bars = ax.bar(x + (index - (len(variants) - 1) / 2) * width, means,
                          width=width * 0.94, color=color, hatch=hatch,
                          edgecolor="#3f3f3f", linewidth=0.25, label=label,
                          yerr=np.array([means - lows, highs - means]),
                          error_kw=dict(ecolor="#343434", elinewidth=0.45,
                                         capsize=1, capthick=0.45), zorder=3)
            handles[variant] = bars
            ceiling = max(ceiling, max(highs))
            for case, (mean, low, high), measurement in zip(cases, values, measurements):
                metric, _, source, source_variant, source_metric = measurement
                evidence.setdefault(case, {})[variant] = dict(
                    mean=float(mean), ci95=[float(low), float(high)], n=metric["n"],
                    unit="s" if scale == 1e9 else "ms", metric=source_metric,
                    source=str(source.relative_to(ROOT)), source_variant=source_variant)
        ax.set_xticks(x, ticks)
        if cases[0].startswith("commit"):
            plt.setp(ax.get_xticklabels(), rotation=25, ha="right", rotation_mode="anchor")
        ax.set_ylabel(ylabel, fontsize=7, labelpad=2)
        ax.set_xlabel(xlabel, fontsize=7, labelpad=4)
        ax.xaxis.set_label_coords(0.5, -0.48)
        ax.set_ylim(0, ceiling * 1.12)
        ax.set_xlim(-0.5, len(cases)-0.5)
        ax.tick_params(labelsize=6.2, length=2, pad=2, direction="in",
                       top=False, right=False)
        ax.tick_params(axis="x", length=0, pad=3)
        ax.grid(axis="y", color="#e5e5e5", linewidth=0.3, zorder=0)
        ax.set_axisbelow(True)
        ax.yaxis.set_major_locator(plt.MaxNLocator(3))
    fig.legend([handles[v] for v in commit_variants],
               [VARIANTS[v][0] for v in commit_variants],
               loc="upper center", bbox_to_anchor=(0.53, 1),
               ncol=len(commit_variants), fontsize=6.4,
               handlelength=1.2, handleheight=0.75, labelspacing=0.18,
               handletextpad=0.4, columnspacing=0.85, borderaxespad=0)
    fig.subplots_adjust(left=0.10, right=0.99, bottom=0.34,
                        top=0.82, wspace=0.41)
    stem = "FIG-RQ5-OverlayFS"
    for folder in (ROOT / "paper/img", OUTPUT):
        folder.mkdir(parents=True, exist_ok=True)
        for suffix in ("pdf", "png"):
            fig.savefig(folder / f"{stem}.{suffix}", dpi=300,
                        bbox_inches="tight", pad_inches=0.025)
    plt.close(fig)
    return evidence


def main() -> None:
    configure_style()
    checkpoints = json.loads(CHECKPOINT_SOURCE.read_text())
    assert checkpoints["counts"]["latency_sessions"] == 192
    assert checkpoints["counts"]["failed"] == 0
    commits = json.loads(COMMIT_SOURCE.read_text())
    assert commits["verified_sessions"] == 192 and commits["measured_sessions"] == 160
    evidence = {"OverlayFS": draw(checkpoints, commits)}
    audit = dict(sources={str(path.relative_to(ROOT)):
                         hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in (CHECKPOINT_SOURCE, COMMIT_SOURCE)},
                 error_bars="95% session-bootstrap intervals for mean; 10,000 resamples",
                 baseline_protocol=dict(
                     overlay_checkpoint="Synchronous unmount/remount; no retained mounts.",
                     agenttx_overlay_checkpoint="Retained read-only mounts and generation records.",
                     deltafs_checkpoint="Recovered paper kernel module; native checkpoint ioctl, same kernel and Ext4."),
                 palette=BAR_PALETTE, figures=evidence)
    (OUTPUT / "figure-data.json").write_text(json.dumps(audit, indent=2) + "\n")
    print("Generated the RQ5 storage comparison from final measured sessions.")


if __name__ == "__main__":
    main()
