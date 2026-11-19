#!/usr/bin/env python3
"""Audit native BranchFS sessions and summarize the six RQ5 metrics."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from analyze_rq5_deltafs import digest, stats
from analyze_rq5_backing_filesystems import growth
from bench_rq5_branchfs import COMMIT, FILESYSTEMS, DEPTHS


def summarize(root):
    manifest = json.loads((root/'manifest.json').read_text())
    complete = json.loads((root/'complete.json').read_text())
    paths = sorted((root/'sessions').glob('*.json'))
    rows = [json.loads(path.read_text()) for path in paths]
    assert len(rows) == len(manifest['plan']) == complete['passed'] == 302
    assert complete['failed'] == complete['model_calls'] == 0
    assert complete['all_source_hashes_unchanged']
    assert manifest['artifact']['commit'] == COMMIT
    assert not manifest['artifact']['filesystem_source_modified']
    assert manifest['kernel'] == '6.8.0-147-generic'
    for row, planned in zip(rows, manifest['plan']):
        assert all(row[key] == value for key,value in planned.items())
        assert row['status'] == 'passed' and row['model_calls'] == 0
        assert row['cleanup_passed'] and not row.get('cleanup_error')
        assert ' - fuse.' in row['mountinfo'] or ' - fuse ' in row['mountinfo']
        if row['phase'] == 'history-canary':
            assert row['history_checks'] == [dict(generation=g,files=110) for g in (1,2,3)]
            continue
        if row['kind'] == 'io':
            assert row['steps'] == len(row['writes']) == 5 and row['size_kib'] == 1024
            assert row['final_validation'] == dict(bytes=1048576,mode='0644',history_versions=6)
            assert row['history_checks'] == [dict(generation=g,bytes=1048576) for g in range(1,7)]
            for point in row['writes']:
                assert point['bytes'] == 4096 and point['offset']+4096 <= 1048576
                assert point['edit_synced_ns'] >= point['edit_ns'] > 0
                assert point['snapshot_ns'] > 0
                assert point['edit_write_bytes'] == point['device_after_edit']['write_bytes']-point['device_before']['write_bytes']
                assert point['snapshot_write_bytes'] == point['device_after_snapshot']['write_bytes']-point['device_after_edit']['write_bytes']
                assert point['total_write_bytes'] == point['edit_write_bytes']+point['snapshot_write_bytes']
            continue
        assert row['validation'] == dict(validated_files=110,validated_bytes=110*4096)
        assert row['lifecycle_ns'] == row['setup_ns']+row['operation_ns']+row['finish_ns']
        assert len(row['snapshots']) == row['depth']
        assert sum(p['snapshot_ns'] for p in row['snapshots']) == row['snapshot_total_ns']
        if row['kind'] == 'resources':
            assert row['daemon_in_worker_cgroup']
            assert row['checkpoints_created'] == (row['depth'] if row['snapshots_enabled'] else 0)
            for when in ('setup','held'):
                assert row[when]['backing']['storage_method'] == 'FIEMAP physical union + non-data blocks'
                assert row[when]['memory']['accounted_bytes'] == sum(row[when]['memory']['stat'][key] for key in ('anon','file','kernel'))
    measured = [row for row in rows if row['phase']=='measure']
    assert len(measured) == complete['measured'] == 280
    result = dict(io={},filesystems={},counts=dict(verified=len(rows),measured=len(measured),
        io_measured=40,checkpoint_measured=120,resource_sessions=120,resource_pairs=60,failed=0,model_calls=0),
        kernel=manifest['kernel'],artifact=manifest['artifact'],protocol=manifest['protocol'],
        backing=manifest['backing'],sources={str(p):digest(p) for p in [root/'manifest.json',root/'complete.json',*paths]},
        bootstrap='10000 independent session/pair resamples; 95% percentile intervals; average five edits within each session first.')
    for fs in FILESYSTEMS:
        selected = [row for row in measured if row['filesystem']==fs]
        io = [row for row in selected if row['kind']=='io']
        assert len(io) == 20 and {row['repeat'] for row in io} == set(range(20))
        metrics = dict(edit_ms=('edit_ns',1e6),edit_synced_ms=('edit_synced_ns',1e6),
            edit_write_kib=('edit_write_bytes',1024),checkpoint_write_kib=('snapshot_write_bytes',1024),
            total_write_kib=('total_write_bytes',1024))
        key = 'xfs-reflink' if fs=='xfs' else fs
        result['io'][key] = dict(branchfs={'1024': {
            metric:stats([sum(p[field] for p in row['writes'])/5/divisor for row in io])
            for metric,(field,divisor) in metrics.items()}})
        output = dict(latency=dict(branchfs={}),resources=dict(branchfs={}))
        for depth in DEPTHS:
            group = [row for row in selected if row['kind']=='checkpoint' and row['depth']==depth]
            assert len(group)==20 and {row['repeat'] for row in group}==set(range(20))
            output['latency']['branchfs'][str(depth)] = dict(
                snapshot_ms=stats([row['snapshot_total_ns']/depth/1e6 for row in group]),
                lifecycle_ms=stats([row['lifecycle_ns']/1e6 for row in group]),
                setup_ms=stats([row['setup_ns']/1e6 for row in group]),
                finish_ms=stats([row['finish_ns']/1e6 for row in group]))
            group = [row for row in selected if row['kind']=='resources' and row['depth']==depth]
            assert len(group)==20
            pairs=[]
            for repeat in range(10):
                pair={row['snapshots_enabled']:row for row in group if row['repeat']==repeat}
                assert set(pair)=={False,True}
                pairs.append(pair)
            output['resources']['branchfs'][str(depth)] = {
                kind+'_kib':stats([(growth(pair[True],kind)-growth(pair[False],kind))/1024 for pair in pairs])
                for kind in ('storage','memory')}
        result['filesystems'][fs] = output
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=summarize(args.input)
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result['counts'],indent=2))
    for fs in FILESYSTEMS:
        print(fs,'checkpoint ms:',{d:round(result['filesystems'][fs]['latency']['branchfs'][d]['snapshot_ms']['mean'],3) for d in ('1','10','30')})
        print(fs,'30 checkpoints:',json.dumps(result['filesystems'][fs]['resources']['branchfs']['30']))


if __name__=='__main__':
    main()
