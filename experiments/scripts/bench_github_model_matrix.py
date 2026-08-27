#!/usr/bin/env python3
"""Compatibility alias for the official cheap-model matrix."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.scripts.bench_official_model_matrix import main


if __name__ == "__main__":
    raise SystemExit(main())
