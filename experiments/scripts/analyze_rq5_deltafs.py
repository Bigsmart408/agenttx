#!/usr/bin/env python3
"""Audit and summarize the matched kernel DeltaFS checkpoint experiment."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


VARIANTS = ('overlay-sync', 'agenttx-overlay', 'deltafs')
DEPTHS = (1, 10, 30)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stats(values):
    values = np.asarray(values, dtype=float)
    rng = np.random.default_rng(20261117)
    means = values[rng.integers(0, len(values), size=(10000, len(values)))].mean(axis=1)
    return dict(n=len(values), mean=float(values.mean()), median=float(np.median(values)),
                ci95=[float(x) for x in np.quantile(means, [.025, .975])],
                min=float(values.min()), max=float(values.max()), samples=values.tolist())


def load(root):
    paths = sorted((root/'sessions').glob('*.json'))
    rows = [json.loads(p.read_text()) for p in paths]
    complete = json.loads((root/'complete.json').read_text())
    assert complete['passed'] == len(rows) and complete['failed'] == 0
    assert all(r['status'] == 'passed' and r['model_calls'] == 0 for r in rows)
    for row in rows:
        if row['phase'] != 'canary' and 'validation' in row:
            assert row['validation'] == dict(validated_files=110, validated_bytes=110*4096)
    canaries = [r for r in rows if r['phase'] == 'canary']
    assert len(canaries) == 3
    assert all(len(r['history_checks']) == 3 for r in canaries)
    return rows, {str(p): digest(p) for p in [root/'manifest.json', root/'complete.json', *paths]}


def growth(row, kind):
    if kind == 'memory':
        return row['held']['memory']['accounted_bytes']-row['setup']['memory']['accounted_bytes']
    return row['held']['backing']['allocated_bytes']-row['setup']['backing']['allocated_bytes']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--latency', type=Path, required=True)
    parser.add_argument('--resources', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    latency, sources_l = load(args.latency)
    resources, sources_r = load(args.resources)
    result = dict(latency={}, resources={}, sources={**sources_l, **sources_r},
                  units=dict(latency='ms', resources='KiB'),
                  ci='10000 session/pair bootstrap resamples, 95% percentile intervals',
                  protocol='Resource increment is enabled-minus-disabled of held-minus-setup growth.')
    for variant in VARIANTS:
        result['latency'][variant] = {}
        result['resources'][variant] = {}
        for depth in DEPTHS:
            rows = [r for r in latency if r['phase'] == 'measure' and
                    r['variant'] == variant and r['depth'] == depth]
            assert len(rows) == 20
            assert all(r['validated_files'] == 110 and len(r['snapshots']) == depth for r in rows)
            result['latency'][variant][str(depth)] = dict(
                snapshot_ms=stats([r['snapshot_total_ns']/depth/1e6 for r in rows]),
                lifecycle_ms=stats([r['lifecycle_ns']/1e6 for r in rows]))
            rows = [r for r in resources if r['phase'] == 'measure' and
                    r['variant'] == variant and r['depth'] == depth]
            pairs = []
            for repeat in range(10):
                pair = {r['snapshots_enabled']: r for r in rows if r['repeat'] == repeat}
                assert set(pair) == {False, True}
                assert all(r['checkpoint_requests'] == depth for r in pair.values())
                pairs.append(pair)
            result['resources'][variant][str(depth)] = {
                kind+'_kib': stats([(growth(p[True], kind)-growth(p[False], kind))/1024 for p in pairs])
                for kind in ['storage', 'memory']}
    result['counts'] = dict(latency_sessions=len(latency), resource_sessions=len(resources),
        total_sessions=len(latency)+len(resources),
        measured_sessions=sum(r['phase']=='measure' for r in latency+resources),
        model_calls=0, failed=0)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'summary.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'sources'}, indent=2))


if __name__ == '__main__':
    main()
