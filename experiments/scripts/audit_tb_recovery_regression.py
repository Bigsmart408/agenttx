#!/usr/bin/env python3
"""Read-only audit of TB handoffs, recorded usage, and matched historical cases."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from agenttx.conversation import ConversationLog
from experiments.scripts.bench_tb_image_native import logged_usage, result_success


def read(path):
    return json.loads(Path(path).read_text())


def audit(root, historical):
    progress=read(root/'progress.json')
    by={};issues=[];counts=Counter();usage_errors=[]
    for cell in progress['tasks']:
        if cell['state']!='completed':continue
        path=Path(cell['result_path']);row=read(path)
        if row.get('protocol')!='agenttx.tb-image-native-recovery/v6':continue
        folder=path.parent;h=row['harness'];p=row['policy'];task=row['task']
        by.setdefault(h,{}).setdefault(p,{})[task]=row
        counts['complete_v6']+=1
        counts['success_v6']+=result_success(row)
        raw=logged_usage(folder,h,row)
        if raw!=row['api_usage']:usage_errors.append(str(path))
        if p not in ('causal','ideal_oracle'):continue
        snapshot=read(folder/'recovered-conversation.json')
        conversation=ConversationLog.from_dict(snapshot)
        live=sum(len(t.active_calls()) for t in conversation.active_turns())
        counts['compact_prefixes']+=1
        counts['compact_prefixes_over_six_calls']+=live>6
        manifest=read(folder/'recovery-manifest.json')
        source=row['injected']['faulty_path']
        disclosed=manifest['invalidated'][0]['path']
        if Path(source).as_posix()!=disclosed:
            issues.append(dict(task=task,harness=h,policy=p,kind='fault_path_changed',
                               actual=source,disclosed=disclosed,result=str(path)))
        if h=='codex':
            model=read(folder/'model-result.json')
            commands=[e.get('item',{}).get('command','') for e in model.get('events',[])
                      if e.get('type')=='item.completed' and e.get('item',{}).get('type')=='command_execution']
            wrong=[c for c in commands if 'auto-localization-crash-verifier' in c]
            if wrong:issues.append(dict(task=task,harness=h,policy=p,kind='internal_verifier_as_command',commands=wrong,result=str(path)))
    oldrows=read(historical)['rows'];old={}
    for row in oldrows:
        if row['suite']=='tb':old.setdefault(row['mode'],{})[row['task']]=row
    matched={}
    for policy in ('crash_no_rollback','temporal_checkpoint','whole_branch_abort'):
        ca=by.get('codex',{}).get('causal',{});co=by.get('codex',{}).get(policy,{})
        tasks=sorted(ca.keys()&co.keys()&old.get('causal',{}).keys()&old.get(policy,{}).keys())
        tasks=[t for t in tasks if result_success(ca[t]) and result_success(co[t])
               and old['causal'][t]['success'] is True and old[policy][t]['success'] is True]
        def oldcost(row):return int(row['total_tokens'])+int(row['doc_replay_tokens'])
        a=sum(ca[t]['api_usage']['total_tokens'] for t in tasks);b=sum(co[t]['api_usage']['total_tokens'] for t in tasks)
        x=sum(oldcost(old['causal'][t]) for t in tasks);y=sum(oldcost(old[policy][t]) for t in tasks)
        matched[policy]=dict(tasks=tasks,four_way_success_pairs=len(tasks),
            native_saving_pct=100*(1-a/b) if b else None,historical_saving_pct=100*(1-x/y) if y else None,
            historical_causal_tokens=x,historical_control_tokens=y,native_causal_tokens=a,native_control_tokens=b)
    profile={}
    for task in ('build-tcc-qemu','build-stp'):
        for harness in by:
            for policy,rows in by[harness].items():
                if task not in rows:continue
                row=rows[task]
                profile[f'{task}/{harness}/{policy}']=dict(tokens=row['api_usage']['total_tokens'],
                    tools=row['tool_calls'],main_tokens=row['total_tokens'],replay_tokens=row['doc_replay_tokens'],
                    success=result_success(row),model_wall_s=row['timings']['model_wall_s'])
    return dict(checked_at=datetime.now(timezone.utc).isoformat(),source_cohort=str(root),
        counts=dict(counts),usage_recomputation_mismatches=usage_errors,handoff_issues=issues,
        issue_counts=dict(Counter(i['kind'] for i in issues)),matched_historical=matched,case_profiles=profile,
        historical_source=str(historical),historical_sha256=hashlib.sha256(historical.read_bytes()).hexdigest(),
        no_model_calls=True,raw_results_modified=False)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cohort',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--historical',type=Path,default=Path('/mnt/data/agenttx-runtime/results/codex_luna_max_realworld_5mode_full_20260930/merged/official_tasks.json'))
    args=p.parse_args();result=audit(args.cohort,args.historical)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({k:result[k] for k in ['counts','usage_recomputation_mismatches','issue_counts','matched_historical']},ensure_ascii=False))


if __name__=='__main__':main()
