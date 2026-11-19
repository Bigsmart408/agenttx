#!/usr/bin/env python3
"""Compatibility entry point for the real-workload per-task localization figure."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from motivation.plot_realworld_localization_per_task import main
if __name__ == "__main__":
    main()
