#!/usr/bin/env python3
"""Audit explicitly declared task paths outside the captured working directory.

This is a separate scope sensitivity study. It does not replace the published
workspace scope or infer useful mutations from arbitrary runtime metadata.
"""
from __future__ import annotations

import csv
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import re

from experiments.scripts import audit_native_clean_scope as prior
from experiments.scripts.analyze_native_dependency_opportunity import write_csv

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'review/native-clean-declared-paths-audit-20261105'
BASE = ROOT / 'review/native-clean-scope-audit-20261104'
SCOPE = 'artifacts_positive_dirs'
PATH_PATTERN = re.compile(
    r'(?<![A-Za-z0-9_:/~$])/(?:[A-Za-z0-9_.+@-]+/)*[A-Za-z0-9_.+@-]+/?'
)


def declared_paths(instruction):
    # Require a named child under a top-level directory. Do not turn /tmp,
    # /etc, a URL, ~ expansion, or an example shell variable into broad scope.
    return sorted({p for m in PATH_PATTERN.finditer(instruction)
                   if len(Path(p := m.group().rstrip('.,;:)').rstrip('/')).parts) >= 3})


def analyze(row):
    task_file = Path(row['trace_path']).parents[1] / 'task.json'
    task_bytes = task_file.read_bytes()
    instruction = json.loads(task_bytes)['instruction']
    targets = declared_paths(instruction)
    cache_file = OUT / 'cache' / row['harness'] / (row['task']+'.json')
    cache_key = hashlib.sha256(Path(__file__).read_bytes()+task_bytes+row['trace_sha256'].encode()).hexdigest()
    if cache_file.exists():
        cached = json.loads(cache_file.read_bytes())
        if cached['key'] == cache_key:
            return cached['result']
    target_prefixes = tuple(t+'/' for t in targets)
    base_filter = prior.artifact_path

    def extended_filter(raw, root):
        existing = base_filter(raw, root)
        if existing is not None:
            return existing
        if raw not in targets and not raw.startswith(target_prefixes):
            return None
        path = Path(raw)
        # Keep the incidental metadata exclusion even for named task trees.
        if set(path.parts) & prior.INCIDENTAL or path.suffix in {'.pyc', '.pyo'}:
            return None
        if path.is_relative_to(root):
            return None
        return '__declared_external__/' + path.as_posix().lstrip('/')

    prior.artifact_path = extended_filter
    try:
        tasks, roots = prior.analyze(row)
    finally:
        prior.artifact_path = base_filter
    tasks = [t for t in tasks if t['scope'] == SCOPE]
    roots = [r for r in roots if r['scope'] == SCOPE and r['root_policy'] == 'mutations']
    data = json.loads(Path(row['trace_path']).read_bytes())
    steps = {s['step_id']: s for s in data['ledger']['steps']}
    calls = {c['step_id']: c for t in data['conversation']['turns']
             for c in t.get('calls', []) if c.get('step_id') is not None}
    for r in roots:
        i = r['root_step']
        r['external_mutated_paths'] = json.dumps(sorted({e['path'] for e in steps[i]['effects']
            if e['kind'] in {'W', 'D'} and not Path(e['path']).is_relative_to(data['workspace_root'])
            and extended_filter(e['path'], data['workspace_root']) is not None}))
        r['root_arguments'] = calls[i]['arguments']
    source = dict(suite=row['suite'], harness=row['harness'], task=row['task'],
                  task_file=str(task_file), task_sha256=hashlib.sha256(task_bytes).hexdigest(),
                  declared_paths=targets, trace_path=row['trace_path'], trace_sha256=row['trace_sha256'])
    result = (tasks, roots, source)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(dict(key=cache_key, result=result)))
    return result


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    inventory = [r for r in csv.DictReader((ROOT/'review/native-clean-campaign-20261104/capture_inventory.csv').open())
                 if r['trace_quality'] == 'complete']
    baseline = [r for r in csv.DictReader((BASE/'root_comparison.csv').open())
                if r['scope'] == SCOPE and r['root_policy'] == 'mutations']
    # SWE scope is unchanged: only TB's original instructions define external
    # targets here. Retain the existing SWE rows without rerunning the corpus.
    tasks = [r for r in csv.DictReader((BASE/'task_summary.csv').open())
             if r['scope'] == SCOPE and r['suite'] == 'swe_bench_lite']
    roots = [dict(r, external_mutated_paths='[]', root_arguments='')
             for r in baseline if r['suite'] == 'swe_bench_lite']
    sources = []
    tb = [r for r in inventory if r['suite'] == 'terminal_bench']
    with ProcessPoolExecutor(max_workers=8) as pool:
        for i, (tt, rr, source) in enumerate(pool.map(analyze, tb)):
            tasks.extend(tt)
            roots.extend(rr)
            sources.append(source)
            if (i+1) % 50 == 0:
                print(f'Audited declared paths {i+1}/{len(tb)}', flush=True)
    key = lambda r: (r['suite'], r['harness'], r['task'])
    root_key = lambda r: (*key(r), int(r['root_step']))
    old_roots = {root_key(r): r for r in baseline}
    new_roots = {root_key(r): r for r in roots}
    assert old_roots.keys() <= new_roots.keys(), 'No previous root may be lost'
    # Extra paths may add dependencies. They may not erase prior edges to
    # increase the number of independent task calls for an existing root.
    for k, old in old_roots.items():
        assert int(new_roots[k]['independent_task_calls']) <= int(old['independent_task_calls'])
    groups = {}
    for suite in ('swe_bench_lite', 'terminal_bench'):
        for harness in ('sweagent', 'codex'):
            tt = [t for t in tasks if (t['suite'], t['harness']) == (suite, harness)]
            rr = [r for r in roots if (r['suite'], r['harness']) == (suite, harness)]
            groups[suite+'_'+harness] = dict(
                traces=len(tt), candidate_roots=len(rr),
                traces_with_file_dependency=sum(int(t['file_dependency_edges']) > 0 for t in tt),
                traces_with_independent_useful_calls=len({r['task'] for r in rr if int(r['independent_useful_calls']) > 0}),
                traces_with_interleaved_useful_calls=len({r['task'] for r in rr if int(r['interleaved_useful_calls']) > 0}),
                roots_with_independent_useful_calls=sum(int(r['independent_useful_calls']) > 0 for r in rr))
    positive = {key(r) for r in roots if int(r['independent_useful_calls']) > 0}
    old_positive = {key(r) for r in baseline if int(r['independent_useful_calls']) > 0}
    added = [r for r in roots if key(r) in positive-old_positive and int(r['independent_useful_calls']) > 0]
    removed = sorted(old_positive-positive)
    report = dict(schema='agenttx.native_clean_declared_path_audit/v1',
                  definition='Workspace/build artifacts plus absolute named task paths from original TB instruction; same roots and dependencies otherwise.',
                  traces=len(tasks), traces_with_opportunity=len(positive),
                  baseline_traces_with_opportunity=len(old_positive),
                  added_traces=sorted(positive-old_positive), removed_traces=removed,
                  existing_roots_preserved=True, existing_roots_never_gain_independent_task_calls=True,
                  groups=groups)
    assert len(tasks) == 946
    write_csv(OUT/'root_comparison.csv', roots)
    write_csv(OUT/'task_summary.csv', tasks)
    (OUT/'declared-paths.json').write_text(json.dumps(sources, indent=2)+'\n')
    (OUT/'new-witnesses.json').write_text(json.dumps(added, indent=2)+'\n')
    (OUT/'summary.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
