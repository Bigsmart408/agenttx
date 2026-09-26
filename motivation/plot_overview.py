#!/usr/bin/env python3
"""Draw the rollback-only Agent Effect Transaction overview."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


BLUE = "#1b4f72"
BLUE_BG = "#eaf1f7"
GREEN = "#16837a"
GREEN_BG = "#e8f3eb"
RED = "#b42318"
RED_BG = "#f8e9e8"
GOLD = "#d97706"
GOLD_BG = "#fbf4e5"
GRAY = "#6b7280"
LIGHT = "#d9dee2"
TEXT = "#20252a"


def configure_style() -> None:
    plt.rcParams.update(plt.rcParamsDefault)
    plt.rcParams.update(
        {
            "font.family": "Nimbus Roman",
            "font.size": 7.0,
            "axes.linewidth": 0.6,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )


def box(ax, x, y, w, h, text, *, face, edge, size=6.7, weight="normal", ls="-"):
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.008,rounding_size=0.018",
        facecolor=face,
        edgecolor=edge,
        linewidth=0.9,
        linestyle=ls,
        zorder=3,
    )
    ax.add_patch(patch)
    ax.text(
        x + w / 2,
        y + h / 2,
        text,
        ha="center",
        va="center",
        color=TEXT,
        fontsize=size,
        fontweight=weight,
        linespacing=1.05,
        zorder=4,
    )
    return patch


def arrow(ax, start, end, *, color=GRAY, lw=0.85, rad=0.0, ls="-"):
    patch = FancyArrowPatch(
        start,
        end,
        arrowstyle="-|>",
        mutation_scale=8,
        linewidth=lw,
        linestyle=ls,
        color=color,
        connectionstyle=f"arc3,rad={rad}",
        zorder=2,
    )
    ax.add_patch(patch)
    return patch


def draw(output_dirs: list[Path]) -> None:
    configure_style()
    fig = plt.figure(figsize=(3.35, 3.05), dpi=300)
    ax = fig.add_axes([0.02, 0.02, 0.96, 0.96])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    ax.text(
        0.02,
        0.965,
        "Trajectory and dependency-defined rollback",
        fontsize=8.3,
        fontweight="bold",
        va="top",
        color=TEXT,
    )
    ax.text(0.02, 0.905, "execution order", fontsize=6.0, color=GRAY, va="center")

    xs = [0.03, 0.225, 0.42, 0.615, 0.81]
    labels = [
        "s0\nsetup",
        "s1\nfaulty edit",
        "s2\nindependent",
        "s3\nderived output",
        "s4\nfailed test",
    ]
    selected = [False, True, False, True, True]
    node_w, node_h, node_y = 0.155, 0.105, 0.82
    centers = []
    for x, label, rollback in zip(xs, labels, selected):
        color, fill = (RED, RED_BG) if rollback else (GREEN, GREEN_BG)
        box(
            ax,
            x,
            node_y,
            node_w,
            node_h,
            label,
            face=fill,
            edge=color,
            size=5.8,
            weight="bold" if rollback else "normal",
        )
        centers.append((x + node_w / 2, node_y + node_h / 2))

    for left, right in zip(centers, centers[1:]):
        arrow(
            ax,
            (left[0] + node_w / 2, left[1]),
            (right[0] - node_w / 2, right[1]),
            color=LIGHT,
            lw=0.7,
        )

    arrow(
        ax,
        (centers[1][0], node_y),
        (centers[3][0], node_y),
        color=RED,
        lw=1.0,
        rad=0.23,
    )
    arrow(
        ax,
        (centers[3][0], node_y),
        (centers[4][0], node_y),
        color=RED,
        lw=1.0,
        rad=0.23,
    )
    ax.text(
        0.57,
        0.745,
        "observed dependencies",
        color=RED,
        fontsize=6.0,
        ha="center",
    )

    ax.text(0.02, 0.68, "Recovery set", fontsize=7.3, fontweight="bold", color=TEXT)
    ax.text(0.25, 0.68, r"$C(s_1)=\{s_1,s_3,s_4\}$", fontsize=7.1, color=RED)
    ax.text(0.74, 0.68, "retain s0, s2", fontsize=6.6, color=GREEN)

    ax.plot([0.02, 0.98], [0.635, 0.635], color=LIGHT, lw=0.7)
    ax.text(
        0.02,
        0.605,
        "AET state and synchronized recovery",
        fontsize=8.3,
        fontweight="bold",
        va="top",
        color=TEXT,
    )

    state_y, state_h, state_w = 0.445, 0.105, 0.205
    state_x = [0.02, 0.265, 0.51, 0.755]
    state = [
        (r"$V$", "workspace view"),
        (r"$L$", "effect ledger"),
        (r"$H$", "object history"),
        (r"$M$", "message history"),
    ]
    for x, (symbol, desc) in zip(state_x, state):
        box(
            ax,
            x,
            state_y,
            state_w,
            state_h,
            f"{symbol}\n{desc}",
            face=BLUE_BG,
            edge=BLUE,
            size=6.0,
        )

    ax.text(
        0.5,
        0.382,
        r"$\mathsf{Recover}\ C(f)$",
        ha="center",
        color=RED,
        fontsize=7.3,
    )

    out_y, out_h = 0.205, 0.12
    box(
        ax,
        0.07,
        out_y,
        0.39,
        out_h,
        "Workspace survivors\nreconstruct V from H without C(f)",
        face=GOLD_BG,
        edge=GOLD,
        size=5.8,
    )
    box(
        ax,
        0.54,
        out_y,
        0.39,
        out_h,
        "Context survivors\nrewind M and remove stale results",
        face=GOLD_BG,
        edge=GOLD,
        size=5.8,
    )
    arrow(ax, (0.47, 0.363), (0.34, out_y + out_h), color=RED, lw=0.95)
    arrow(ax, (0.53, 0.363), (0.66, out_y + out_h), color=RED, lw=0.95)

    ax.text(
        0.50,
        0.155,
        "same retained step set",
        color=GREEN,
        fontsize=6.7,
        ha="center",
        fontweight="bold",
    )
    ax.plot([0.26, 0.74], [0.178, 0.178], color=GREEN, lw=1.0)
    ax.scatter(
        [0.26, 0.74],
        [0.178, 0.178],
        s=13,
        facecolor="white",
        edgecolor=GREEN,
        linewidth=1.0,
        zorder=5,
    )

    box(
        ax,
        0.05,
        0.035,
        0.90,
        0.075,
        "Invariant: filesystem state and agent-visible context agree after rollback",
        face="#f5f6f7",
        edge=GRAY,
        size=5.5,
        weight="bold",
    )

    for out_dir in output_dirs:
        out_dir.mkdir(parents=True, exist_ok=True)
        fig.savefig(
            out_dir / "FIG-AgentTX-Overview.pdf",
            bbox_inches="tight",
            pad_inches=0.02,
            metadata={"CreationDate": None, "ModDate": None},
        )
        fig.savefig(
            out_dir / "FIG-AgentTX-Overview.png",
            dpi=300,
            bbox_inches="tight",
            pad_inches=0.02,
        )
        print("wrote", out_dir / "FIG-AgentTX-Overview.pdf")
    plt.close(fig)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    draw([root / "motivation", root / "paper" / "img"])


if __name__ == "__main__":
    main()
