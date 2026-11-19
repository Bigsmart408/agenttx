#!/usr/bin/env python3
"""Audit and plot every localization run in both model-assisted microbenchmarks."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import shutil
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from motivation.plot_motivation_four import CM, configure_style
from motivation.plot_system_micro_final import STYLES, frame

SOURCE = ROOT / "review/controlled-both-agents-20261110/runs.csv"
COORDINATES = ROOT / "review/rq1-paper-update-20261111/trajectory-coordinates.json"
AUDIT = ROOT / "review/localization-per-task-20261115-111529"
STEM = "FIG-Controlled-Localization-Per-Task"
AGENTS = (("sweagent", "SWE-agent"), ("codex", "Codex"))
FAMILIES = (
    ("mb2_task_length", "Trajectory length", 100, "operation_count"),
    ("mb3_fault_position", "Fault position", 90, "fault_position"),
    ("mb4_dependency", "Affected-call fraction", 210, "actual_dependency_value"),
)
METRICS = ("first_passing_trial", "total_trials")
LABELS = {"first_passing_trial": "First passing plan", "total_trials": "Complete search"}
PLOT_STYLES = {
    "first_passing_trial": dict(STYLES["causal"], markersize=1.3, linewidth=.75),
    "total_trials": dict(STYLES["turn_checkpoint"], markersize=1.5, linewidth=.65),
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(source):
    coordinates = json.loads(COORDINATES.read_text())
    assert len(coordinates) == 400
    with source.open(newline="") as handle:
        selected = [r for r in csv.DictReader(handle) if r["policy"] == "causal"]
    assert len(selected) == 2400
    records, hashes, identities = [], {}, set()
    for row in selected:
        identity = row["harness"], row["trace_id"], int(row["repeat"])
        assert identity not in identities
        identities.add(identity)
        coordinate = coordinates[row["trace_id"]]
        assert row["manifest_hash"] == coordinate["manifest_hash"]
        assert row["family"] == coordinate["family"]
        path = Path(row["result_path"])
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        assert sha == row["sha256"], path
        result = json.loads(raw)
        assert result["harness"] == row["harness"]
        assert result["trace_id"] == row["trace_id"]
        assert result["manifest_hash"] == row["manifest_hash"]
        attempts = result["localization"]["attempts"]
        first = next(i + 1 for i, a in enumerate(attempts) if a["validation"] == "passed")
        assert len(attempts) == result["localization_probe_count"] == int(row["probe_count"])
        n = result["operation_count"]
        roots = result["injected_root_steps_for_scoring"]
        assert len(roots) == 1 and result["localization"]["validation"] == "passed"
        assert result["localized_root_steps"] == roots
        # Verify search order from attempts; trial counts above come from actual records.
        assert [a["root_step_ids"] for a in attempts] == [[i] for i in range(n - 1, -1, -1)]
        assert first == n - roots[0] and len(attempts) == n
        records.append(dict(harness=row["harness"], family=row["family"],
            trace_id=row["trace_id"], repeat=int(row["repeat"]),
            operation_count=n, fault_step=roots[0],
            fault_position=coordinate["fault_position"],
            actual_dependency_value=coordinate["actual_dependency_value"],
            first_passing_trial=first, total_trials=len(attempts),
            model_completed=result["completed"], result_path=str(path), result_sha256=sha))
        hashes[str(path)] = sha
    assert Counter((r["harness"], r["trace_id"]) for r in records) == {
        (agent, trace): 3 for agent, _ in AGENTS for trace in coordinates}
    tasks = {}
    for family, _, cases, xfield in FAMILIES:
        traces = sorted((t for t, c in coordinates.items() if c["family"] == family),
                        key=lambda t: (coordinates[t][xfield], t))
        assert len(traces) == cases
        tasks[family] = traces
        ranks = {t: i + 1 for i, t in enumerate(traces)}
        for row in records:
            if row["family"] == family:
                row["task_id"] = ranks[row["trace_id"]]
    for trace in coordinates:
        group = [r for r in records if r["trace_id"] == trace]
        assert len(group) == 6
        assert len({(r["first_passing_trial"], r["total_trials"]) for r in group}) == 1
    return records, hashes, tasks


def write_data(records, hashes, tasks, output):
    order = {f[0]: i for i, f in enumerate(FAMILIES)}
    records = sorted(records, key=lambda r: (order[r["family"]], r["task_id"],
                                            r["harness"], r["repeat"]))
    with (output / "per-run-trials.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    index = {(r["trace_id"], r["harness"], r["repeat"]): r for r in records}
    task_rows = []
    lines = ["# 逐任务候选恢复试验次数", "",
             "三个 microbenchmark、两个 agent、每任务三次重复，共 2,400 次定位搜索。",
             "每个数字来自原始 localization.attempts；包含后续模型未完成的运行。",
             "First 是首个通过验证的候选序号，Total 是完整搜索的候选总数；均不是模型请求次数。",
             "R0/R1/R2 分别列出三次重复，不计算均值。", "",
             "同一固定任务的两个 agent、三次重复计数相同。全部搜索按调用逆序尝试：",
             "First = 调用数 − 错误调用索引（索引从 0 开始）；Total = 调用数。",
             "该顺序来自共享定位器，搜索发生在模型继续任务之前。", ""]
    for family, title, _, _ in FAMILIES:
        lines += [f"## {title}", "",
                  "| Task ID | Task | SWE-agent first R0/R1/R2 | SWE-agent total R0/R1/R2 | Codex first R0/R1/R2 | Codex total R0/R1/R2 |",
                  "|---:|---|---|---|---|---|"]
        for trace in tasks[family]:
            row = dict(family=family, task_id=index[trace, "sweagent", 0]["task_id"], trace_id=trace)
            displays = []
            for agent, _ in AGENTS:
                for metric in METRICS:
                    counts = [index[trace, agent, repeat][metric] for repeat in range(3)]
                    displays.append(" / ".join(map(str, counts)))
                    row.update({f"{agent}_{metric}_r{repeat}": value for repeat, value in enumerate(counts)})
            lines.append(f"| {row['task_id']} | {trace} | " + " | ".join(displays) + " |")
            task_rows.append(row)
        lines.append("")
    (output / "per-task-trials.md").write_text("\n".join(lines) + "\n")
    with (output / "per-task-trials.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(task_rows[0]))
        writer.writeheader()
        writer.writerows(task_rows)
    (output / "source-hashes.json").write_text(json.dumps(hashes, indent=2) + "\n")


def plot(records):
    configure_style()
    fig, axes = plt.subplots(2, 3, sharey=True, figsize=(17.8 * CM, 8.2 * CM), dpi=300)
    panel_data = []
    for row_index, (agent, name) in enumerate(AGENTS):
        for column, (family, title, cases, _) in enumerate(FAMILIES):
            ax = axes[row_index, column]
            group = sorted((r for r in records if r["family"] == family and r["harness"] == agent),
                           key=lambda r: (r["task_id"], r["repeat"]))
            assert len(group) == 3 * cases
            # Repeats share the task coordinate; equal observations overlap.
            x = [r["task_id"] for r in group]
            for metric in ("total_trials", "first_passing_trial"):
                ax.plot(x, [r[metric] for r in group], **PLOT_STYLES[metric])
            letter = chr(97 + row_index * 3 + column)
            ax.set_title(f"({letter}) {title} / {name}", fontsize=7.5, pad=5)
            ax.set_xlabel("Task ID", fontsize=7.5, labelpad=3)
            ax.set_xticks([1, cases // 2, cases])
            ax.set_xlim(0, cases + 1)
            ax.set_ylim(0, 170)
            ax.set_yticks([0, 80, 160])
            if column == 0:
                ax.set_ylabel("Candidate trials", fontsize=8, labelpad=3)
            frame(ax)
            panel_data.append(dict(harness=agent, family=family, tasks=cases, runs=len(group),
                                   points=[{k: r[k] for k in ("task_id", "trace_id", "repeat", *METRICS)}
                                           for r in group]))
    handles = [Line2D([], [], label=LABELS[m], **PLOT_STYLES[m]) for m in METRICS]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(.53, 1.0),
               ncol=2, fontsize=8, columnspacing=2, handlelength=2)
    fig.subplots_adjust(left=.065, right=.985, bottom=.12, top=.85, wspace=.25, hspace=.55)
    paths = []
    for suffix in ("pdf", "png"):
        path = ROOT / "motivation" / f"{STEM}.{suffix}"
        fig.savefig(path, dpi=300, bbox_inches="tight", pad_inches=.025)
        shutil.copyfile(path, ROOT / "paper/img" / path.name)
        paths.append(str(path.relative_to(ROOT)))
    plt.close(fig)
    return panel_data, paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=AUDIT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    records, hashes, tasks = load(args.source)
    write_data(records, hashes, tasks, args.output)
    panels, paths = plot(records)
    first = sorted(r["first_passing_trial"] for r in records)
    middle = len(first) // 2
    report = dict(runs=len(records), tasks=400, repeats=3, agents=[a for a, _ in AGENTS],
                  source=str(args.source), source_sha256=digest(args.source),
                  coordinates_sha256=digest(COORDINATES), source_results_verified=len(hashes),
                  metrics=METRICS, aggregation="none; one observation per task, agent and repeat",
                  includes_subsequent_model_failures=True,
                  same_counts_across_agents_and_repeats=True,
                  observed_search_order="reverse call order in every run",
                  median_first_passing_trial=(first[middle - 1] + first[middle]) / 2,
                  min_first_passing_trial=first[0], max_first_passing_trial=first[-1],
                  layout=dict(rows=2, columns=3, row_agents=[a for a, _ in AGENTS]),
                  panels=panels, figures=paths)
    (args.output / "figure-data.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "panels"}, indent=2))


if __name__ == "__main__":
    main()
