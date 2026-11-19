#!/usr/bin/env python3
"""Audit per-task localization counts and plot their discrete distributions."""
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
from matplotlib.patches import Patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from motivation.plot_motivation_four import CM, configure_style
from motivation.plot_system_micro_final import frame
from motivation.paper_palette import BAR_PALETTE

SOURCE = Path('/mnt/data/agenttx-runtime/results/realworld-localization-trials-20261115-r2')
AUDIT = ROOT / 'review/realworld-localization-per-task-20261115'
STEM = 'FIG-RQ1-Localization-Search'
AGENTS = (('sweagent', 'SWE-agent'), ('codex', 'Codex'))
BENCHMARKS = (('swe', 'SWE-Bench Lite', 300), ('tb', 'Terminal-Bench', 241))
METRICS = ('first_passing_trial', 'total_trials')
LABELS = ('First passing plan', 'Complete search')
COLORS = (BAR_PALETTE['red'], BAR_PALETTE['orange'])


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')


def write_csv(path, rows):
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def load(source):
    registration = read(source / 'registration.json')
    assert registration['measurement'] == 'supplemental localization-only rerun'
    assert registration['external_model_calls'] == 0
    cells = read(source / 'cells.json')
    assert len(cells) == registration['expected_cells'] == 1082
    assert not any(c['state'] in ('pending', 'queued', 'running') for c in cells)
    hashes = {str(source / p): digest(source / p) for p in
              ('registration.json', 'source-manifest.json', 'cells.json', 'execution.json')}
    for p, expected in registration['sources'].items():
        assert digest(p) == expected, p
        hashes[p] = expected
    rows, attempts = [], []
    for benchmark, _, count in BENCHMARKS:
        names = sorted({c['task'] for c in cells if c['benchmark'] == benchmark})
        assert len(names) == count
        ranks = {name: i + 1 for i, name in enumerate(names)}
        for harness, _ in AGENTS:
            group = sorted((c for c in cells if c['benchmark'] == benchmark and c['harness'] == harness),
                           key=lambda c: c['task'])
            assert len(group) == count
            for c in group:
                assert digest(c['original_result']) == c['original_sha256']
                hashes[c['original_result']] = c['original_sha256']
                row = dict(benchmark=benchmark, harness=harness, task_id=ranks[c['task']], task=c['task'],
                    state=c['state'], first_passing_trial=None, total_trials=None,
                    verifier_executions=None, rejected_candidates=None,
                    original_result=c['original_result'], original_sha256=c['original_sha256'],
                    result_path=c.get('result_path'), result_sha256=c.get('result_sha256'))
                if c['state'] == 'completed':
                    path = Path(c['result_path'])
                    assert digest(path) == c['result_sha256']
                    hashes[str(path)] = c['result_sha256']
                    raw = read(path)
                    assert raw['measurement'] == 'supplemental localization-only rerun'
                    assert raw['external_model_calls'] == 0 and raw['stopped_before_model_repair']
                    assert (raw['benchmark'], raw['harness'], raw['task']) == (benchmark, harness, c['task'])
                    assert raw['injected'] == c['original_injected'] and c['injection_matches_original']
                    if benchmark == 'tb':
                        original = read(c['original_result'])
                        host_path = path.parent / 'host-result.json'
                        host = read(host_path)
                        assert host['image_id'] == original['image_id'], c['key']
                        hashes[str(host_path)] = digest(host_path)
                    assert raw['plan']['validation'] == 'passed'
                    assert raw['plan']['root_step_ids'] == [raw['injected']['root_step']]
                    recorded = raw['plan']['attempts']
                    first = next(i + 1 for i, a in enumerate(recorded) if a['validation'] == 'passed')
                    total = len(recorded)
                    assert first == raw['first_passing_trial'] == c['first_passing_trial']
                    assert total == raw['total_trials'] == c['total_trials']
                    executed = sum(a['verifier_returncode'] is not None for a in recorded)
                    row.update(first_passing_trial=first, total_trials=total,
                        verifier_executions=executed, rejected_candidates=total - executed)
                    for i, a in enumerate(recorded, 1):
                        attempts.append(dict(benchmark=benchmark, harness=harness, task=c['task'],
                            candidate_index=i, root_step_ids=json.dumps(a['root_step_ids']),
                            rollback_targets=json.dumps(a['rollback_targets']), validation=a['validation'],
                            verifier_returncode=a['verifier_returncode'], error=a['error'],
                            source=str(path), source_sha256=c['result_sha256']))
                    for name in ('before-localization-ledger.json', 'after-localization-ledger.json'):
                        ledger = path.parent / name
                        hashes[str(ledger)] = digest(ledger)
                rows.append(row)
    return rows, attempts, hashes, registration


