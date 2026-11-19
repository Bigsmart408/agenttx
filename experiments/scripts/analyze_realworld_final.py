#!/usr/bin/env python3
"""Freeze auditable real-workload plot data; never modify the runtime results.

SWE uses the existing API-usage audit. TB uses the completed, frozen category
audit and its compatible-configuration pair lists. Failed and retry attempts
remain in TB costs. Unknown usage is never imputed as zero.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
POLICIES = ("crash_no_rollback", "causal", "temporal_checkpoint",
            "whole_branch_abort", "ideal_oracle", "clean_recovery")
CONTROLS = tuple(p for p in POLICIES if p != "causal")
SWE = Path("/mnt/data/agenttx-runtime/validation/swe-paper-comparison-20261019")
TB = Path("/mnt/data/agenttx-runtime/validation/tb-category-effects-20261029/20261030T051423Z")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path):
    return json.loads(path.read_text())


def bootstrap(rows, seed: int, reps: int):
    """Paired task resampling, preserving each observed configuration's n."""
    rng = np.random.default_rng(seed)
    strata = defaultdict(list)
    for row in rows:
        strata[row["configuration_id"]].append(row)
    a_total, b_total = np.zeros(reps), np.zeros(reps)
    for cid, group in sorted(strata.items()):
        group = sorted(group, key=lambda r: r["task"])
        a = np.array([r["causal_tokens"] for r in group], dtype=np.float64)
        b = np.array([r["control_tokens"] for r in group], dtype=np.float64)
        indices = rng.integers(0, len(group), size=(reps, len(group)))
        a_total += a[indices].sum(axis=1)
        b_total += b[indices].sum(axis=1)
    assert np.all(b_total > 0)
    return (100 * (1 - a_total / b_total)).tolist()


