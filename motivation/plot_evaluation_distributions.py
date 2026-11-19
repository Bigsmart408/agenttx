#!/usr/bin/env python3
"""Build evaluation plots from audited case records and returned API usage."""
from __future__ import annotations

from collections import defaultdict
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
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from motivation.plot_motivation_four import configure_style,CM,STYLES
from motivation.paper_palette import BAR_PALETTE
from motivation.paper_labels import WORK_LABELS

CONTROL=ROOT/'review/system-micro-controls-analysis-20261103'
COMBINED=ROOT/'review/controlled-both-agents-20261110/four-policy'
MICRO=ROOT/'review/system-micro-results-20261031'
REAL=ROOT/'review/realworld-results-20261106'
OUT=ROOT/'review/evaluation-figure-expansion-20261106'
POLICIES=('causal','crash_no_rollback','turn_checkpoint','temporal_checkpoint','whole_branch_abort')
LABELS=tuple('No rollback' if p=='crash_no_rollback' else WORK_LABELS[p] for p in POLICIES)
RED=STYLES['ideal_oracle']['color']
BLUE=STYLES['turn_checkpoint_proxy']['color']
ORANGE=STYLES['temporal_step']['color']
TEAL=STYLES['whole_branch']['color']
GRAY=STYLES['no_recovery']['color']
COLORS=dict(zip(POLICIES,(RED,GRAY,BLUE,ORANGE,TEAL)))
FAMILIES=('mb2_task_length','mb3_fault_position','mb4_dependency')
FAMILY_LABELS=('Length','Position','Dependency')
FAMILY_COLORS=(BLUE,TEAL,RED)
SOURCES={}