def write_data(output, rows, attempts, hashes):
    write_csv(output / 'per-run-trials.csv', rows)
    write_csv(output / 'candidate-attempts.csv', attempts)
    write_json(output / 'source-hashes.json', hashes)
    index = {(r['benchmark'], r['harness'], r['task']): r for r in rows}
    tasks = []
    lines = ['# SWE-Bench Lite / Terminal-Bench 逐任务定位尝试次数', '',
        'SWE-Bench Lite 300 个任务、Terminal-Bench 241 个任务，分别运行 SWE-agent 与 Codex 的定位阶段。',
        '以下是独立的零模型补测；原实验未保存候选列表，不能把本表称为原实验的历史尝试次数。',
        '沿用注册任务、故障种子和真实工作区；逐任务核对注入记录与原记录完全一致。',
        'First：首次通过候选的序号；Total：完整搜索的候选尝试总数。',
        '计数包含安全检查拒绝的候选；实际 verifier 执行数和拒绝数另见 per-run-trials.csv。',
        'Task ID 按任务名排序，两个 agent 使用相同编号；不取平均。', '']
    for benchmark, title, _ in BENCHMARKS:
        lines += [f'## {title}', '',
            '| ID | Task | SWE-agent First | SWE-agent Total | Codex First | Codex Total |',
            '|---:|---|---:|---:|---:|---:|']
        for task in sorted({r['task'] for r in rows if r['benchmark'] == benchmark}):
            first_row = index[benchmark, 'sweagent', task]
            row = dict(benchmark=benchmark, task_id=first_row['task_id'], task=task)
            displays = []
            for harness, _ in AGENTS:
                raw = index[benchmark, harness, task]
                for metric in METRICS:
                    value = raw[metric]
                    row[harness + '_' + metric] = value
                    displays.append(str(value) if value is not None else '缺失')
            tasks.append(row)
            lines.append(f"| {row['task_id']} | {task} | " + ' | '.join(displays) + ' |')
        lines.append('')
    write_csv(output / 'per-task-trials.csv', tasks)
    (output / 'per-task-trials.md').write_text('\n'.join(lines) + '\n')


def plot(rows):
    configure_style()
    fig, axes = plt.subplots(1, 2, sharey=True, figsize=(8.5 * CM, 4.6 * CM), dpi=300)
    panels = []
    largest = max(r['total_trials'] or 0 for r in rows)
    bins = list(range(1, largest + 1))
    width = .35
    for j, (benchmark, title, count) in enumerate(BENCHMARKS):
        ax = axes[j]
        groups = [sorted((r for r in rows if r['benchmark'] == benchmark and r['harness'] == harness),
                         key=lambda r: r['task_id']) for harness, _ in AGENTS]
        # Display the common distribution once, after checking equality for
        # every task. The denominator remains the task count for one agent.
        assert all(len(group) == count for group in groups)
        observed = [[(r['task'], *(r[m] for m in METRICS)) for r in group] for group in groups]
        assert all(values == observed[0] for values in observed[1:]), 'agent counts differ; show them separately'
        group = groups[0]
        distributions = {}
        for metric_index, metric in enumerate(METRICS):
            frequencies = Counter(r[metric] for r in group)
            assert frequencies.get(None, 0) == 0, 'distribution requires a complete task population'
            values = [frequencies[n] for n in bins]
            assert sum(values) == count
            percentages = [100 * value / count for value in values]
            offset = (metric_index - .5) * width
            ax.bar([n + offset for n in bins], percentages, width=width,
                   color=COLORS[metric_index], edgecolor='white', linewidth=.3, zorder=3)
            distributions[metric] = dict(attempts=bins, task_counts=values,
                                        task_percentages=percentages)
        ax.set_title(f'({chr(97 + j)}) {title}', fontsize=7.5, pad=5)
        ax.set_xlabel('Candidate attempts', fontsize=7.5, labelpad=3)
        ax.set_xticks(bins)
        ax.set_xlim(.4, largest + .6)
        ax.set_ylim(0, 105)
        ax.set_yticks([0, 25, 50, 75, 100])
        ax.yaxis.grid(True, color='.9', linewidth=.4, zorder=0)
        if j == 0:
            ax.set_ylabel('Tasks (%)', fontsize=7.5, labelpad=3)
        frame(ax)
        panels.append(dict(benchmark=benchmark, harnesses=[a for a, _ in AGENTS],
            tasks_per_agent=count, missing=0, same_counts_for_every_task=True,
            first_passing_counts=dict(Counter(str(r['first_passing_trial']) for r in group)),
            total_counts=dict(Counter(str(r['total_trials']) for r in group)),
            distributions=distributions))
    handles = [Patch(facecolor=color, label=label) for label, color in zip(LABELS, COLORS)]
    fig.legend(handles=handles, loc='upper center', bbox_to_anchor=(.53, 1.0),
               ncol=len(handles), fontsize=7.5, columnspacing=1.2, handlelength=1.3)
    fig.subplots_adjust(left=.135, right=.985, bottom=.24, top=.75, wspace=.20)
    for suffix in ('pdf', 'png'):
        path = ROOT / 'motivation' / f'{STEM}.{suffix}'
        fig.savefig(path, dpi=300, bbox_inches='tight', pad_inches=.025)
        shutil.copyfile(path, ROOT / 'paper/img' / path.name)
    plt.close(fig)
    return panels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--output', type=Path, default=AUDIT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows, attempts, hashes, registration = load(args.source)
    write_data(args.output, rows, attempts, hashes)
    panels = plot(rows)
    report = dict(measurement=registration['measurement'], source=str(args.source),
        source_sha256=registration['source_sha256'], runs=len(rows), tasks=541,
        measured=sum(r['total_trials'] is not None for r in rows), external_model_calls=0,
        aggregation='identical per-agent distributions displayed once; no pooling or averaging',
        layout='one row, two benchmarks; single column',
        chart='grouped frequency bars', x_axis='candidate attempts', y_axis='tasks (%)',
        candidate_attempts_include_pre_verifier_rejections=True, panels=panels)
    write_json(args.output / 'figure-data.json', report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
