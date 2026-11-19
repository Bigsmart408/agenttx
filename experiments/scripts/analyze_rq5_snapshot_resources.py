#!/usr/bin/env python3
"""Audit paired snapshot/no-snapshot runs and summarize retained resources."""
from collections import defaultdict
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


VARIANTS = ('overlay-sync', 'agenttx-overlay', 'yolofs-api', 'agenttx-yolofs')


def distribution(values):
    array = np.asarray(values, dtype=float)
    randomizer = np.random.default_rng(20261116)
    means = randomizer.choice(array, (10000, len(array)), replace=True).mean(axis=1)
    return dict(n=len(array), samples=array.tolist(), mean=float(array.mean()),
                median=float(np.median(array)), ci95=np.quantile(means, [.025, .975]).tolist())


def growth(row, kind):
    if kind == 'storage':
        return row['held']['backing']['allocated_bytes']-row['setup']['backing']['allocated_bytes']
    return row['held']['memory'][kind]-row['setup']['memory'][kind]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.source/'manifest.json').read_text())
    complete = json.loads((args.source/'complete.json').read_text())
    plan = {row['serial']: row for row in manifest['plan']}
    rows = [json.loads(p.read_text()) for p in sorted((args.source/'sessions').glob('*.json'))]
    assert len(rows) == len(plan) == complete['passed'] == 268
    assert complete['failed'] == 0 and complete['measured'] == 240
    grouped = defaultdict(dict)
    canaries = []
    for row in rows:
        assert row['serial'] in plan and all(row[k] == v for k, v in plan[row['serial']].items())
        assert row['status'] == 'passed' and row['model_calls'] == 0
        if row['phase'] == 'canary':
            assert len(row['history_checks']) == 3
            assert [check['distinct_seed_byte'] for check in row['history_checks']] == [1, 2, 3]
            assert all(check['validated_files'] == 110 for check in row['history_checks'])
            canaries.append(row['variant'])
            continue
        assert row['validation']['validated_files'] == 110
        assert row['checkpoint_requests'] == row['depth']
        assert row['checkpoints_created'] == (row['depth'] if row['snapshots_enabled'] else 0)
        for stage in ('setup', 'held', 'after_finish'):
            memory = row[stage]['memory']
            assert memory['accounted_bytes'] == sum(memory['stat'][k] for k in ('anon', 'file', 'kernel'))
            expected_mounts = int(stage != 'after_finish' and row['variant'] in
                                  ('overlay-sync', 'agenttx-overlay'))
            # YoloFS exposes its view through a symlink; lstat does not
            # follow it. OverlayFS mounts the view directly below the root.
            assert row[stage]['backing']['skipped_mounts'] == expected_mounts
        if row['phase'] == 'measure':
            key = (row['variant'], row['depth'], row['repeat'])
            assert row['snapshots_enabled'] not in grouped[key]
            grouped[key][row['snapshots_enabled']] = row
    assert sorted(canaries) == sorted(VARIANTS)
    result = dict(verified_sessions=len(rows), measured_sessions=240, paired_sessions=120,
                  manifest_sha256=hashlib.sha256((args.source/'manifest.json').read_bytes()).hexdigest(),
                  protocol=manifest['protocol'], groups={}, comparisons={})
    lines = ['| Backend | Snapshots | Extra storage (KiB) | Extra accounted memory (KiB) |',
             '|---|---:|---:|---:|']
    for variant in VARIANTS:
        result['groups'][variant] = {}
        for depth in (1, 10, 30):
            metrics = defaultdict(list)
            for repeat in range(10):
                pair = grouped[(variant, depth, repeat)]
                assert pair.keys() == {False, True}
                enabled, disabled = pair[True], pair[False]
                for name, kind in [('storage', 'storage'), ('memory', 'accounted_bytes'),
                                   ('process_rss', 'process_rss_bytes'), ('current_memory', 'current_bytes')]:
                    on, off = growth(enabled, kind)/1024, growth(disabled, kind)/1024
                    metrics[f'{name}_extra_kib'].append(on-off)
                    metrics[f'{name}_enabled_growth_kib'].append(on)
                    metrics[f'{name}_disabled_growth_kib'].append(off)
                for kind in ('anon', 'file', 'kernel'):
                    def stat_growth(row):
                        return row['held']['memory']['stat'][kind]-row['setup']['memory']['stat'][kind]
                    metrics[f'{kind}_extra_kib'].append((stat_growth(enabled)-stat_growth(disabled))/1024)
                metrics['extra_open_fds'].append(enabled['held']['open_fds']-disabled['held']['open_fds'])
            summarized = {key: distribution(values) for key, values in metrics.items()}
            result['groups'][variant][str(depth)] = summarized
            lines.append(f'| {variant} | {depth} | {summarized["storage_extra_kib"]["mean"]:.1f} | '
                         f'{summarized["memory_extra_kib"]["mean"]:.1f} |')
    for backend, native, agenttx in [('OverlayFS', 'overlay-sync', 'agenttx-overlay'),
                                      ('YoloFS', 'yolofs-api', 'agenttx-yolofs')]:
        result['comparisons'][backend] = {}
        for depth in ('1', '10', '30'):
            result['comparisons'][backend][depth] = {
                name: distribution(np.asarray(result['groups'][agenttx][depth][name]['samples'])-
                                   np.asarray(result['groups'][native][depth][name]['samples']))
                for name in ('storage_extra_kib', 'memory_extra_kib')}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'summary.json').write_text(json.dumps(result, indent=2)+'\n')
    (args.output/'RESULTS.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))
    print('Audited all sessions; paired increments retain every sample, including negative noise.')


if __name__ == '__main__':
    main()
