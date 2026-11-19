#!/usr/bin/env python3
"""Plot the controlled OverlayFS checkpoint-lifetime ablation."""
from pathlib import Path
import hashlib
import json
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

SOURCE = ROOT/'review/rq5-checkpoint-lifetime-20261116/summary.json'


def main():
    configure_style()
    plt.rcParams['hatch.linewidth'] = .45
    data = json.loads(SOURCE.read_text())
    assert data['verified_sessions'] == 192 and data['measured_sessions'] == 180
    variants = [('overlay-sync', 'OverlayFS (sync)', 'purple', ''),
                ('overlay-retained', '+ retained mounts', 'orange', ''),
                ('agenttx', 'Lancet', 'red', '//')]
    panels = [('per_checkpoint_ms', 'ms / checkpoint', '(a) Checkpoint creation'),
              ('lifecycle_ms', 'Workflow time (ms)', '(b) Complete workflow')]
    fig, axes = plt.subplots(1, 2, figsize=(8.5*CM, 4.15*CM))
    handles = []
    for ax, (metric, ylabel, title) in zip(axes, panels):
        maximum = 0
        for index, (variant, name, color, hatch) in enumerate(variants):
            metrics = [data['groups'][str(d)][variant][metric] for d in (1, 10, 30)]
            for measurement in metrics:
                assert measurement['n'] == len(measurement['samples']) == 20
                assert np.isclose(np.mean(measurement['samples']), measurement['mean'])
            means = np.asarray([m['mean'] for m in metrics])
            low = np.asarray([m['ci95'][0] for m in metrics])
            high = np.asarray([m['ci95'][1] for m in metrics])
            bars = ax.bar(np.arange(3)+(index-1)*.24, means, width=.225,
                          color=BAR_PALETTE[color], edgecolor='#3f3f3f', linewidth=.25,
                          hatch=hatch, yerr=[means-low, high-means],
                          error_kw=dict(ecolor='#343434',elinewidth=.45,capsize=1,capthick=.45),
                          zorder=3, label=name)
            if metric == 'per_checkpoint_ms':
                handles.append(bars)
            maximum = max(maximum, max(high))
        ax.set_xticks(range(3), ['1', '10', '30'])
        ax.set_xlim(-.5, 2.5)
        ax.set_ylim(0, maximum*1.13)
        ax.set_ylabel(ylabel, fontsize=7, labelpad=2)
        ax.set_xlabel('Checkpoint count\n'+title, fontsize=7, labelpad=4)
        ax.tick_params(direction='in', labelsize=6.5, length=2, pad=2)
        ax.tick_params(axis='x', length=0, pad=3)
        ax.yaxis.set_major_locator(plt.MaxNLocator(3))
        ax.grid(axis='y', color='#e5e5e5', linewidth=.3)
        ax.set_axisbelow(True)
    fig.legend(handles, [v[1] for v in variants], loc='upper center',
               bbox_to_anchor=(.53,1.005), ncol=3, fontsize=6.3,
               handlelength=1,handletextpad=.35,columnspacing=.8,borderaxespad=0)
    fig.subplots_adjust(left=.11,right=.99,bottom=.30,top=.82,wspace=.48)
    folder = ROOT/'paper/figures/rq5-checkpoint-lifetime'
    folder.mkdir(parents=True,exist_ok=True)
    for suffix in ('pdf','png'):
        dest = folder/f'FIG-RQ5-Checkpoint-Lifetime.{suffix}'
        fig.savefig(dest, dpi=300,bbox_inches='tight',pad_inches=.025)
        shutil.copy2(dest,ROOT/'paper/img'/dest.name)
    (folder/'figure-data.json').write_text(json.dumps(dict(source=str(SOURCE.relative_to(ROOT)),
        source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        error_bars='95% session-bootstrap intervals for mean; 10,000 resamples',
        groups=data['groups'],comparisons=data['comparisons'],variants=variants),indent=2)+'\n')
    plt.close(fig)


if __name__ == '__main__':
    main()
