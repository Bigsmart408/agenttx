#!/usr/bin/env python3
"""Audit matched real-task quartets and decompose their recorded API cost."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'src')]
from experiments.scripts.recovery_cross_ablation import VARIANTS
from scripts.maintenance.watch_rq2_comparisons import project, digest


def decompose(values):
    a,b,c,d=(values[x] for x in VARIANTS)
    filesystem=((a-c)+(b-d))/2
    context=((a-b)+(c-d))/2
    total=a-d
    return dict(baseline_tokens=a,full_tokens=d,total_saved_tokens=total,
        filesystem_saved_tokens=filesystem,context_saved_tokens=context,
        filesystem_share_pct=100*filesystem/total if total else None,
        context_share_pct=100*context/total if total else None,
        combined_saving_pct=100*total/a if a else None,
        filesystem_saving_with_synced_context_pct=100*(b-d)/b if b else None,
        context_saving_with_restored_files_pct=100*(c-d)/c if c else None,
        nonadditivity_tokens=(a-d)-(a-b)-(a-c))


def analyze(cohort):
    cells=json.loads((cohort/'cells.json').read_text())
    groups=defaultdict(dict)
    rows=[]
    for cell in cells:
        if cell['state'] not in ('completed','infrastructure_error'):
            continue
        attempt=cell['attempts'][-1]
        path=Path(attempt['result_path'])
        if not path.is_file():
            continue
        if digest(path)!=attempt['result_sha256']:
            raise RuntimeError('terminal result changed: '+cell['key'])
        raw=json.loads(path.read_text())
        row=project(raw,path,cell['benchmark'],cell['harness'],cell['state'])
        proof=path.parent/'cross-treatment.json'
        row.update(variant=cell['variant'],key=cell['key'],treatment_path=str(proof))
        if proof.exists():
            p=json.loads(proof.read_text())
            if (p['protocol']!='agenttx.recovery-cross-treatment/v2' or
                p['variant']!=cell['variant'] or not p['original_observation_treatment_verified']):
                raise RuntimeError('invalid treatment: '+cell['key'])
            row['treatment']=p
            row['treatment_sha256']=digest(proof)
        rows.append(row)
        groups[(cell['benchmark'],cell['harness'],cell['task'])][cell['variant']]=row
    summaries={}
    for benchmark in ('swe','tb'):
        for harness in ('sweagent','codex'):
            quartets=[]
            for (b,h,t), variants in groups.items():
                if (b,h)!=(benchmark,harness) or set(variants)!=set(VARIANTS):
                    continue
                if not all(v.get('treatment') for v in variants.values()):
                    continue
                signatures={v['treatment']['injection_signature_sha256'] for v in variants.values()}
                if len(signatures)!=1:
                    raise RuntimeError('injected prefixes differ: '+t)
                for label, stale_name, sync_name in (
                    ('files-disabled','no_rollback','context_only'),
                    ('files-enabled','filesystem_only','agenttx')):
                    stale=variants[stale_name]['treatment']
                    synced=variants[sync_name]['treatment']
                    if (stale['rollback_ids']!=synced['rollback_ids'] or
                        stale['filesystem_after_policy']!=synced['filesystem_after_policy']):
                        raise RuntimeError('context arms have different filesystem recovery: '+t+'/'+label)
                for name in ('no_rollback','context_only'):
                    proof=variants[name]['treatment']
                    if proof['rollback_ids'] or not proof['file_disabled_preserves_injected_files']:
                        raise RuntimeError('file-disabled arm changed injected files: '+t+'/'+name)
                selections={tuple(variants[name]['treatment']['selected_causal_ids'])
                            for name in ('context_only','filesystem_only','agenttx')}
                if len(selections)!=1:
                    raise RuntimeError('component arms selected different causal sets: '+t)
                quartets.append((t,variants))
            def comparison(require_success):
                matched=[(t,v) for t,v in quartets if all(r['state']=='completed' and r['complete_usage']
                    and (r['success'] or not require_success) for r in v.values())]
                totals={variant:sum(v[variant]['tokens'] for _,v in matched) for variant in VARIANTS}
                return dict(tasks=len(matched),task_ids=[t for t,_ in matched],
                    variant_total_tokens=totals,contributions=decompose(totals) if matched else None)
            summaries[benchmark+'/'+harness]=dict(verified_quartets=len(quartets),
                all_complete_usage=comparison(False),common_task_success=comparison(True))
    return dict(protocol='agenttx.recovery-cross-analysis/v2',counts=dict(Counter(c['state'] for c in cells)),
        registered_cells=len(cells),recorded_results=len(rows),groups=summaries,rows=rows,
        attribution='average marginal token reduction across both orders of enabling filesystem recovery/context synchronization; negative contributions retained')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cohort',type=Path,required=True)
    args=p.parse_args()
    result=analyze(args.cohort)
    target=args.cohort/'analysis.json'
    temporary=target.with_suffix('.tmp')
    temporary.write_text(json.dumps(result,indent=2)+'\n')
    temporary.replace(target)
    print(json.dumps({k:v for k,v in result.items() if k!='rows'}))


if __name__=='__main__':
    main()
