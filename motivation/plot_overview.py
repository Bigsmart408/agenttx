#!/usr/bin/env python3
"""Draw the AgentTX / AET overview figure (concept-first, two layers)."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch


def cm_to_inch(v: float) -> float:
    return v / 2.54


def configure_style() -> None:
    plt.rcParams.update(plt.rcParamsDefault)
    matplotlib.rcParams["text.usetex"] = False
    plt.rcParams["font.family"] = "Nimbus Roman"
    plt.rcParams["axes.linewidth"] = 0.6
    plt.rcParams["pdf.fonttype"] = 42
    plt.rcParams["ps.fonttype"] = 42


def rounded(ax, xy, w, h, fc, ec="#333333", lw=0.8, r=0.02, z=2):
    patch = FancyBboxPatch(
        xy,
        w,
        h,
        boxstyle="round,pad=0.008,rounding_size=%.3f" % r,
        facecolor=fc,
        edgecolor=ec,
        linewidth=lw,
        zorder=z,
    )
    ax.add_patch(patch)
    return patch


def arrow(ax, p0, p1, color="#333333", lw=0.9, style="-|>", rad=0.0, ms=10):
    arr = FancyArrowPatch(
        p0,
        p1,
        arrowstyle=style,
        mutation_scale=ms,
        linewidth=lw,
        color=color,
        connectionstyle="arc3,rad=%.3f" % rad,
        zorder=3,
    )
    ax.add_patch(arr)
    return arr


def draw(output_dirs):
    configure_style()
    fig = plt.figure(figsize=(cm_to_inch(17.8), cm_to_inch(8.6)), dpi=300)
    ax = fig.add_axes([0.01, 0.02, 0.98, 0.96])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    red = "#c00000"
    green = "#2e7d32"
    gray = "#666666"
    light_red = "#f8e0e0"
    light_green = "#e4f0e4"
    light_blue = "#e8eef6"
    light_gray = "#f3f3f3"
    border = "#333333"

    rounded(ax, (0.015, 0.56), 0.97, 0.42, "#ffffff", ec="#bbbbbb", lw=0.7, r=0.015, z=0)
    ax.text(
        0.03,
        0.94,
        "(a) Recovery unit: temporal suffix vs causal subgraph",
        fontsize=8.5,
        fontweight="bold",
        va="top",
        zorder=5,
    )

    rounded(ax, (0.015, 0.03), 0.97, 0.50, "#ffffff", ec="#bbbbbb", lw=0.7, r=0.015, z=0)
    ax.text(
        0.03,
        0.50,
        "(b) Agent Effect Transaction and AgentTX realization",
        fontsize=8.5,
        fontweight="bold",
        va="top",
        zorder=5,
    )

    ax.text(0.06, 0.885, "Trajectory (shared speculation)", fontsize=7.2, color=gray)

    nodes = [
        ("s0", "faulty\nwrite", 0.12),
        ("s1", "derived\nreport", 0.30),
        ("s2", "independent\nedit", 0.48),
        ("s3", "test\nfail", 0.66),
    ]
    ny = 0.78
    nw, nh = 0.12, 0.10
    centers = {}
    for name, subtitle, x in nodes:
        if name == "s2":
            fc, ec = light_green, green
        else:
            fc, ec = light_red, red
        rounded(ax, (x, ny), nw, nh, fc, ec=ec, lw=1.0, r=0.02, z=4)
        ax.text(
            x + nw / 2,
            ny + nh * 0.62,
            name,
            ha="center",
            va="center",
            fontsize=8,
            fontweight="bold",
            zorder=5,
        )
        ax.text(
            x + nw / 2,
            ny + nh * 0.28,
            subtitle,
            ha="center",
            va="center",
            fontsize=6.2,
            color="#222222",
            zorder=5,
        )
        centers[name] = (x + nw / 2, ny + nh / 2)

    # Light time-order baseline (not causal)
    ax.annotate(
        "",
        xy=(centers["s3"][0] - 0.06, ny - 0.015),
        xytext=(centers["s0"][0] + 0.06, ny - 0.015),
        arrowprops=dict(arrowstyle="-|>", color="#bbbbbb", lw=0.7),
    )
    ax.text(0.39, ny - 0.035, "time order", fontsize=5.5, color="#999999", ha="center")

    # Causal dependency edges only
    for a, b in [("s0", "s1"), ("s1", "s3")]:
        x0, y0 = centers[a]
        x1, y1 = centers[b]
        arrow(ax, (x0 + 0.06, y0), (x1 - 0.06, y1), color=red, lw=0.95, ms=9)

    ax.text(0.21, 0.835, "dep.", fontsize=5.8, color=red, ha="center")
    ax.text(
        centers["s2"][0],
        ny + nh + 0.012,
        "independent",
        fontsize=5.8,
        color=green,
        ha="center",
    )

    rounded(ax, (0.805, 0.815), 0.15, 0.095, light_gray, ec=gray, lw=0.8, r=0.02, z=4)
    ax.text(
        0.88,
        0.875,
        "Host FS",
        ha="center",
        va="center",
        fontsize=7.2,
        fontweight="bold",
        zorder=5,
    )
    ax.text(
        0.88,
        0.838,
        "unchanged until\nfinalize",
        ha="center",
        va="center",
        fontsize=5.5,
        color=gray,
        zorder=5,
    )

    ax.text(0.06, 0.675, "Temporal recovery", fontsize=7, fontweight="bold", color=gray)
    for name, x in [("s0", 0.12), ("s1", 0.30), ("s2", 0.48), ("s3", 0.66)]:
        rounded(ax, (x, 0.610), 0.12, 0.045, light_red, ec=red, lw=0.7, r=0.015, z=4)
        label = "remove" if name != "s2" else "also lost"
        ax.text(
            x + 0.06,
            0.632,
            "%s: %s" % (name, label),
            ha="center",
            va="center",
            fontsize=5.8,
            zorder=5,
        )
    ax.text(0.80, 0.632, "loses s2", fontsize=6.2, color=red, va="center", ha="left")

    ax.text(
        0.06,
        0.590,
        "Causal recovery (ours)",
        fontsize=7,
        fontweight="bold",
        color=border,
    )
    for name, x, keep in [
        ("s0", 0.12, False),
        ("s1", 0.30, False),
        ("s2", 0.48, True),
        ("s3", 0.66, False),
    ]:
        if keep:
            rounded(
                ax, (x, 0.525), 0.12, 0.045, light_green, ec=green, lw=0.9, r=0.015, z=4
            )
            ax.text(
                x + 0.06,
                0.547,
                "%s: retain" % name,
                ha="center",
                va="center",
                fontsize=5.8,
                zorder=5,
            )
        else:
            rounded(
                ax, (x, 0.525), 0.12, 0.045, light_red, ec=red, lw=0.7, r=0.015, z=4
            )
            ax.text(
                x + 0.06,
                0.547,
                "%s: remove" % name,
                ha="center",
                va="center",
                fontsize=5.8,
                zorder=5,
            )
    ax.text(0.80, 0.547, "keeps s2", fontsize=6.2, color=green, va="center", ha="left")

    ax.text(0.06, 0.455, r"AET state  $\langle V, L, H, F\rangle$", fontsize=7.5, fontweight="bold")

    boxes = [
        ("V\nspeculative view", 0.06, light_blue),
        ("L\ncausal ledger", 0.28, light_blue),
        ("H\nhistorical states", 0.50, light_blue),
        ("F\napproval frontier", 0.72, light_blue),
    ]
    by, bw, bh = 0.33, 0.18, 0.095
    for title, x, fc in boxes:
        rounded(ax, (x, by), bw, bh, fc, ec="#3a5a8c", lw=0.9, r=0.02, z=4)
        ax.text(
            x + bw / 2,
            by + bh / 2,
            title,
            ha="center",
            va="center",
            fontsize=7,
            zorder=5,
        )

    ax.text(0.06, 0.445, "tool call", fontsize=6.0, color="#3a5a8c", ha="left")
    arrow(ax, (0.12, 0.445), (0.145, 0.425), color="#3a5a8c", lw=0.8, ms=8)
    ax.text(0.18, 0.448, "Append", fontsize=6.5, color="#3a5a8c", ha="left")
    # Recover uses L and H to rewrite V
    arrow(ax, (0.37, 0.355), (0.24, 0.355), color=red, lw=0.9, rad=0.0, ms=9)
    ax.text(0.305, 0.368, "Recover", fontsize=6.5, color=red, ha="center")
    arrow(ax, (0.50, 0.355), (0.41, 0.355), color=red, lw=0.8, style="-", ms=1)

    rounded(ax, (0.80, 0.20), 0.155, 0.08, light_gray, ec=gray, lw=0.8, r=0.02, z=4)
    ax.text(
        0.877,
        0.255,
        "Host FS",
        ha="center",
        va="center",
        fontsize=7.5,
        fontweight="bold",
        zorder=5,
    )
    ax.text(
        0.877,
        0.220,
        "publish approved\nversions only",
        ha="center",
        va="center",
        fontsize=5.6,
        color=gray,
        zorder=5,
    )
    arrow(ax, (0.81, 0.355), (0.85, 0.28), color=green, lw=0.95, ms=9)
    ax.text(0.90, 0.325, "Finalize", fontsize=6.5, color=green, ha="left")

    rounded(ax, (0.06, 0.255), 0.70, 0.055, "#fafafa", ec="#cccccc", lw=0.6, r=0.015, z=3)
    ax.text(
        0.41,
        0.282,
        "Invariants: host clean before finalize  ·  remove C(f), keep non-overlapping "
        "effects  ·  frontier monotonic",
        ha="center",
        va="center",
        fontsize=6.0,
        zorder=5,
    )

    ax.text(0.06, 0.225, "AgentTX realization", fontsize=7.5, fontweight="bold")
    impl = [
        ("Tool-boundary\ncapture", 0.06),
        ("Shared\nsemisolate", 0.24),
        ("Snapshots +\nobject identity", 0.42),
        ("Selective\nreconstruction", 0.60),
        ("Policy + WAL\npublication", 0.78),
    ]
    iy, iw, ih = 0.06, 0.155, 0.12
    for i, (label, x) in enumerate(impl):
        rounded(ax, (x, iy), iw, ih, "#fff8e8", ec="#9a7b2f", lw=0.8, r=0.02, z=4)
        ax.text(
            x + iw / 2,
            iy + ih / 2,
            label,
            ha="center",
            va="center",
            fontsize=6.4,
            zorder=5,
        )
        if i < len(impl) - 1:
            arrow(
                ax,
                (x + iw + 0.002, iy + ih / 2),
                (impl[i + 1][1] - 0.002, iy + ih / 2),
                color="#9a7b2f",
                lw=0.8,
                ms=8,
            )

    for x0, x1 in [(0.15, 0.137), (0.37, 0.317), (0.59, 0.497), (0.81, 0.857)]:
        ax.plot([x0, x1], [0.325, 0.185], color="#cccccc", lw=0.5, ls=":", zorder=1)

    for out in output_dirs:
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        fig.savefig(
            out / "FIG-Overview.pdf",
            bbox_inches="tight",
            pad_inches=0.03,
            metadata={"CreationDate": None, "ModDate": None},
        )
        fig.savefig(
            out / "FIG-Overview.png", dpi=300, bbox_inches="tight", pad_inches=0.03
        )
        print("wrote", out / "FIG-Overview.pdf")
    plt.close(fig)


def main():
    root = Path(__file__).resolve().parents[1]
    draw([root / "motivation", root / "paper" / "img"])


if __name__ == "__main__":
    main()
