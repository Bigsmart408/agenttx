#!/usr/bin/env python3
"""Run randomized multi-crash localization cases with bounded parallelism."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import random
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--log-dir", type=Path, required=True)
    parser.add_argument("--cases", type=int, default=50)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--crashes", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260911)
    parser.add_argument(
        "--python",
        default="/home/bfq/miniconda3/envs/agenttx/bin/python3.11",
    )
    args = parser.parse_args()
    if min(args.cases, args.workers, args.crashes) < 1:
        parser.error("cases, workers, and crashes must all be positive")

    manifest = ROOT / "experiments" / "cache" / "swe_bench" / "lite_test_manifest.json"
    task_ids = sorted(row["instance_id"] for row in json.loads(manifest.read_text()))
    if args.cases > len(task_ids):
        parser.error(f"requested {args.cases} cases from a {len(task_ids)}-case manifest")
    selected = sorted(random.Random(args.seed).sample(task_ids, args.cases))
    args.result_dir.mkdir(parents=True, exist_ok=True)
    args.log_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "cases": args.cases,
        "workers": args.workers,
        "crashes_per_case": args.crashes,
        "seed": args.seed,
        "tasks": selected,
    }
    (args.result_dir / "run_config.json").write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    def run_case(task_id: str) -> tuple[str, int]:
        output = args.result_dir / f"{task_id}.json"
        log = args.log_dir / f"{task_id}.log"
        if output.exists():
            try:
                prior = json.loads(output.read_text(encoding="utf-8"))
                if int(prior.get("crashes_requested", 0)) == args.crashes:
                    return task_id, 0 if prior.get("success") else 1
            except (OSError, ValueError, TypeError):
                pass
        command = [
            args.python,
            str(ROOT / "experiments" / "scripts" / "smoke_auto_fault_localization.py"),
            "--task",
            task_id,
            "--python",
            args.python,
            "--seed",
            str(args.seed),
            "--crashes",
            str(args.crashes),
            "--output",
            str(output),
        ]
        with log.open("a", encoding="utf-8") as handle:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                stdout=handle,
                stderr=subprocess.STDOUT,
                check=False,
            )
        return task_id, completed.returncode

    failures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_case, task): task for task in selected}
        for completed, future in enumerate(
            concurrent.futures.as_completed(futures), start=1
        ):
            task, returncode = future.result()
            if returncode != 0:
                failures.append(task)
            print(
                json.dumps(
                    {
                        "completed": completed,
                        "total": len(selected),
                        "task": task,
                        "returncode": returncode,
                        "failures": len(failures),
                    }
                ),
                flush=True,
            )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
