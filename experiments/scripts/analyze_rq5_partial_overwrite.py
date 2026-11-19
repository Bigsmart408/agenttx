#!/usr/bin/env python3
"""Audit the partial-overwrite campaign; bootstrap independent sessions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from analyze_rq5_deltafs import digest, stats
from bench_rq5_partial_overwrite import FILESYSTEMS, VARIANTS, SIZES_KIB, DEPTHS


def average(rows, key, divisor=1):
    return sum(row[key] for row in rows)/len(rows)/divisor


def summarize(root):
    manifest = json.loads((root/'manifest.json').read_text())
    complete = json.loads((root/'complete.json').read_text())
    paths = sorted((root/'sessions').glob('*.json'))
    rows = [json.loads(p.read_text()) for p in paths]
    assert len(rows) == len(manifest['plan']) == complete['passed']
    assert complete['failed'] == complete['model_calls'] == 0
    assert complete['all_source_hashes_unchanged']
    assert manifest['protocol']['non_truncating']
    for row, planned in zip(rows, manifest['plan']):
        assert all(row[key] == value for key,value in planned.items())
        assert row['status'] == 'passed' and row['cleanup_passed']
        assert row['model_calls'] == 0
        if row['kind'] == 'io':
            assert len(row['writes']) == row['steps']
            history_count = 0 if row['variant']=='native' else row['steps']+1
            assert row['final_validation'] == dict(bytes=row['size_kib']*1024,
                                                  mode='0644',history_versions=history_count)
            assert len(row['history_checks']) == history_count
            assert len({v['inode'] for v in row['history_checks']}) == history_count
            for step in row['writes']:
                assert step['bytes'] == 4096 and step['offset']+4096 <= row['size_kib']*1024
                assert step['edit_ns'] > 0 and step['edit_synced_ns'] >= step['edit_ns']
                assert step['edit_write_bytes'] == step['device_after_edit']['write_bytes']-step['device_before']['write_bytes']
                assert step['snapshot_write_bytes'] == step['device_after_snapshot']['write_bytes']-step['device_after_edit']['write_bytes']
                assert step['total_write_bytes'] == step['edit_write_bytes']+step['snapshot_write_bytes']
                assert step['total_write_bytes'] >= 4096
                if row['variant']=='deltafs':
                    assert step['snapshot_ns'] == step['ioctl_ns']+step['outside_ioctl_ns']
            if row['filesystem'] != 'xfs-reflink':
                assert row['held_storage']['duplicate_extent_bytes'] == 0
            elif row['variant'] != 'native' and row['size_kib']==1024:
                assert row['held_storage']['duplicate_extent_bytes'] > 0
        else:
            assert len(row['snapshots']) == row['depth']
            assert row['history_checks'] == row['depth']*10
            if row['variant']=='deltafs':
                assert all(point['snapshot_ns']==point['ioctl_ns']+point['outside_ioctl_ns']
                           for point in row['snapshots'])
    measured = [row for row in rows if row['phase']=='measure']
    assert len(measured) == complete['measured']
    result = dict(io={}, checkpoint={}, counts=dict(verified=len(rows),measured=len(measured),failed=0,
        model_calls=0,io_measured=sum(row['kind']=='io' for row in measured),
        checkpoint_measured=sum(row['kind']=='checkpoint' for row in measured)),
        protocol=manifest['protocol'],kernel=manifest['kernel'],backing=manifest['backing'],
        bootstrap='10000 independent-session resamples, 95% percentile intervals; average steps within each session first.',
        source_manifest_sha256=digest(root/'manifest.json'),
        sources={str(p):digest(p) for p in [root/'manifest.json',root/'complete.json',*paths]})
    for fs in FILESYSTEMS:
        result['io'][fs], result['checkpoint'][fs] = {}, {}
        for variant in VARIANTS:
            result['io'][fs][variant] = {}
            for size in SIZES_KIB:
                group = [row for row in measured if row['kind']=='io' and
                         row['filesystem']==fs and row['variant']==variant and row['size_kib']==size]
                assert len(group) == manifest['protocol']['io_repeats']
                metrics = dict(edit_ms=('edit_ns',1e6),edit_synced_ms=('edit_synced_ns',1e6),
                               edit_write_kib=('edit_write_bytes',1024),
                               checkpoint_write_kib=('snapshot_write_bytes',1024),
                               total_write_kib=('total_write_bytes',1024))
                if variant != 'native':
                    metrics['snapshot_ms'] = ('snapshot_ns',1e6)
                if variant == 'deltafs':
                    metrics.update(ioctl_ms=('ioctl_ns',1e6),outside_ioctl_ms=('outside_ioctl_ns',1e6))
                cell = {metric:stats([average(row['writes'],key,divisor) for row in group])
                        for metric,(key,divisor) in metrics.items()}
                cell['retained_growth_kib'] = stats([(row['held_storage']['allocated_bytes']-
                    row['baseline_storage']['allocated_bytes'])/1024 for row in group])
                cell['shared_data_kib'] = stats([row['held_storage']['duplicate_extent_bytes']/1024 for row in group])
                result['io'][fs][variant][str(size)] = cell
            if variant == 'native':
                continue
            result['checkpoint'][fs][variant] = {}
            for depth in DEPTHS:
                group = [row for row in measured if row['kind']=='checkpoint' and
                         row['filesystem']==fs and row['variant']==variant and row['depth']==depth]
                assert len(group) == manifest['protocol']['checkpoint_repeats']
                metrics = dict(snapshot_ms='snapshot_ns')
                if variant == 'deltafs':
                    metrics.update(ioctl_ms='ioctl_ns',outside_ioctl_ms='outside_ioctl_ns')
                result['checkpoint'][fs][variant][str(depth)] = {
                    metric:stats([average(row['snapshots'],key,1e6) for row in group])
                    for metric,key in metrics.items()}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    result = summarize(args.input)
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result['counts'],indent=2))
    for fs in FILESYSTEMS:
        print(fs,'1 MiB edit KiB:',{v:round(result['io'][fs][v]['1024']['edit_write_kib']['mean'],3) for v in VARIANTS})
        print(fs,'DeltaFS full/ioctl ms:',{d:tuple(round(result['checkpoint'][fs]['deltafs'][d][m]['mean'],5)
            for m in ('snapshot_ms','ioctl_ms')) for d in ('1','10','30')})


if __name__ == '__main__':
    main()
