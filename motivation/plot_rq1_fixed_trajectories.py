#!/usr/bin/env python3
"""Plot fixed-trajectory recovery, including the completed try-scope supplement."""

from __future__ import annotations

import csv
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from motivation.plot_motivation_four import CM, configure_style
from motivation.plot_system_micro_final import LABELS, STYLES, frame

DATA = ROOT / "review/rq1-strategy-only-final-20261110"
SUPPLEMENT = ROOT / "review/rq1-selected-effects-final-20261114"
AUDIT = ROOT / "review/try-rq1-presentation-20261118"
POLICIES = ("causal", "crash_no_rollback", "turn_checkpoint",
            "temporal_checkpoint", "whole_branch_abort")
FIGURE_POLICIES = ("causal", "root_only", "turn_checkpoint", "temporal_checkpoint",
                   "whole_branch_abort")
FAMILIES = ("mb2_task_length", "mb3_fault_position", "mb4_dependency")
FIELDS = ("operation_count", "fault_position", "actual_dependency_value")
TITLES = ("Trajectory length", "Fault position", "Affected-call fraction")
STEM = "FIG-System-Micro-Recovery"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_cohort(data):
    summary = json.loads((data / "summary.json").read_text())
    cohort = Path(summary["cohort"])
    registration = json.loads((cohort / "registration.json").read_text())
    assert sha(cohort / "cells.json") == summary["cells_sha256"]
    cells = json.loads((cohort / "cells.json").read_text())["main"]
    cells_by_key = {cell["key"]: cell for cell in cells}
    assert len(cells) == len(cells_by_key) == registration["expected_cells"] == summary["main_runs"]
    assert all(cell["state"] == "passed" and not cell["error"] for cell in cells)

    # Read parameter coordinates from the exact generator frozen for this run.
    code = """
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path[:0] = [str(root / 'src'), str(root)]
from experiments.scripts.bench_external_recovery_artifacts import motivation_400_cases
print(json.dumps({c.plan.trace_id: dict(
    family=c.suite_slice, manifest_hash=c.plan.manifest_hash,
    operation_count=len(c.plan.steps),
    fault_position=c.plan.requested_fault_position,
    dependency_value=c.plan.requested_dependency_value,
    actual_dependency_value=c.plan.actual_dependency_value)
    for c in motivation_400_cases()}))
"""
    coordinates = json.loads(subprocess.run(
        [sys.executable, "-c", code, registration["frozen_source"]],
        check=True, capture_output=True, text=True).stdout)
    assert len(coordinates) == 400
    hashes = json.loads((data / "result-hashes.json").read_text())
    with (data / "runs.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == registration["expected_cells"] and {r["key"] for r in rows} == set(cells_by_key)
    for row in rows:
        cell = cells_by_key[row["key"]]
        coordinate = coordinates[row["trace_id"]]
        assert coordinate["manifest_hash"] == cell["manifest_hash"]
        assert coordinate["family"] == row["family"] == cell["family"]
        path = Path(cell["result"])
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == cell["result_sha256"] == hashes[row["key"]]
        result = json.loads(raw)
        assert result["model_calls"] == result["api_requests"] == 0
        assert not result["native_agent_started"]
        assert result["policy_contract_passed"]
        for key in ("operation_count", "invalid_count", "valid_count",
                    "rollback_calls", "valid_discarded_calls", "invalid_retained_calls"):
            row[key] = int(row[key])
        for key in ("invalid_removal", "valid_retention"):
            row[key] = float(row[key])
            assert row[key] == result[key]
        original = set(range(row["operation_count"]))
        removed = set(result["rollback_ids"]) & original
        affected = set(result["injected_invalid_ids"])
        assert row["operation_count"] == coordinate["operation_count"]
        assert row["invalid_count"] == len(affected)
        assert row["rollback_calls"] == len(removed)
        assert row["valid_discarded_calls"] == len(removed - affected)
        assert row["invalid_retained_calls"] == len(affected - removed)
        row.update({key: coordinate[key] for key in
                    ("fault_position", "dependency_value", "actual_dependency_value")})
        row["manifest_hash"] = cell["manifest_hash"]

    for policy in registration["policies"]:
        group = [r for r in rows if r["policy"] == policy]
        assert len(group) == 1200 and len({r["trace_id"] for r in group}) == 400
        for field, summary_field, scale in (
                ("invalid_removal", "invalid_removal_mean_pct", 100),
                ("valid_retention", "valid_retention_mean_pct", 100),
                ("rollback_calls", "mean_rollback_calls", 1),
                ("valid_discarded_calls", "mean_valid_discarded_calls", 1)):
            assert np.isclose(np.mean([r[field] for r in group]) * scale,
                              summary["policies"][policy][summary_field])
    AUDIT.mkdir(parents=True, exist_ok=True)
    (AUDIT / "trajectory-coordinates.json").write_text(
        json.dumps(coordinates, indent=2) + "\n")
    return rows, summary


def load():
    rows, summary = load_cohort(DATA)
    assert len(rows) == 6000
    extra_rows, extra_summary = load_cohort(SUPPLEMENT)
    assert len(extra_rows) == 1200 and {r["policy"] for r in extra_rows} == {"root_only"}
    coordinates = {r["trace_id"]: r["manifest_hash"] for r in rows}
    assert all(r["manifest_hash"] == coordinates[r["trace_id"]] for r in extra_rows)
    rows += extra_rows
    summary = dict(summary, main_runs=len(rows),
                   policies={**summary["policies"], **extra_summary["policies"]},
                   supplemental_cohorts=[extra_summary["cohort"]],
                   source_datasets=[dict(path=str(DATA), csv_sha256=sha(DATA / "runs.csv"),
                                         cells_sha256=summary["cells_sha256"]),
                                    dict(path=str(SUPPLEMENT), csv_sha256=sha(SUPPLEMENT / "runs.csv"),
                                         cells_sha256=extra_summary["cells_sha256"])])
    return rows, summary


def interval(group, field):
    cases = {}
    for row in group:
        cases.setdefault(row["trace_id"], []).append(row[field])
    assert len(cases) == 10 and all(len(v) == 3 for v in cases.values())
    values = np.array([np.mean(v) for _, v in sorted(cases.items())])
    rng = np.random.default_rng(20261111)
    samples = values[rng.integers(0, len(values), (20000, len(values)))].mean(axis=1)
    return float(values.mean()), np.quantile(samples, [.025, .975]).tolist()


def plot(rows, summary):
    configure_style()
    fig, axes = plt.subplots(2, 3, figsize=(17.8 * CM, 7.0 * CM), dpi=300)
    points = []
    metrics = ("valid_discarded_calls", "rollback_calls")
    for index, (family, xfield) in enumerate(zip(FAMILIES, FIELDS)):
        group = [r for r in rows if r["family"] == family]
        for row_index, metric in enumerate(metrics):
            ax = axes[row_index, index]
            # A dashed try line over Lancet's solid zero line keeps both visible.
            for policy in (*FIGURE_POLICIES[2:], "causal", "root_only"):
                xs = sorted({r[xfield] for r in group})
                means, lows, highs = [], [], []
                for value in xs:
                    selected = [r for r in group if r["policy"] == policy and r[xfield] == value]
                    mean, ci = interval(selected, metric)
                    points.append(dict(family=family, xfield=xfield, x=value,
                                       metric=metric, policy=policy, runs=len(selected),
                                       cases=10, mean=mean, ci95=ci))
                    means.append(mean)
                    lows.append(ci[0])
                    highs.append(ci[1])
                scale = 1 if xfield == "operation_count" else 100
                display_x = np.asarray(xs) * scale
                style = dict(STYLES[policy])
                ax.plot(display_x, means, label=LABELS[policy],
                        markevery=(1 if policy == "root_only" else 0, 2), **style)
                ax.fill_between(display_x, lows, highs,
                                color=style["color"], alpha=.10, linewidth=0)
            ax.set_title(f"({chr(97 + row_index * 3 + index)}) {TITLES[index]}",
                         fontsize=8, pad=4)
            ax.set_ylabel("Valid calls discarded" if row_index == 0 else "Calls withdrawn",
                          fontsize=7.5, labelpad=3)
            ax.set_xlabel(("Original tool calls", "Fault position (%)",
                           "Later calls affected (%)")[index], fontsize=7.5, labelpad=3)
            if index == 0:
                ax.set_xticks([16, 80, 160])
            else:
                ax.set_xticks([10, 50, 90] if index == 1 else [0, 50, 100])
            maximum = 160 if index < 2 else 80
            ax.set_ylim(-maximum * .05, maximum * 1.05)
            ax.set_yticks([0, maximum // 2, maximum])
            frame(ax)
    handles = [Line2D([], [], label=LABELS[p], **STYLES[p]) for p in FIGURE_POLICIES]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(.53, 1.005),
               ncol=3, fontsize=7, borderpad=.35, columnspacing=1.4,
               handlelength=1.8, handletextpad=.45, labelspacing=.4)
    fig.subplots_adjust(left=.065, right=.985, bottom=.14, top=.82,
                        wspace=.32, hspace=.55)
    paths = []
    for suffix in ("pdf", "png"):
        path = ROOT / "motivation" / f"{STEM}.{suffix}"
        fig.savefig(path, dpi=300, bbox_inches="tight", pad_inches=.025)
        shutil.copyfile(path, ROOT / "paper/img" / path.name)
        paths.append(str(path.relative_to(ROOT)))
    plt.close(fig)
    plotted_runs = sum(r["policy"] in FIGURE_POLICIES for r in rows)
    report = dict(cohort=summary["cohort"], runs=plotted_runs, source_runs=len(rows),
                  supplemental_cohorts=summary["supplemental_cohorts"],
                  cases=400, repeats=3,
                  policies=FIGURE_POLICIES, plotted_runs=plotted_runs,
                  registered_policies=(*POLICIES, "root_only"), metrics=metrics, points=points, figures=paths,
                  layout=dict(rows=2, columns=3, spans_columns=2,
                              row_metrics=list(metrics),
                              column_families=list(FAMILIES)),
                  source_datasets=summary["source_datasets"],
                  cells_sha256=summary["cells_sha256"],
                  source_results_verified=len(rows), model_calls=0, api_requests=0,
                  bootstrap_reps=20000, bootstrap_unit="case; mean of three repetitions",
                  style_source="motivation.plot_system_micro_final:STYLES,frame")
    (AUDIT / "figure-data.json").write_text(json.dumps(report, indent=2) + "\n")
    for family in FAMILIES:
        subset = [p for p in points if p["family"] == family]
        for x in (min(p["x"] for p in subset), max(p["x"] for p in subset)):
            print(family, x, {p["policy"]: p["mean"] for p in subset
                               if p["x"] == x and p["metric"] == "rollback_calls"})


def write_table(summary):
    lines = [
        r"\begin{table}[!ht]", r"\centering",
        r"\caption{\textbf{Error removal and valid-work retention.}",
        r"\small\emph{Each method has 1,200 fixed-trajectory runs. Percentages and",
        r"call counts are averaged over runs.}}",
        r"\label{tab:controlled-policy-results}", r"\footnotesize",
        r"\setlength{\tabcolsep}{2pt}",
        r"\begin{tabular*}{\linewidth}{@{\extracolsep{\fill}}lrrrr@{}}",
        r"\toprule",
        r"Method & \shortstack{Affected\\removed} & \shortstack{Valid\\retained} & \shortstack{Valid\\discarded} & \shortstack{Calls\\withdrawn} \\",
        r"\midrule",
    ]
    for policy in FIGURE_POLICIES:
        row = summary["policies"][policy]
        label = r"\system" if policy == "causal" else LABELS[policy]
        def rounded(value):
            return format(Decimal(str(value)).quantize(Decimal("0.01"),
                          rounding=ROUND_HALF_UP), ".2f")
        removed_value = row["invalid_removal_mean_pct"]
        removed = (str(int(removed_value)) if removed_value in (0, 100)
                   else rounded(removed_value)) + r"\%"
        retained_value = row["valid_retention_mean_pct"]
        retained = (str(int(retained_value)) if retained_value in (0, 100)
                    else rounded(retained_value)) + r"\%"
        discarded = rounded(row["mean_valid_discarded_calls"])
        withdrawn = rounded(row["mean_rollback_calls"])
        lines.append(f"{label} & {removed} & {retained} & {discarded} & {withdrawn} " + r"\\")
    lines += [r"\bottomrule", r"\end{tabular*}", r"\end{table}"]
    (ROOT / "paper/tables/rq1-fixed-trajectories.tex").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    data, statistics = load()
    plot(data, statistics)
    write_table(statistics)
