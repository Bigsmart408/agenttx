#!/usr/bin/env python3
"""Audit and combine the completed SWE-agent/Codex controlled campaigns.

Only derived files under review/ are written. Source results stay immutable.
Cost comparisons pair within harness/case/repeat; confidence intervals
resample original cases, retaining their harness and repeat observations.
"""
from __future__ import annotations

import collections
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import json
from pathlib import Path
import re

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT.parent / 'agenttx-experiment-data/results'
SOURCES = (
    Path('/mnt/data/agenttx-runtime/results/system-micro-canonical400-paired-20261030-r2'),
    RESULTS / 'system-micro-controls-context-20261031-r2',
    RESULTS / 'system-micro-codex-6000-20261107-r4',
)
OUT = ROOT / 'review/controlled-both-agents-20261110'
POLICIES = ('causal', 'crash_no_rollback', 'turn_checkpoint',
            'temporal_checkpoint', 'whole_branch_abort')
LABELS = dict(zip(POLICIES, ('AgentTX', 'No rollback', 'Turn checkpoint',
                            'Temporal checkpoint', 'Whole branch')))
METRICS = ('total_tokens', 'model_calls', 'tool_calls', 'full_wall_s', 'model_wall_s')


def read(path):
    return json.loads(Path(path).read_text())


def resolve(path):
    p = Path(path)
    if not p.exists():
        p = Path(str(p).replace('/home/bfq/agenttx-experiment-data/',
                               '/home/bfq/agenttx-workspace/agenttx-experiment-data/'))
    assert p.exists(), p
    return p


def load_cell(cell):
    p = resolve(cell['result'])
    raw = p.read_bytes()
    r = json.loads(raw)
    u = r['api_usage']
    assert cell['state'] in ('passed', 'task_failed')
    assert u['complete'] and not u['budget_exceeded']
    assert all(isinstance(q.get('total_tokens'), int) for q in u['requests'])
    for field in ('total_tokens', 'prompt_tokens', 'completion_tokens'):
        assert sum(q[field] for q in u['requests']) == u[field]
    assert u['total_tokens'] == r['total_tokens']
    assert u['prompt_tokens'] + u['completion_tokens'] == u['total_tokens']
    assert len(u['requests']) == r['model_calls']
    assert u['total_tokens'] <= 8_000_000
    assert all(cell[k] == r[k] for k in
               ('trace_id', 'manifest_hash', 'policy', 'operation_count', 'harness'))
    assert r['model'] == 'gpt-5.6-luna' and r['reasoning_effort'] == 'max'
    assert not r['runtime_only']
    audit = read(p.parent / 'command-audit.json')
    assert audit['passed'] == r['command_audit_passed']
    completed = r['model_returncode'] == 0 and r['verifier_passed'] and audit['passed']
    assert completed == r['completed'] == (cell['state'] == 'passed')
    row = {k: cell[k] for k in ('key', 'trace_id', 'manifest_hash', 'family',
                                'repeat', 'policy', 'state', 'operation_count', 'harness')}
    for k in METRICS[:3] + ('valid_retention',
                            'invalid_removal', 'recovery_safe', 'conversation_matches_rollback',
                            'retained_bytes_unchanged', 'verifier_passed', 'command_audit_passed',
                            'completed', 'exact_selective_state', 'model_returncode'):
        row[k] = r[k]
    row.update(prompt_tokens=u['prompt_tokens'], completion_tokens=u['completion_tokens'])
    row.update(r['timings'])
    probes = r.get('localization_probe_count', r.get('checkpoint_probes', []))
    roots = set(r.get('injected_root_steps_for_scoring', []))
    rank = next((i + 1 for i, v in enumerate(r.get('candidate_ranking', []))
                 if set(v['root_step_ids']) == roots), None)
    dependency = re.search(r'-d([\d.]+)-', r['trace_id'])
    row.update(useful_discarded=len(r['useful_discarded']), invalid_count=r['invalid_count'],
               valid_count=r['valid_count'], replayed_operations=len(audit['replayed_ids']),
               result_path=str(p), sha256=hashlib.sha256(raw).hexdigest(),
               task_sha256=hashlib.sha256((p.parent / 'prompt.txt').read_text().split(
                   '\nAn interruption occurred.', 1)[0].encode()).hexdigest(),
               violations=audit['violations'], budget_exhausted=u.get('budget_exhausted', False),
               localized_exact=r.get('localization_matches_injected_roots'),
               probe_count=probes if isinstance(probes, int) else len(probes), root_rank=rank,
               selection_precision=r['selection_precision'],
               missing_direct_edges=len(r['missing_direct_edges']),
               fault_position=float(re.search(r'-f([\d.]+)-', r['trace_id'])[1]),
               dependency_value=float(dependency[1]) if dependency else None,
               capture_ms_per_operation=1000*r['timings']['materialize_wall_s']/r['operation_count'],
               protocol_validation_only=r.get('protocol_validation_only'),
               source_formal_eligible=r.get('formal_eligible'))
    return row


