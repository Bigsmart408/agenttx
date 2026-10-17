#!/usr/bin/env python3
"""Gate a live-agent smoke run before starting the full official catalog."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("--expected-cells", type=int, required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()

    config = json.loads((args.result_dir / "run_config.json").read_text(encoding="utf-8"))
    failures: list[str] = []
    if config.get("model") != args.model or config.get("reasoning_effort") != "max":
        failures.append("model or reasoning effort differs from the registered run")
    if config.get("expected_cells") != args.expected_cells:
        failures.append("configured cell count differs from the smoke gate")

    rows: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for path in sorted((args.result_dir / "shards").glob("shard_*/official_tasks_raw.csv")):
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                key = (row["suite"], row["task"], row["mode"], row["repeat"])
                rows[key] = row
    if len(rows) != args.expected_cells:
        failures.append(f"only {len(rows)}/{args.expected_cells} distinct cells have results")

    for key, row in rows.items():
        label = ":".join(key)
        if row.get("harness_backend") != "codex" or row.get("model") != args.model:
            failures.append(f"{label}: wrong harness or model")
        if row.get("environment_error") != "False":
            failures.append(f"{label}: environment error")
        if row.get("finished") != "True" or row.get("finish_called") != "True":
            failures.append(f"{label}: unfinished agent run")
        if row.get("usage_source") in (None, "", "none") or int(row.get("total_tokens") or 0) <= 0:
            failures.append(f"{label}: model token usage missing")
        if row.get("tests_ok") not in ("True", "False"):
            failures.append(f"{label}: official verifier result missing")
        if row["mode"] in {"causal", "clean_recovery"} and row.get("recovery_context_variant") != "compact":
            failures.append(f"{label}: compact recovery context missing")

    if failures:
        print("smoke gate failed:\n" + "\n".join(failures[:20]), flush=True)
        return 1
    print(f"smoke gate passed: {len(rows)} Codex cells", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
