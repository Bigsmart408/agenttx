#!/usr/bin/env python3
"""Compatibility alias for the official application figure."""

from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
runpy.run_path(str(ROOT / "experiments" / "scripts" / "plot_official_tasks.py"), run_name="__main__")
