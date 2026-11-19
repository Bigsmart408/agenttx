#!/usr/bin/env python3
"""Plot the completed autonomous SWE-agent/Codex Clean campaign."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from motivation.plot_motivation_four import configure_style, CM
from motivation.paper_palette import BAR_PALETTE

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'review/native-clean-observation-sensitivity-20261105'
SCOPE = 'mutations_or_observations'
STEM = 'FIG-Motivation-Native-Opportunity'
# Use Figure 10's fills; the legend identifies agents.
AGENT_STYLES = {
    'sweagent': dict(facecolor=BAR_PALETTE['orange'], edgecolor=BAR_PALETTE['orange']),
    'codex': dict(facecolor=BAR_PALETTE['teal'], edgecolor=BAR_PALETTE['teal']),
}
SUITES = ('swe_bench_lite', 'terminal_bench')
AGENT_LABELS = {'sweagent': 'SWE-agent', 'codex': 'Codex'}


def draw_incidence(ax, report: dict, metric: str, title: str) -> list[dict]:
    rows = []
    for group, suite in enumerate(SUITES):
        for offset, (harness, style) in zip((-0.235, 0.235), AGENT_STYLES.items()):
            stats = report['groups'][f'{suite}_{harness}'][SCOPE]
            count, total = stats[metric], stats['traces']
            percent = 100 * count / total
            x = group + offset
            ax.bar(x, percent, width=0.32, **style, linewidth=0.6, zorder=2,
                   label=AGENT_LABELS[harness] if group == 0 else None)
            ax.annotate(f'{percent:.1f}%', (x, percent),
                        xytext=(-2 if offset < 0 else 2, 3),
                        textcoords='offset points', ha='center', va='bottom', fontsize=7)
            rows.append(dict(suite=suite, harness=harness, metric=metric,
                             numerator=count, denominator=total, percent=percent))
    tick_labels = []
    for name, suite in zip(('SWE', 'TB'), SUITES):
        sizes = [report['groups'][f'{suite}_{h}'][SCOPE]['traces'] for h in AGENT_STYLES]
        tick_labels.append(f'{name}\nN = {sizes[0]}, {sizes[1]}')
    ax.set_xticks([0, 1], tick_labels)
    ax.set_xlim(-0.62, 1.62)
    ax.set_ylim(0, 120)
    ax.set_yticks([0, 50, 100])
    ax.set_title(title, loc='left', fontsize=7, pad=5)
    ax.tick_params(axis='both', direction='in', top=True, right=True, length=2.4, pad=2)
    return rows


def main() -> None:
    configure_style()
    plt.rcParams.update({'font.size': 7, 'axes.labelsize': 7,
                         'xtick.labelsize': 6.3, 'ytick.labelsize': 6.3,
                         'legend.fontsize': 6.5})
    report = json.loads((DATA / 'summary.json').read_text())
    fig = plt.figure(figsize=(8.6 * CM, 4.2 * CM))
    dependencies = fig.add_axes([0.13, 0.24, 0.36, 0.55])
    interleaved = fig.add_axes([0.63, 0.24, 0.36, 0.55])
    rows = draw_incidence(dependencies, report, 'traces_with_file_dependency', '(a) File dependencies')
    rows += draw_incidence(interleaved, report, 'traces_with_interleaved_with_affected_useful_calls', '(b) Dependency interleaving')
    dependencies.set_ylabel('Complete traces (%)', labelpad=2)
    handles, labels = dependencies.get_legend_handles_labels()
    fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(0.55, 1.04),
               ncol=2, handlelength=1.0, handletextpad=0.4, columnspacing=1.3)
    (DATA / 'figure-data.json').write_text(json.dumps(dict(
        layout='Two measured panels, each covering both benchmarks and agents; introductory schematic is in Section 3.1.',
        input_summary_sha256=hashlib.sha256((DATA / 'summary.json').read_bytes()).hexdigest(),
        selected_scope=SCOPE,
        interleaving_definition='Three useful task calls i<j<k: i reaches k but not j in the complete recorded dependency graph. Candidates may modify workload files or return task observations.',
        panels=rows, shared_percentage_axis=[0, 120],
        percentage_ticks=[0, 50, 100],
        zero_opportunity_traces_included=True,
        displayed_denominator_order=list(AGENT_STYLES),
    ), indent=2) + '\n')
    for suffix in ['pdf', 'png']:
        path = ROOT / 'motivation' / f'{STEM}.{suffix}'
        fig.savefig(path, dpi=300, bbox_inches='tight', pad_inches=0.02)
        shutil.copyfile(path, ROOT / 'paper/img' / path.name)
    plt.close(fig)


if __name__ == '__main__':
    main()
