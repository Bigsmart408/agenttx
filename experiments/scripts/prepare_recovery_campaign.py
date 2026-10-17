#!/usr/bin/env python3
"""Register future cells and pilot selection without executing experiments."""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from experiments.scripts.bench_external_recovery_artifacts import motivation_400_cases
from experiments.scripts.experiment_provenance import source_manifest

MICRO_POLICIES = ("clean_recovery", "crash_no_rollback", "turn_checkpoint", "temporal_checkpoint", "whole_branch_abort", "causal", "ideal_oracle")
OFFICIAL_POLICIES = ("clean_recovery", "crash_no_rollback", "temporal_checkpoint", "whole_branch_abort", "causal")


def write_cells(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error("output directory must be empty; keep prior registrations immutable")
    cases = motivation_400_cases()  # Validates exact registered hashes against all three source CSVs.
    assert len(cases) == 400 and len({c.plan.trace_id for c in cases}) == 400
    families = defaultdict(list)
    micro = []
    for case in cases:
        families[case.suite_slice].append(case)
        for policy in MICRO_POLICIES:
            for repeat in range(3):
                micro.append(dict(suite_slice=case.suite_slice, trace_id=case.plan.trace_id,
                                  manifest_hash=case.plan.manifest_hash, policy=policy,
                                  repeat=repeat, state="registered_not_run"))
    pilot = []
    for family in sorted(families):
        group = families[family]
        for index in (0, len(group) // 3, 2 * len(group) // 3, len(group) - 1):
            case = group[index]
            for policy in MICRO_POLICIES:
                pilot.append(dict(suite_slice=family, trace_id=case.plan.trace_id,
                                  manifest_hash=case.plan.manifest_hash, policy=policy,
                                  repeat=0, state="registered_not_run"))
    old_config = ROOT / "experiments/results/codex_luna_max_realworld_5mode_full_20260930/run_config.json"
    tasks = json.loads(old_config.read_text())["tasks"]
    assert len(tasks) == len(set(tasks)) == 541
    assert sum(t.startswith("swe:") for t in tasks) == 300
    assert sum(t.startswith("tb:") for t in tasks) == 241
    official = [dict(suite=task.split(":", 1)[0], task=task.split(":", 1)[1], harness=harness,
                     policy=policy, repeat=0, state="registered_not_run")
                for task in tasks for harness in ("codex", "sweagent") for policy in OFFICIAL_POLICIES]
    assert len(micro) == 8400 and len(pilot) == 84 and len(official) == 5410
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in (("system_micro_cells.csv", micro), ("system_micro_pilot_cells.csv", pilot), ("official_cells.csv", official)):
        write_cells(args.output_dir / name, rows)
    sources = source_manifest(ROOT)
    (args.output_dir / "source_manifest.json").write_text(json.dumps(sources, indent=2) + "\n")
    registration = dict(
        schema="agenttx-recovery-campaign-registration-v1", status="registered_not_run",
        system_micro_cells=len(micro), pilot_cells=len(pilot), official_cells=len(official),
        pilot_selection="For each registered family, choose indices 0, floor(n/3), floor(2n/3), n-1 in canonical generator order; seven policies, one repeat. Diagnostic only.",
        source_manifest_sha256=sources["sha256"], historical_model="gpt-5.6-luna", reasoning_effort="max",
        model_availability_verified=False, official_count_caps=None,
        localization_protocol="causal_localizer_coarse_hidden_oracle_registered_truth_v1",
        gates_before_launch=[
            "Complete the actual runtime+model system runner: bench_intra_turn_replay currently edits artifacts directly and is not a validated full-runtime measurement.",
            "Pin current model access, prompt text/hash, fault schedule, token/wall budget and usage collection for both harnesses.",
            "Resolve every official task version and evaluator image ID; old task IDs are a scope list, not a complete frozen dataset.",
            "Implement image-native formal Terminal-Bench agent path; retain host-projected rejection guard.",
            "Pass backend correctness gates and record the validated environment lock.",
        ],
        files={name: hashlib.sha256((args.output_dir / name).read_bytes()).hexdigest()
               for name in ("system_micro_cells.csv", "system_micro_pilot_cells.csv", "official_cells.csv")},
    )
    (args.output_dir / "registration.json").write_text(json.dumps(registration, indent=2) + "\n")
    print(json.dumps({k: registration[k] for k in ("status", "system_micro_cells", "pilot_cells", "official_cells", "source_manifest_sha256")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
