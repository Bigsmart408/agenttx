#!/usr/bin/env python3
"""Validate and summarize the matched RQ5 DeltaFS file-operation sessions."""
import argparse
import json
from pathlib import Path

from analyze_rq5_deltafs import digest, stats

VARIANTS = ('ext4', 'overlay-metacopy', 'agenttx-metacopy', 'deltafs')
OPERATIONS = ('create', 'overwrite', 'rename', 'unlink')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    paths = sorted((args.input/'sessions').glob('*.json'))
    rows = [json.loads(p.read_text()) for p in paths]
    complete = json.loads((args.input/'complete.json').read_text())
    manifest = json.loads((args.input/'manifest.json').read_text())
    assert complete['passed'] == len(rows) == 192 and complete['failed'] == 0
    assert complete['all_source_hashes_unchanged'] and complete['model_calls'] == 0
    assert len(manifest['plan']) == len(rows)
    for row, plan in zip(rows, manifest['plan']):
        assert all(row[k] == value for k, value in plan.items())
        assert row['status'] == 'passed' and row['model_calls'] == 0
        assert row['snapshots'] == [] and row['snapshot_total_ns'] == 0
        assert row['lifecycle_ns'] == row['setup_ns']+row['operation_and_snapshots_ns']+row['finish_ns']
        expected = row['case']['files'] if row['case']['operation'] != 'unlink' else 0
        assert row['commit_validation'] == dict(validated_files=expected, validated_bytes=4096*expected)
        mount = row['mount']['mountinfo']
        fstype = 'ext4' if row['variant'] == 'ext4' else 'deltafs' if row['variant'] == 'deltafs' else 'overlay'
        assert f' - {fstype} ' in mount
        if fstype != 'ext4':
            assert all(option in mount for option in ('metacopy=on', 'redirect_dir=on'))
    result = dict(groups={}, verified_sessions=len(rows), measured_sessions=complete['measured'],
                  complete=complete, kernel=manifest['kernel'], protocol=manifest['protocol'],
                  sources={str(p): digest(p) for p in [args.input/'manifest.json', args.input/'complete.json', *paths]})
    for operation in OPERATIONS:
        result['groups'][f'commit/{operation}'] = {}
        for variant in VARIANTS:
            selected = [r for r in rows if r['phase'] == 'measured' and
                        r['variant'] == variant and r['case']['operation'] == operation]
            assert len(selected) == 10 and {r['repeat'] for r in selected} == set(range(10))
            result['groups'][f'commit/{operation}'][variant] = {
                metric: stats([r[metric] for r in selected])
                for metric in ('lifecycle_ns', 'setup_ns', 'operation_and_snapshots_ns', 'finish_ns')}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'summary.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({case:{v: round(m['lifecycle_ns']['mean']/1e9, 6) for v,m in variants.items()}
                      for case,variants in result['groups'].items()}, indent=2))


if __name__ == '__main__':
    main()
