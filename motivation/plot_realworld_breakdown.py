#!/usr/bin/env python3
"""Decompose the exact RQ2 real-task pairs using their recorded phase timers."""
from __future__ import annotations

from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
import shutil
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from motivation.plot_motivation_four import CM, configure_style
from motivation.paper_labels import WORK_LABELS
from motivation.paper_palette import BAR_PALETTE

TIME = ROOT / "review/realworld-overlapped-search-20261118/time-analysis.json"
BASELINE = Path("/mnt/data/agenttx-runtime/results/rq2-turn-checkpoint-20261111-r2/comparison-monitor/baseline.json")
TURN = ROOT / "review/rq2-turn-checkpoint-final-20261114/attempt-accounting.json"
OUT = ROOT / "review/realworld-breakdown-20261118"
TRY_ANALYSIS = ROOT / "review/try-realworld-paper-20261118/analysis.json"
COHORTS = ("swe_sweagent", "swe_codex", "tb_sweagent", "tb_codex")
HEADINGS = ("SWE / SWE-agent", "SWE / Codex", "TB / SWE-agent", "TB / Codex")
CONTROLS = ("turn_checkpoint", "temporal_checkpoint", "whole_branch_abort")
STAGES = (
    ("preparation_replay_s", "Setup / replay", BAR_PALETTE["purple"]),
    ("agent_execution_s", "Agent execution", BAR_PALETTE["orange"]),
    ("verification_s", "Verification", BAR_PALETTE["teal"]),
)


