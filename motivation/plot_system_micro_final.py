#!/usr/bin/env python3
"""Paper figures for the completed 400-case paired recovery experiment."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import shutil
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from motivation.plot_motivation_four import configure_style, CM, STYLES as MOTIVATION_STYLES
from motivation.paper_labels import WORK_LABELS

DATA = ROOT/'review/system-micro-results-20261031'
RECOVERY_DATA = ROOT/'review/controlled-both-agents-20261110/four-policy'
FIGURE_AUDIT = RECOVERY_DATA/'recovery-figure'
POLICIES = ('causal', 'temporal_checkpoint')
RECOVERY_POLICIES = ('causal', 'turn_checkpoint',
                     'temporal_checkpoint', 'whole_branch_abort')
LABELS = {'crash_no_rollback': 'No rollback', **WORK_LABELS}
COLORS = {'causal':MOTIVATION_STYLES['ideal_oracle']['color'],
          'temporal_checkpoint':MOTIVATION_STYLES['temporal_step']['color'],
          'crash_no_rollback':MOTIVATION_STYLES['no_recovery']['color'],
          'turn_checkpoint':MOTIVATION_STYLES['turn_checkpoint_proxy']['color'],
          'whole_branch_abort':MOTIVATION_STYLES['whole_branch']['color'],
          'root_only':MOTIVATION_STYLES['root_only']['color']}
STYLES = {'causal':dict(color=COLORS['causal'], marker='s', linestyle='-', markersize=3),
          'temporal_checkpoint':dict(color=COLORS['temporal_checkpoint'], marker='x', linestyle=':', markersize=3.7),
          'crash_no_rollback':dict(color=COLORS['crash_no_rollback'], marker='v', linestyle=(0,(2,1)), markerfacecolor='none', markersize=3),
          'turn_checkpoint':dict(MOTIVATION_STYLES['turn_checkpoint_proxy'], markersize=3),
          'whole_branch_abort':dict(MOTIVATION_STYLES['whole_branch'], markersize=3),
          'root_only':dict(MOTIVATION_STYLES['root_only'], markersize=3.5)}
FAMILIES = ('mb2_task_length','mb3_fault_position','mb4_dependency')
TITLES = ('Task length', 'Fault position', 'Dependency value')


def setup():
    configure_style()


def load():
    with (DATA/'runs.csv').open() as f:
        rows = list(csv.DictReader(f))
    numeric = ('operation_count','repeat','fault_position','dependency_value','total_tokens',
               'full_wall_s','probe_count','root_rank','valid_retention','selection_precision',
               'materialize_wall_s','policy_wall_s','model_wall_s','verification_wall_s')
    for r in rows:
        for k in numeric:
            r[k] = float(r[k]) if r[k] else None
        r['success'] = r['success']=='True'
    return rows, json.loads((DATA/'analysis.json').read_text())


def frame(ax):
    ax.tick_params(direction='in', top=True, right=True, labelsize=7, length=2.5, pad=2)


def load_recovery(original_rows):
    """Join audited recovery results to the original manifest coordinates."""
    coordinates = {}
    for r in original_rows:
        values = {k:r[k] for k in ('operation_count','fault_position','dependency_value')}
        assert r['trace_id'] not in coordinates or coordinates[r['trace_id']] == values
        coordinates[r['trace_id']] = values
    with (RECOVERY_DATA/'runs.csv').open() as f:
        rows = [r for r in csv.DictReader(f) if r['policy'] in RECOVERY_POLICIES]
    assert len(rows) == 9600
    for r in rows:
        for k in ('repeat','operation_count','total_tokens','full_wall_s','replayed_operations'):
            r[k] = float(r[k])
        assert r['operation_count'] == coordinates[r['trace_id']]['operation_count']
        r.update(coordinates[r['trace_id']])
        r['success'] = r['completed'] == 'True'
    return rows


def save(fig, stem):
    for suffix in ('pdf','png'):
        p=ROOT/'motivation'/f'{stem}.{suffix}'
        fig.savefig(p, dpi=300, bbox_inches='tight', pad_inches=.025)
        shutil.copyfile(p,ROOT/'paper/img'/p.name)
    return fig


def mean_interval(group, field):
    cases={}
    for r in group:
        cases.setdefault(r['trace_id'],[]).append(r[field])
    values=np.asarray([np.mean(v) for v in cases.values()])
    rng=np.random.default_rng(20261031)
    means=values[rng.integers(0,len(values),(20000,len(values)))].mean(axis=1)
    return float(values.mean()),np.quantile(means,[.025,.975])


def recovery(rows):
    assert {r['policy'] for r in rows} == set(RECOVERY_POLICIES)
    groups={}
    for r in rows:
        group=groups.setdefault((r['harness'],r['trace_id'],r['repeat']),{})
        assert r['policy'] not in group
        group[r['policy']]=r
    assert len(groups)==2400 and all(set(g)==set(RECOVERY_POLICIES) for g in groups.values())
    eligible={k for k,g in groups.items() if all(r['success'] for r in g.values())}
    assert len(eligible)==2088
    paired=[r for r in rows if (r['harness'],r['trace_id'],r['repeat']) in eligible]
    points=[]
    fig,axes=plt.subplots(3,2,figsize=(8.5*CM,11.4*CM),dpi=300)
    fields=('operation_count','fault_position','dependency_value')
    for col,(family,xfield) in enumerate(zip(FAMILIES,fields)):
        group=[r for r in paired if r['family']==family]
        for row,metric in enumerate(('total_tokens','full_wall_s')):
            ax=axes[col,row]
            # Draw AgentTX last so overlapping curves do not hide it.
            for p in (*RECOVERY_POLICIES[1:], RECOVERY_POLICIES[0]):
                x=sorted({r[xfield] for r in group}); means=[]; low=[]; high=[]
                for value in x:
                    selected=[r for r in group if r[xfield]==value and r['policy']==p]
                    mean,interval=mean_interval(selected,metric)
                    points.append(dict(family=family, xfield=xfield, x=value, metric=metric,
                                       policy=p, runs=len(selected), cases=len({r['trace_id'] for r in selected}),
                                       mean=mean, ci95=interval.tolist()))
                    scale=1000 if metric=='total_tokens' else 1
                    means.append(mean/scale);low.append(interval[0]/scale);high.append(interval[1]/scale)
                display_x=np.asarray(x)*(100 if xfield=='fault_position' else 1)
                ax.plot(display_x,means,label=LABELS[p],**STYLES[p],markevery=2 if len(x)>10 else 1)
                ax.fill_between(display_x,low,high,color=COLORS[p],alpha=.10,linewidth=0)
            ax.set_title(f'({chr(97+col*2+row)}) {TITLES[col]}',fontsize=8,pad=4)
            ax.set_ylabel('API tokens (k)' if row==0 else 'Workflow time (s)',fontsize=7.5,labelpad=3)
            ax.set_xlabel(('Original tool calls','Fault position (%)','Dependency value')[col],fontsize=7.5,labelpad=3)
            if col==0:ax.set_xticks([16,80,160])
            elif col==1:ax.set_xticks([10,50,90])
            else:ax.set_xticks([0,.5,1])
            ax.locator_params(axis='y',nbins=3)
            ax.set_ylim(bottom=0);frame(ax)
    handles=[Line2D([],[],label=LABELS[p],**STYLES[p]) for p in RECOVERY_POLICIES]
    fig.legend(handles=handles,loc='upper center',bbox_to_anchor=(.54,1.005),ncol=2,fontsize=7,
               borderpad=.35,columnspacing=1,handlelength=1.8,handletextpad=.45,labelspacing=.4)
    fig.subplots_adjust(left=.15,right=.985,bottom=.085,top=.825,wspace=.60,hspace=.94)
    FIGURE_AUDIT.mkdir(parents=True,exist_ok=True)
    audit=dict(source=str(RECOVERY_DATA/'runs.csv'),policies=RECOVERY_POLICIES,
               successful_agent_case_repetitions=len(eligible), cases=len({k[1] for k in eligible}),
               successful_groups_by_harness={h:sum(k[0]==h for k in eligible) for h in ('sweagent','codex')},
               bootstrap_reps=20000, bootstrap_unit='original case; average available successful harness/repeat observations within each case',
               common_task_set_for_every_curve=True, points=points,
               redone_means={p:float(np.mean([r['replayed_operations'] for r in paired if r['policy']==p])) for p in RECOVERY_POLICIES})
    (FIGURE_AUDIT/'figure-data.json').write_text(json.dumps(audit,indent=2)+'\n')
    return save(fig,'FIG-System-Micro-Recovery')


def localization(rows, report):
    fig,axes=plt.subplots(1,2,figsize=(17.8*CM,4.9*CM),dpi=300)
    a=report['statistics']['causal']
    labels=['Top-ranked\ncandidate','Verified\nroot','Exact\nclosure','File/history\nagreement']
    values=[100*a['top1']/1200,100*a['localized_exact']/1200,
            100*a['exact_selective_state']/1200,100*a['conversation_matches_rollback']/1200]
    ax=axes[0]
    ax.bar(range(4),values,width=.55,facecolor='#f2dfdf',edgecolor=COLORS['causal'],linewidth=.8)
    ax.plot(range(4),values,linestyle='none',marker='s',color=COLORS['causal'],markersize=3)
    for x,y in enumerate(values):ax.text(x,y+3,f'{y:.0f}',ha='center',fontsize=7)
    ax.set_xticks(range(4),labels,fontsize=6.7);ax.set_ylabel('Runs (%)');ax.set_ylim(0,115)
    ax.set_yticks([0,25,50,75,100]);ax.set_title('(a) From ranking to validated recovery',fontsize=8,pad=5);frame(ax)
    ax=axes[1]
    for p in POLICIES:
        group=[r for r in rows if r['policy']==p]
        x=sorted({r['operation_count'] for r in group})
        ax.plot(x,[np.mean([r['probe_count'] for r in group if r['operation_count']==v]) for v in x],
                label=LABELS[p],**STYLES[p])
    ax.set_xticks([16,48,80,112,160]);ax.set_xlabel('Original tool calls');ax.set_ylabel('Validation / boundary checks')
    ax.set_ylim(0,175);ax.set_title('(b) Checks used to select a recovery plan',fontsize=8,pad=5)
    ax.legend(loc='upper left',fontsize=6.6,borderpad=.35);frame(ax)
    fig.subplots_adjust(left=.07,right=.99,bottom=.26,top=.86,wspace=.30)
    return save(fig,'FIG-System-Micro-Localization')


def phases(rows):
    fig,axes=plt.subplots(1,3,figsize=(17.8*CM,5.2*CM),dpi=300,sharey=True)
    stages=[('materialize_wall_s','Execution + recording','#e8e8e8',''),
            ('policy_wall_s','Discovery + recovery','#bcbcbc','///'),
            ('model_wall_s','Model repair','#ffffff',''),
            ('verification_wall_s','Final verification','#747474','')]
    for col,family in enumerate(FAMILIES):
        ax=axes[col]
        for i,p in enumerate(POLICIES):
            group=[r for r in rows if r['family']==family and r['policy']==p]
            bottom=0
            for field,label,color,hatch in stages:
                height=np.mean([r[field] for r in group])
                ax.bar(i,height,bottom=bottom,width=.56,facecolor=color,edgecolor=COLORS[p],
                       linewidth=.7,hatch=hatch);bottom+=height
            ax.text(i,bottom+8,f'{bottom:.1f}',ha='center',fontsize=7,color=COLORS[p])
        ax.set_xticks([0,1],['AgentTX','Temporal'],fontsize=7)
        ax.set_title(f'({chr(97+col)}) {TITLES[col]}',fontsize=8,pad=4)
        ax.set_ylim(0,495);ax.set_xlim(-.6,1.6);frame(ax)
    axes[0].set_ylabel('Mean stage time (s)')
    fig.legend(handles=[Patch(facecolor=c,edgecolor='0.35',hatch=h,label=l) for _,l,c,h in stages],
               loc='upper center',bbox_to_anchor=(.53,1.01),ncol=2,fontsize=6.7,
               columnspacing=1.7,handlelength=1.5,borderpad=.35)
    fig.subplots_adjust(left=.075,right=.99,bottom=.14,top=.75,wspace=.20)
    return save(fig,'FIG-System-Micro-Phases')


def main():
    setup();rows,report=load()
    for fig in (recovery(load_recovery(rows)),localization(rows,report),phases(rows)):
        plt.close(fig)


if __name__=='__main__':
    main()
