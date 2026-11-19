#!/usr/bin/env python3
"""Add the audited Turn checkpoint experiment to the RQ2 paper analysis."""

from __future__ import annotations

from collections import defaultdict
import copy
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.scripts.analyze_realworld_final import comparison, bootstrap
from experiments.scripts.run_tb_dual_local import configuration_id
from scripts.maintenance.watch_rq2_comparisons import compare, history_summary

BASE = ROOT / "review/realworld-results-20261106"
TURN = ROOT / "review/rq2-turn-checkpoint-final-20261114"
OUT = ROOT / "review/realworld-turn-results-20261116"
POLICIES = ("causal", "turn_checkpoint", "temporal_checkpoint", "whole_branch_abort")
CONTROLS = POLICIES[1:]
CORE_CONFIGURATION = ("protocol", "completion_contract", "model_session_wall_s", "verifier_wall_s")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def equivalent(left, right) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(equivalent(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(equivalent(a, b) for a, b in zip(left, right))
    if isinstance(left, float) and isinstance(right, float):
        return bool(np.isclose(left, right, rtol=1e-12, atol=1e-12))
    return left == right


def paper_history(rows):
    """Pool infrastructure variants under the user-confirmed comparison rule."""
    normalized = []
    for row in rows:
        item = dict(row)
        if row["configuration"]:
            item["configuration"] = {k: row["configuration"][k] for k in CORE_CONFIGURATION}
            item["configuration_id"] = configuration_id(item["configuration"])
        normalized.append(item)
    return history_summary(normalized)


def main() -> None:
    sources = {}

    def read(path: Path):
        sources[str(path)] = digest(path)
        return json.loads(path.read_text())

    analysis = read(BASE / "analysis.json")
    times = read(BASE / "time-analysis.json")
    final = read(TURN / "final-audit.json")
    attempts = read(TURN / "attempt-accounting.json")
    assert len(attempts) == final["recorded_attempts"] == 1175
    for path, expected in final["inputs"].items():
        assert digest(Path(path)) == expected, path
        sources[path] = expected
    baseline_path = next(Path(p) for p in final["inputs"] if p.endswith("/baseline.json"))
    baseline = read(baseline_path)
    histories = defaultdict(list)
    verified = {}

    def verify_attempt(row):
        for key, hash_key in (("result_path", "result_sha256"),
                              ("usage_log", "usage_log_sha256")):
            if not row.get(key):
                continue
            path, expected = row[key], row[hash_key]
            if path not in verified:
                assert digest(Path(path)) == expected, path
                verified[path] = expected
            assert verified[path] == expected

    for row in attempts:
        verify_attempt(row)
        histories[row["cell"]].append(row)
    turns = {key: history_summary(sorted(rows, key=lambda r: r["attempt_number"]))
             for key, rows in histories.items()}
    assert len(turns) == 1082
    strict_comparisons = compare(turns, baseline["cells"])
    assert equivalent(strict_comparisons, final["comparisons"]), "Final per-task accounting changed"
    for key, row in turns.items():
        baseline_key = key + "/causal"
        for attempt in baseline["attempts"].get(baseline_key, []):
            verify_attempt(attempt)

    paper_turns = {k: paper_history(sorted(v, key=lambda r: r["attempt_number"]))
                   for k, v in histories.items()}
    paper_baselines = {k: paper_history(v) for k, v in baseline["attempts"].items()}
    comparisons = compare(paper_turns, paper_baselines)
    image_checks = {}
    for group, cohort in comparisons.items():
        if not group.startswith("tb/"):
            continue
        for pair in cohort["policies"]["causal"]["pairs"]:
            cell = group + "/" + pair["task"]
            rows = histories[cell] + baseline["attempts"][cell + "/causal"]
            raws = [json.loads(Path(r["result_path"]).read_text()) for r in rows]
            images = {r["image_id"] for r in raws}
            assert len(images) == 1 and None not in images, cell
            assert {r["reasoning_effort"] for r in raws} == {"max"}, cell
            image_checks[cell] = next(iter(images))

    combined, combined_times = copy.deepcopy(analysis), copy.deepcopy(times)
    combined["policy_order"], combined["control_order"] = POLICIES, CONTROLS
    exclusions = {}
    report = []
    for index, (key, cohort) in enumerate(combined["cohorts"].items()):
        group = key.replace("_", "/", 1)
        audited = comparisons[group]["policies"]["causal"]
        default_cid = cohort["comparisons"]["temporal_checkpoint"]["pairs"][0]["configuration_id"]
        token_pairs, time_pairs = [], []
        for pair in audited["pairs"]:
            cell = group + "/" + pair["task"]
            # Retain the historical AgentTX identifier for curve joins and
            # bootstrap strata; matching uses the common core above.
            cid = baseline["cells"][cell + "/causal"]["configuration_id"] or default_cid
            token_pairs.append(dict(task=pair["task"], configuration_id=cid,
                                    matching_configuration_id=pair["configuration_id"],
                                    turn_configuration_id=turns[cell]["configuration_id"],
                                    causal_tokens=pair["baseline_tokens"],
                                    control_tokens=pair["turn_tokens"]))
            assert pair["baseline_s"] > 0 and pair["turn_s"] > 0
            time_pairs.append(dict(task=pair["task"], configuration_id=cid,
                                   causal_s=pair["baseline_s"], control_s=pair["turn_s"]))
        c = comparison(token_pairs, 20261116 + index, 20000)
        assert c["n"] == audited["eligible_cost_pairs"]
        assert np.isclose(c["saving_pct"], audited["baseline_token_saving_vs_turn_pct"])
        # Preserve the published accounting for the two existing competitors.
        cohort["comparisons"] = {p: cohort["comparisons"][p]
                                  for p in CONTROLS if p != "turn_checkpoint"}
        cohort["comparisons"]["turn_checkpoint"] = c
        cohort["comparisons"] = {p: cohort["comparisons"][p] for p in CONTROLS}
        cohort["outcomes"] = {p: v for p, v in cohort["outcomes"].items() if p in POLICIES}
        outcome = final["groups"][group]
        cohort["outcomes"]["turn_checkpoint"] = dict(
            success=outcome["passed"], planned=outcome["planned"],
            completed=outcome["completed"], infrastructure_error=outcome["infrastructure_error"],
            failed=outcome["failed"], recorded_attempts=outcome["recorded_attempts"])
        time_bootstrap_rows = [dict(task=r["task"], configuration_id=r["configuration_id"],
                                   causal_tokens=r["causal_s"], control_tokens=r["control_s"])
                               for r in time_pairs]
        seed = 20261216 + index
        ci = np.quantile(bootstrap(time_bootstrap_rows, seed, 20000), [.025, .975]).tolist()
        a, b = sum(r["causal_s"] for r in time_pairs), sum(r["control_s"] for r in time_pairs)
        tc = dict(n=len(time_pairs), causal_total_s=a, control_total_s=b,
                  causal_mean_s=a / len(time_pairs), control_mean_s=b / len(time_pairs),
                  saving_pct=100 * (1 - a / b), ci95_pct=ci, bootstrap_seed=seed,
                  slower_tasks=sum(r["causal_s"] > r["control_s"] for r in time_pairs),
                  pairs=time_pairs)
        assert np.isclose(tc["saving_pct"], audited["baseline_time_saving_vs_turn_pct"])
        combined_times["cohorts"][key] = {
            p: tc if p == "turn_checkpoint" else times["cohorts"][key][p] for p in CONTROLS}
        exclusions[key] = audited["excluded"]
        report.append(dict(cohort=key, success=outcome["passed"], pairs=c["n"],
                           token_saving_pct=c["saving_pct"], token_ci95=c["ci95_pct"],
                           time_saving_pct=tc["saving_pct"], time_ci95=ci,
                           cheaper_tasks=c["cheaper_tasks"], more_expensive_tasks=c["more_expensive_tasks"]))
    combined["turn_checkpoint_audit"] = dict(
        experiment_cells=1082, recorded_attempts=1175,
        source=str(TURN.relative_to(ROOT)), exclusions=exclusions,
        pairing_rule="Pool infrastructure variants; match task image, model, reasoning effort, fault seed, core protocol, recovery contract, and time budgets; require successful terminal outcomes and complete histories.",
        strict_configuration_pair_counts={k: v["policies"]["causal"]["eligible_cost_pairs"]
                                          for k, v in strict_comparisons.items()},
        turn_scope="Exact affected turns; each injected-prefix tool call occupies one turn.",
        context="AgentTX compact; all three competitors full.",
        outcome_rule="Normal terminal full-contract successes; infrastructure exceptions are not successes.")
    combined["sources"].extend(dict(path=p, sha256=h) for p, h in sources.items())
    OUT.mkdir(parents=True, exist_ok=True)
    for name, data in (("analysis.json", combined), ("time-analysis.json", combined_times),
                       ("turn-summary.json", report),
                       ("source-verification.json", dict(inputs=sources, verified_attempt_files=verified,
                                                         matched_task_images=image_checks))):
        (OUT / name).write_text(json.dumps(data, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    print(f"Verified {len(verified)} result/usage files; original analyses and runs unchanged.")


if __name__ == "__main__":
    main()
