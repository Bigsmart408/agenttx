#!/usr/bin/env python3
"""Run 5 SWE-Bench + 5 Terminal-Bench crash-in-the-middle recovery cells.

Compares causal rollback vs temporal/whole checkpoint: official-test success
after oracle repair, independent-doc retention, and live tokens spent replaying
documents that checkpoint policies discarded.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from experiments.scripts import bench_official_tasks as bench
from experiments.workloads import swe_bench_suite as swe
from experiments.workloads import terminal_bench_suite as tb

SWE_PREFERRED = [
    "pallets__flask-4992",
    "pylint-dev__pylint-5859",
    "django__django-10914",
    "django__django-10924",
    "django__django-11039",
]
TB_PREFERRED = [
    "hello-world",
    "csv-to-parquet",
    "log-summary",
    "analyze-access-logs",
    "bank-trans-filter",
]


def _fill(preferred, catalog, limit):
    selected = [name for name in preferred if name in catalog]
    extras = sorted(
        catalog,
        key=lambda name: (
            {"short": 0, "medium": 1, "long": 2}.get(getattr(catalog[name], "scale", "medium"), 1),
            name,
        ),
    )
    for name in extras:
        if len(selected) >= limit:
            break
        if name not in selected:
            selected.append(name)
    return selected[:limit]


def main() -> int:
    cache = ROOT / "experiments" / "cache"
    swe_catalog = swe.load_tasks(cache)
    tb.ensure_tb_repo(cache)
    tb_catalog = tb.load_tasks(cache)
    # Always include the three in-tree TB snapshots even before full clone extras.
    tb_catalog = {**tb_catalog, **tb.TASKS}
    swe_tasks = _fill(SWE_PREFERRED, swe_catalog, 5)
    tb_tasks = _fill(TB_PREFERRED, tb_catalog, 5)
    print("SWE sample:", swe_tasks, flush=True)
    print("TB sample:", tb_tasks, flush=True)
    argv = [
        "--task-set", "selected",
        "--tasks", *swe_tasks, *tb_tasks,
        "--modes", "causal", "temporal_checkpoint", "whole_branch_abort",
        "--oracle",
        "--replay-docs",
        "--repeats", "1",
        "--result-subdir", "crash_sample_10",
        "--python", os.environ.get("AGENTTX_PYTHON", bench.DEFAULT_PYTHON),
    ]
    if os.environ.get("AGENTTX_PROVIDER"):
        argv.extend(["--provider", os.environ["AGENTTX_PROVIDER"]])
    return bench.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
