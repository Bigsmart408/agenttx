#!/usr/bin/env python3
"""Plot RQ5 storage costs in six panels on a two-by-three grid."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from motivation.paper_palette import BAR_PALETTE
from motivation.plot_motivation_four import CM, configure_style

SOURCES = {
    'storage': ROOT/'review/rq5-backing-filesystems-20261117/summary.json',
    'partial': ROOT/'review/rq5-partial-overwrite-20261117/summary.json',
    'branchfs': ROOT/'review/rq5-branchfs-20261117/summary.json',
    'try': ROOT/'review/rq5-try-20261117/summary.json',
}
OUTPUT = ROOT/'paper/figures/rq5-storage'
RQ5_PALETTE = {'gray': '#b7bdc5', 'blue': '#6b8ba4', 'teal': BAR_PALETTE['teal'],
               'orange': BAR_PALETTE['orange'], 'red': BAR_PALETTE['red'],
               'gold': '#b8a05d'}
VARIANTS = {'native': ('Native FS', 'gray', ''),
            'overlay-sync': ('OverlayFS', 'blue', ''),
            'deltafs': ('DeltaBox', 'orange', ''),
            'branchfs': ('BranchFS', 'teal', ''),
            'try': ('try', 'gold', ''),
            'agenttx-overlay': ('Lancet', 'red', '//')}
LAYERED = ('overlay-sync', 'deltafs', 'branchfs', 'try', 'agenttx-overlay')


def main():
    configure_style()
    plt.rcParams['hatch.linewidth'] = .45
    summaries = {key: json.loads(path.read_text()) for key, path in SOURCES.items()}
    assert summaries['storage']['counts']['measured'] == 1040
    assert summaries['partial']['counts']['measured'] == 2700
    assert summaries['branchfs']['counts']['measured'] == 280
    assert summaries['try']['counts']['measured'] == 280
    assert len({s['kernel'] for s in summaries.values()}) == 1
    assert all(s['counts']['failed'] == 0 for s in summaries.values())
    fig = plt.figure(figsize=(17.8*CM, 9.2*CM))
    grid = fig.add_gridspec(2, 3, wspace=.50, hspace=.90)
    axes = [fig.add_subplot(grid[row, col]) for row in range(2) for col in range(3)]
    handles, evidence = {}, {fs: {} for fs in ('ext4', 'xfs-reflink')}
    panels = [
            ('partial', 'io', 'edit_write_kib', 1, 'KiB', 'Write I/O (KiB/edit)', 'Write I/O',
             ['1 MiB'], ('1024',), tuple(VARIANTS), ('ext4', 'xfs-reflink')),
            ('partial', 'io', 'edit_synced_ms', 1, 'ms', 'Time (ms/edit)', 'Edit latency',
             ['1 MiB'], ('1024',), tuple(VARIANTS), ('ext4', 'xfs-reflink')),
            ('storage', 'latency', 'snapshot_ms', 1, 'ms', 'ms / checkpoint', 'Checkpoint latency',
             ['1', '10', '30'], ('1', '10', '30'), LAYERED, ('ext4', 'xfs')),
            ('storage', 'latency', 'lifecycle_ms', 1, 'ms', 'Time (ms)', 'Workflow time',
             ['1', '10', '30'], ('1', '10', '30'), LAYERED, ('ext4', 'xfs')),
            ('storage', 'resources', 'storage_kib', 1024, 'MiB', 'Extra storage (MiB)', 'Storage overhead',
             ['1', '10', '30'], ('1', '10', '30'), LAYERED, ('ext4', 'xfs')),
            ('storage', 'resources', 'memory_kib', 1024, 'MiB', 'Extra memory (MiB)', 'Memory overhead',
             ['1', '10', '30'], ('1', '10', '30'), LAYERED, ('ext4', 'xfs')),
    ]
    for index, (ax, panel) in enumerate(zip(axes, panels)):
        campaign, section, metric, divisor, unit, ylabel, title, ticks, cases, variants, filesystems = panel
        letter = chr(ord('a')+index)
        width, gap = .72/len(variants), .65
        lower, upper = 0., 0.
        positions = []
        for block, filesystem in enumerate(filesystems):
            data = (summaries[campaign]['io'][filesystem] if campaign == 'partial'
                    else summaries[campaign]['filesystems'][filesystem][section])
            data = {**data, **(summaries['branchfs']['io'][filesystem] if campaign == 'partial'
                             else summaries['branchfs']['filesystems'][filesystem][section])}
            data = {**data, **(summaries['try']['io'][filesystem] if campaign == 'partial'
                             else summaries['try']['filesystems'][filesystem][section])}
            key = 'xfs-reflink' if filesystem == 'xfs' else filesystem
            x = np.arange(len(cases))+block*(len(cases)+gap)
            positions.extend(x)
            evidence[key][letter] = dict(campaign=campaign, source_filesystem=filesystem,
                                        metric=metric, unit=unit, groups={},
                                        variant_sources={v: v if v in ('branchfs', 'try') else campaign
                                                         for v in variants})
            for offset, variant in enumerate(variants):
                name, color, hatch = VARIANTS[variant]
                groups = [data[variant][case][metric] for case in cases]
                expected = 10 if section == 'resources' else 20
                assert all(g['n'] == len(g['samples']) == expected for g in groups)
                assert all(np.isclose(np.mean(g['samples']), g['mean']) for g in groups)
                means = np.array([g['mean'] for g in groups])/divisor
                lows = np.array([g['ci95'][0] for g in groups])/divisor
                highs = np.array([g['ci95'][1] for g in groups])/divisor
                bars = ax.bar(x+(offset-(len(variants)-1)/2)*width, means, width=width*.94,
                    color=RQ5_PALETTE[color], hatch=hatch, edgecolor='#3f3f3f', linewidth=.3,
                    yerr=np.maximum(0, [means-lows, highs-means]), zorder=3,
                    error_kw=dict(ecolor='#343434', elinewidth=.5, capsize=1.2, capthick=.5))
                handles[variant] = bars
                evidence[key][letter]['groups'][variant] = {
                    case:dict(mean=float(mean), ci95=[float(low), float(high)], n=g['n'])
                    for case, mean, low, high, g in zip(cases, means, lows, highs, groups)}
                lower, upper = min(lower, min(lows)), max(upper, max(highs))
            if campaign != 'partial':
                ax.text((block+.5)/len(filesystems), 1.035, 'Ext4' if filesystem == 'ext4' else 'XFS',
                        transform=ax.transAxes, ha='center', va='bottom',
                        fontsize=7.8, fontweight='bold')
            assert 'deltafs' in evidence[key][letter]['groups']
        ax.set_xticks(positions, ['Ext4', 'XFS'] if campaign == 'partial'
                      else ticks*len(filesystems))
        ax.set_xlim(-.5, positions[-1]+.5)
        if campaign != 'partial':
            ax.axvline(len(cases)-.5+gap/2, color='#aaaeb3',
                       linewidth=.5, linestyle=(0, (3, 3)), zorder=1)
        ax.set_ylabel(ylabel, fontsize=8, labelpad=3)
        ax.set_xlabel(('Backing filesystem' if campaign == 'partial' else 'Checkpoint count')+
                      f'\n({letter}) {title}', fontsize=8, labelpad=5)
        ax.tick_params(direction='in', labelsize=7.3, length=2, pad=2)
        ax.tick_params(axis='x', length=0, pad=3)
        ax.grid(axis='y', color='#e5e5e5', linewidth=.35, zorder=0)
        ax.set_axisbelow(True)
        ax.yaxis.set_major_locator(plt.MaxNLocator(4))
        ax.set_ylim(lower*1.25, upper*1.15)
        if metric == 'edit_write_kib':
            ax.set_yscale('log', base=2)
            ax.set_ylim(2, 1600)
            ax.set_yticks([4, 16, 64, 256, 1024], ['4', '16', '64', '256', '1024'])
        if metric in ('snapshot_ms', 'lifecycle_ms'):
            positive = [point['ci95'][0] for filesystem in evidence.values()
                        for group in filesystem[letter]['groups'].values()
                        for point in group.values() if point['ci95'][0] > 0]
            ax.set_yscale('log')
            ax.set_ylim(min(positive)/10, upper*2)
            ax.yaxis.set_major_locator(matplotlib.ticker.LogLocator(base=10, numticks=12))
            ax.yaxis.set_minor_locator(matplotlib.ticker.NullLocator())
        if lower < 0:
            ax.axhline(0, color='#737373', linewidth=.35)
    fig.legend([handles[v] for v in VARIANTS], [VARIANTS[v][0] for v in VARIANTS],
        loc='upper center', bbox_to_anchor=(.53, .998), ncol=6, fontsize=7.7,
        handlelength=1.25, handleheight=.85, handletextpad=.45, columnspacing=1.15, borderaxespad=0)
    fig.subplots_adjust(left=.075, right=.995, bottom=.16, top=.885)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for suffix in ('pdf', 'png'):
        path = OUTPUT/f'FIG-RQ5-Storage.{suffix}'
        fig.savefig(path, dpi=300, bbox_inches='tight', pad_inches=.025)
        shutil.copy2(path, ROOT/'paper/img'/path.name)
    audit = dict(sources={key: dict(path=str(path.relative_to(ROOT)),
                     sha256=hashlib.sha256(path.read_bytes()).hexdigest()) for key, path in SOURCES.items()},
                 by_filesystem=evidence, palette=RQ5_PALETTE, variants=VARIANTS,
                 annotations=[],
                 layout='two by three; partial-update writes/latency, checkpoints, storage, memory',
                 filesystem_order=['ext4', 'xfs-reflink'], panel_count=6,
                 logarithmic_panels=['a', 'c', 'd'],
                 counts={key: summary['counts'] for key, summary in summaries.items()},
                 protocol={key: summary['protocol'] for key, summary in summaries.items()})
    (OUTPUT/'figure-data.json').write_text(json.dumps(audit, indent=2)+'\n')
    plt.close(fig)
    print('Generated six RQ5 panels; partial-update measurements replace file-commit comparisons.')


if __name__ == '__main__':
    main()