def analyze():
    sources, cache = {}, {}

    def read(path, expected=None):
        path = Path(path)
        key = str(path)
        if key not in cache:
            content = path.read_bytes()
            sources[key] = hashlib.sha256(content).hexdigest()
            cache[key] = json.loads(content)
        if expected is not None:
            assert sources[key] == expected, key
        return cache[key]

    time_data = read(TIME)
    baseline = read(BASELINE)
    manifest = read(BASELINE.with_name("baseline-manifest.json"))
    assert sources[str(BASELINE)] == manifest["sha256"]
    turns = defaultdict(list)
    for attempt in read(TURN):
        turns[attempt["cell"]].append(attempt)
    for attempts in turns.values():
        attempts.sort(key=lambda row: row["attempt_number"])

    def phases(attempts, cohort, is_agenttx):
        acc = defaultdict(float)
        for attempt in attempts:
            raw = read(attempt["result_path"], attempt["result_sha256"])
            if cohort.startswith("swe_"):
                full = raw["wall_s"]
                model = raw["official_model_wall_s"]
                verify = raw["official_grade_wall_s"]
                policy = raw["policy_wall_s"]
            else:
                timing = raw["timings"]
                full = timing["full_wall_s"]
                model = timing["model_wall_s"]
                verify = timing["verification_wall_s"]
                policy = timing["policy_wall_s"]
            excluded = live_apply = 0.0
            if is_agenttx:
                loc = raw.get("localization") or raw
                excluded = loc["localization_probe_wall_s"] + loc["localization_search_wall_s"]
                live_apply = loc["localization_apply_wall_s"]
            total = full - excluded
            preparation = total - model - verify
            assert min(preparation, model, verify, policy - excluded) >= 0, attempt["result_path"]
            values = dict(total_s=total, preparation_replay_s=preparation,
                          agent_execution_s=model, verification_s=verify,
                          policy_s=policy-excluded, live_apply_s=live_apply,
                          excluded_localization_s=excluded, recorded_s=full)
            for key, value in values.items():
                assert np.isfinite(value)
                acc[key] += value
        assert np.isclose(sum(acc[key] for key, _, _ in STAGES), acc["total_s"], rtol=0, atol=1e-5)
        return dict(acc, attempts=len(attempts))

    result = dict(
        source_time_analysis=str(TIME),
        population="Unchanged RQ2 control-specific matched successful tasks; all recorded attempts contribute to each task.",
        stages=dict(
            agent_execution_s="SWE official_model_wall_s or TB timings.model_wall_s; model calls and agent tool execution during task continuation.",
            verification_s="SWE official_grade_wall_s or TB timings.verification_wall_s; final task grading.",
            preparation_replay_s="Adjusted workflow time minus agent execution and final grading; setup, fault-prefix execution, live recovery, replay, and cleanup.",
        ),
        accounting="Use the same full localization deduction as RQ2 for every Lancet attempt; live application is retained. Controls, pair populations, and raw results are unchanged.",
        new_model_calls=0,
        cohorts={},
    )
    table = []
    for cohort in COHORTS:
        group = cohort.replace("_", "/", 1)
        result["cohorts"][cohort] = {}
        for control in CONTROLS:
            comparison = time_data["cohorts"][cohort][control]
            pairs = []
            for pair in comparison["pairs"]:
                cell = group + "/" + pair["task"]
                agent_attempts = baseline["attempts"][cell + "/causal"]
                control_attempts = (turns[cell] if control == "turn_checkpoint" else
                                    baseline["attempts"][cell + "/" + control])
                sides = dict(causal=phases(agent_attempts, cohort, True),
                             control=phases(control_attempts, cohort, False))
                for side, values in sides.items():
                    assert np.isclose(values["total_s"], pair[side + "_s"], rtol=0, atol=1e-4), (cohort, control, pair["task"], side)
                pairs.append(dict(task=pair["task"], configuration_id=pair["configuration_id"], **sides))
            assert len(pairs) == comparison["n"]
            assert len({pair["task"] for pair in pairs}) == len(pairs)
            means = {}
            for side in ("causal", "control"):
                values = {key: float(np.mean([pair[side][key] for pair in pairs]))
                          for key in pairs[0][side] if key != "attempts"}
                expected = comparison[side + "_mean_s"]
                assert np.isclose(values["total_s"], expected, rtol=0, atol=1e-4)
                values["agent_execution_pct"] = 100 * values["agent_execution_s"] / values["total_s"]
                means[side] = values
                table.append(dict(cohort=cohort, control=WORK_LABELS[control], side=side,
                                  n=len(pairs), **values))
            result["cohorts"][cohort][control] = dict(n=len(pairs), means=means, pairs=pairs)
    supplement = read(TRY_ANALYSIS)
    assert supplement["terminal"]
    for cohort, values in supplement["stage_comparisons"].items():
        result["cohorts"][cohort]["root_only"] = values
        for side, means in values["means"].items():
            table.append(dict(cohort=cohort, control=WORK_LABELS["root_only"], side=side,
                              n=values["n"], **means))
    result["sources"] = sources
    result["verified_result_files"] = sum(path.endswith(("/result.json", "/host-result.json")) for path in sources)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "analysis.json").write_text(json.dumps(result, indent=2) + "\n")
    with (OUT / "summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(table[0]))
        writer.writeheader()
        writer.writerows(table)
    return result


@matplotlib.rc_context({"hatch.linewidth": .4})
def plot(data):
    configure_style()
    plt.rcParams["hatch.linewidth"] = .4
    fig, axes = plt.subplots(2, 2, figsize=(8.5*CM, 7.7*CM), dpi=300)
    for index, (cohort, heading) in enumerate(zip(COHORTS, HEADINGS)):
        ax = axes.flat[index]
        maximum = 0.0
        labels = []
        controls = [p for p in (*CONTROLS, "root_only") if p in data["cohorts"][cohort]]
        positions = list(reversed(range(len(controls))))
        for y, control in zip(positions, controls):
            comparison = data["cohorts"][cohort][control]
            labels.append(f"{WORK_LABELS[control]}\n(n={comparison['n']})")
            for side, offset, hatch in (("causal", .17, "///"), ("control", -.17, "")):
                values = comparison["means"][side]
                left = 0.0
                for field, _, color in STAGES:
                    ax.barh(y+offset, values[field], left=left, height=.28,
                            color=color, edgecolor="#3f3f3f", linewidth=.25,
                            hatch=hatch, zorder=3)
                    left += values[field]
                assert np.isclose(left, values["total_s"], rtol=0, atol=1e-5)
                maximum = max(maximum, left)
        ax.set_yticks(positions, labels, fontsize=6.5)
        ax.set_ylim(-.5, len(controls)-.5)
        ax.set_xlim(0, maximum * 1.06)
        ax.xaxis.set_major_locator(MaxNLocator(nbins=3, steps=[1, 2, 2.5, 5, 10]))
        ax.set_title(f"({chr(97+index)}) {heading}", fontsize=7.4, pad=5)
        ax.tick_params(direction="in", top=True, right=True, length=2.2, pad=2, labelsize=6.5)
        ax.tick_params(axis="y", length=0, pad=3)
        ax.grid(axis="x", color="#dddddd", linewidth=.4)
        ax.set_axisbelow(True)
    handles = [Patch(facecolor=color, edgecolor="none", label=label) for _, label, color in STAGES]
    handles.append(Patch(facecolor="white", edgecolor="#3f3f3f", linewidth=.4,
                         hatch="///", label="Lancet"))
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(.54, 1.005),
               ncol=2, fontsize=6.8, handlelength=1.25, handletextpad=.45,
               columnspacing=1.4, labelspacing=.5, borderpad=.1)
    fig.supxlabel("Mean workflow time (s)", fontsize=7.5, x=.56, y=.025)
    fig.subplots_adjust(left=.16, right=.99, top=.82, bottom=.12,
                        wspace=.65, hspace=.48)
    outputs = {}
    for suffix in ("pdf", "png"):
        path = ROOT / "motivation" / ("FIG-Realworld-Stage-Cost." + suffix)
        fig.savefig(path, dpi=300, bbox_inches="tight", pad_inches=.025)
        paper_path = ROOT / "paper/img" / path.name
        shutil.copyfile(path, paper_path)
        outputs[str(paper_path)] = hashlib.sha256(paper_path.read_bytes()).hexdigest()
    plt.close(fig)
    (OUT / "figure-manifest.json").write_text(json.dumps(dict(
        outputs=outputs, group_by=["benchmark", "agent", "competitor"],
        layout="Single column; 2 by 2; paired horizontal stacked bars; Lancet hatched.",
        colors={key: color for key, _, color in STAGES},
        analysis_sha256=hashlib.sha256((OUT / "analysis.json").read_bytes()).hexdigest(),
    ), indent=2) + "\n")


def main():
    data = analyze()
    plot(data)
    print("Verified result logs:", data["verified_result_files"])
    print("All bar totals reproduce the corresponding RQ2 means.")
    for cohort in COHORTS:
        for control in data["cohorts"][cohort]:
            c = data["cohorts"][cohort][control]
            print(cohort, WORK_LABELS[control], "n=", c["n"])
            for side, means in c["means"].items():
                print(" ", side, {key: round(means[key], 3) for key in
                      ("total_s", "preparation_replay_s", "agent_execution_s", "verification_s", "live_apply_s")})


if __name__ == "__main__":
    main()
