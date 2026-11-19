#!/usr/bin/env python3
"""Audit task-work scope without selecting tasks or changing error outcomes.

Compare the published source-only path filter with one that also admits build
artifacts and installed environments. Keep mutation-root and all-call-root
experiments separate. All-call roots describe a different fault model.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
import csv
from functools import lru_cache
import hashlib
import json
from pathlib import Path

from agenttx.conversation import copied_tokens
from experiments.scripts.analyze_native_dependency_opportunity import (
    descendants, trace_workspace_root, workspace_path, write_csv,
)

ROOT = Path(__file__).resolve().parents[2]
PREVIOUS = ROOT / 'review/native-clean-metric-audit-20261104'
OUT = ROOT / 'review/native-clean-scope-audit-20261104'
# Add task artifacts/environments, retaining exclusions for runtime metadata and
# incidental caches. No changes to the graph are made merely to increase counts.
INCIDENTAL = {'.git', '.agenttx', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.tox'}


def artifact_path(raw, root):
    try:
        relative = Path(raw).relative_to(root)
    except ValueError:
        return None
    if not relative.parts or set(relative.parts) & INCIDENTAL or relative.suffix in {'.pyc', '.pyo'}:
        return None
    return relative.as_posix()


def observation(call, harness):
    if harness == 'codex':
        item = json.loads(call.get('arguments') or '{}')
        return str(item.get('aggregated_output') or '').strip()
    text = call.get('result') or ''
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return str(text).strip()
    if isinstance(payload, dict):
        return '\n'.join(str(payload.get(k) or '') for k in ('stdout', 'stderr')).strip()
    return str(payload).strip()


def submission_call(call, harness):
    """SWE-agent's submit command delivers the patch; it is not task progress."""
    if harness != 'sweagent':
        return False
    args = json.loads(call.get('arguments') or '{}')
    command = str(args.get('cmd') or '').strip().rstrip(';').strip()
    return command == 'submit'