def comparison(rows, seed: int, reps: int, existing_ci=None):
    assert rows and len({r["task"] for r in rows}) == len(rows)
    a = np.array([r["causal_tokens"] for r in rows], dtype=np.int64)
    b = np.array([r["control_tokens"] for r in rows], dtype=np.int64)
    assert np.all(a > 0) and np.all(b > 0)
    intervals = (np.quantile(bootstrap(rows, seed, reps), [.025, .975]).tolist()
                 if existing_ci is None else existing_ci)
    # Keep identities and both token counts beside ratios for independent checks.
    sorted_rows = sorted(rows, key=lambda r: r["causal_tokens"] / r["control_tokens"])
    return dict(n=len(rows), causal_tokens=int(a.sum()), control_tokens=int(b.sum()),
                saving_pct=float(100 * (1 - a.sum() / b.sum())),
                median_saving_pct=float(np.median(100 * (1 - a / b))),
                cheaper_tasks=int((a < b).sum()), more_expensive_tasks=int((a > b).sum()),
                equal_cost_tasks=int((a == b).sum()), ci95_pct=intervals,
                bootstrap_seed=seed if existing_ci is None else None,
                bootstrap_reps=reps if existing_ci is None else None,
                ci_source="stratified_task_paired_bootstrap" if existing_ci is None else "existing_swe_task_paired_bootstrap",
                strata={c:sum(r["configuration_id"] == c for r in rows)
                        for c in sorted({r["configuration_id"] for r in rows})},
                pairs=sorted_rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--swe", type=Path, default=SWE)
    parser.add_argument("--tb", type=Path, default=TB)
    parser.add_argument("--output", type=Path, default=ROOT / "review/realworld-results-20261101")
    parser.add_argument("--bootstrap-reps", type=int, default=20_000)
    args = parser.parse_args()
    assert args.bootstrap_reps >= 1000
    args.output.mkdir(parents=True, exist_ok=True)
    inputs = [args.swe / "report.json", args.swe / "phase-accounting.json",
              args.tb / "effects.json", args.tb / "source-progress.json",
              args.tb / "source-retry-report.json"]
    report, phases, effects, progress, retry = map(load, inputs)
    tb_root = Path('/mnt/data/agenttx-runtime/results') / effects['cohort']
    inputs.extend([tb_root / 'registration.json', tb_root / 'execution.json'])
    registration, execution = map(load, inputs[-2:])
    assert execution['finished_at'] and execution['stop_reason'] is None
    assert registration['expected_cells'] == 2892
    assert registration['formal_eligible'] is False
    assert sha(inputs[3]) == effects["source_progress_sha256"]
    assert sha(inputs[4]) == effects["source_report_sha256"]
    assert not progress["active"] and not progress["deferred_pending"]
    assert not progress["stop_reason"]
    assert effects["formal_eligible"] is False
    cells = {p:{} for p in POLICIES}
    for row in phases:
        assert row["task"] not in cells[row["policy"]]
        cells[row["policy"]][row["task"]] = row
    swe_outcomes = {}
    for policy in POLICIES:
        group = report["groups"][policy]
        assert len(cells[policy]) == group["completed"]
        assert sum(r["api_main"] + r["api_replay"] for r in cells[policy].values()) == group["completed_api_tokens"]
        assert sum(bool(r["success"]) for r in cells[policy].values()) == group["success"]
        swe_outcomes[policy] = {k:group[k] for k in ("success", "planned", "completed", "completed_api_tokens", "mean_api_tokens", "main_api_tokens", "replay_api_tokens")}
    comparisons = {}
    for i, policy in enumerate(CONTROLS):
        old = report["comparisons"][policy]
        rows = []
        for task in sorted(cells["causal"].keys() & cells[policy].keys()):
            a, b = cells["causal"][task], cells[policy][task]
            if a["success"] and b["success"]:
                rows.append(dict(task=task, configuration_id="swe-single-attempt",
                                 causal_tokens=a["api_main"] + a["api_replay"],
                                 control_tokens=b["api_main"] + b["api_replay"]))
        c = comparison(rows, 0, args.bootstrap_reps, old["saving_bootstrap_95_ci"])
        assert c["n"] == old["both_success_pairs"]
        assert c["causal_tokens"] == old["causal_tokens"]
        assert c["control_tokens"] == old["control_tokens"]
        assert np.isclose(c["saving_pct"], old["saving_pct"])
        comparisons[policy] = c
    cohorts = {"swe_sweagent":dict(benchmark="SWE-Bench Lite", harness="SWE-agent",
                                 planned_per_policy=300, outcomes=swe_outcomes,
                                 comparisons=comparisons, cohort=report["cohort"],
                                 accounting="One attempt; main and replay API usage.")}

    raw_cache = {}
    all_attempt_sources = {}
    def raw_result(path):
        key = str(path)
        if key not in raw_cache:
            content = Path(path).read_bytes()
            raw_cache[key] = json.loads(content)
            raw = raw_cache[key]
            all_attempt_sources[key] = dict(sha256=hashlib.sha256(content).hexdigest(),
                success=raw.get('success'), official_success=raw.get('official_success'),
                usage=raw.get('api_usage'))
        return raw_cache[key]

    # Verify the recorded bytes of every attempt used by the published cost pairs.
    verified = {}
    for row in effects["pairs"]:
        for side, cell in row["sides"].items():
            assert cell["tokens"] == sum(a["usage"]["total_tokens"] for a in cell["attempts"])
            for attempt in cell["attempts"]:
                path = Path(attempt["result_path"])
                if str(path) not in verified:
                    raw = raw_result(path)
                    assert all_attempt_sources[str(path)]['sha256'] == attempt["result_sha256"], path
                    assert raw["api_usage"] == attempt["usage"]
                    assert raw["api_usage"]["complete"] is True
                    verified[str(path)] = dict(sha256=attempt["result_sha256"], usage=raw["api_usage"])
        assert row["causal_tokens"] == row["sides"]["causal"]["tokens"]
        assert row["control_tokens"] == row["sides"][row["control"]]["tokens"]

    # Latest terminal result determines outcome; never use any-attempt/best-of.
    latest = {}
    histories = defaultdict(list)
    for attempt in progress["tasks"] + progress["retry_tasks"]:
        assert attempt["state"] in ("completed", "infrastructure_error")
        assert attempt["finished_at"] <= retry["checked_at"]
        key = (attempt["task"], attempt["harness"], attempt["policy"])
        histories[key].append(attempt)
        latest[key] = attempt
        raw_result(Path(attempt['result_path']))
    assert len(latest) == 241 * 2 * 6
    assert len(all_attempt_sources) == sum(map(len, histories.values())) == 3736
    known_totals = [(s['usage'] or {}).get('total_tokens') for s in all_attempt_sources.values()]
    # Sum only known returned usage; absent totals stay null in the manifest.
    assert sum(v for v in known_totals if v is not None) == retry['known_cumulative_tokens']
    assert sum(not bool((s['usage'] or {}).get('complete')) for s in all_attempt_sources.values()) == retry['attempts_with_incomplete_usage']
    tb_outcomes = {h:{} for h in ("sweagent", "codex")}
    for harness in tb_outcomes:
        for policy in POLICIES:
            attempts = [v for (t,h,p),v in latest.items() if h == harness and p == policy]
            assert len(attempts) == 241
            raws = [raw_result(Path(v["result_path"])) for v in attempts]
            tb_outcomes[harness][policy] = dict(planned=241,
                success=sum(r.get("success") is True for r in raws),
                official_success=sum(r.get("official_success") is True for r in raws),
                latest_usage_incomplete=sum(not bool((r.get("api_usage") or {}).get("complete")) for r in raws))
        comparisons = {}
        for i, policy in enumerate(CONTROLS):
            rows = [{k:r[k] for k in ("task", "configuration_id", "causal_tokens", "control_tokens", "category_group")}
                    for r in effects["pairs"] if r["harness"] == harness and r["control"] == policy]
            c = comparison(rows, 20261030 + 100 * (harness == "codex") + i, args.bootstrap_reps)
            old = effects["aggregate"][harness][policy]
            assert (c["n"], c["causal_tokens"], c["control_tokens"]) == (old["pairs"], old["causal_tokens"], old["control_tokens"])
            assert np.isclose(c["saving_pct"], old["saving_pct"])
            # Reproduce each compatible-configuration pair list, not just totals.
            for cid, n in c["strata"].items():
                source = retry["cumulative_configuration_groups"][cid]["cumulative_paired_success"][harness][policy]
                selected = [r for r in rows if r["configuration_id"] == cid]
                assert sorted(r["task"] for r in selected) == sorted(source["tasks"])
                assert len(selected) == n == source["both_success_pairs"]
            comparisons[policy] = c
        cohorts[f"tb_{harness}"] = dict(benchmark="Terminal-Bench", harness="SWE-agent" if harness == "sweagent" else "Codex",
            cohort=effects["cohort"], planned_per_policy=241, outcomes=tb_outcomes[harness],
            comparisons=comparisons,
            accounting="Latest full-contract outcome. Costs sum all recorded main/replay/failed/retry attempts; both histories must have complete usage and one identical compatible configuration.")
    assert sum(g["success"] for h in tb_outcomes.values() for g in h.values()) == 2583
    assert sum(g["official_success"] for h in tb_outcomes.values() for g in h.values()) == 2585
    analysis = dict(cohorts=cohorts, policy_order=POLICIES, control_order=CONTROLS,
        sources=[dict(path=str(p), sha256=sha(p)) for p in inputs],
        tb=dict(final_report_at=retry["checked_at"],
                terminal_cells=len(latest), recorded_attempts=sum(map(len,histories.values())),
                latest_outcome_counts=retry["latest_outcome_counts"],
                known_cumulative_tokens=retry["known_cumulative_tokens"],
                attempts_with_incomplete_usage=retry["attempts_with_incomplete_usage"],
                eligible_pairs=len(effects["pairs"]), configuration_groups=len(retry["cumulative_configuration_groups"]),
                nonempty_configuration_groups=len({r["configuration_id"] for r in effects["pairs"]}),
                protocol_validation_only=True, formal_eligible=False,
                categories=effects["categories"], benchmark_tasks_by_category=effects["benchmark_tasks_by_category"]),
        uncertainty=f"TB: {args.bootstrap_reps:,} paired task bootstrap samples within each observed configuration stratum, preserving its sample size; conditional on recorded runs, not repeated-run model variance. SWE: retain the existing task-paired audit intervals.",
        limits=["SWE success is target FAIL_TO_PASS plus recovery/finish/commit checks, not official resolved rate.",
                "TB reports latest outcomes after authorized outcome retries and separately audited environment/provider repairs, not pass@1.",
                "Compact context is used by causal/ideal/clean, full by operational controls; observed effects include context-policy differences.",
                "SWE fault injection uses gold-patch file paths. Do not claim unassisted benchmark performance.",
                "TB has no fixed token budget or full prompt-accounting gate; formal_eligible=false.",
                "Cost pairs are conditioned on both policies succeeding and having compatible complete histories.",
                "Task sets differ by control. Cohort success rates and costs do not establish a cross-harness ranking."])
    (args.output / "analysis.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n")
    with (args.output / "paired-summary.csv").open("w") as out:
        columns = ["cohort", "control", "n", "causal_tokens", "control_tokens", "saving_pct", "ci95_low", "ci95_high", "more_expensive_tasks"]
        writer = csv.DictWriter(out, fieldnames=columns)
        writer.writeheader()
        for cohort, data in cohorts.items():
            for control, c in data["comparisons"].items():
                writer.writerow(dict(cohort=cohort, control=control, **{k:c[k] for k in columns if k in c},
                                     ci95_low=c["ci95_pct"][0], ci95_high=c["ci95_pct"][1]))
    manifest = dict(cost_pair_source_attempts_verified=len(verified),
                    all_source_attempts_hashed=len(all_attempt_sources), attempts=all_attempt_sources)
    (args.output / "source-attempts.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(args.output / "analysis.json")
    for key, data in cohorts.items():
        print(key, [(p, data["outcomes"][p]["success"]) for p in POLICIES])
        for p, c in data["comparisons"].items():
            print(p, c["n"], round(c["saving_pct"], 3), [round(v,3) for v in c["ci95_pct"]], "higher-cost tasks", c["more_expensive_tasks"])


if __name__ == "__main__":
    main()
