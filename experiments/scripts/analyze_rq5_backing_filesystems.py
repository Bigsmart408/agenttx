#!/usr/bin/env python3
"""Audit every RQ5 sample and summarize Ext4 and XFS separately."""
import argparse
import json
from pathlib import Path

from analyze_rq5_deltafs import digest, stats
from bench_rq5_backing_filesystems import FILESYSTEMS, LAYERED, OPERATIONS, DEPTHS


def growth(row, kind):
    section, metric = ('memory', 'accounted_bytes') if kind == 'memory' else ('backing', 'allocated_bytes')
    return row['held'][section][metric]-row['setup'][section][metric]


def summarize(root):
    manifest = json.loads((root/'manifest.json').read_text())
    complete = json.loads((root/'complete.json').read_text())
    paths = sorted((root/'sessions').glob('*.json'))
    rows = [json.loads(path.read_text()) for path in paths]
    assert len(rows) == len(manifest['plan']) == complete['passed']
    assert complete['failed'] == complete['model_calls'] == 0
    assert complete['all_source_hashes_unchanged']
    protocol = manifest['protocol']
    repeats = {'commit': protocol['commit_repeats'], 'checkpoint': protocol['checkpoint_repeats'],
               'resources': protocol['resource_pairs']}
    assert repeats == {'commit': 10, 'checkpoint': 20, 'resources': 10}
    assert protocol['files'] == 10000
    for row, item in zip(rows, manifest['plan']):
        assert all(row[key] == value for key, value in item.items())
        assert row['status'] == 'passed' and row['model_calls'] == 0
        assert not row.get('cleanup_error')
        if row['variant'] != 'native':
            fstype = 'deltafs' if row['variant'] == 'deltafs' else 'overlay'
            assert f' - {fstype} ' in row['mountinfo']
            assert all(option in row['mountinfo'] for option in ('metacopy=on', 'redirect_dir=on'))
        if row['phase'] == 'history-canary':
            assert len(row['history_checks']) == 3
            assert all(point['files'] == 110 for point in row['history_checks'])
            continue
        expected_files = (0 if row['operation'] == 'unlink' else row['files']) if row['kind']=='commit' else 110
        assert row['validation'] == dict(validated_files=expected_files, validated_bytes=4096*expected_files)
        assert row['lifecycle_ns'] == row['setup_ns']+row['operation_ns']+row['finish_ns']
        if row['kind'] != 'commit':
            assert len(row['snapshots']) == row['depth']
            assert sum(point['snapshot_ns'] for point in row['snapshots']) == row['snapshot_total_ns']
        if row['kind'] == 'resources':
            assert row['checkpoints_created'] == (row['depth'] if row['snapshots_enabled'] else 0)
            for point in ('setup', 'held'):
                backing = row[point]['backing']
                assert backing['storage_method'] == 'FIEMAP physical union + non-data blocks'
                assert backing['data_reference_bytes'] >= backing['data_unique_bytes']
    measured = [row for row in rows if row['phase']=='measure']
    assert len(measured) == complete['measured'] == 1040
    summary = dict(filesystems={}, effects={}, counts=dict(verified=len(rows), measured=len(measured),
        failed=0, model_calls=0), kernel=manifest['kernel'], backing=manifest['backing'], protocol=protocol,
        sources={str(path):digest(path) for path in [root/'manifest.json', root/'complete.json', *paths]})
    for filesystem in FILESYSTEMS:
        selected = [row for row in measured if row['filesystem'] == filesystem]
        assert len(selected) == 520
        output = dict(commit={}, latency={}, resources={})
        for operation in OPERATIONS:
            output['commit'][operation] = {}
            for variant in ('native', *LAYERED):
                group = [row for row in selected if row['kind']=='commit' and
                         row['variant']==variant and row['operation']==operation]
                assert len(group) == 10 and {row['repeat'] for row in group} == set(range(10))
                output['commit'][operation][variant] = {
                    metric+'_s': stats([row[metric+'_ns']/1e9 for row in group])
                    for metric in ('lifecycle', 'setup', 'operation', 'finish')}
        for variant in LAYERED:
            output['latency'][variant], output['resources'][variant] = {}, {}
            for depth in DEPTHS:
                group = [row for row in selected if row['kind']=='checkpoint' and
                         row['variant']==variant and row['depth']==depth]
                assert len(group) == 20 and {row['repeat'] for row in group} == set(range(20))
                output['latency'][variant][str(depth)] = dict(
                    snapshot_ms=stats([row['snapshot_total_ns']/depth/1e6 for row in group]),
                    lifecycle_ms=stats([row['lifecycle_ns']/1e6 for row in group]))
                group = [row for row in selected if row['kind']=='resources' and
                         row['variant']==variant and row['depth']==depth]
                assert len(group) == 20
                pairs = []
                for repeat in range(10):
                    pair = {row['snapshots_enabled']:row for row in group if row['repeat']==repeat}
                    assert set(pair) == {False, True}
                    pairs.append(pair)
                output['resources'][variant][str(depth)] = {
                    kind+'_kib': stats([(growth(pair[True],kind)-growth(pair[False],kind))/1024 for pair in pairs])
                    for kind in ('storage', 'memory')}
        summary['filesystems'][filesystem] = output
        summary['effects'][filesystem] = dict(
            agenttx_over_overlay_commit={op: output['commit'][op]['agenttx-overlay']['lifecycle_s']['mean'] /
                output['commit'][op]['overlay-sync']['lifecycle_s']['mean'] for op in OPERATIONS},
            overlay_over_agenttx_checkpoint={str(d): output['latency']['overlay-sync'][str(d)]['snapshot_ms']['mean'] /
                output['latency']['agenttx-overlay'][str(d)]['snapshot_ms']['mean'] for d in DEPTHS},
            workflow_30_reduction_percent=100*(1-output['latency']['agenttx-overlay']['30']['lifecycle_ms']['mean'] /
                output['latency']['overlay-sync']['30']['lifecycle_ms']['mean']))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    summary = summarize(args.input)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(dict(counts=summary['counts'], effects=summary['effects']), indent=2))


if __name__ == '__main__':
    main()
