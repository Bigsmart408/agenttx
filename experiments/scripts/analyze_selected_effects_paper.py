#!/usr/bin/env python3
"""Audit the terminal try-scope cohort and export matched paper data."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from experiments.scripts.analyze_realworld_final import bootstrap, comparison
from scripts.maintenance.watch_rq2_comparisons import project, history_summary

COHORT = Path("/var/tmp/agenttx-selected-effects/results/selected-effects-full-20261116-r1")
BASELINE = Path("/mnt/data/agenttx-runtime/results/rq2-turn-checkpoint-20261111-r2/comparison-monitor/baseline.json")
OUTPUT = ROOT / "review/try-realworld-paper-20261118"
CORE = ("protocol", "completion_contract", "model_session_wall_s", "verifier_wall_s")


def swe_contract(path, task, read):
    """Resolve immutable task images and budgets from the dispatch registration."""
    for parent in Path(path).parents:
        registration = parent / "registration.json"
        if registration.is_file():
            data = read(registration)
            images = data.get("swe_images", data.get("images", []))
            image = next(item for item in images if item["task"] == task)
            return dict(image_id=image["image_id"], base_commit=image["base_commit"],
                        model=data["model"], reasoning_effort=data["reasoning_effort"],
                        model_timeout_s=data.get("swe_model_timeout_s", data.get("model_wall_timeout_per_session_s")))
    raise FileNotFoundError("Missing SWE dispatch registration for " + str(path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", type=Path, default=COHORT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    sources, cache = {}, {}

    def read(path, expected=None):
        path = Path(path)
        key = str(path)
        if key not in cache:
            raw = path.read_bytes()
            sources[key] = hashlib.sha256(raw).hexdigest()
            cache[key] = json.loads(raw)
        if expected is not None:
            assert sources[key] == expected, key
        return cache[key]

    cells = read(args.cohort / "cells.json")
    registration = read(args.cohort / "registration.json")
    baseline = read(BASELINE)
    manifest = read(BASELINE.with_name("baseline-manifest.json"))
    assert sources[str(BASELINE)] == manifest["sha256"]
    assert len(cells) == len({c["key"] for c in cells}) == registration["expected_cells"] == 1082
    assert all(c["state"] in {"completed", "infrastructure_error"} for c in cells)
    outcomes, selected = {}, {}
    for group in ("swe/sweagent", "swe/codex", "tb/sweagent", "tb/codex"):
        group_cells = [c for c in cells if c["key"].startswith(group + "/")]
        states = dict(Counter(c["state"] for c in group_cells))
        outcome = dict(planned=len(group_cells), states=states,
                       valid=states.get("completed", 0), success=0, official_success=0)
        for cell in group_cells:
            attempts = cell["attempts"]
            raw = [read(a["result_path"], a["result_sha256"]) for a in attempts]
            assert raw
            if cell["state"] != "completed":
                continue
            final = raw[-1]
            assert final["task"] == cell["task"]
            outcome["success"] += final.get("success") is True
            official = "tests_ok" if cell["benchmark"] == "swe" else "official_success"
            outcome["official_success"] += final.get(official) is True
            rows = []
            for attempt, result in zip(attempts, raw):
                row = project(result, Path(attempt["result_path"]), cell["benchmark"], cell["harness"],
                              "infrastructure_error" if attempt.get("infrastructure_issue") else "completed",
                              attempt["result_sha256"])
                assert row["policy"] == "root_only" and row["context"] == "full"
                rows.append(row)
            selected[cell["key"]] = dict(history_summary(rows), records=rows)
        outcomes[group.replace("/", "_")] = outcome

    def phases(attempts, agenttx):
        total = defaultdict(float)
        for attempt in attempts:
            raw = read(attempt["result_path"], attempt["result_sha256"])
            if "timings" in raw:
                t = raw["timings"]
                loc = raw.get("localization") or {}
            else:
                t = dict(full_wall_s=raw["wall_s"], model_wall_s=raw["official_model_wall_s"],
                         verification_wall_s=raw["official_grade_wall_s"], policy_wall_s=raw["policy_wall_s"])
                loc = raw
            excluded = (loc["localization_probe_wall_s"] + loc["localization_search_wall_s"]
                        if agenttx else 0.0)
            full = t["full_wall_s"] - excluded
            preparation = full - t["model_wall_s"] - t["verification_wall_s"]
            assert min(full, preparation, t["model_wall_s"], t["verification_wall_s"]) >= 0
            values = dict(total_s=full, preparation_replay_s=preparation,
                          agent_execution_s=t["model_wall_s"], verification_s=t["verification_wall_s"],
                          policy_s=t["policy_wall_s"] - excluded,
                          live_apply_s=loc.get("localization_apply_wall_s", 0) if agenttx else 0,
                          excluded_localization_s=excluded, recorded_s=t["full_wall_s"])
            assert all(np.isfinite(v) for v in values.values())
            for key, value in values.items():
                total[key] += value
        assert np.isclose(total["total_s"], sum(total[k] for k in
                          ("preparation_replay_s", "agent_execution_s", "verification_s")))
        return dict(total, attempts=len(attempts))

    tokens, times, stages, exclusions = {}, {}, {}, {}
    for index, group in enumerate(("swe/sweagent", "swe/codex", "tb/sweagent", "tb/codex")):
        pairs, time_pairs, stage_pairs = [], [], []
        excluded = Counter()
        for key, row in sorted(selected.items()):
            if not key.startswith(group + "/"):
                continue
            a = baseline["cells"].get(key + "/causal")
            swe = group.startswith("swe/")
            ra = read(a["result_path"], a["result_sha256"]) if a is not None else {}
            rb = read(row["result_path"], row["result_sha256"])
            success_a = ra.get("tests_ok") is True if swe else a is not None and a["success"]
            success_b = rb.get("tests_ok") is True if swe else row["success"]
            reason = None
            if a is None or a["state"] != "completed":
                reason = "missing_valid_agenttx"
            elif not success_a or not success_b:
                reason = "not_both_task_verified" if swe else "not_both_full_contract_successful"
            elif not row["complete_history"] or not a["complete_history"]:
                reason = "incomplete_usage_or_infrastructure_history"
            elif row["mixed_configuration"] or a["mixed_configuration"]:
                reason = "mixed_configuration_history"
            elif row["model"] != a["model"] or row["trial_seed"] != a["trial_seed"]:
                reason = "model_or_fault_mismatch"
            elif not swe and any(row["configuration"].get(k) != a["configuration"].get(k) for k in CORE):
                reason = "protocol_or_budget_mismatch"
            if reason:
                excluded[reason] += 1
                continue
            same_environment = (swe_contract(a["result_path"], row["task"], read) ==
                                swe_contract(row["result_path"], row["task"], read)
                                if swe else ra.get("image_id") == rb.get("image_id"))
            if not same_environment:
                excluded["image_or_budget_mismatch"] += 1
                continue
            cid = ("swe-single-attempt" if swe else
                   hashlib.sha256((a["configuration_id"] + "/" + row["configuration_id"]).encode()).hexdigest())
            identity = dict(task=row["task"], configuration_id=cid)
            pairs.append(dict(identity, causal_tokens=a["cumulative_tokens"], control_tokens=row["cumulative_tokens"]))
            sides = dict(causal=phases(baseline["attempts"][key + "/causal"], True),
                         control=phases(row["records"], False))
            stage_pairs.append(dict(identity, **sides))
            time_pairs.append(dict(identity, causal_s=sides["causal"]["total_s"],
                                   control_s=sides["control"]["total_s"]))
        cohort = group.replace("/", "_")
        assert pairs
        tokens[cohort] = comparison(pairs, 20261118 + index, 20000)
        time_samples = [dict(p, causal_tokens=p["causal_s"], control_tokens=p["control_s"]) for p in time_pairs]
        totals = {side: sum(p[side + "_s"] for p in time_pairs) for side in ("causal", "control")}
        times[cohort] = dict(n=len(time_pairs), pairs=time_pairs,
            causal_total_s=totals["causal"], control_total_s=totals["control"],
            causal_mean_s=totals["causal"] / len(pairs), control_mean_s=totals["control"] / len(pairs),
            saving_pct=100 * (1 - totals["causal"] / totals["control"]),
            ci95_pct=np.quantile(bootstrap(time_samples, 20261120 + index, 20000), [.025, .975]).tolist())
        means = {}
        for side in ("causal", "control"):
            means[side] = {k: float(np.mean([p[side][k] for p in stage_pairs]))
                           for k in stage_pairs[0][side] if k != "attempts"}
            means[side]["agent_execution_pct"] = 100 * means[side]["agent_execution_s"] / means[side]["total_s"]
        stages[cohort] = dict(n=len(pairs), means=means, pairs=stage_pairs)
        exclusions[cohort] = dict(excluded)

    result = dict(checked_at=datetime.now(timezone.utc).isoformat(), cohort=str(args.cohort),
        terminal=True, states=dict(Counter(c["state"] for c in cells)), outcomes=outcomes,
        token_comparisons=tokens, time_comparisons=times, stage_comparisons=stages,
        exclusions=exclusions, sources=sources, new_model_calls=0,
        population="Control-specific matched tasks: task-test success for SWE try comparisons; full-contract success for TB. Match image, model, fault seed and budgets; pool TB infrastructure strata.",
        accounting="All recorded attempts included; API-reported tokens; subtract full AgentTX localization probe/search stage only, matching RQ3; never impute unknown usage as zero.",
        uncertainty="20,000 paired task bootstrap samples within joint configuration strata, preserving stratum sizes.",
        scope="Exact root-only withdrawal in the shared runtime, displayed as the try-scope control; distinct from native try storage measurements.",
        swe_scoring="SWE root_only prompt omits the derived.txt cleanup instruction required by full-contract scoring. Report recorded full-contract and official outcomes separately; cost pairing does not relabel task-test success or failed tasks as full-contract recovery success.")
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "analysis.json").write_text(json.dumps(result, indent=2) + "\n")
    for cohort in tokens:
        print(cohort, "outcomes", outcomes[cohort])
        print("tokens", {k:v for k,v in tokens[cohort].items() if k not in {"pairs", "strata"}})
        print("time", {k:v for k,v in times[cohort].items() if k != "pairs"})
    print("states", result["states"], "verified sources", len(sources))


if __name__ == "__main__":
    main()
