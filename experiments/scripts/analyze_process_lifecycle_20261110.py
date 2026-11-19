#!/usr/bin/env python3
"""Independently audit three-arm continuations and export appendix tables."""
from collections import defaultdict
import argparse
import csv
import hashlib
import json
from pathlib import Path

FAMILIES=('request_scope','read_through','lru','ttl','job_exit','unused_cache','persistent')
ARMS=('retain_all','clear_all','clean')
NAMES=dict(request_scope='Request-local buffers',read_through='Refresh on query',
           lru='LRU eviction',ttl='Expiry on access',job_exit='Finite worker job',
           unused_cache='Unused persistent cache',persistent='Reused persistent cache')


def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def audit(cohort):
    registration=read(cohort/'registration.json')
    assert registration['expected_trials']==630 and registration['continuation_calls']==16
    assert registration['model_calls']==registration['model_api_requests']==0
    environment=read(cohort/'environment-validation.json')
    assert environment['network_mode']=='none'
    assert not any('API_KEY' in k or k.startswith('CODEX_') for k in environment['environment_names'])
    manifest=read(cohort/'source-manifest.json')
    assert all(sha(Path(registration['frozen_source'])/x['path'])==x['sha256'] for x in manifest['files'])
    paths=sorted((cohort/'trials').glob('*/*/*/*/result.json'))
    assert len(paths)==630, f'Only {len(paths)}/630 results'
    rows,evidence,seen=[],[],set()
    for path in paths:
        folder=path.parent;result=read(path)
        family,seed,repeat,arm=(result[k] for k in ('family','seed','repeat','arm'))
        key=(family,seed,repeat,arm)
        assert key not in seen;seen.add(key)
        assert family in FAMILIES and seed in range(10) and repeat in range(3) and arm in ARMS
        assert result['execution_passed'] and all(result['checks'].values())
        assert result['model_calls']==result['model_api_requests']==0
        n=32*(seed+1);valid=f'independent-{seed}\n'*256;good=f'correct-{seed}\n'
        expected_memo=[hashlib.sha256((valid+str(i)).encode()).hexdigest() for i in range(n)]
        memo_hash=hashlib.sha256(json.dumps(expected_memo).encode()).hexdigest()
        empty_hash=hashlib.sha256(json.dumps([]).encode()).hexdigest()
        cut,after,final=(read(folder/name) for name in ('at-cut.json','after-policy.json','final-processes.json'))
        series,lifecycle=read(folder/'continuation.json'),read(folder/'lifecycle.json')
        assert len(series)==16 and [s['step'] for s in series]==list(range(1,17))
        expected_valid=(1 if family=='job_exit' else 2)*n
        for snapshots in (cut,final):
            assert snapshots[1]['memo_count']==n and snapshots[1]['memo_sha256']==memo_hash
            assert snapshots[0]['memo_count']==(0 if family=='job_exit' else n)
            assert snapshots[0]['memo_sha256']==(empty_hash if family=='job_exit' else memo_hash)
        assert sum(x['memo_count'] for x in after)==(0 if arm=='clear_all' else expected_valid)
        assert all((a['pid']==b['pid'])==(arm!='clear_all') for a,b in zip(cut,after))
        created=cut[0]['obsolete']['created'];at_cut=cut[0]['obsolete']['resident']
        assert created==(0 if arm=='clean' else n)
        assert at_cut==(0 if arm=='clean' or family=='request_scope' else n)
        for snapshots in (cut,after,final,*[s['responses'] for s in series]):
            assert snapshots[1]['obsolete']['created']==0
            for s in snapshots:
                obs=s['obsolete']
                assert obs['created']==obs['released']+obs['resident']
                ids=[v['index'] for v in obs['releases']]
                assert len(ids)==len(set(ids))==obs['released']
                assert all(0<=x<obs['created'] for x in ids)
                if arm=='clean':assert obs['created']==0
        if arm=='clear_all':
            assert all(s['obsolete']['created']==0 for s in after)
        assert all(x['exit_code']==0 and x['reason']=='normal-job-completion' for x in lifecycle)
        assert bool(lifecycle)==(family=='job_exit')
        assert [v['step'] for v in lifecycle]==[s['step'] for s in series if s.get('natural_exit_after_response')]
        for event in lifecycle:
            observed=series[event['step']-1]['responses'][0]
            assert observed['exiting'] and observed['pid']==event['pid']
            assert event['released_units']==observed['obsolete']['resident']
        wrong=sum(s['responses'][0]['value']!=good for s in series)
        assert all(s['responses'][1]['value']==good for s in series)
        assert result['wrong_queries']==wrong and result['all_queries_correct']==(wrong==0)
        if arm=='clean':assert wrong==0
        curve={0:created-at_cut}
        residents=[]
        for s in series:
            resident=0 if s.get('natural_exit_after_response') else s['responses'][0]['obsolete']['resident']
            residents.append(resident)
            curve[s['step']]=created-resident
        if arm=='retain_all':
            assert residents==sorted(residents,reverse=True)
            assert result['naturally_released_total']==curve[16]
            assert result['naturally_released_after_recovery']==curve[16]-curve[0]
            assert result['invalid_units_at_end']==final[0]['obsolete']['resident']
        ledger=read(folder/'ledger.json')['steps'];names={x['tool_name']:x for x in ledger}
        targets=set() if arm=='clean' else {names[x]['step_id'] for x in ('write-input','consume-input','derive-output')}
        assert set(result['rollback_ids'])==targets
        assert {x['step_id'] for x in ledger if x['status']=='rolled_back'}==targets
        context=read(folder/'context-at-recovery.json')
        active={c['step_id'] for t in context['turns'] for c in t['calls'] if c['status']=='active'}
        prefix={names[x]['step_id'] for x in ('write-input','consume-input','derive-output','independent-output')}
        assert active==prefix-targets
        logical=folder/'session/upperdir/results'/folder.relative_to(cohort)/'workspace'
        def view(name):
            upper=logical/name
            if upper.exists():return upper.read_text() if upper.is_file() else None
            lower=folder/'workspace'/name
            return lower.read_text() if lower.is_file() else None
        assert view('fault.txt')==view('derived.txt')==good and view('independent.txt')==valid
        persisted=read(folder/'session/agenttx.json')['conversation']
        calls={c['tool_name']:c for t in persisted['turns'] for c in t['calls']}
        for s in series:
            assert json.loads(calls[f"continue-{s['step']:02d}"]['result'])==s['responses']
        # Derive work from actual counter snapshots, including stopped instances.
        warm=(sum(s['warm_items'] for s in cut) if arm=='clear_all' else 0)
        warm+=sum(s['warm_items'] for s in final)
        warm+=sum(series[x['step']-1]['responses'][0]['warm_items'] for x in lifecycle)
        assert warm==result['warm_items_total']
        rows.append(dict(family=family,seed=seed,repeat=repeat,arm=arm,correct=wrong==0,
            wrong_queries=wrong,created=created,at_cut=at_cut,resident_at_end=final[0]['obsolete']['resident'],
            released_before=created-at_cut,released_after=(curve[16]-curve[0]) if arm=='retain_all' else 0,
            released_total=curve[16] if arm=='retain_all' else 0,curve=curve,
            valid_expected=expected_valid,valid_immediate=sum(x['memo_count'] for x in after),
            warm_items=warm,natural_exits=len(lifecycle),wall_s=result['wall_s']))
        evidence.append(dict(path=str(path.relative_to(cohort)),result_sha256=sha(path),
            continuation_sha256=sha(folder/'continuation.json'),lifecycle_sha256=sha(folder/'lifecycle.json'),
            cut_sha256=sha(folder/'at-cut.json'),after_sha256=sha(folder/'after-policy.json'),
            final_sha256=sha(folder/'final-processes.json'),ledger_sha256=sha(folder/'ledger.json')))
    triples=defaultdict(dict)
    for r in rows:triples[r['family'],r['seed'],r['repeat']][r['arm']]=r
    assert len(triples)==210 and all(set(t)==set(ARMS) for t in triples.values())
    for triple in triples.values():
        reference=triple['clean']
        for r in triple.values():
            r['extra_warm_items']=r['warm_items']-reference['warm_items']
    arms=[]
    for arm in ARMS:
        selected=[r for r in rows if r['arm']==arm]
        baseline=sum(t['clean']['warm_items'] for t in triples.values())
        arms.append(dict(arm=arm,n=len(selected),correct=sum(r['correct'] for r in selected),
            wrong_queries=sum(r['wrong_queries'] for r in selected),
            immediate_valid_retention=sum(r['valid_immediate'] for r in selected)/sum(r['valid_expected'] for r in selected),
            extra_warm_items=sum(r['extra_warm_items'] for r in selected),
            extra_warm_fraction=sum(r['extra_warm_items'] for r in selected)/baseline))
    families=[]
    for family in FAMILIES:
        selected=[r for r in rows if r['family']==family and r['arm']=='retain_all']
        total=sum(r['created'] for r in selected);at_cut=sum(r['at_cut'] for r in selected)
        families.append(dict(family=family,n=len(selected),created=total,at_cut=at_cut,
            correct=sum(r['correct'] for r in selected),resident_at_end=sum(r['resident_at_end'] for r in selected),
            released_after=sum(r['released_after'] for r in selected),
            released_fraction={k:sum(r['curve'][k] for r in selected)/total for k in (0,1,4,8,16)},
            post_recovery_release_fraction=sum(r['released_after'] for r in selected)/at_cut if at_cut else None,
            all_obsolete_gone=sum(r['resident_at_end']==0 for r in selected),
            initial_worker_exited=sum(r['natural_exits']>0 for r in selected)))
    retain=[r for r in rows if r['arm']=='retain_all']
    total=sum(r['created'] for r in retain);at_cut=sum(r['at_cut'] for r in retain)
    after=sum(r['released_after'] for r in retain);released=sum(r['released_total'] for r in retain)
    summary=dict(cohort=str(cohort),source_sha256=registration['source_sha256'],
        analysis_source_sha256=sha(Path(__file__)),audited=630,
        matched_triples=210,model_calls=0,model_api_requests=0,arms=arms,families=families,
        obsolete_created=total,obsolete_at_recovery=at_cut,naturally_released_after_recovery=after,
        naturally_released_total=released,post_recovery_release_fraction=after/at_cut,
        total_release_fraction=released/total,resident_at_end=at_cut-after,
        state_unit='4-KiB payload object; logical lifetime, not RSS returned to OS',
        evidence=evidence)
    return rows,summary


