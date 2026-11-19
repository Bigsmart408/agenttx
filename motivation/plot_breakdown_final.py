#!/usr/bin/env python3
"""Plot recovery checks and the paired agent-context synchronization ablation."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import shutil
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import FancyBboxPatch
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from motivation.plot_motivation_four import CM, configure_style, STYLES

BASE = ROOT/'review/system-micro-results-20261031'
CONTROLS = ROOT/'review/system-micro-controls-analysis-20261103'
OUT = ROOT/'review/evaluation-tables-breakdown-20261106'
RED = STYLES['ideal_oracle']['color']
BLUE = STYLES['turn_checkpoint_proxy']['color']
ORANGE = STYLES['temporal_step']['color']


def read_csv(path):
    with path.open() as f:
        return list(csv.DictReader(f))


def save(fig, name):
    for suffix in ('pdf','png'):
        p=ROOT/'motivation'/f'{name}.{suffix}'
        fig.savefig(p,dpi=300,bbox_inches='tight',pad_inches=.025)
        shutil.copyfile(p,ROOT/'paper/img'/p.name)
    plt.close(fig)


def frame(ax):
    ax.tick_params(direction='in',top=True,right=True,labelsize=6.5,length=2.4,pad=2)
    ax.grid(axis='y',color='#dddddd',linewidth=.4)
    ax.set_axisbelow(True)


def correctness():
    data=json.loads((BASE/'analysis.json').read_text())
    a=data['statistics']['causal']
    rows=read_csv(BASE/'runs.csv')
    n=a['runs']
    fields=('localized_exact','exact_selective_state','retained_bytes_unchanged','conversation_matches_rollback')
    fig=plt.figure(figsize=(8.5*CM,6.5*CM),dpi=300)
    grid=fig.add_gridspec(2,1,height_ratios=[1.15,2.35])
    audit_ax=fig.add_subplot(grid[0]);audit_ax.set_axis_off()
    audit_ax.text(0,1.12,'(a) Recovery checks',fontsize=8,va='bottom',transform=audit_ax.transAxes)
    names=('Fault\nroot','Rollback\nset','Retained\nfiles','Agent\ncontext')
    for i,(name,field) in enumerate(zip(names,fields)):
        x=i*.255
        audit_ax.add_patch(FancyBboxPatch((x,.02),.235,.94,
            boxstyle='round,pad=0.006,rounding_size=0.025',
            facecolor='white',edgecolor=BLUE,linewidth=.65,
            transform=audit_ax.transAxes,clip_on=False))
        audit_ax.text(x+.1175,.65,name,ha='center',va='center',fontsize=7.1,
                      color=BLUE,linespacing=1.05,transform=audit_ax.transAxes)
        audit_ax.text(x+.1175,.20,f'{a[field]:,}/{n:,}',ha='center',va='center',
                      fontsize=7.3,color=RED,transform=audit_ax.transAxes)
    ax=fig.add_subplot(grid[1])
    points=[]
    for policy,color,marker,line in [('causal',RED,'s','-'),('temporal_checkpoint',ORANGE,'x',':')]:
        group=[r for r in rows if r['policy']==policy]
        x=sorted({int(r['operation_count']) for r in group})
        y=[float(np.mean([float(r['probe_count']) for r in group if int(r['operation_count'])==v])) for v in x]
        ax.plot(x,y,color=color,marker=marker,linestyle=line,markersize=3,linewidth=.9)
        points.extend(dict(policy=policy,original_calls=v,mean_checks=w) for v,w in zip(x,y))
    ax.set_xlim(10,166);ax.set_xticks([16,48,80,112,160])
    ax.set_ylim(0,175);ax.set_yticks([0,40,80,120,160])
    ax.set_xlabel('Original tool calls',fontsize=8,labelpad=2)
    ax.set_ylabel('Checks per run',fontsize=8,labelpad=3)
    ax.set_title('(b) Validation checks',loc='left',fontsize=8,pad=7)
    frame(ax)
    ax.legend(handles=[Line2D([],[],color=RED,marker='s',markersize=3,label='AgentTX'),
                       Line2D([],[],color=ORANGE,marker='x',markersize=3,linestyle=':',label='Temporal checkpoint')],
              loc='upper left',fontsize=7,handlelength=1.8,
              handletextpad=.45,borderaxespad=.4,labelspacing=.3)
    fig.subplots_adjust(left=.16,right=.97,bottom=.16,top=.90,hspace=.42)
    save(fig,'FIG-System-Micro-Correctness')
    return dict(runs=n,checks={k:a[k] for k in fields},validation_points=points)


def context_ablation():
    rows=read_csv(CONTROLS/'runs.csv')
    report=json.loads((CONTROLS/'analysis.json').read_text())
    ablation=[r for r in rows if r['policy']=='causal_workspace_only']
    keys={(r['trace_id'],r['repeat']) for r in ablation}
    causal={(r['trace_id'],r['repeat']):r for r in rows if r['policy']=='causal' and (r['trace_id'],r['repeat']) in keys}
    pairs=[]
    for r in sorted(ablation,key=lambda r:(r['trace_id'],int(r['repeat']))):
        c=causal[r['trace_id'],r['repeat']]
        assert r['completed']==c['completed']=='True'
        pairs.append(dict(case=r['trace_id'],repeat=int(r['repeat']),
                          synchronized_tokens=int(c['total_tokens']),stale_tokens=int(r['total_tokens'])))
    assert len(pairs)==36 and len({r['case'] for r in pairs})==12
    sync=np.array([r['synchronized_tokens'] for r in pairs],dtype=float)/1000
    stale=np.array([r['stale_tokens'] for r in pairs],dtype=float)/1000
    saving=100*(1-sync.sum()/stale.sum())
    assert np.isclose(saving,report['ablation']['metrics']['total_tokens']['saving_pct'])
    assert int((sync<stale).sum())==35
    fig,ax=plt.subplots(figsize=(8.5*CM,5.4*CM),dpi=300)
    case_order=sorted({r['case'] for r in pairs})
    positions={case:i+1 for i,case in enumerate(case_order)}
    x=np.array([positions[r['case']]+(r['repeat']-1)*.20 for r in pairs])
    ax.vlines(x,np.minimum(sync,stale),np.maximum(sync,stale),color='#b4bcc4',linewidth=.85,zorder=2)
    ax.plot(x,stale,linestyle='none',marker='o',markersize=3.2,
            markerfacecolor='white',markeredgecolor=BLUE,markeredgewidth=.85,zorder=3)
    ax.plot(x,sync,linestyle='none',marker='s',markersize=2.8,
            markerfacecolor=RED,markeredgecolor=RED,markeredgewidth=.5,zorder=4)
    ax.set_xlim(.4,12.6);ax.set_ylim(0,680)
    ax.set_xticks(range(1,13));ax.set_yticks([0,200,400,600])
    ax.set_xlabel('Controlled case (three repeats each)',fontsize=8,labelpad=3)
    ax.set_ylabel('API tokens (thousands)',fontsize=8,labelpad=3)
    frame(ax)
    fig.legend(handles=[Line2D([],[],linestyle='none',marker='o',markersize=3.5,
                              markerfacecolor='white',markeredgecolor=BLUE,label='Stale context'),
                       Line2D([],[],linestyle='none',marker='s',markersize=3.2,
                              color=RED,label='Synchronized context')],
              loc='upper center',bbox_to_anchor=(.565,1.005),ncol=2,fontsize=7,
              handlelength=.8,handletextpad=.4,columnspacing=1.2,borderaxespad=0)
    ax.text(.035,.91,f'Mean: {stale.mean():.1f}K → {sync.mean():.1f}K  (−{saving:.2f}%)',
            ha='left',va='center',fontsize=7,transform=ax.transAxes,color='#222222')
    fig.subplots_adjust(left=.16,right=.97,bottom=.18,top=.88)
    save(fig,'FIG-System-Micro-Context-Ablation')
    return dict(pairs=pairs,synchronized_mean_tokens=float(sync.mean()*1000),
                stale_mean_tokens=float(stale.mean()*1000),saving_pct=saving,
                saving_ci95=report['ablation']['metrics']['total_tokens']['ci95_pct'],
                successes={'synchronized':36,'stale_context':36},fewer_token_pairs=35,
                case_order=case_order,plot='36 paired vertical segments; three runs per case')


def main():
    configure_style();OUT.mkdir(parents=True,exist_ok=True)
    result=dict(correctness=correctness(),context_ablation=context_ablation())
    sources=[BASE/'analysis.json',BASE/'runs.csv',CONTROLS/'analysis.json',CONTROLS/'runs.csv']
    result['sources']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    (OUT/'breakdown-figure-data.json').write_text(json.dumps(result,indent=2)+'\n')
    print('Generated single-column recovery-correctness and context-ablation figures.')


if __name__=='__main__':
    main()
