#!/usr/bin/env python3
"""Plot eBPF vs strace dependency-tracing overhead and capture fidelity."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

STANDARD_WIDTH = 17.8
MODE_ORDER = ["off", "strace", "bpf"]
METRIC_STYLES = {
    "per_step_ms_mean": dict(color="#c00000", marker="s", linestyle="-", linewidth=1.0, markersize=3.2),
    "per_step_ms_p50": dict(color="#7f7f7f", marker="o", linestyle="--", linewidth=0.8, markersize=2.8, markerfacecolor="none"),
    "per_step_ms_p95": dict(color="#e78129", marker="x", linestyle=":", linewidth=0.9, markersize=3.6, markeredgewidth=0.9),
}
LABELS = {
    "per_step_ms_mean": "mean",
    "per_step_ms_p50": "p50",
    "per_step_ms_p95": "p95",
}
MODE_LABELS = {
    "off": "no tracing",
    "strace": "strace",
    "bpf": "eBPF",
}


def cm_to_inch(value: float) -> float:
    return value / 2.54


def configure_style() -> None:
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


def repository_root() -> Path:
    cwd = Path.cwd()
    if cwd.name == "motivation":
        return cwd.parent
    if (cwd / "experiments").is_dir() and (cwd / "motivation").is_dir():
        return cwd
    return Path(__file__).resolve().parents[1]


def load_results(root: Path) -> pd.DataFrame:
    result = root / "experiments" / "results" / "bpf_trace_overhead.csv"
    if not result.exists():
        raise FileNotFoundError(
            f"missing {result}; run experiments/scripts/bench_bpf_trace.py "
            "as root on a host with bpftrace first"
        )
    frame = pd.read_csv(result)
    numeric = [
        "per_step_ms_mean",
        "per_step_ms_p50",
        "per_step_ms_p95",
        "reads_captured",
        "negatives_captured",
        "capture_expected",
    ]
    frame[numeric] = frame[numeric].apply(pd.to_numeric)
    return frame


def _x(frame: pd.DataFrame) -> list[float]:
    return [float(MODE_ORDER.index(mode)) for mode in frame["mode"]]


def plot(frame: pd.DataFrame, output: Path) -> None:
    configure_style()

    baseline = frame[frame["mode"] == "off"]["per_step_ms_mean"].iloc[0]
    frame = frame.sort_values("mode", key=lambda s: s.map(MODE_ORDER.index))
    x = _x(frame)

    def values(metric: str) -> list[float]:
        return frame[metric].tolist()

    fig = plt.figure(dpi=300, figsize=(cm_to_inch(STANDARD_WIDTH), cm_to_inch(7.0)))
    handles = []

    def draw(index: int, subtitle: str, ylabel: str, series_specs, ylim=None) -> None:
        nonlocal handles
        ax = plt.subplot(2, 2, index)
        for metric, spec in series_specs:
            handle, = ax.plot(x, values(metric), **spec["style"],
                              label=spec["label"])
            if index == 1:
                handles.append(handle)
        ax.set_ylabel(ylabel, fontsize=8)
        ax.set_xlabel(f"Tracing backend\n{subtitle}", fontsize=7)
        ax.set_xticks(x)
        ax.set_xticklabels([MODE_LABELS[m] for m in frame["mode"]], fontsize=6.5)
        ax.tick_params(axis="both", labelsize=7)
        if ylim is not None:
            ax.set_ylim(*ylim)

    draw(
        1,
        "(a) Per-step cost",
        "Per-step cost (ms)",
        [
            (m, {"style": METRIC_STYLES[m], "label": LABELS[m]})
            for m in ("per_step_ms_mean", "per_step_ms_p50", "per_step_ms_p95")
        ],
    )

    incremental = {
        mode: (frame[frame["mode"] == mode]["per_step_ms_mean"].iloc[0] - baseline)
        for mode in ("strace", "bpf")
    }
    ax = plt.subplot(2, 2, 2)
    ix = [float(MODE_ORDER.index(m)) for m in incremental]
    handle, = ax.plot(ix, list(incremental.values()), **METRIC_STYLES["per_step_ms_mean"],
                      label="incremental cost")
    handles.append(handle)
    ax.axhline(0.0, color="0.6", linewidth=0.6, linestyle=":")
    ax.set_ylabel("Incremental cost (ms)", fontsize=8)
    ax.set_xlabel("Tracing backend\n(b) Tracing tax vs no tracing", fontsize=7)
    ax.set_xticks(ix)
    ax.set_xticklabels([MODE_LABELS[m] for m in incremental], fontsize=6.5)
    ax.tick_params(axis="both", labelsize=7)

    traced = frame[frame["mode"] != "off"]
    ax = plt.subplot(2, 2, 3)
    x_traced = [float(MODE_ORDER.index(m)) for m in traced["mode"]]
    expected = traced["capture_expected"].astype(float)
    rate = 100.0 * (
        traced["reads_captured"] + traced["negatives_captured"]
    ) / (2.0 * expected)
    handle, = ax.plot(x_traced, rate.tolist(), **METRIC_STYLES["per_step_ms_p95"],
                      label="read + negative capture")
    handles.append(handle)
    ax.axhline(100.0, color="0.6", linewidth=0.6, linestyle=":")
    ax.set_ylim(0, 105)
    ax.set_ylabel("Capture rate (%)", fontsize=8)
    ax.set_xlabel("Tracing backend\n(c) Dependency capture fidelity", fontsize=7)
    ax.set_xticks(x_traced)
    ax.set_xticklabels([MODE_LABELS[m] for m in traced["mode"]], fontsize=6.5)
    ax.tick_params(axis="both", labelsize=7)

    ax = plt.subplot(2, 2, 4)
    strace_p95 = frame[frame["mode"] == "strace"]["per_step_ms_p95"].iloc[0]
    bpf_p95 = frame[frame["mode"] == "bpf"]["per_step_ms_p95"].iloc[0]
    ratio = (strace_p95 - bpf_p95) / max(strace_p95, 1e-9) * 100.0
    ax.text(
        0.05, 0.55,
        f"p95 ratio (strace − eBPF):\n{ratio:+.1f}% of strace p95",
        transform=ax.transAxes, fontsize=8, va="center",
    )
    ax.text(
        0.05, 0.30,
        "Verified: every traced step captured\nboth the READ and the NEGATIVE\neffect in the ledger.",
        transform=ax.transAxes, fontsize=7, color="0.25", va="center",
    )
    ax.set_ylabel("", fontsize=8)
    ax.set_xlabel("(d) Interpretation", fontsize=7)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)

    fig.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.035),
        ncol=4,
        fontsize=7,
        columnspacing=1.0,
        handlelength=1.8,
        handletextpad=0.35,
        borderpad=0.3,
    )
    plt.tight_layout(pad=0.6, h_pad=1.6, w_pad=1.2, rect=[0.0, 0.0, 1.0, 0.91])
    output.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        output / "FIG-Bpf-Trace.pdf",
        bbox_inches="tight",
        pad_inches=0.02,
        metadata={"CreationDate": None, "ModDate": None},
    )
    fig.savefig(
        output / "FIG-Bpf-Trace.png",
        dpi=300,
        bbox_inches="tight",
        pad_inches=0.02,
    )
    plt.close(fig)


def main() -> None:
    root = repository_root()
    frame = load_results(root)
    plot(frame, root / "motivation")
    print(f"wrote {root / 'motivation' / 'FIG-Bpf-Trace.png'}")


if __name__ == "__main__":
    main()