def tables(summary):
    a=[r'\begin{table}[!ht]',r'\centering',
       r'\caption{\textbf{Process policies versus clean execution.}',
       r'Each variant executes 210 trajectories and 16 continuation calls per trajectory.}',
       r'\label{tab:process-state}',r'\footnotesize',r'\setlength{\tabcolsep}{4pt}',
       r'\begin{tabular}{@{}lrrr@{}}',r'\toprule',
       r'Variant & \shortstack{Correct\\continuations} & \shortstack{Valid state\\retained} & \shortstack{Extra cache\\construction} \\',r'\midrule']
    names=dict(retain_all='Retain all',clear_all='Clear all',clean='Clean execution')
    for row in summary['arms']:
        a.append(f"{names[row['arm']]} & {row['correct']}/{row['n']} & {100*row['immediate_valid_retention']:.0f}\\% & +{100*row['extra_warm_fraction']:.0f}\\% "+r'\\')
    a += [r'\bottomrule',r'\end{tabular}',r'\end{table}']
    b=[r'\begin{table}[!ht]',r'\centering',
       r'\caption{\textbf{Natural release of obsolete state under retention.}',
       r'Percentages count released faulty payload objects, including release before recovery.}',
       r'\label{tab:process-lifetimes}',r'\footnotesize',r'\setlength{\tabcolsep}{3pt}',
       r'\begin{tabular}{@{}lrrrr@{}}',r'\toprule',
       r'& \multicolumn{3}{c}{Released by continuation call} & \shortstack{Correct\\traces} \\',
       r'\cmidrule(lr){2-4}',r'State lifecycle & 0 & 4 & 16 & \\',r'\midrule']
    for row in summary['families']:
        f=row['released_fraction']
        b.append(NAMES[row['family']]+' & '+' & '.join(f'{100*f[k]:.1f}\\%' for k in (0,4,16))+f" & {row['correct']}/{row['n']} "+r'\\')
    b += [r'\bottomrule',r'\end{tabular}',r'\end{table}']
    return '\n'.join(a)+'\n','\n'.join(b)+'\n'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cohort',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--tables',type=Path,required=True);args=p.parse_args()
    rows,summary=audit(args.cohort.resolve());args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'audit.json').write_text(json.dumps(summary,indent=2)+'\n')
    with (args.output/'runs.csv').open('w') as stream:
        fields=[k for k in rows[0] if k!='curve'];writer=csv.DictWriter(stream,fieldnames=fields)
        writer.writeheader();writer.writerows({k:v for k,v in r.items() if k!='curve'} for r in rows)
    first,second=tables(summary)
    (args.tables/'process-state.tex').write_text(first)
    (args.tables/'process-lifetimes.tex').write_text(second)
    print(json.dumps({k:v for k,v in summary.items() if k!='evidence'},indent=2))


if __name__=='__main__':main()
