#!/usr/bin/env python3
"""Audit all checkpoint-lifetime sessions and retain every measured sample."""
from collections import defaultdict
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def distribution(values):
    values = np.asarray(values, dtype=float)
    rng = np.random.default_rng(20261116)
    means = rng.choice(values, (10000, len(values)), replace=True).mean(axis=1)
    return dict(n=len(values), samples=values.tolist(), mean=float(values.mean()),
                median=float(np.median(values)), ci95=np.quantile(means, [.025, .975]).tolist())


def compare(port, control, metric):
    assert port.keys() == control.keys()
    pairs = np.asarray([(port[r][metric], control[r][metric]) for r in sorted(port)])
    rng = np.random.default_rng(20261116)
    sampled = pairs[rng.integers(0, len(pairs), (10000, len(pairs)))].mean(axis=1)
    ratios = sampled[:, 0]/sampled[:, 1]
    ratio = float(pairs[:, 0].mean()/pairs[:, 1].mean())
    return dict(ratio=ratio, ratio_ci95=np.quantile(ratios, [.025, .975]).tolist(),
                reduction_pct=100*(1-ratio), speedup=1/ratio)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.source/'manifest.json').read_text())
    planned = {r['serial']: r for r in manifest['plan']}
    rows = [json.loads(p.read_text()) for p in sorted((args.source/'sessions').glob('*.json'))]
    assert len(rows) == len(planned) == len({r['serial'] for r in rows})
    groups = defaultdict(dict)
    for row in rows:
        assert all(row[k] == v for k, v in planned[row['serial']].items())
        assert row['status'] == 'passed' and row['model_calls'] == 0
        assert 'metacopy=on' in row['mountinfo'] and 'redirect_dir=on' in row['mountinfo']
        if row['phase'] == 'canary':
            assert row['history_checks'] == [dict(generation=i, files=110) for i in (1, 2, 3)]
        else:
            assert len(row['snapshots']) == row['depth']
            assert row['snapshot_total_ns'] == sum(r['snapshot_ns'] for r in row['snapshots'])
            assert row['lifecycle_ns'] == row['setup_ns']+row['operation_and_snapshots_ns']+row['finish_ns']
            assert row['validated_files'] == 110
            if row['phase'] == 'measure':
                row['per_checkpoint_ms'] = row['snapshot_total_ns']/row['depth']/1e6
                row['lifecycle_ms'] = row['lifecycle_ns']/1e6
                row['finish_ms'] = row['finish_ns']/1e6
                groups[(row['depth'], row['variant'])][row['repeat']] = row
    result = dict(verified_sessions=len(rows), measured_sessions=sum(len(g) for g in groups.values()),
                  manifest_sha256=hashlib.sha256((args.source/'manifest.json').read_bytes()).hexdigest(),
                  manifest=manifest, groups={}, comparisons={})
    lines = ['| Checkpoints | Variant | Snapshot (ms/call) | Full lifecycle (ms) | Finalization (ms) |',
             '|---:|---|---:|---:|---:|']
    for depth in (1, 10, 30):
        result['groups'][str(depth)] = {}
        for variant in ('overlay-sync', 'overlay-retained', 'agenttx'):
            group = groups[(depth, variant)]
            assert len(group) == 20 and set(group) == set(range(20))
            metrics = {k: distribution([row[k] for row in group.values()])
                       for k in ('per_checkpoint_ms', 'lifecycle_ms', 'finish_ms')}
            result['groups'][str(depth)][variant] = metrics
            means = [metrics[k]['mean'] for k in ('per_checkpoint_ms', 'lifecycle_ms', 'finish_ms')]
            lines.append(f'| {depth} | {variant} | {means[0]:.3f} | {means[1]:.2f} | {means[2]:.2f} |')
        result['comparisons'][str(depth)] = {
            baseline: {metric: compare(groups[(depth, 'agenttx')], groups[(depth, baseline)], metric)
                       for metric in ('per_checkpoint_ms', 'lifecycle_ms')}
            for baseline in ('overlay-sync', 'overlay-retained')}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'summary.json').write_text(json.dumps(result, indent=2)+'\n')
    (args.output/'RESULTS.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))
    print('Verified all sessions and paired bootstrap comparisons.')


if __name__ == '__main__':
    main()