def digest(path):
    SOURCES[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    digest(path)
    return json.loads(path.read_text())


def read_csv(path):
    digest(path)
    with path.open() as f:
        return list(csv.DictReader(f))


def frame(ax,grid='x'):
    ax.tick_params(direction='in',top=True,right=True,length=2.5,pad=2,labelsize=7)
    ax.grid(axis=grid,color='#dddddd',linewidth=.45)
    ax.set_axisbelow(True)


def save(fig,stem):
    paths=[]
    for suffix in ('pdf','png'):
        p=ROOT/'motivation'/f'{stem}.{suffix}'
        fig.savefig(p,dpi=300,bbox_inches='tight',pad_inches=.025)
        shutil.copyfile(p,ROOT/'paper/img'/p.name)
        paths.append(str(p.relative_to(ROOT)))
    plt.close(fig)
    return paths


def box(ax,values,y,color):
    # The whiskers include the full observed range; diamonds mark means.
    ax.boxplot([values],positions=[y],vert=False,widths=.48,
               whis=(0,100),showfliers=False,patch_artist=True,manage_ticks=False,
               boxprops=dict(facecolor=matplotlib.colors.to_rgba(color,.14),edgecolor=color,linewidth=.8),
               medianprops=dict(color=color,linewidth=1.1),
               whiskerprops=dict(color=color,linewidth=.7),capprops=dict(color=color,linewidth=.7))
    ax.plot(np.mean(values),y,marker='D',color=color,markersize=3,linestyle='none',zorder=4)


def retained_and_redone(rows):
    grouped=defaultdict(dict)
    for r in rows:
        if r['policy'] in POLICIES:
            grouped[r['trace_id'],r['repeat']][r['policy']]=r
    assert len(grouped)==1200 and all(set(g)==set(POLICIES) for g in grouped.values())
    common={k for k,g in grouped.items() if all(r['completed']=='True' for r in g.values())}
    assert len(common)==1173
    fig,axes=plt.subplots(1,2,figsize=(8.5*CM,5.8*CM),sharey=True,dpi=300)
    stats={}
    for i,p in enumerate(POLICIES):
        cases=defaultdict(list)
        for r in rows:
            if r['policy']==p:
                cases[r['trace_id']].append(100*float(r['valid_retention']))
        retention=[float(np.mean(v)) for v in cases.values()]
        redo=[float(grouped[k][p]['replayed_operations']) for k in sorted(common)]
        assert len(retention)==400 and len(redo)==1173
        box(axes[0],retention,i,COLORS[p]);box(axes[1],redo,i,COLORS[p])
        stats[p]=dict(retention_case_means=retention,redone=redo,
                      retained_mean_pct=float(np.mean(retention)),redone_mean=float(np.mean(redo)))
    axes[0].set_yticks(range(5),['AgentTX','No rollback','Turn\ncheckpoint','Temporal\ncheckpoint','Whole branch'])
    axes[0].set_ylim(4.6,-.6)
    for ax in axes:
        frame(ax)
        ax.tick_params(axis='y',length=0,pad=4)
    axes[0].set_xlim(-5,105);axes[0].set_xticks([0,50,100])
    axes[1].set_xlim(-6,170);axes[1].set_xticks([0,80,160])
    axes[0].set_title('(a) Retained work',fontsize=8,pad=7)
    axes[1].set_title('(b) Re-execution',fontsize=8,pad=7)
    axes[0].set_xlabel('Valid work (%)',fontsize=8,labelpad=3)
    axes[1].set_xlabel('Operations redone',fontsize=8,labelpad=3)
    fig.subplots_adjust(left=.29,right=.98,bottom=.19,top=.84,wspace=.22)
    paths=save(fig,'FIG-System-Micro-Retention-Redo')
    return dict(figures=paths,common_success_pairs=1173,statistics=stats,
                box_definition='case means for retention; paired run counts for redo; box=IQR, line=median, whiskers=min/max, diamond=mean')


def workflow_time():
    data=read_json(REAL/'time-analysis.json')
    policies=('crash_no_rollback','temporal_checkpoint','whole_branch_abort','clean_recovery')
    colors=(GRAY,ORANGE,TEAL,'#7c5aa6')
    marks=('v','x','D','^')
    names=('No rollback','Temporal checkpoint','Whole branch','Clean reference')
    cohorts=('swe_sweagent','swe_codex','tb_sweagent','tb_codex')
    fig,ax=plt.subplots(figsize=(8.5*CM,6.7*CM),dpi=300)
    points=[]
    for i,key in enumerate(cohorts):
        for j,(p,color,mark) in enumerate(zip(policies,colors,marks)):
            c=data['cohorts'][key][p];y=i+(j-1.5)*.18
            lo,hi=c['ci95_pct'];s=c['saving_pct']
            ax.errorbar(s,y,xerr=[[s-lo],[hi-s]],fmt=mark,color=color,
                        markersize=3.5,markerfacecolor='white',markeredgewidth=.8,
                        elinewidth=.9,capsize=2,capthick=.7,zorder=3)
            points.append(dict(cohort=key,policy=p,saving_pct=s,ci95_pct=[lo,hi],pairs=c['n']))
    ax.axvline(0,color='#555555',linewidth=.7,linestyle='--')
    ax.set_yticks(range(4),['SWE /\nSWE-agent','SWE /\nCodex','TB /\nSWE-agent','TB /\nCodex'])
    ax.set_ylim(3.55,-.55);ax.set_xlim(-28,65);ax.set_xticks([-20,0,20,40,60])
    ax.set_xlabel('AgentTX workflow-time savings (%)',fontsize=8,labelpad=4)
    frame(ax);ax.tick_params(axis='y',length=0,pad=4)
    fig.legend(handles=[Line2D([],[],color=c,marker=m,markerfacecolor='white',
                              linestyle='-',markersize=3.5,label=n)
                        for c,m,n in zip(colors,marks,names)],
               loc='upper center',bbox_to_anchor=(.57,1.01),ncol=2,fontsize=7,
               handlelength=1.6,columnspacing=.7,handletextpad=.4,labelspacing=.4)
    fig.subplots_adjust(left=.24,right=.98,bottom=.17,top=.79)
    paths=save(fig,'FIG-Realworld-Workflow-Time')
    return dict(figures=paths,points=points)


def localization(rows):
    causal=[r for r in rows if r['policy']=='causal']
    assert len(causal)==1200
    cases=defaultdict(list)
    for r in causal:
        cases[r['trace_id']].append(r)
    assert len(cases)==400
    fig,axes=plt.subplots(1,2,figsize=(8.5*CM,5.9*CM),dpi=300)
    ranks={}
    for family,label,color in zip(FAMILIES,FAMILY_LABELS,FAMILY_COLORS):
        values=[]
        for group in cases.values():
            if group[0]['family']==family:
                v={int(r['root_rank']) for r in group};assert len(v)==1
                values.append(v.pop())
        x=np.sort(values);y=100*np.arange(1,len(x)+1)/len(x)
        axes[0].step(np.r_[0,x],np.r_[0,y],where='post',label=label,color=color,linewidth=1)
        ranks[family]=values
    axes[0].set_xlim(0,160);axes[0].set_ylim(0,105)
    axes[0].set_xticks([0,80,160]);axes[0].set_yticks([0,50,100])
    axes[0].set_xlabel('Initial fault-root rank',fontsize=7,labelpad=3)
    axes[0].set_ylabel('Cases (%)',fontsize=8,labelpad=2)
    axes[0].set_title('(a) Candidate ranking',fontsize=8,pad=7)
    axes[0].legend(loc='lower right',fontsize=6.2,handlelength=1.3,
                   labelspacing=.3,handletextpad=.35,borderaxespad=.35)
    checks=[]
    for p,label,color,marker,style in [('causal','AgentTX',RED,'s','-'),
                                     ('temporal_checkpoint','Temporal',ORANGE,'x',':')]:
        group=[r for r in rows if r['policy']==p]
        xx=sorted({int(r['operation_count']) for r in group})
        yy=[np.mean([float(r['probe_count']) for r in group if int(r['operation_count'])==n]) for n in xx]
        axes[1].plot(xx,yy,label=label,color=color,marker=marker,linestyle=style,
                     linewidth=1,markersize=2.7)
        checks.extend(dict(policy=p,operations=n,mean_checks=float(y)) for n,y in zip(xx,yy))
    axes[1].set_xlim(10,166);axes[1].set_ylim(0,175)
    axes[1].set_xticks([16,80,160]);axes[1].set_yticks([0,80,160])
    axes[1].set_xlabel('Original tool calls',fontsize=7,labelpad=3)
    axes[1].set_ylabel('Checks per run',fontsize=8,labelpad=2)
    axes[1].set_title('(b) Validation work',fontsize=8,pad=7)
    axes[1].legend(loc='upper left',fontsize=6.2,handlelength=1.3,
                   labelspacing=.3,handletextpad=.35,borderaxespad=.35)
    for ax in axes:frame(ax,'y')
    fig.subplots_adjust(left=.14,right=.98,bottom=.22,top=.85,wspace=.68)
    paths=save(fig,'FIG-System-Micro-Localization-Detail')
    return dict(figures=paths,rank_case_samples=ranks,validation_points=checks,
                all_correct_roots=sum(r['localized_exact']=='True' for r in causal))


def raw_result(row):
    path=Path(row['result_path'])
    if not path.exists():
        path=Path(str(path).replace('/home/bfq/agenttx-experiment-data/',
                                   '/home/bfq/agenttx-workspace/agenttx-experiment-data/'))
    raw=path.read_bytes()
    h=hashlib.sha256(raw).hexdigest();assert h==row['sha256']
    SOURCES[str(path)]=h
    return json.loads(raw)


def context_detail(rows):
    ab=[r for r in rows if r['policy']=='causal_workspace_only']
    causal={(r['trace_id'],r['repeat']):r for r in rows if r['policy']=='causal'}
    requests={'synchronized':[],'stale':[]}
    usage={p:dict(prompt_tokens=0,completion_tokens=0,total_tokens=0) for p in requests}
    pairs=[]
    for r in ab:
        c=causal[r['trace_id'],r['repeat']]
        for name,row in [('synchronized',c),('stale',r)]:
            d=raw_result(row);u=d['api_usage']
            assert u['complete'] and u['total_tokens']==int(row['total_tokens'])
            assert u['prompt_tokens']+u['completion_tokens']==u['total_tokens']
            assert len(u['requests'])==int(float(row['model_calls']))
            assert sum(q['prompt_tokens'] for q in u['requests'])==u['prompt_tokens']
            requests[name].extend(q['prompt_tokens'] for q in u['requests'])
            for k in usage[name]:usage[name][k]+=u[k]
        pairs.append(dict(case=r['trace_id'],repeat=int(r['repeat']),family=r['family'],
                          stale_pairs=int(r['stale_count']),
                          saving_pct=100*(1-int(c['total_tokens'])/int(r['total_tokens']))))
    assert len(pairs)==36 and sum(r['saving_pct']>0 for r in pairs)==35
    fig,axes=plt.subplots(1,2,figsize=(8.5*CM,6.0*CM),dpi=300)
    for name,label,color,style in [('synchronized','Synchronized',RED,'-'),('stale','Stale',BLUE,'--')]:
        v=np.sort(requests[name])/1000;y=100*np.arange(1,len(v)+1)/len(v)
        axes[0].step(np.r_[0,v],np.r_[0,y],where='post',label=label,color=color,linestyle=style,linewidth=1)
    axes[0].set_xlim(0,15);axes[0].set_ylim(0,105)
    axes[0].set_yticks([0,50,100]);axes[0].set_xticks([0,5,10,15])
    axes[0].set_xlabel('Input per request\n(thousand tokens)',fontsize=7,labelpad=3)
    axes[0].set_ylabel('Model requests (%)',fontsize=8,labelpad=2)
    axes[0].set_title('(a) Prompt cost',fontsize=8,pad=7)
    axes[0].legend(loc='lower right',fontsize=6.2,handlelength=1.3,
                   labelspacing=.3,handletextpad=.35,borderaxespad=.35)
    for family,label,color,marker in zip(FAMILIES,FAMILY_LABELS,FAMILY_COLORS,('o','D','s')):
        group=[r for r in pairs if r['family']==family]
        axes[1].scatter([r['stale_pairs'] for r in group],[r['saving_pct'] for r in group],
                        label=label,color=color,marker=marker,s=10,alpha=.75,linewidths=.4,
                        facecolors=color if family=='mb4_dependency' else 'none')
    axes[1].axhline(0,color='#555555',linewidth=.65,linestyle='--')
    axes[1].set_xlim(-3,64);axes[1].set_ylim(-15,60)
    axes[1].set_xticks([0,30,60]);axes[1].set_yticks([-10,0,25,50])
    axes[1].set_xlabel('Stale action/observation\npairs',fontsize=7,labelpad=3)
    axes[1].set_ylabel('Token savings (%)',fontsize=8,labelpad=2)
    axes[1].set_title('(b) Context savings',fontsize=8,pad=7)
    axes[1].legend(loc='lower right',fontsize=6.2,handlelength=1.0,
                   labelspacing=.3,handletextpad=.3,borderaxespad=.35)
    for ax in axes:frame(ax,'y')
    fig.subplots_adjust(left=.14,right=.98,bottom=.25,top=.85,wspace=.68)
    paths=save(fig,'FIG-System-Micro-Context-Detail')
    return dict(figures=paths,pairs=pairs,prompt_tokens_per_request=requests,usage=usage,
                prompt_saving_pct=100*(1-usage['synchronized']['prompt_tokens']/usage['stale']['prompt_tokens']),
                total_saving_pct=100*(1-usage['synchronized']['total_tokens']/usage['stale']['total_tokens']))


@matplotlib.rc_context({'hatch.linewidth':.45})
def stage_cost(rows):
    recorded_stages=('materialize_wall_s','policy_wall_s','model_wall_s')
    stages=('materialize_wall_s','model_wall_s','verification_other')
    # Show the measured execution/repair/check stages, excluding policy time.
    stage_colors=dict(zip(stages,(BAR_PALETTE['red'],BAR_PALETTE['orange'],
                                 BAR_PALETTE['teal'])))
    labels=('Execution / recording','Model repair','Verification / other')
    fig,axes=plt.subplots(1,3,sharey=True,figsize=(17.8*CM,5.2*CM),dpi=300)
    stats={}
    policies=('causal','turn_checkpoint','temporal_checkpoint','whole_branch_abort')
    harnesses=(('sweagent','SWE-agent',-.16,''),('codex','Codex',.16,'//'))
    counts=(300,270,630)
    titles=('Task length','Fault position','Dependency fraction')
    assert len(rows)==9600
    for j,(ax,family,title,expected) in enumerate(zip(axes,FAMILIES,titles,counts)):
        stats[family]={}
        maximum=0
        for harness,name,offset,hatch in harnesses:
            stats[family][harness]={}
            for i,p in enumerate(policies):
                group=[r for r in rows if (r['family'],r['harness'],r['policy'])==(family,harness,p)]
                assert len(group)==expected
                assert len({(r['trace_id'],r['repeat']) for r in group})==expected
                v={k:float(np.mean([float(r[k]) for r in group])) for k in recorded_stages}
                total=float(np.mean([float(r['full_wall_s']) for r in group]))
                v['verification_other']=total-sum(v.values());assert v['verification_other']>=0
                display={k:v[k] for k in stages}
                shown_total=sum(display.values())
                assert np.isclose(shown_total,total-v['policy_wall_s'])
                start=0
                for k in stages:
                    ax.barh(i+offset,display[k],left=start,height=.27,
                            color=stage_colors[k],edgecolor='#3f3f3f',
                            linewidth=.25,hatch=hatch,zorder=3)
                    start+=display[k]
                ax.annotate(f'{shown_total:.1f}',xy=(shown_total,i+offset),
                            xytext=(2,0),textcoords='offset points',
                            va='center',fontsize=6.7)
                maximum=max(maximum,shown_total)
                searches=[float(r['localization_search_wall_s']) for r in group
                          if r['localization_search_wall_s']]
                verifications=[float(r['verification_wall_s']) for r in group
                               if r['verification_wall_s']]
                stats[family][harness][p]=dict(
                    runs=len(group),cases=len({r['trace_id'] for r in group}),
                    full_wall_mean_s=total,stages=v,display_stages=display,
                    displayed_mean_s=shown_total,excluded_policy_mean_s=v['policy_wall_s'],
                    search_mean_s=float(np.mean(searches)) if searches else None,
                    verification_mean_s=float(np.mean(verifications)) if verifications else None,
                    successes=sum(r['completed']=='True' for r in group),
                )
        ax.set_yticks(range(4),[WORK_LABELS[p] for p in policies])
        ax.set_title(f'({chr(97+j)}) {title}',fontsize=7.5,pad=5)
        ax.set_ylim(3.55,-.55);ax.set_xlim(0,maximum*1.25)
        ax.xaxis.set_major_locator(MaxNLocator(nbins=3,steps=[1,2,2.5,5,10]))
        frame(ax)
        ax.tick_params(axis='y',length=0,labelsize=7,pad=4)
    fig.supxlabel('Mean time (s)',fontsize=7.5,x=.57,y=.02)
    handles=[Patch(facecolor=stage_colors[k],edgecolor='none',label=label)
             for k,label in zip(stages,labels)]
    handles.extend(Patch(facecolor='white',edgecolor='#3f3f3f',hatch=hatch,
                         linewidth=.4,label=name) for _,name,_,hatch in harnesses)
    fig.legend(handles=handles,loc='upper center',bbox_to_anchor=(.565,1.005),
               ncol=5,fontsize=7,borderpad=.1,handlelength=1.2,
               handletextpad=.4,columnspacing=1.2)
    fig.subplots_adjust(left=.15,right=.995,bottom=.19,top=.80,wspace=.17)
    paths=save(fig,'FIG-System-Micro-Stage-Cost')
    return dict(figures=paths,group_by=['family','harness','policy'],statistics=stats,
                stage_colors=stage_colors,
                layout=dict(rows=1,columns=3,width='double column',
                            orientation='horizontal',paired_bars=True,
                            shared_policy_labels=True,
                            legend_rows=1),
                include_failed_runs=True,
                display_stage_definition=dict(
                    materialize_wall_s='execution and recording',
                    model_wall_s='model-assisted repair',
                    verification_other='final verification plus remaining orchestration'),
                excluded_stage_definition=dict(
                    policy_wall_s='recovery-plan discovery and application'),
                displayed_metric='sum of the shown stages; excludes policy_wall_s and is not full-workflow elapsed time',
                verification_other_definition='verification plus full_wall_s residual outside the other three recorded stages')


def main():
    configure_style();OUT.mkdir(parents=True,exist_ok=True)
    rows=read_csv(CONTROL/'runs.csv');micro=read_csv(MICRO/'runs.csv')
    report=dict(retention_redo=retained_and_redone(rows),workflow_time=workflow_time(),
                localization=localization(micro),context=context_detail(rows),
                stages=stage_cost(read_csv(COMBINED/'runs.csv')))
    report['sources']=SOURCES
    (OUT/'figure-data.json').write_text(json.dumps(report,indent=2)+'\n')
    for k,v in report.items():
        if k!='sources':print(k,', '.join(v['figures']))


if __name__=='__main__':
    main()
