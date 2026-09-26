#!/usr/bin/env python3
"""Apply the paper palette to the cited per-call schematic bitmap."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from motivation.plot_paper_evaluation import COLORS


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "paper" / "img" / "FIG-Per-Call-Continuous-Speculation.png"
SOURCE = ROOT / "motivation" / "FIG-Per-Call-Continuous-Speculation-Source.png"


def main() -> None:
    if not SOURCE.exists():
        shutil.copyfile(OUTPUT, SOURCE)
    image = Image.open(SOURCE).convert("RGB")
    rgb = np.asarray(image, dtype=np.float32)
    high = rgb.max(axis=2)
    low = rgb.min(axis=2)
    chroma = high - low
    saturation = chroma / np.maximum(high, 1)
    hue = np.zeros_like(high)
    nonzero = chroma > 0
    red = nonzero & (rgb[:, :, 0] == high)
    green = nonzero & ~red & (rgb[:, :, 1] == high)
    blue = nonzero & ~red & ~green
    hue[red] = ((rgb[:, :, 1][red] - rgb[:, :, 2][red]) / chroma[red]) % 6
    hue[green] = (rgb[:, :, 2][green] - rgb[:, :, 0][green]) / chroma[green] + 2
    hue[blue] = (rgb[:, :, 0][blue] - rgb[:, :, 1][blue]) / chroma[blue] + 4
    hue *= 60

    mappings = (
        (((hue < 12) | (hue >= 330)), COLORS["ideal"]),
        (((hue >= 12) & (hue < 50)), COLORS["temporal"]),
        (((hue >= 50) & (hue < 190)), COLORS["whole"]),
        (((hue >= 190) & (hue < 255)), COLORS["checkpoint"]),
        (((hue >= 255) & (hue < 330)), COLORS["agenttx"]),
    )
    output = rgb.copy()
    colored = (saturation > 0.25) & (chroma > 15)
    for region, hex_color in mappings:
        mask = colored & region
        target = np.array([int(hex_color[i:i + 2], 16) for i in (1, 3, 5)])
        weight = saturation[mask, None]
        output[mask] = 255 * (1 - weight) + target * weight
    Image.fromarray(np.uint8(np.rint(output))).save(OUTPUT, optimize=True)


if __name__ == "__main__":
    main()
