#!/usr/bin/env python3
"""Remove harness submission from task progress without changing graph edges."""
from __future__ import annotations

import csv
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from experiments.scripts import audit_native_clean_scope as prior
from experiments.scripts.analyze_native_dependency_opportunity import write_csv

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'review/native-clean-submission-audit-20261105'
BASE = ROOT/'review/native-clean-scope-audit-20261104'
SCOPE = 'artifacts_positive_dirs'


def analyze(row):
    tt, rr = prior.analyze(row, exclude_submission=True, selected_scopes={SCOPE})
    data = json.loads(Path(row['trace_path']).read_bytes())
    submits = [dict(step_id=c['step_id'], arguments=c['arguments'], result=c.get('result'))
               for t in data['conversation']['turns'] for c in t.get('calls', [])
               if c.get('step_id') is not None and prior.submission_call(c, row['harness'])]
    return tt, [r for r in rr if r['root_policy']=='mutations'], dict(
        suite=row['suite'], harness=row['harness'], task=row['task'],
        trace_path=row['trace_path'], trace_sha256=row['trace_sha256'], submissions=submits)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    inventory = [r for r in csv.DictReader((ROOT/'review/native-clean-campaign-20261104/capture_inventory.csv').open())
                 if r['trace_quality']=='complete']
    old_tasks = [r for r in csv.DictReader((BASE/'task_summary.csv').open()) if r['scope']==SCOPE]
    old_roots = [r for r in csv.DictReader((BASE/'root_comparison.csv').open())
                 if r['scope']==SCOPE and r['root_policy']=='mutations']
    tasks = [r for r in old_tasks if r['harness']=='codex']
    roots = [r for r in old_roots if r['harness']=='codex']
    source = []
    rows = [r for r in inventory if r['harness']=='sweagent']
    with ProcessPoolExecutor(max_workers=8) as pool:
        for i, (tt,rr,ss) in enumerate(pool.map(analyze, rows)):
            tasks.extend(tt); roots.extend(rr); source.append(ss)
            if (i+1)%50==0:
                print(f'Audited submission {i+1}/{len(rows)}', flush=True)
    key = lambda r:(r['suite'],r['harness'],r['task'])
    old_positive={key(r) for r in old_roots if int(r['independent_useful_calls'])>0}
    positive={key(r) for r in roots if int(r['independent_useful_calls'])>0}
    assert positive <= old_positive, 'Removing submission cannot add an opportunity'
    assert len(tasks)==946
    groups={}
    for suite in ('swe_bench_lite','terminal_bench'):
        for harness in ('sweagent','codex'):
            tt=[t for t in tasks if (t['suite'],t['harness'])==(suite,harness)]
            rr=[r for r in roots if (r['suite'],r['harness'])==(suite,harness)]
            stats=dict(traces=len(tt),candidate_roots=len(rr),
                       traces_with_file_dependency=sum(int(t['file_dependency_edges'])>0 for t in tt))
            for field in ('independent_project_calls','independent_useful_calls','independent_task_calls',
                          'interleaved_project_calls','interleaved_useful_calls'):
                pp=[r for r in rr if int(r[field])>0]
                stats['traces_with_'+field]=len({r['task'] for r in pp})
                stats['roots_with_'+field]=len(pp)
            groups[suite+'_'+harness]={SCOPE+'_mutations':stats}
    report=dict(schema='agenttx.native_clean_submission_audit/v1',
                denominator='Same 946 complete clean traces, including failed and zero-opportunity attempts.',
                correction='Exclude exact SWE-agent submit command from task roots and useful/affected task calls; retain every node and dependency edge for propagation.',
                previous_traces_with_opportunity=len(old_positive),traces_with_opportunity=len(positive),
                removed_traces=sorted(old_positive-positive),groups=groups)
    write_csv(OUT/'task_summary.csv',tasks);write_csv(OUT/'root_comparison.csv',roots)
    (OUT/'submission-calls.json').write_text(json.dumps(source,indent=2)+'\n')
    (OUT/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    (OUT/'input-hashes.json').write_text(json.dumps({
        str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [
            BASE/'root_comparison.csv', BASE/'task_summary.csv',
            Path(prior.__file__),Path(__file__)]},indent=2)+'\n')
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':
    main()
