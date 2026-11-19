"""Pair component ablations only after checking scope and checkpoint equality."""
from __future__ import annotations
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from scripts.maintenance.watch_rq2_comparisons import project

VARIANTS=('context_only','filesystem_only','agenttx')


def compare(rows, control, *, require_all_three=False):
    matched=[]
    for key, variants in rows.items():
        needed=VARIANTS if require_all_three else (control,'agenttx')
        if all(v in variants and variants[v]['success'] and variants[v]['complete_usage']
               and variants[v]['state']=='completed' for v in needed):
            matched.append((key,variants))
    baseline=sum(v[control]['tokens'] for _,v in matched)
    synchronized=sum(v['agenttx']['tokens'] for _,v in matched)
    return dict(pairs=len(matched), task_ids=[k for k,_ in matched],
                control_tokens=baseline, synchronized_tokens=synchronized,
                saved_tokens=baseline-synchronized,
                saving_pct=100*(baseline-synchronized)/baseline if baseline else None,
                control_workflow_s=sum(v[control]['duration_s'] or 0 for _,v in matched),
                synchronized_workflow_s=sum(v['agenttx']['duration_s'] or 0 for _,v in matched))


def analyze(cohort):
    cells=json.loads((cohort/'cells.json').read_text())
    grouped=defaultdict(dict)
    rows=[]
    issues=[]
    for cell in cells:
        if not cell['attempts']:
            continue
        attempt=cell['attempts'][-1]
        path=Path(attempt['result_path'])
        if not path.exists():
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest()!=attempt['result_sha256']:
            raise RuntimeError('terminal result changed: '+cell['key'])
        raw=json.loads(path.read_text())
        proof_path=path.parent/'cross-treatment.json'
        if not proof_path.exists():
            issues.append(dict(key=cell['key'],reason='missing_component_proof'))
            continue
        proof=json.loads(proof_path.read_text())
        if cell['benchmark']=='micro':
            usage=raw.get('api_usage') or {}
            row=dict(task=cell['task'],benchmark='micro',harness=cell['harness'],
                state=cell['state'],success=bool(raw.get('success')),
                tokens=usage.get('total_tokens'),complete_usage=usage.get('complete') is True,
                duration_s=raw.get('timings',{}).get('full_wall_s'))
            if not proof.get('filesystem_treatment_verified'):
                raise RuntimeError('invalid filesystem treatment: '+cell['key'])
        else:
            row=project(raw,path,cell['benchmark'],cell['harness'],cell['state'])
            if cell['benchmark']=='tb':
                from experiments.scripts.bench_tb_image_native import result_success
                row['success']=result_success(raw)
        if proof['variant']!=cell['variant'] or not proof['original_observation_treatment_verified']:
            raise RuntimeError('invalid observation treatment: '+cell['key'])
        if proof.get('policy_input',{}).get('kind')!='file':
            raise RuntimeError('component arm did not receive a file report')
        row.update(variant=cell['variant'],proof=proof,result_path=str(path))
        rows.append(row)
        grouped[(cell['benchmark'],cell['harness'],cell['task'])][cell['variant']]=row
    summaries={}
    for benchmark in ('micro','swe','tb'):
        for harness in ('sweagent','codex'):
            matching={}
            for (b,h,task),variants in grouped.items():
                if (b,h)!=(benchmark,harness):
                    continue
                proofs=[v['proof'] for v in variants.values()]
                checkpoints={json.dumps(p['checkpoint_configuration'],sort_keys=True) for p in proofs}
                scopes={tuple(p['selected_causal_ids']) for p in proofs}
                signatures={p.get('injection_signature_sha256',p.get('manifest_hash')) for p in proofs}
                if len(checkpoints)!=1 or len(scopes)!=1 or len(signatures)!=1:
                    raise RuntimeError('scope, prefix, or checkpoint settings differ: '+task)
                if 'filesystem_only' in variants and 'agenttx' in variants and benchmark!='micro':
                    if (variants['filesystem_only']['proof']['filesystem_after_policy'] !=
                            variants['agenttx']['proof']['filesystem_after_policy']):
                        raise RuntimeError('FS-only and synchronized files differ: '+task)
                if 'context_only' in variants and benchmark!='micro':
                    if not variants['context_only']['proof']['file_disabled_preserves_injected_files']:
                        raise RuntimeError('context-only arm restored files: '+task)
                matching[task]=variants
            local=[r for r in rows if (r['benchmark'],r['harness'])==(benchmark,harness)]
            summaries[f'{benchmark}/{harness}']=dict(
                variants={v:dict(recorded=sum(r['variant']==v for r in local),
                    successes=sum(r['variant']==v and r['success'] for r in local)) for v in VARIANTS},
                complete_triads=sum(set(v)==set(VARIANTS) and all(r['state']=='completed' for r in v.values())
                                    for v in matching.values()),
                fs_benefit_given_agent_rollback=compare(matching,'context_only'),
                agent_benefit_given_fs_rollback=compare(matching,'filesystem_only'),
                common_success_fs_comparison=compare(matching,'context_only',require_all_three=True),
                common_success_agent_comparison=compare(matching,'filesystem_only',require_all_three=True))
    return dict(protocol='agenttx.rq5-components-analysis/v1',
                counts=dict(Counter(c['state'] for c in cells)), registered_cells=len(cells),
                groups=summaries, issues=issues,
                attribution='conditional benefit of adding the missing component; no additive shares inferred',
                cost_population='successful matched pairs with complete observed API usage', rows=rows)
