#!/usr/bin/env python3
"""Plot retained snapshot resources for OverlayFS, DeltaFS and AgentTX."""
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

SOURCE = ROOT/'review/rq5-deltafs-comparison-20261117/summary.json'


def main():
    configure_style()
    plt.rcParams['hatch.linewidth'] = .45
    data = json.loads(SOURCE.read_text())
    assert data['counts']['resource_sessions'] == 201 and data['counts']['failed'] == 0
    fig, axes = plt.subplots(1, 2, figsize=(8.5*CM, 4.15*CM))
    variants = [('overlay-sync', 'OverlayFS', 'purple', ''),
                ('deltafs', 'DeltaFS', 'orange', ''),
                ('agenttx-overlay', 'AgentTX', 'red', '//')]
    metrics = [('storage_kib', 'Extra storage (MiB)', '(a) Retained storage'),
               ('memory_kib', 'Extra memory (MiB)', '(b) Retained memory')]
    handles = []
    evidence = {}
    for ax, (metric, ylabel, title) in zip(axes, metrics):
        bounds = []
        width = .66 / len(variants)
        for index, (variant, name, color, hatch) in enumerate(variants):
            groups = [data['resources'][variant][str(d)][metric] for d in (1, 10, 30)]
            evidence.setdefault(variant, {})[metric] = dict(
                source=str(SOURCE.relative_to(ROOT)), metric=metric,
                depths={str(d): group for d, group in zip((1, 10, 30), groups)})
            assert all(g['n'] == len(g['samples']) == 10 for g in groups)
            assert all(np.isclose(np.mean(g['samples']), g['mean']) for g in groups)
            means = np.asarray([g['mean'] for g in groups])/1024
            low = np.asarray([g['ci95'][0] for g in groups])/1024
            high = np.asarray([g['ci95'][1] for g in groups])/1024
            bars = ax.bar(np.arange(3)+(index-(len(variants)-1)/2)*width, means, width=width*.94,
                color=BAR_PALETTE[color], hatch=hatch,
                edgecolor='#3f3f3f', linewidth=.25,
                yerr=[means-low, high-means], zorder=3,
                error_kw=dict(ecolor='#343434',elinewidth=.45,capsize=1,capthick=.45))
            if metric == 'storage_kib':
                handles.append(bars)
            bounds.extend([min(low), max(high)])
        ax.set_xticks(range(3), ['1','10','30'])
        ax.set_xlim(-.5, 2.5)
        ax.set_xlabel('Checkpoint count\n'+title, fontsize=7, labelpad=4)
        ax.tick_params(direction='in', labelsize=6.5, length=2, pad=2)
        ax.tick_params(axis='x', length=0, pad=3)
        ax.grid(axis='y', color='#e5e5e5', linewidth=.3)
        ax.set_axisbelow(True)
        ax.axhline(0, color='#737373', linewidth=.35)
        ax.yaxis.set_major_locator(plt.MaxNLocator(3))
        ax.set_ylabel(ylabel, fontsize=7, labelpad=2)
        # Keep all measured values and confidence limits, including the
        # negative memory increment from the native one-checkpoint rollover.
        floor = min(0, min(bounds)*1.2)
        ceiling = max(bounds)*1.13
        ax.set_ylim(floor, ceiling)
    fig.legend(handles, [variant[1] for variant in variants], loc='upper center',
        bbox_to_anchor=(.55,1.005), ncol=3, fontsize=6.8,
        handlelength=1.1, handletextpad=.4, columnspacing=1.3, borderaxespad=0)
    fig.subplots_adjust(left=.13,right=.99,bottom=.30,top=.82,wspace=.52)
    folder = ROOT/'paper/figures/rq5-snapshot-resources'
    folder.mkdir(parents=True,exist_ok=True)
    for suffix in ('pdf','png'):
        path = folder/f'FIG-RQ5-Snapshot-Resources.{suffix}'
        fig.savefig(path,dpi=300,bbox_inches='tight',pad_inches=.025)
        shutil.copy2(path,ROOT/'paper/img'/path.name)
    (folder/'figure-data.json').write_text(json.dumps(dict(
        sources={str(SOURCE.relative_to(ROOT)):hashlib.sha256(SOURCE.read_bytes()).hexdigest()},
        normalization='Enabled minus disabled resource growth for identical writes; no clipping.',
        groups=evidence),indent=2)+'\n')
    plt.close(fig)


if __name__ == '__main__':
    main()
