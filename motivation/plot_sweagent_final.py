#!/usr/bin/env python3
"""Plot final SWE-agent/SWE-bench API costs using the historical SWE figure style."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from motivation.plot_motivation_four import CM, configure_style

DEFAULT = Path('/mnt/data/agenttx-runtime/validation/swe-paper-comparison-20261019/report.json')
# Preserve the four original encodings in plot_swe_tokens.ipynb. New reference
# policies receive separate colors; historical Codex figure files stay intact.
STYLES = {
    'crash_no_rollback': dict(color='#2f6f9f', marker='^', linestyle='-.', markerfacecolor='none'),
    'causal': dict(color='#c00000', marker='s', linestyle='-'),
    'temporal_checkpoint': dict(color='#e78129', marker='x', linestyle=':'),
    'whole_branch_abort': dict(color='black', marker='o', linestyle='--', markerfacecolor='none'),
    'ideal_oracle': dict(color='#7c5aa6', marker='D', linestyle=(0, (4, 1, 1, 1)), markerfacecolor='none'),
    'clean_recovery': dict(color='#16837a', marker='v', linestyle=(0, (1, 1)), markerfacecolor='none'),
}
LABELS = {
    'crash_no_rollback': 'No rollback', 'causal': 'AgentTX causal (ours)',
    'temporal_checkpoint': 'Temporal checkpoint', 'whole_branch_abort': 'Whole-branch rollback',
    'ideal_oracle': 'Ideal', 'clean_recovery': 'Clean',
}
SHORT = {'crash_no_rollback':'No RB', 'causal':'Causal', 'temporal_checkpoint':'Ckpt',
         'whole_branch_abort':'Branch', 'ideal_oracle':'Ideal', 'clean_recovery':'Clean'}
FACES = {'crash_no_rollback':'#77a9cf', 'causal':'#c00000',
         'temporal_checkpoint':'#e78129', 'whole_branch_abort':'#d9d9d9',
         'ideal_oracle':'#c0acd9', 'clean_recovery':'#9ac9bf'}
POLICIES = tuple(STYLES)
CONTROLS = tuple(p for p in POLICIES if p != 'causal')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, default=DEFAULT)
    args = parser.parse_args()
    font_root = Path('/mnt/data/agenttx-runtime/cache/fonts-urw-base35')
    for font in font_root.rglob('NimbusRoman*.otf'):
        font_manager.fontManager.addfont(str(font))
    # Fail explicitly rather than silently changing the paper's typeface.
    font_manager.findfont(font_manager.FontProperties(family='Nimbus Roman'), fallback_to_default=False)
    report = json.loads(args.report.read_text())
    phases_path = args.report.with_name('phase-accounting.json')
    phases = json.loads(phases_path.read_text())
    cells = {p:{} for p in POLICIES}
    for row in phases:
        assert row['task'] not in cells[row['policy']], 'duplicate task/policy'
        cells[row['policy']][row['task']] = row
    for p in POLICIES:
        group = report['groups'][p]
        assert len(cells[p]) == group['completed']
        assert sum(r['api_main'] + r['api_replay'] for r in cells[p].values()) == group['completed_api_tokens']

    configure_style()
    plt.rcParams.update({'font.family':'serif', 'font.serif':['Nimbus Roman','Times New Roman','Times'],
                         'legend.frameon':True, 'legend.edgecolor':'0.55', 'legend.framealpha':1.,
                         'legend.fancybox':False, 'hatch.linewidth':.45})
    fig, axes = plt.subplots(2, 2, figsize=(17.8*CM, 9.4*CM), dpi=300)
    ax = axes[0, 0]
    for i,p in enumerate(POLICIES):
        y = report['groups'][p]['success_pct_of_planned']
        ax.plot(i, y, **{**STYLES[p], 'linestyle':'none'}, markersize=3.5, markeredgewidth=.8)
        ax.text(i, y+3.8, f'{y:.1f}', ha='center', fontsize=6.4)
    ax.set_xticks(range(6), [SHORT[p] for p in POLICIES])
    ax.set_xlim(-.5, 5.5); ax.set_ylim(0, 110); ax.set_yticks([0,25,50,75,100])
    ax.set_ylabel('Task success (%)', fontsize=8)
    ax.set_xlabel('Recovery policy\n(a) Task outcomes', fontsize=7)

    ax = axes[0, 1]
    for i,p in enumerate(POLICIES):
        g = report['groups'][p]
        main = g['main_api_tokens']/g['completed']/1e3
        replay = g['replay_api_tokens']/g['completed']/1e3
        ax.bar(i, main, width=.65, facecolor=FACES[p], edgecolor=STYLES[p]['color'], linewidth=.55)
        if replay:
            ax.bar(i, replay, bottom=main, width=.65, facecolor='white',
                   edgecolor=STYLES[p]['color'], hatch='///', linewidth=.55)
        ax.text(i, main+replay+8, f'{main+replay:.0f}', ha='center', fontsize=6.4)
    ax.set_xticks(range(6), [SHORT[p] for p in POLICIES])
    ax.set_xlim(-.5,5.5); ax.set_ylim(0,355)
    ax.set_ylabel('Mean API tokens (K)', fontsize=8)
    ax.set_xlabel('Recovery policy\n(b) Main session + replay', fontsize=7)
    ax.legend(handles=[Patch(facecolor='0.7', edgecolor='0.3', label='Main'),
                       Patch(facecolor='white', edgecolor='0.3', hatch='///', label='Replay')],
              loc='upper right', fontsize=6, borderpad=.25, handlelength=1.1)

    ax = axes[1, 0]
    ax.axhline(0, color=STYLES['causal']['color'], linewidth=.7)
    for i,p in enumerate(CONTROLS):
        c = report['comparisons'][p]
        y = c['saving_pct']; lo,hi = c['saving_bootstrap_95_ci']
        style = {k:v for k,v in STYLES[p].items() if k != 'linestyle'}
        ax.errorbar(i, y, yerr=[[y-lo],[hi-y]], **style, linestyle='none',
                    markersize=3.5, markeredgewidth=.8, linewidth=.8, capsize=2, capthick=.6)
        ax.text(i, hi+4, f'{y:.2f}%', ha='center', fontsize=6.2)
    ax.set_xticks(range(5), [f"{SHORT[p]}\nn={report['comparisons'][p]['both_success_pairs']}" for p in CONTROLS])
    ax.set_xlim(-.5,4.5); ax.set_ylim(-15,78); ax.set_yticks([0,20,40,60])
    ax.set_ylabel('Total token savings (%)', fontsize=8)
    ax.set_xlabel('Matched successful tasks\n(c) Savings and 95% bootstrap interval', fontsize=7)

    ax = axes[1, 1]
    ax.axvline(1, color=STYLES['causal']['color'], linewidth=.7)
    curves = {}
    for p in CONTROLS:
        tasks = sorted(t for t in cells['causal'].keys() & cells[p].keys()
                       if cells['causal'][t]['success'] and cells[p][t]['success'])
        assert len(tasks) == report['comparisons'][p]['both_success_pairs']
        a = np.array([cells['causal'][t]['api_main']+cells['causal'][t]['api_replay'] for t in tasks])
        b = np.array([cells[p][t]['api_main']+cells[p][t]['api_replay'] for t in tasks])
        assert np.all(a > 0) and np.all(b > 0)
        assert np.isclose(100*(1-a.sum()/b.sum()), report['comparisons'][p]['saving_pct'])
        x = np.sort(a/b); y = np.arange(1,len(x)+1)/len(x)*100
        ax.plot(x, y, **STYLES[p], linewidth=.8, markersize=2.5, markeredgewidth=.6,
                drawstyle='steps-post', markevery=np.unique(np.linspace(0,len(x)-1,10,dtype=int)))
        curves[p] = {'tasks':tasks, 'sorted_causal_over_control':x.tolist(), 'cdf_pct':y.tolist()}
    ax.set_xscale('log'); ax.set_ylim(0,105); ax.set_yticks([0,25,50,75,100])
    ax.set_ylabel('Matched successful tasks (%)', fontsize=7)
    ax.set_xlabel('Causal / control API tokens (log scale)\n(d) Per-task cost distribution', fontsize=7)
    ax.text(.04,.92,'<1: Causal uses fewer tokens', transform=ax.transAxes, fontsize=6)

    for ax in axes.flat:
        ax.tick_params(axis='both', labelsize=7, direction='in', top=True, right=True)
    handles = [Line2D([],[],**STYLES[p],label=LABELS[p],linewidth=.9,markersize=3.2) for p in POLICIES]
    fig.legend(handles=handles, loc='upper center', bbox_to_anchor=(.5,1.015), ncol=3,
               fontsize=6.5, columnspacing=1.1, handlelength=1.8, handletextpad=.35, borderpad=.3)
    fig.tight_layout(pad=.55, h_pad=1.6, w_pad=1.15, rect=[0,0,1,.89])
    stem = 'FIG-SWEagent-SWEbench-Results'
    for suffix in ['pdf','png']:
        dest = ROOT/'motivation'/f'{stem}.{suffix}'
        fig.savefig(dest, bbox_inches='tight', pad_inches=.02, dpi=300)
        shutil.copyfile(dest, ROOT/'paper/img'/dest.name)
    plt.close(fig)
    manifest = dict(cohort=report['cohort'], report=str(args.report),
                    report_sha256=hashlib.sha256(args.report.read_bytes()).hexdigest(),
                    phase_accounting_sha256=hashlib.sha256(phases_path.read_bytes()).hexdigest(),
                    style_reference='motivation/plot_swe_tokens.ipynb',
                    planned_denominator=300, incomplete_attempt_excluded_from_mean=True,
                    figure=stem, curves=curves)
    (args.report.parent/'figure-data.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(ROOT/'paper/img'/f'{stem}.pdf')


if __name__ == '__main__':
    main()
