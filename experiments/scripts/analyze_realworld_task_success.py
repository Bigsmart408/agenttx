#!/usr/bin/env python3
"""Count task-test successes separately from additional recovery-contract checks."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from scripts.maintenance.watch_rq2_comparisons import history_summary

BASELINE = Path("/mnt/data/agenttx-runtime/results/rq2-turn-checkpoint-20261111-r2/comparison-monitor/baseline.json")
TURN = ROOT / "review/rq2-turn-checkpoint-final-20261114/attempt-accounting.json"
TRY = ROOT / "review/try-realworld-paper-20261118/analysis.json"
OUTPUT = ROOT / "review/realworld-task-success-20261118"
POLICIES = ("causal", "turn_checkpoint", "temporal_checkpoint", "whole_branch_abort")


def main():
    sources = {}

    def read(path, expected=None):
        path = Path(path)
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if expected is not None:
            assert digest == expected, str(path)
        sources[str(path)] = digest
        return json.loads(content)

    baseline = read(BASELINE)
    manifest = read(BASELINE.with_name("baseline-manifest.json"))
    assert sources[str(BASELINE)] == manifest["sha256"]
    turns = defaultdict(list)
    for attempt in read(TURN):
        turns[attempt["cell"]].append(attempt)
    turn_cells = {key: history_summary(sorted(rows, key=lambda r:r["attempt_number"]))
                  for key, rows in turns.items()}
    supplement = read(TRY)
    result = dict(metric="Task-test success: tests_ok for SWE, official_success for TB; additional recovery checks are recorded separately.",
                  attempt_selection="One terminal task outcome per method; costs retain the existing success-only pair selection.",
                  new_model_calls=0, cohorts={})
    for benchmark in ("swe", "tb"):
        for harness in ("sweagent", "codex"):
            cohort = benchmark + "_" + harness
            prefix = benchmark + "/" + harness + "/"
            groups = result["cohorts"][cohort] = {}
            for policy in POLICIES:
                rows = [row for key, row in (turn_cells if policy == "turn_checkpoint" else baseline["cells"]).items()
                        if key.startswith(prefix) and (policy == "turn_checkpoint" or key.endswith("/" + policy))]
                assert len({row["task"] for row in rows}) == len(rows)
                tasks = []
                for row in rows:
                    raw = read(row["result_path"], row["result_sha256"])
                    tasks.append(dict(task=row["task"], state=row["state"],
                        official_success=raw.get("tests_ok" if benchmark == "swe" else "official_success") is True,
                        full_contract_success=row["success"], result_path=row["result_path"],
                        result_sha256=row["result_sha256"]))
                groups[policy] = dict(planned=300 if benchmark == "swe" else 241,
                    observed=len(tasks), states=dict(Counter(t["state"] for t in tasks)),
                    official_success=sum(t["official_success"] for t in tasks),
                    full_contract_success=sum(t["full_contract_success"] for t in tasks), tasks=tasks)
            extra = supplement["outcomes"][cohort]
            groups["root_only"] = dict(planned=extra["planned"], observed=extra["valid"],
                states=extra["states"], official_success=extra["official_success"],
                full_contract_success=extra["success"])
            print(cohort, {p:g["official_success"] for p,g in groups.items()})
    result["sources"] = sources
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "analysis.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