def summary(rows):
    d = dict(runs=len(rows), cases=len({r['trace_id'] for r in rows}),
             passed=sum(r['completed'] for r in rows))
    for k in ('recovery_safe', 'conversation_matches_rollback', 'retained_bytes_unchanged',
              'verifier_passed', 'command_audit_passed', 'exact_selective_state', 'localized_exact'):
        d[k] = sum(r[k] is True for r in rows)
    for k in METRICS + ('replayed_operations', 'valid_retention', 'invalid_removal',
                         'useful_discarded', 'materialize_wall_s', 'policy_wall_s',
                         'verification_wall_s', 'localization_search_wall_s',
                         'probe_count', 'root_rank', 'selection_precision',
                         'missing_direct_edges', 'capture_ms_per_operation'):
        a = np.asarray([r.get(k) for r in rows if r.get(k) is not None], dtype=float)
        if len(a):
            d[k] = dict(mean=float(a.mean()), total=float(a.sum()), median=float(np.median(a)),
                        p95=float(np.quantile(a, .95)), minimum=float(a.min()), maximum=float(a.max()))
    return d


def pairkey(row):
    return row['harness'], row['trace_id'], row['repeat']


def common_success(rows, policies=POLICIES):
    groups = collections.defaultdict(dict)
    for r in rows:
        assert r['policy'] not in groups[pairkey(r)]
        groups[pairkey(r)][r['policy']] = r
    assert all(set(g) == set(policies) for g in groups.values())
    return {k for k, g in groups.items() if all(r['completed'] for r in g.values())}


def compare(rows, control, eligible=None):
    pairs = collections.defaultdict(dict)
    for r in rows:
        if r['policy'] in ('causal', control):
            pairs[pairkey(r)][r['policy']] = r
    complete = [p for k, p in pairs.items() if eligible is None or k in eligible]
    assert all(len(p) == 2 for p in complete)
    paired = [p for p in complete if all(r['completed'] for r in p.values())]
    groups = collections.defaultdict(lambda: np.zeros((2, len(METRICS))))
    for p in paired:
        for i, policy in enumerate(('causal', control)):
            groups[p[policy]['trace_id']][i] += [p[policy][m] for m in METRICS]
    a = np.asarray(list(groups.values()))
    totals = a.sum(axis=0)
    rng = np.random.default_rng(20261110)
    samples = []
    for _ in range(40):
        ix = rng.integers(0, len(a), (500, len(a)))
        sums = a[ix].sum(axis=1)
        samples.append(100 * (1 - sums[:, 0] / sums[:, 1]))
    ci = np.quantile(np.concatenate(samples), [.025, .975], axis=0)
    d = dict(pairs=len(paired), cases=len(groups),
             bootstrap='20000 original-case draws; harness and repeat observations retained', metrics={})
    for j, k in enumerate(METRICS):
        d['metrics'][k] = dict(causal_mean=float(totals[0, j] / len(paired)),
            control_mean=float(totals[1, j] / len(paired)),
            saving_pct=float(100 * (1 - totals[0, j] / totals[1, j])),
            ci95_pct=ci[:, j].tolist(),
            wins=sum(p['causal'][k] < p[control][k] for p in paired),
            ties=sum(p['causal'][k] == p[control][k] for p in paired),
            losses=sum(p['causal'][k] > p[control][k] for p in paired))
    d['all_outcomes'] = {k: dict(
        causal_mean=float(np.mean([p['causal'][k] for p in complete])),
        control_mean=float(np.mean([p[control][k] for p in complete])),
        saving_pct=float(100 * (1 - sum(p['causal'][k] for p in complete) /
                               sum(p[control][k] for p in complete)))) for k in METRICS}
    return d


