#!/usr/bin/env python3
"""Export the completed paired microbenchmark and case-cluster statistics."""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
from pathlib import Path
import re

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COHORT = Path('/mnt/data/agenttx-runtime/results/system-micro-canonical400-paired-20261030-r2')
OUT = ROOT / 'review/system-micro-results-20261031'
POLICIES = ('causal', 'temporal_checkpoint')


def read(path):
    return json.loads(Path(path).read_text())


def distribution(values):
    a = np.asarray(values, dtype=float)
    return dict(mean=float(a.mean()), p50=float(np.median(a)),
                p95=float(np.quantile(a, .95)), p99=float(np.quantile(a, .99)),
                minimum=float(a.min()), maximum=float(a.max()))


def comparison(rows, repetitions=20000):
    pairs = collections.defaultdict(dict)
    for r in rows:
        pairs[r['trace_id'], r['repeat']][r['policy']] = r
    paired = [p for p in pairs.values() if len(p) == 2 and all(r['success'] for r in p.values())]
    grouped = collections.defaultdict(lambda: np.zeros((2, 3)))
    for p in paired:
        for i, policy in enumerate(POLICIES):
            r = p[policy]
            grouped[r['trace_id']][i] += [r['total_tokens'], r['full_wall_s'], r['model_wall_s']]
    matrix = np.asarray(list(grouped.values()))
    totals = matrix.sum(axis=0)
    rng = np.random.default_rng(20261031)
    samples = []
    for start in range(0, repetitions, 500):
        indices = rng.integers(0, len(matrix), size=(min(500, repetitions-start), len(matrix)))
        sampled = matrix[indices].sum(axis=1)
        samples.append(100 * (1 - sampled[:, 0, :] / sampled[:, 1, :]))
    intervals = np.quantile(np.concatenate(samples), [.025, .975], axis=0)
    result = dict(pairs=len(paired), cases=len(grouped), bootstrap_samples=repetitions)
    for j, metric in enumerate(('total_tokens', 'full_wall_s', 'model_wall_s')):
        result[metric] = dict(causal_mean=float(totals[0, j]/len(paired)),
                              temporal_mean=float(totals[1, j]/len(paired)),
                              saving_pct=float(100*(1-totals[0, j]/totals[1, j])),
                              ci95_pct=intervals[:, j].tolist())
    return result


def summarize(rows):
    result = {}
    for policy in POLICIES:
        group = [r for r in rows if r['policy'] == policy]
        d = dict(runs=len(group), successes=sum(r['success'] for r in group),
                 cases=len({r['trace_id'] for r in group}))
        for field in ('valid_retention', 'selection_precision', 'useful_discarded',
                      'probe_count', 'candidate_count', 'root_rank', 'materialize_wall_s',
                      'policy_wall_s', 'localization_search_wall_s', 'model_wall_s',
                      'verification_wall_s', 'full_wall_s', 'cell_wall_s', 'total_tokens',
                      'capture_ms_per_operation', 'model_calls', 'tool_calls'):
            values = [r[field] for r in group if r[field] is not None]
            if values:
                d[field] = distribution(values)
        for field in ('invalid_removal', 'recovery_safe', 'conversation_matches_rollback',
                      'retained_bytes_unchanged', 'verifier_passed', 'exact_selective_state'):
            d[field] = sum(r[field] == 1 for r in group)
        if policy == 'causal':
            d['localized_exact'] = sum(r['localized_exact'] for r in group)
            d['top1'] = sum(r['root_rank'] == 1 for r in group)
            d['top5'] = sum(r['root_rank'] <= 5 for r in group)
        result[policy] = d
    result['comparison'] = comparison(rows)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', type=Path, default=DEFAULT_COHORT)
    parser.add_argument('--output', type=Path, default=OUT)
    args = parser.parse_args()
    audit_path = Path('/mnt/data/agenttx-runtime/validation/system-micro-monitor-20261030-r2/final-audit.json')
    audit = read(audit_path)
    assert audit['audit_passed'] and audit['audited_cells'] == 2400
    cells = read(args.cohort/'cells.json')['main']
    assert len(cells) == 2400 and all(c['state'] in ('passed', 'task_failed') for c in cells)
    rows = []
    source_digests = []
    for c in cells:
        path = Path(c['result'])
        raw = path.read_bytes()
        source_digests.append((c['key'], hashlib.sha256(raw).hexdigest()))
        r = json.loads(raw)
        assert r['api_usage']['complete'] and not r['api_usage']['budget_exceeded']
        trace = c['trace_id']
        position = float(re.search(r'-f([\d.]+)-', trace)[1])
        dependency = re.search(r'-d([\d.]+)-', trace)
        root = r.get('injected_root_steps_for_scoring', [])
        rankings = r.get('candidate_ranking', [])
        rank = next((i+1 for i, x in enumerate(rankings) if set(x['root_step_ids']) == set(root)), None)
        row = {k:c[k] for k in ('key', 'trace_id', 'family', 'policy', 'repeat', 'operation_count', 'manifest_hash')}
        row.update(success=c['state']=='passed', state=c['state'], fault_position=position,
                   dependency_value=float(dependency[1]) if dependency else None,
                   useful_discarded=len(r['useful_discarded']), valid_retention=r['valid_retention'],
                   selection_precision=r['selection_precision'], invalid_count=r['invalid_count'],
                   valid_count=r['valid_count'], total_tokens=r['total_tokens'],
                   prompt_tokens=r['api_usage']['prompt_tokens'], completion_tokens=r['api_usage']['completion_tokens'],
                   root_rank=rank, candidate_count=len(rankings) if rankings else None,
                   localized_exact=r.get('localization_matches_injected_roots'),
                   probe_count=r.get('localization_probe_count', len(r.get('checkpoint_probes', []))),
                   cell_wall_s=c['wall_s'], model_calls=r['model_calls'], tool_calls=r['tool_calls'])
        for field in ('invalid_removal', 'recovery_safe', 'conversation_matches_rollback',
                      'retained_bytes_unchanged', 'verifier_passed', 'exact_selective_state'):
            row[field] = r[field]
        for field in ('materialize_wall_s', 'policy_wall_s', 'localization_search_wall_s',
                      'model_wall_s', 'verification_wall_s', 'full_wall_s'):
            row[field] = r['timings'].get(field, 0.)
        row['capture_ms_per_operation'] = 1000*row['materialize_wall_s']/row['operation_count']
        rows.append(row)
    report = dict(cohort=str(args.cohort), protocol=read(args.cohort/'registration.json'),
                  audit=audit, statistics=summarize(rows), families={})
    for family in sorted({r['family'] for r in rows}):
        report['families'][family] = summarize([r for r in rows if r['family']==family])
    report['definitions'] = dict(
        costs='Mutually successful case/repeat pairs; ratio of summed actual API tokens or measured stage times.',
        intervals='20,000 paired cluster-bootstrap samples of original cases, retaining their repeats.',
        accuracy='All 2,400 outcomes; localization uses the 1,200 causal runs.',
        time='full_wall_s includes materialization, discovery/recovery, model repair and verification; cell_wall_s also includes runner startup.',
        capture='Materialization per operation includes tool execution, tracing, snapshots, ledger and initial history; it is not incremental tracing overhead.')
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output/'runs.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    (args.output/'analysis.json').write_text(json.dumps(report, indent=2)+'\n')
    (args.output/'source-digests.json').write_text(json.dumps(source_digests, indent=2)+'\n')
    print(json.dumps(report['statistics'], indent=2))


if __name__ == '__main__':
    main()
