"""Keep incompatible environment/budget histories out of cumulative pairs."""
import json
from pathlib import Path

from experiments.scripts.run_tb_dual_local import configuration_id, result_configuration


def separate_comparisons(report, cells, retries):
    histories={}
    for row in cells+retries:
        if row['state'] not in ('completed','infrastructure_error'):
            continue
        path=Path(row['result_path']); result=json.loads(path.read_text())
        config=result_configuration(result,path)
        histories.setdefault((row['task'],row['harness'],row['policy']),[]).append((row,result,config))
    groups={}; excluded=[]
    for key,history in histories.items():
        task,harness,policy=key
        configs={configuration_id(config) for _,_,config in history}
        if len(configs)!=1:
            excluded.append(dict(task=task,harness=harness,policy=policy,reason='mixed_environment_or_protocol_history'))
            continue
        row,result,config=history[-1]
        group=groups.setdefault(next(iter(configs)),dict(configuration=config,by={}))
        if not result.get('success') or not all(r['state']=='completed' and d.get('api_usage',{}).get('complete') for r,d,_ in history):
            continue
        tokens=sum(d['api_usage']['total_tokens'] for _,d,_ in history)
        group['by'].setdefault(harness,{}).setdefault(policy,{})[task]=tokens
    output={}
    for cid,group in groups.items():
        pairs={}
        for harness,policies in group['by'].items():
            causal=policies.get('causal',{}); pairs[harness]={}
            for policy,control in policies.items():
                if policy=='causal':continue
                tasks=sorted(causal.keys()&control.keys())
                a=sum(causal[t] for t in tasks); b=sum(control[t] for t in tasks)
                pairs[harness][policy]=dict(both_success_pairs=len(tasks),tasks=tasks,
                    causal_cumulative_tokens=a,control_cumulative_tokens=b,saving_pct=100*(1-a/b) if b else None)
        output[cid]=dict(configuration=group['configuration'],cumulative_paired_success=pairs)
    report['cumulative_paired_success']={}
    report['cumulative_configuration_groups']=output
    report['cumulative_pair_exclusions']=excluded
    report['cumulative_pair_scope']='Identical configuration throughout both cells; mixed cache/limit histories excluded. All original/retry spend remains in known totals.'
    return report