def analyze(row, *, exclude_submission=False, selected_scopes=None,
            additional_root_policies=()):
    raw = Path(row['trace_path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == row['trace_sha256']
    data = json.loads(raw)
    if row['harness'] == 'codex':
        restored = json.loads((PREVIOUS / 'process-reads' / row['suite'] / f"{row['task']}.json").read_bytes())
        assert restored['trace_sha256'] == row['trace_sha256']
        for step in data['ledger']['steps']:
            present = {(e['path'], e['kind']) for e in step['effects']}
            step['effects'].extend(e for e in restored['reads_by_step'].get(str(step['step_id']), [])
                                   if (e['path'], e['kind']) not in present)
            if step['step_id'] in restored['unmatched_step_ids']:
                step['opaque_context'] = True
    steps = {s['step_id']: s for s in data['ledger']['steps']}
    calls = {c['step_id']: c for turn in data['conversation']['turns']
             for c in turn.get('calls', []) if c.get('step_id') is not None}
    nodes = sorted(steps.keys() & calls.keys())
    task = {i for i in nodes if steps[i]['tool_name'] in {'sweagent:command', 'sweagent:execute'}
            or steps[i]['tool_name'].startswith('codex:')}
    if exclude_submission:
        task = {i for i in task if not submission_call(calls[i], row['harness'])}
    runtime_mutations = {i for i in task if any(e['kind'] in {'W', 'D'} for e in steps[i]['effects'])}
    outputs = {i for i in task if observation(calls[i], row['harness'])}
    parents = defaultdict(set)
    for i in nodes:
        for parent in set(steps[i].get('parents', [])) | set(calls[i].get('parents', [])):
            if parent in steps and parent < i:
                parents[i].add(parent)
    if row['harness'] == 'codex':
        previous = []
        for i in nodes:
            item = json.loads(calls[i]['arguments'])
            args = json.dumps({k: v for k, v in item.items()
                               if k not in {'aggregated_output', 'exit_code', 'status', 'id', 'type'}}, sort_keys=True)
            for parent, tokens in previous:
                if any(token in args for token in tokens):
                    parents[i].add(parent)
            previous.append((i, copied_tokens(str(item.get('aggregated_output') or ''))))
    barriers = {i for i in nodes if steps[i].get('opaque_context')}
    for barrier in barriers:
        parents[barrier].update(i for i in nodes if i < barrier)
        for i in nodes:
            if i > barrier:
                parents[i].add(barrier)
    root_path = trace_workspace_root(data)
    result, tasks = [], []
    scopes = [('source', workspace_path), ('artifacts', artifact_path),
              ('source_positive_dirs', workspace_path), ('artifacts_positive_dirs', artifact_path)]
    if selected_scopes is not None:
        scopes = [(name, path_filter) for name, path_filter in scopes if name in selected_scopes]
    for scope, path_filter in scopes:
        included = lru_cache(maxsize=65536)(lambda p: path_filter(p, root_path))
        paths = {p for i in nodes for e in steps[i]['effects'] if (p := included(e['path']))}
        directory_evidence = paths
        if scope.endswith('positive_dirs'):
            # A failed lookup under a regular file (ENOTDIR) does not establish
            # that the file is a directory. The old all-path prefix rule did.
            directory_evidence = {p for i in nodes for e in steps[i]['effects']
                                  if e['kind'] != 'N' and (p := included(e['path']))}
        directories = {q.as_posix() for p in directory_evidence for q in Path(p).parents if q.as_posix() != '.'}
        if scope.endswith('positive_dirs') and row['harness']=='codex':
            # Codex W entries are content fingerprints of git-visible regular
            # files/symlinks, rather than directory metadata mutations.
            known_files = {p for i in nodes for e in steps[i]['effects']
                           if e['kind']=='W' and (p := included(e['path']))}
            directories -= known_files
        graph = defaultdict(set)
        for child, pp in parents.items():
            for parent in pp:
                graph[parent].add(child)
        project, mutations = set(), set()
        file_edges = set()
        writer = {}
        write_paths = set()
        for i in nodes:
            accesses = {(p, e['kind']) for e in steps[i]['effects']
                        if (p := included(e['path'])) and p not in directories
                        and e['kind'] in {'R', 'N', 'W', 'D'}}
            if accesses and i in task:
                project.add(i)
            writes = {p for p, k in accesses if k in {'W', 'D'}}
            if writes and i in task:
                mutations.add(i); write_paths.update(writes)
            for p, _ in accesses:
                if p in writer and writer[p] != i:
                    graph[writer[p]].add(i)
                    if writer[p] in task and i in task:
                        file_edges.add((writer[p], i))
            for p in writes:
                writer[p] = i
        useful = project | outputs
        candidate_nodes = project | runtime_mutations
        if 'mutations_or_observations' in additional_root_policies:
            candidate_nodes |= outputs
        task_roots = [i for i in sorted(candidate_nodes) if any(j > i for j in task)]
        tasks.append(dict(suite=row['suite'], harness=row['harness'], task=row['task'], scope=scope,
                          task_calls=len(task), project_calls=len(project), output_calls=len(outputs),
                          useful_calls=len(useful), mutating_calls=len(mutations), write_paths=len(write_paths),
                          runtime_mutating_calls=len(runtime_mutations),
                          file_dependency_edges=len(file_edges),
                          newly_included_paths=json.dumps(sorted(p for p in write_paths
                              if workspace_path(str(Path(root_path) / p), root_path) is None)[:20]),
                          trace_path=row['trace_path'], trace_sha256=row['trace_sha256']))
        for root in task_roots:
            closure = descendants(root, graph)
            last = max(closure & task)
            later = {i for i in task if i > root}
            independent = later - closure
            policies = []
            if root in mutations:
                policies.append('mutations')
            if root in project:
                policies.append('all_project_calls')
            if root in runtime_mutations:
                policies.append('runtime_mutations')
            if ('mutations_or_observations' in additional_root_policies
                    and root in mutations | outputs):
                policies.append('mutations_or_observations')
            affected_project_mutations = (closure - {root}) & mutations
            last_affected_mutation = max(affected_project_mutations, default=root)
            raw_write_paths = [e['path'] for e in steps[root]['effects'] if e['kind'] in {'W', 'D'}]
            for roots_name in policies:
                result.append(dict(suite=row['suite'], harness=row['harness'], task=row['task'], scope=scope,
                                   root_policy=roots_name, root_step=root, root_mutates=root in mutations,
                                   root_runtime_mutates=root in runtime_mutations,
                                   root_has_output=root in outputs,
                                   affected_project_mutations=len(affected_project_mutations),
                                   suffix_task_calls=len(later)+1,
                                   independent_project_calls=len(independent & project),
                                   independent_output_calls=len(independent & outputs),
                                   independent_useful_calls=len(independent & useful),
                                   independent_task_calls=len(independent),
                                   interleaved_project_calls=sum(root < i < last for i in independent & project),
                                   interleaved_useful_calls=sum(root < i < last for i in independent & useful),
                                   independent_before_affected_mutation_calls=sum(root < i < last_affected_mutation for i in independent & project),
                                   raw_mutated_paths=json.dumps(sorted(set(raw_write_paths))[:20]),
                                   independent_project_step_ids=json.dumps(sorted(independent & project)),
                                   independent_output_only_step_ids=json.dumps(sorted((independent & outputs)-project)),
                                   trace_path=row['trace_path'], trace_sha256=row['trace_sha256']))
                if additional_root_policies:
                    affected_useful = (closure & useful) - {root}
                    last_useful = max(affected_useful, default=root)
                    result[-1].update(
                        affected_later_useful_calls=len(affected_useful),
                        last_affected_useful_step=last_useful,
                        interleaved_with_affected_useful_calls=sum(
                            root < i < last_useful for i in independent & useful),
                        interleaved_useful_step_ids=json.dumps(sorted(
                            i for i in independent & useful if root < i < last_useful)))
    return tasks, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()
    inventory = [r for r in csv.DictReader((ROOT/'review/native-clean-campaign-20261104/capture_inventory.csv').open())
                 if r['trace_quality']=='complete']
    assert len(inventory)==946
    tasks, roots = [], []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for i, (tt, rr) in enumerate(pool.map(analyze, inventory)):
            tasks.extend(tt);roots.extend(rr)
            if (i+1)%50==0:
                print(f'Audited scopes {i+1}/{len(inventory)}',flush=True)
    groups={}
    for suite in ('swe_bench_lite','terminal_bench'):
        for harness in ('sweagent','codex'):
            group={}
            for scope in ('source','artifacts','source_positive_dirs','artifacts_positive_dirs'):
                tt=[t for t in tasks if (t['suite'],t['harness'],t['scope'])==(suite,harness,scope)]
                for root_policy in ('mutations','runtime_mutations','all_project_calls'):
                    rr=[r for r in roots if (r['suite'],r['harness'],r['scope'],r['root_policy'])==(suite,harness,scope,root_policy)]
                    stats=dict(traces=len(tt),candidate_roots=len(rr),
                               traces_with_candidate_roots=len({r['task'] for r in rr}),
                               traces_with_file_dependency=sum(t['file_dependency_edges']>0 for t in tt))
                    for field in ('independent_project_calls','independent_useful_calls','independent_task_calls',
                                  'interleaved_project_calls','interleaved_useful_calls'):
                        positive=[r for r in rr if r[field]>0]
                        stats['traces_with_'+field]=len({r['task'] for r in positive})
                        stats['roots_with_'+field]=len(positive)
                    positive=[r for r in rr if r['independent_before_affected_mutation_calls']>0]
                    stats['traces_with_independent_before_affected_mutation_calls']=len({r['task'] for r in positive})
                    stats['roots_with_independent_before_affected_mutation_calls']=len(positive)
                    group[scope+'_'+root_policy]=stats
            groups[suite+'_'+harness]=group
    OUT.mkdir(parents=True,exist_ok=True)
    write_csv(OUT/'task_summary.csv',tasks);write_csv(OUT/'root_comparison.csv',roots)
    report=dict(schema='agenttx.native_clean_scope_audit/v1',denominator='Same 946 complete traces; no task replacement.',
                scopes={'source':'Previous source-only filter.',
                        'artifacts':'Also include build, dist, node_modules, .venv and .tmp; keep runtime metadata and incidental Python/test caches excluded.',
                        'source_positive_dirs':'Source scope; negative lookups do not establish a directory; preserve Codex fingerprinted files.',
                        'artifacts_positive_dirs':'Artifact scope; negative lookups do not establish a directory; preserve Codex fingerprinted files.'},
                root_policies={'mutations':'Same filesystem mutation fault model as the current localizer.',
                               'runtime_mutations':'All recorded task-call writes/deletions, exactly the current FaultLocalizer._writes candidate predicate; task metadata and external paths retained in the dependency graph.',
                               'all_project_calls':'Additional read-only/observation-error scenarios; a different fault model, reported separately.'},
                retained_calls={'project':'Observed workload file access.',
                                'useful':'Project-file access or nonempty tool stdout/stderr; no adapter calls.',
                                'task':'All actual task actions; transport excluded.'},groups=groups)
    (OUT/'summary.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(groups,indent=2))


if __name__=='__main__':
    main()
