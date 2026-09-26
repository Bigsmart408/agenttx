#!/usr/bin/env python3
"""Plot the repeated comparison-baseline distribution."""
from __future__ import annotations

import csv
import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from motivation.plot_motivation_four import configure_style
from motivation.plot_paper_evaluation import COLORS

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "experiments" / "results" / "comparison_repeats.csv"
OUT = ROOT / "motivation"

ORDER = [
    "bare",
    "per_call_try",
    "session_try",
    "shared_try",
    "shared_checkpoint",
    "bubblewrap",
    "agenttx_without_read_tracing",
    "agenttx_full",
]
LABELS = {
    "bare": "Bare",
    "per_call_try": "per-call try",
    "session_try": "session try",
    "shared_try": "shared try",
    "shared_checkpoint": "shared checkpoint",
    "bubblewrap": "bubblewrap",
    "agenttx_without_read_tracing": "AgentTX no-trace",
    "agenttx_full": "AgentTX full",
}

# The paper's Table 2 predates comparison_repeats.csv and reports a separate
# persistent-execution run. Keep its p99 figure on exactly the table's values.
PAPER_P99 = {
    "bare": 4.976,
    "per_call_try": 202.746,
    "session_try": 22.390,
    "shared_try": 196.776,
    "shared_checkpoint": 52.147,
    "bubblewrap": 1.301,
    "agenttx_without_read_tracing": 55.508,
    "agenttx_full": 68.169,
}


def read_rows() -> dict[str, dict[str, str]]:
    with RESULT.open(newline="", encoding="utf-8") as handle:
        return {
            row["mode"]: row
            for row in csv.DictReader(handle)
            if row.get("suite") == "overhead"
        }


def plot_quantile(rows: dict[str, dict[str, str]], modes: list[str], field: str,
                  title: str, filename: str) -> None:
    values = [float(rows[mode][field]) for mode in modes]
    x = list(range(len(modes)))
    palette = {
        "bare": COLORS["neutral"],
        "per_call_try": COLORS["temporal"],
        "session_try": COLORS["temporal"],
        "shared_try": COLORS["temporal"],
        "shared_checkpoint": COLORS["checkpoint"],
        "bubblewrap": COLORS["neutral"],
        "agenttx_without_read_tracing": COLORS["checkpoint"],
        "agenttx_full": COLORS["agenttx"],
    }
    fig, ax = plt.subplots(figsize=(8.6 / 2.54, 6.0 / 2.54))
    ax.barh(x, values, color=[palette[mode] for mode in modes], height=0.62)
    ax.set_xscale("log")
    ax.set_xlim(0.8, 300)
    ax.set_yticks(x, [LABELS[mode] for mode in modes])
    ax.invert_yaxis()
    ax.set_xlabel("p99 latency per step (ms, log scale)")
    ax.tick_params(axis="y", length=0)
    fig.tight_layout(pad=0.4)
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / f"{filename}.pdf", bbox_inches="tight", pad_inches=0.02)
    if filename == "FIG-Comparison-P99":
        fig.savefig(ROOT / "paper" / "img" / f"{filename}.pdf",
                    bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--paper-p99", action="store_true")
    args = parser.parse_args()
    rows = read_rows()
    modes = [mode for mode in ORDER if mode in rows and rows[mode]["samples"] != "0"]
    configure_style()
    if args.paper_p99:
        rows = {mode: {"per_step_ms_p99": str(value)} for mode, value in PAPER_P99.items()}
        modes = list(ORDER)
        plot_quantile(rows, modes, "per_step_ms_p99", "Repeated baseline p99 latency", "FIG-Comparison-P99")
        return
    x = list(range(len(modes)))
    p50 = [float(rows[mode]["per_step_ms_p50"]) for mode in modes]
    p95 = [float(rows[mode]["per_step_ms_p95"]) for mode in modes]
    means = [float(rows[mode]["per_step_ms_mean"]) for mode in modes]
    correct = {
        mode: float(next(
            item["causal_correct_rate"]
            for item in csv.DictReader(RESULT.open(newline="", encoding="utf-8"))
            if item.get("suite") == "recovery" and item.get("mode") == mode
        ))
        for mode in modes
    }

    plt.rcParams.update({"font.size": 11, "axes.titlesize": 13, "axes.labelsize": 11})
    for field, title, filename in [
        ("per_step_ms_mean", "Repeated baseline mean latency", "FIG-Comparison-Mean"),
        ("per_step_ms_p50", "Repeated baseline p50 latency", "FIG-Comparison-P50"),
        ("per_step_ms_p95", "Repeated baseline p95 latency", "FIG-Comparison-P95"),
        ("per_step_ms_p99", "Repeated baseline p99 latency", "FIG-Comparison-P99"),
    ]:
        plot_quantile(rows, modes, field, title, filename)

    fig, axes = plt.subplots(1, 2, figsize=(13, 4.4), gridspec_kw={"width_ratios": [2.25, 1]})
    colors = ["#dce3ea", "#f7d4b5", "#e8dced", "#f3c8a7",
              "#c8dff0", "#e5e9ef", "#bcdcf0", "#f2b6b6"]
    ax = axes[0]
    ax.plot(x, p50, color="#8fb9d5", marker="o", linewidth=2.0, label="p50")
    ax.plot(x, p95, color="#d88989", marker="s", linewidth=2.0, label="p95")
    ax.scatter(x, means, color=colors, s=45, zorder=3, label="mean")
    ax.set_yscale("log")
    ax.set_xticks(x, [LABELS[mode] for mode in modes], rotation=28, ha="right")
    ax.set_ylabel("Per-step latency (ms, log scale)")
    ax.set_title("Repeated baseline runtime")
    ax.grid(True, axis="y", alpha=0.28)
    ax.legend(frameon=True, ncol=3, loc="upper left")
    ax.annotate(
        f"AgentTX full\np50={p50[-1]:.1f}, p95={p95[-1]:.1f}",
        xy=(x[-1], p95[-1]),
        xytext=(x[-1] - 1.9, p95[-1] * 1.8),
        arrowprops={"arrowstyle": "->", "color": "#b56d6d"},
        color="#b56d6d",
        ha="center",
    )

    ax = axes[1]
    rates = [correct[mode] * 100 for mode in modes]
    ax.plot(x, rates, color="#d88989", marker="s", linewidth=2.0)
    ax.set_xticks(x, [LABELS[mode] for mode in modes], rotation=55, ha="right")
    ax.set_ylim(-5, 105)
    ax.set_ylabel("Causal-correct recovery (%)")
    ax.set_title("10 fresh-workspace repeats")
    ax.grid(True, axis="y", alpha=0.28)
    ax.annotate("10/10", xy=(x[-1], rates[-1]), xytext=(x[-1] - 1.2, 82),
                arrowprops={"arrowstyle": "->", "color": "#b56d6d"}, color="#b56d6d")

    fig.tight_layout()
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / "FIG-Comparison-Repeats.png", dpi=220, bbox_inches="tight")
    fig.savefig(OUT / "FIG-Comparison-Repeats.pdf", bbox_inches="tight")
    print(f"wrote {OUT / 'FIG-Comparison-Repeats.png'}")


if __name__ == "__main__":
    main()
