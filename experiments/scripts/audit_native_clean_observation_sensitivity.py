#!/usr/bin/env python3
"""Compare filesystem-only roots with roots admitting tool-observation errors."""
from __future__ import annotations

import csv
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
from pathlib import Path

from experiments.scripts import audit_native_clean_scope as prior
from experiments.scripts.analyze_native_dependency_opportunity import write_csv

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'review/native-clean-observation-sensitivity-20261105'
BASE = ROOT/'review/native-clean-submission-audit-20261105'
SCOPE = 'artifacts_positive_dirs'
POLICIES = ('mutations', 'mutations_or_observations')


def analyze(row):
    cache = OUT/'cache'/row['suite']/row['harness']/(row['task']+'.json')
    code = Path(__file__).read_bytes()+Path(prior.__file__).read_bytes()
    cache_key = hashlib.sha256(code+row['trace_sha256'].encode()).hexdigest()
    if cache.exists():
        saved = json.loads(cache.read_bytes())
        if saved['key']==cache_key:
            return saved['result']
    tasks, roots = prior.analyze(row, exclude_submission=True, selected_scopes={SCOPE},
                                additional_root_policies={'mutations_or_observations'})
    roots = [r for r in roots if r['root_policy'] in POLICIES]
    for r in roots:
        r['root_error_channel'] = 'filesystem' if r['root_mutates'] else 'observation'
    result = tasks, roots
    cache.parent.mkdir(parents=True,exist_ok=True)
    cache.write_text(json.dumps(dict(key=cache_key,result=result)))
    return result


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    inventory = [r for r in csv.DictReader((ROOT/'review/native-clean-campaign-20261104/capture_inventory.csv').open())
                 if r['trace_quality']=='complete']
    assert len(inventory)==946
    tasks, roots = [], []
    with ProcessPoolExecutor(max_workers=8) as pool:
        for i,(tt,rr) in enumerate(pool.map(analyze,inventory)):
            tasks.extend(tt);roots.extend(rr)
            if (i+1)%50==0:
                print(f'Audited observation scope {i+1}/{len(inventory)}',flush=True)
    old = list(csv.DictReader((BASE/'root_comparison.csv').open()))
    key = lambda r:(r['suite'],r['harness'],r['task'],str(r['root_step']))
    old_map = {key(r):r for r in old}
    filesystem = [r for r in roots if r['root_policy']=='mutations']
    assert {key(r) for r in filesystem}==old_map.keys()
    for r in filesystem:
        expected = old_map[key(r)]
        for field,value in expected.items():
            assert str(r[field])==value,(key(r),field,r[field],value)
    by_policy = {p:{key(r):r for r in roots if r['root_policy']==p} for p in POLICIES}
    for k,r in by_policy['mutations'].items():
        other=by_policy['mutations_or_observations'][k]
        for field,value in r.items():
            if field!='root_policy':
                assert other[field]==value,(k,field)
    groups={}
    for suite in ('swe_bench_lite','terminal_bench'):
        for harness in ('sweagent','codex'):
            tt=[t for t in tasks if (t['suite'],t['harness'])==(suite,harness)]
            group={}
            for policy in POLICIES:
                rr=[r for r in roots if (r['suite'],r['harness'],r['root_policy'])==(suite,harness,policy)]
                stats=dict(traces=len(tt),candidate_roots=len(rr),
                           traces_with_file_dependency=sum(t['file_dependency_edges']>0 for t in tt),
                           observation_only_roots=sum(r['root_error_channel']=='observation' for r in rr))
                for field in ('independent_useful_calls','interleaved_useful_calls',
                              'interleaved_with_affected_useful_calls'):
                    pp=[r for r in rr if r[field]>0]
                    stats['traces_with_'+field]=len({r['task'] for r in pp})
                    stats['roots_with_'+field]=len(pp)
                group[policy]=stats
            groups[suite+'_'+harness]=group
    totals={}
    for policy in POLICIES:
        keys=list(next(iter(groups.values()))[policy])
        totals[policy]={k:sum(g[policy][k] for g in groups.values()) for k in keys}
    report=dict(schema='agenttx.native_clean_observation_sensitivity/v1',
                definitions={'mutations':'Recorded workload-file/build-artifact mutation roots.',
                             'mutations_or_observations':'Mutation roots or actual task calls returning a nonempty tool observation; an additional observation-error model.',
                             'interleaved_with_affected_useful_calls':'Exists i<j<k, i is a candidate root, j and k are useful task calls, i reaches k but not j in the unchanged full dependency graph.'},
                note='Structural opportunities in clean traces, not observed error incidence, recovery success, or token savings.',
                filesystem_baseline_reproduced_exactly=True,existing_root_graphs_unchanged=True,
                groups=groups,totals=totals)
    write_csv(OUT/'task_summary.csv',tasks);write_csv(OUT/'root_comparison.csv',roots)
    (OUT/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    (OUT/'input-hashes.json').write_text(json.dumps({str(p):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [Path(__file__),Path(prior.__file__),OUT/'protocol.md',
                  BASE/'root_comparison.csv',ROOT/'review/native-clean-campaign-20261104/capture_inventory.csv']},indent=2)+'\n')
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':
    main()
