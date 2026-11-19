#!/usr/bin/env python3
"""Compare recovery-work definitions on the same registered Clean traces.

The primary opportunity is temporal-suffix task calls outside the root's
dependent closure. Reads count as work; transport polling does not. A stricter
variant requires observed project-file accesses. Preserve the old write-only
and strict-between tests for comparison, without changing any source export.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import statistics

from experiments.scripts.analyze_native_dependency_opportunity import (
    descendants, trace_workspace_root, trace_quality, workspace_path, write_csv,
)
from agenttx.conversation import copied_tokens

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'review/native-clean-campaign-20261104'
OUT = ROOT / 'review/native-clean-metric-audit-20261104'


def analyze(row: dict) -> tuple[dict, list[dict]]:
    path = Path(row['trace_path'])
    raw = path.read_bytes()
    data = json.loads(raw)
    restored_reads = 0
    unmatched_commands = []
    read_recovery_sha256 = ''
    if row.get('_process_reads') and row['harness'] == 'codex':
        read_path = OUT / 'process-reads' / row['suite'] / f"{row['task']}.json"
        read_raw = read_path.read_bytes()
        recovered = json.loads(read_raw)
        assert recovered['trace_sha256'] == row['trace_sha256']
        restored_reads = recovered['new_read_effects']
        unmatched_commands = recovered['unmatched_step_ids']
        read_recovery_sha256 = hashlib.sha256(read_raw).hexdigest()
        for step in data['ledger']['steps']:
            present = {(e['path'], e['kind']) for e in step.get('effects', [])}
            for effect in recovered['reads_by_step'].get(str(step['step_id']), []):
                if (effect['path'], effect['kind']) not in present:
                    step['effects'].append(effect)
            # An unmatched command has unknown reads. Treat it as a context
            # barrier rather than infer independence from missing observations.
            if step['step_id'] in unmatched_commands:
                step['opaque_context'] = True
    assert hashlib.sha256(raw).hexdigest() == row['trace_sha256']
    quality = trace_quality(data)
    steps = {int(s['step_id']): s for s in data['ledger']['steps']}
    calls = {int(c['step_id']): c for turn in data['conversation']['turns']
             for c in turn.get('calls', []) if c.get('step_id') is not None}
    nodes = sorted(steps.keys() & calls.keys())
    root_path = trace_workspace_root(data)

    @lru_cache(maxsize=65536)
    def included(raw_path):
        return workspace_path(raw_path, root_path)

    paths = {p for i in nodes for e in steps[i].get('effects', [])
             if (p := included(str(e.get('path') or '')))}
    directories = {parent.as_posix() for p in paths for parent in Path(p).parents
                   if parent.as_posix() != '.'}
    accesses = {}
    mutating, task_calls, project_calls = set(), set(), set()
    last_writer = {}
    children = defaultdict(set)
    for i in nodes:
        accesses[i] = {(p, e.get('kind')) for e in steps[i].get('effects', [])
                       if (p := included(str(e.get('path') or '')))
                       and p not in directories and e.get('kind') in {'R', 'N', 'W', 'D'}}
        writes = {p for p, k in accesses[i] if k in {'W', 'D'}}
        if writes:
            mutating.add(i)
        # bash-only SWE-agent uses communicate for actions and read_file for
        # adapter state/patch retrieval. The latter is not agent task progress.
        tool = steps[i].get('tool_name', '')
        if tool in {'sweagent:command', 'sweagent:execute'} or tool.startswith('codex:'):
            task_calls.add(i)
            if accesses[i]:
                project_calls.add(i)
        for p, _ in accesses[i]:
            parent = last_writer.get(p)
            if parent is not None and parent != i:
                children[parent].add(i)
        for p in writes:
            last_writer[p] = i
    project_file_edges = sum(parent in task_calls and child in task_calls
                             for parent, cc in children.items() for child in cc)
    for barrier in nodes:
        if steps[barrier].get('opaque_context'):
            for i in nodes:
                if i < barrier:
                    children[i].add(barrier)
                elif i > barrier:
                    children[barrier].add(i)
    graphs = {'path': children}
    recorded = defaultdict(set, {i: set(cc) for i, cc in children.items()})
    for i in nodes:
        for parent in set(steps[i].get('parents', [])) | set(calls[i].get('parents', [])):
            if parent in steps and parent < i:
                recorded[parent].add(i)
    graphs['recorded'] = recorded
    context = defaultdict(set, {i: set(cc) for i, cc in recorded.items()})
    copied_edges = 0
    if row['harness'] == 'codex':
        prior = []
        for i in nodes:
            item = json.loads(calls[i]['arguments'])
            inputs = {k: v for k, v in item.items()
                      if k not in {'aggregated_output', 'exit_code', 'status', 'id', 'type'}}
            arguments = json.dumps(inputs, sort_keys=True)
            for parent, tokens in prior:
                if any(token in arguments for token in tokens):
                    if i not in context[parent]:
                        copied_edges += 1
                    context[parent].add(i)
            result = item.get('aggregated_output') or ''
            prior.append((i, copied_tokens(str(result))))
    graphs['context'] = context
    roots = []
    for root in sorted(mutating & task_calls):
        later_task = {i for i in task_calls if i > root}
        if not later_task:
            continue
        for mode, graph in graphs.items():
            closure = descendants(root, graph)
            last_affected_task_call = max(closure & task_calls)
            valid_task = later_task - closure
            valid_project = valid_task & project_calls
            suffix_task = {root} | later_task
            suffix_project = suffix_task & project_calls
            roots.append(dict(
                suite=data['suite'], harness=row['harness'], task=data['task'],
                trace_quality=quality, graph=mode, root_step=root,
                suffix_task_calls=len(suffix_task), affected_task_calls=len(suffix_task & closure),
                independent_task_calls=len(valid_task),
                suffix_project_calls=len(suffix_project), affected_project_calls=len(suffix_project & closure),
                independent_project_calls=len(valid_project),
                independent_write_calls=len(valid_task & mutating),
                interleaved_task_calls=sum(root < i < last_affected_task_call for i in valid_task),
                interleaved_project_calls=sum(root < i < last_affected_task_call for i in valid_project),
                interleaved_write_calls=sum(root < i < last_affected_task_call for i in valid_task & mutating),
                independent_project_step_ids=json.dumps(sorted(valid_project)),
                affected_task_step_ids=json.dumps(sorted(closure & task_calls)),
                closure_calls=len(closure), trace_path=str(path), trace_sha256=row['trace_sha256']))
    task = dict(suite=data['suite'], harness=row['harness'], task=data['task'],
                trace_quality=quality, exported_calls=len(nodes), task_calls=len(task_calls),
                project_calls=len(project_calls), adapter_calls=len(nodes)-len(task_calls),
                project_file_edges=project_file_edges,
                mutating_task_calls=len(mutating & task_calls), trace_path=str(path),
                trace_sha256=row['trace_sha256'], restored_read_effects=restored_reads,
                unmatched_commands=len(unmatched_commands), copied_edges_added=copied_edges,
                read_recovery_sha256=read_recovery_sha256)
    return task, roots


def aggregate(tasks, roots):
    out = dict(traces=len(tasks), hypothetical_roots=len(roots),
               exported_calls=sum(t['exported_calls'] for t in tasks),
               task_calls=sum(t['task_calls'] for t in tasks),
               project_calls=sum(t['project_calls'] for t in tasks),
               traces_with_file_dependency=sum(t['project_file_edges'] > 0 for t in tasks),
               adapter_calls_removed=sum(t['adapter_calls'] for t in tasks))
    for kind in ('task', 'project', 'write'):
        field = f'independent_{kind}_calls'
        qualifying = [r for r in roots if r[field] > 0]
        out[f'traces_with_independent_{kind}_calls'] = len({r['task'] for r in qualifying})
        out[f'roots_with_independent_{kind}_calls'] = len(qualifying)
        field = f'interleaved_{kind}_calls'
        qualifying = [r for r in roots if r[field] > 0]
        out[f'traces_with_interleaved_{kind}_calls'] = len({r['task'] for r in qualifying})
        out[f'roots_with_interleaved_{kind}_calls'] = len(qualifying)
    for kind in ('task', 'project'):
        total = sum(r[f'suffix_{kind}_calls'] for r in roots)
        extra = sum(r[f'independent_{kind}_calls'] for r in roots)
        out[f'summed_suffix_{kind}_calls'] = total
        out[f'summed_independent_{kind}_calls'] = extra
        out[f'pooled_independent_{kind}_call_share'] = extra / total if total else 0
        ratios = [r[f'independent_{kind}_calls'] / r[f'suffix_{kind}_calls']
                  for r in roots if r[f'suffix_{kind}_calls']]
        out[f'mean_root_independent_{kind}_call_share'] = statistics.mean(ratios) if ratios else 0
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--process-reads', action='store_true')
    args = parser.parse_args()
    import csv
    inventory = list(csv.DictReader((SOURCE / 'capture_inventory.csv').open()))
    # All 946 complete captures, chosen before the metric audit.
    inventory = [r for r in inventory if r['trace_quality'] == 'complete']
    assert len(inventory) == 946
    for row in inventory:
        row['_process_reads'] = args.process_reads
    tasks, roots = [], []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for index, (t, rr) in enumerate(pool.map(analyze, inventory)):
            tasks.append(t); roots.extend(rr)
            if (index + 1) % 50 == 0:
                print(f'Audited {index + 1}/{len(inventory)} complete traces', flush=True)
    groups = {}
    for suite in ('swe_bench_lite', 'terminal_bench'):
        for harness in ('sweagent', 'codex'):
            tt = [t for t in tasks if (t['suite'], t['harness']) == (suite, harness)]
            groups[f'{suite}_{harness}'] = {
                graph: aggregate(tt, [r for r in roots if (r['suite'], r['harness'], r['graph']) == (suite, harness, graph)])
                for graph in ('path', 'recorded', 'context')}
    destination = OUT / 'corrected' if args.process_reads else OUT
    destination.mkdir(parents=True, exist_ok=True)
    write_csv(destination / 'task_summary.csv', tasks)
    write_csv(destination / 'root_comparison.csv', roots)
    report = dict(schema='agenttx.native_clean_metric_audit/v1',
                  denominator='The same 946 complete captures as the first audit; no task resampling.',
                  task_calls='Agent actions only: sweagent:command/execute and codex tools; adapter state and patch reads excluded.',
                  project_calls='Task calls with observed included project-file access; unchanged observed-path-v2 path and directory filters.',
                  graph_variants={'path':'Unchanged observed-path-v2 file graph plus opaque context barriers.',
                                  'recorded':'Path graph union all exported ledger and conversation parents.',
                                  'context':'Recorded graph plus Codex result-to-argument copied-token edges using the runtime copied_tokens rule.'},
                  process_reads_recovered=args.process_reads,
                  primary_metric='context graph; at least one later project-access task call outside the affected closure. Read-only observations count; adapter calls do not.',
                  secondary_metric='Same graph and calls, requiring a retained call strictly before an affected task descendant.',
                  restored_read_effects=sum(t['restored_read_effects'] for t in tasks),
                  unmatched_commands=sum(t['unmatched_commands'] for t in tasks),
                  groups=groups)
    (destination / 'summary.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(groups, indent=2))


if __name__ == '__main__':
    main()