def analyze(rows, policies=POLICIES):
    rows = [r for r in rows if r['policy'] in policies]
    common = common_success(rows, policies)
    shared = [r for r in rows if pairkey(r) in common]
    return dict(
        statistics={p: summary([r for r in rows if r['policy'] == p]) for p in policies},
        comparisons={p: compare(rows, p) for p in policies[1:]},
        common_success_pairs=len(common),
        common_success_comparisons={p: compare(rows, p, common) for p in policies[1:]},
        common_success_statistics={p: summary([r for r in shared if r['policy'] == p]) for p in policies},
        families={f: {p: compare([r for r in rows if r['family'] == f], p, common)
                      for p in policies[1:]} for f in sorted({r['family'] for r in rows})})


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    registrations = [read(p / 'registration.json') for p in SOURCES]
    fields = ('model', 'reasoning_effort', 'repeats', 'manifest_count', 'model_wall_s',
              'token_budget', 'max_probes', 'image_id', 'profile_sha256')
    assert all(all(r.get(k) == registrations[0].get(k) for k in fields)
               for r in registrations[1:])
    cells = [c for p in SOURCES for c in read(p / 'cells.json')['main'] if c['policy'] in POLICIES]
    assert len(cells) == 12000
    assert len({(c['harness'], c['key']) for c in cells}) == 12000
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = []
        for i, row in enumerate(pool.map(load_cell, cells), 1):
            rows.append(row)
            if i % 2000 == 0:
                print(f'Audited {i}/12000 results', flush=True)
    cases = collections.defaultdict(list)
    for r in rows:
        cases[r['trace_id']].append(r)
    assert len(cases) == 400
    for group in cases.values():
        assert len(group) == 30
        assert len({r['manifest_hash'] for r in group}) == 1
        assert len({r['task_sha256'] for r in group}) == 1
        assert {(r['harness'], r['policy'], r['repeat']) for r in group} == {
            (h, p, n) for h in ('sweagent', 'codex') for p in POLICIES for n in range(3)}
    with (ROOT / 'review/system-micro-controls-analysis-20261103/runs.csv').open() as f:
        original = {r['key']: r for r in csv.DictReader(f) if r['policy'] in POLICIES}
    assert all(original[r['key']]['sha256'] == r['sha256'] for r in rows if r['harness'] == 'sweagent')
    report = dict(sources=[str(p) for p in SOURCES], audit=dict(
        results_read=len(rows), cases=400, matched_protocol_fields=fields,
        same_manifest_and_task_across_harnesses=True, all_final_usage_complete=True,
        unchanged_sweagent_result_hashes=True,
        source_eligibility_flags=dict(collections.Counter(str(r['source_formal_eligible']) for r in rows)),
        codex_source_audit=read(SOURCES[2] / 'reference-source-audit.json'),
        scope='Controlled microbenchmarks; 36 SWE-agent context ablations remain separate.'),
        by_harness={h: analyze([r for r in rows if r['harness'] == h]) for h in ('sweagent', 'codex')},
        pooled=analyze(rows),
        failures=[{k: r[k] for k in ('harness', 'key', 'family', 'model_returncode',
            'verifier_passed', 'command_audit_passed', 'retained_bytes_unchanged',
            'violations', 'budget_exhausted', 'total_tokens', 'full_wall_s')}
                  for r in rows if not r['completed']])
    (OUT / 'analysis.json').write_text(json.dumps(report, indent=2) + '\n')
    with (OUT / 'runs.csv').open('w') as f:
        w = csv.DictWriter(f, fieldnames=sorted(set().union(*(r.keys() for r in rows))))
        w.writeheader()
        w.writerows(rows)
    lines = ['# SWE-agent + Codex controlled results', '',
        '400 identical cases × 2 harnesses × 5 policies × 3 repeats = 12,000 runs.', '',
        'Costs pair successful outcomes within harness/case/repeat. Savings are ratios of summed costs.', '',
        '| Harness | Policy | Passed / runs | Valid retained | Tokens saved | Workflow time saved | Paired runs |',
        '|---|---|---:|---:|---:|---:|---:|']
    for name, d in [*report['by_harness'].items(), ('pooled', report['pooled'])]:
        for p in POLICIES:
            s = d['statistics'][p]
            c = d['comparisons'].get(p)
            token = f"{c['metrics']['total_tokens']['saving_pct']:.2f}%" if c else '—'
            wall = f"{c['metrics']['full_wall_s']['saving_pct']:.2f}%" if c else '—'
            n = c['pairs'] if c else '—'
            lines.append(f"| {name} | {LABELS[p]} | {s['passed']} / {s['runs']} | "
                         f"{100*s['valid_retention']['mean']:.2f}% | {token} | {wall} | {n} |")
    lines += ['', 'Negative savings mean higher AgentTX cost.', '',
              'The 27 interrupted Codex infrastructure attempts remain separately archived; '
              'their 31,553,103 known tokens and 27 requests with unknown usage do not replace final outcomes.',
              'Source eligibility flags are preserved; this export records the completed coverage, '
              'input, usage, and command-audit checks without modifying registration or runtime evidence.']
    (OUT / 'summary.md').write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines), flush=True)


if __name__ == '__main__':
    main()
