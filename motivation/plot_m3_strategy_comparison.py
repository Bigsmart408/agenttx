#!/usr/bin/env python3
"""Plot within-agent M3 recovery-strategy comparisons."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import numpy as np
import pandas as pd


STANDARD_WIDTH_CM = 17.8
ORDER = [
    "clean_recovery",
    "crash_no_rollback",
    "turn_checkpoint_proxy",
    "temporal_step",
    "whole_branch",
    "agenttx_causal",
    "ideal_oracle",
]
LABELS = {
    "clean_recovery": "Clean",
    "crash_no_rollback": "No rollback",
    "turn_checkpoint_proxy": "Turn checkpoint",
    "temporal_step": "Temporal",
    "whole_branch": "Whole branch",
    "agenttx_causal": "AgentTX causal",
    "ideal_oracle": "Ideal oracle",
}
BAR_STYLES = {
    "clean_recovery": dict(facecolor="white", edgecolor="#5f6872", hatch=".."),
    "crash_no_rollback": dict(facecolor="#77a9cf", edgecolor="#2f6f9f", hatch=""),
    "turn_checkpoint_proxy": dict(facecolor="#d9d9d9", edgecolor="#656565", hatch="///"),
    "temporal_step": dict(facecolor="#f0ad68", edgecolor="#b35f14", hatch="xx"),
    "whole_branch": dict(facecolor="#8c8c8c", edgecolor="#343434", hatch=""),
    "agenttx_causal": dict(facecolor="#c00000", edgecolor="#8a0000", hatch=""),
    "ideal_oracle": dict(facecolor="white", edgecolor="#c00000", hatch="\\\\"),
}


def cm_to_inch(value: float) -> float:
    return value / 2.54


def configure_style() -> None:
    plt.rcParams.update(plt.rcParamsDefault)
    matplotlib.rcParams["text.usetex"] = False
    plt.rcParams["font.family"] = "serif"
    plt.rcParams["font.serif"] = ["Nimbus Roman", "Times New Roman", "Times"]
    plt.rcParams["axes.grid"] = False
    plt.rcParams["axes.linewidth"] = 0.6
    plt.rcParams["xtick.direction"] = "in"
    plt.rcParams["ytick.direction"] = "in"
    plt.rcParams["legend.frameon"] = True
    plt.rcParams["legend.edgecolor"] = "0.55"
    plt.rcParams["legend.framealpha"] = 1.0
    plt.rcParams["legend.fancybox"] = False
    plt.rcParams["hatch.linewidth"] = 0.45
    plt.rcParams["pdf.fonttype"] = 42
    plt.rcParams["ps.fonttype"] = 42


def load_agent(root: Path, agent: str) -> pd.DataFrame:
    result_dir = (
        root
        / "experiments"
        / "results"
        / f"motivation_v7_strict_{agent}_full_20260915"
    )
    config = json.loads((result_dir / "motivation_replay_config.json").read_text())
    profile = config.get("codex_gpt_profile", {})
    reasoning_effort = config.get("reasoning_effort", profile.get("reasoning_effort"))
    if config["model"] != "gpt-5.6-luna" or reasoning_effort != "max":
        raise RuntimeError(f"unexpected {agent} model configuration")
    if config["policies"] != ORDER:
        raise RuntimeError(f"unexpected {agent} policy set")
    if config["replay_protocol"] != "one_artifact_per_completed_write_event_strict_audit":
        raise RuntimeError(f"unexpected {agent} replay protocol")

    frame = pd.read_csv(result_dir / "motivation_replay_summary.csv").set_index("policy")
    missing = set(ORDER) - set(frame.index)
    if missing:
        raise RuntimeError(f"missing {agent} policies: {sorted(missing)}")
    return frame.loc[ORDER].copy()


def token_label(value: float) -> str:
    return f"{value / 1000:.0f}K"


def main() -> None:
    configure_style()
    root = Path(__file__).resolve().parents[1]
    frames = {
        "Codex": load_agent(root, "codex"),
        "SWEAgent": load_agent(root, "sweagent"),
    }
    x = np.arange(len(ORDER))

    fig = plt.figure(
        dpi=300,
        figsize=(cm_to_inch(STANDARD_WIDTH_CM), cm_to_inch(8.2)),
    )
    grid = fig.add_gridspec(2, 2, height_ratios=[3.3, 1.0], hspace=0.10, wspace=0.19)

    for column, (agent, frame) in enumerate(frames.items()):
        tokens = frame["observed_tokens_per_success"].to_numpy(dtype=float)
        rates = 100 * frame["success_rate"].to_numpy(dtype=float)
        cells = frame["cells"].to_numpy(dtype=int)

        ax = fig.add_subplot(grid[0, column])
        for index, policy in enumerate(ORDER):
            ax.bar(
                index,
                tokens[index],
                width=0.68,
                linewidth=0.6,
                zorder=2,
                **BAR_STYLES[policy],
            )
            ax.text(
                index,
                tokens[index] * 1.09,
                token_label(tokens[index]),
                ha="center",
                va="bottom",
                fontsize=5.8,
                color="#303030",
            )
        ax.set_yscale("log")
        ax.set_ylim(3_000, 800_000)
        ax.set_xticks(x, [])
        ax.set_ylabel("Tokens per successful cell (log scale)" if column == 0 else "", fontsize=7.5)
        ax.set_title(agent, fontsize=8.5, pad=3)
        ax.tick_params(axis="both", labelsize=6.5, top=True, right=True)
        ax.tick_params(axis="x", labelbottom=False)

        ax_rate = fig.add_subplot(grid[1, column], sharex=ax)
        ax_rate.axhline(100, color="0.75", linewidth=0.55, linestyle=":", zorder=1)
        ax_rate.plot(
            x,
            rates,
            color="#303030",
            marker="D",
            linewidth=0.7,
            markersize=2.7,
            markerfacecolor="white",
            markeredgewidth=0.65,
            zorder=3,
        )
        for index, (rate, count) in enumerate(zip(rates, cells)):
            ax_rate.text(
                index,
                rate - 0.65,
                f"n={count}",
                ha="center",
                va="top",
                fontsize=5.2,
                color="0.35",
            )
        ax_rate.set_ylim(89, 101.5)
        ax_rate.set_yticks([90, 95, 100])
        ax_rate.set_ylabel("Success (%)" if column == 0 else "", fontsize=7.5)
        panel = "(a)" if column == 0 else "(b)"
        ax_rate.set_xlabel(f"Recovery policy\n{panel} {agent}, within-agent comparison", fontsize=7)
        ax_rate.set_xticks(x, [LABELS[p] for p in ORDER], rotation=28, ha="right")
        ax_rate.tick_params(axis="both", labelsize=6, top=True, right=True)

    handles = [
        Patch(linewidth=0.6, label=LABELS[policy], **BAR_STYLES[policy])
        for policy in ORDER
    ]
    fig.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.02),
        ncol=7,
        fontsize=5.7,
        columnspacing=0.65,
        handlelength=1.25,
        handletextpad=0.3,
        borderpad=0.3,
    )
    fig.subplots_adjust(left=0.075, right=0.995, bottom=0.25, top=0.84)

    outputs = [root / "motivation", root / "paper" / "img"]
    for output in outputs:
        output.mkdir(parents=True, exist_ok=True)
        stem = output / "FIG-M3-Strategy-Comparison"
        fig.savefig(stem.with_suffix(".pdf"), bbox_inches="tight", pad_inches=0.02)
        fig.savefig(stem.with_suffix(".png"), dpi=300, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)

    print("wrote FIG-M3-Strategy-Comparison")
    for agent, frame in frames.items():
        values = {
            LABELS[policy]: {
                "tokens_per_success": round(float(frame.loc[policy, "observed_tokens_per_success"])),
                "success_rate": round(float(frame.loc[policy, "success_rate"]), 4),
                "cells": int(frame.loc[policy, "cells"]),
            }
            for policy in ORDER
        }
        print(agent, json.dumps(values, sort_keys=True))


if __name__ == "__main__":
    main()
