#!/usr/bin/env python3
"""Audit the matched four-configuration RQ5 checkpoint ablation."""
import argparse
import json
from pathlib import Path

from analyze_rq5_deltafs import digest, stats

VARIANTS = ('overlay-sync', 'overlay-retained', 'deltafs', 'agenttx-overlay')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    paths = sorted((args.input/'sessions').glob('*.json'))
    rows = [json.loads(p.read_text()) for p in paths]
    manifest = json.loads((args.input/'manifest.json').read_text())
    complete = json.loads((args.input/'complete.json').read_text())
    assert len(rows) == complete['passed'] == 256 and complete['failed'] == 0
    assert complete['measured'] == 240 and tuple(manifest['variants']) == VARIANTS
    assert len(manifest['plan']) == len(rows)
    canaries = [r for r in rows if r['phase'] == 'canary']
    assert len(canaries) == 4 and {r['variant'] for r in canaries} == set(VARIANTS)
    for row, plan in zip(rows, manifest['plan']):
        assert all(row[k] == value for k, value in plan.items())
        assert row['status'] == 'passed' and row['model_calls'] == 0
        fstype = 'deltafs' if row['variant'] == 'deltafs' else 'overlay'
        assert f' - {fstype} ' in row['mountinfo']
        assert all(x in row['mountinfo'] for x in ('metacopy=on', 'redirect_dir=on'))
        if row['phase'] == 'canary':
            assert len(row['history_checks']) == 3
            assert all(h['files'] == 110 for h in row['history_checks'])
        else:
            assert row['validated_files'] == 110 and len(row['snapshots']) == row['depth']
            assert sum(s['snapshot_ns'] for s in row['snapshots']) == row['snapshot_total_ns']
            assert row['lifecycle_ns'] == row['setup_ns']+row['operation_and_snapshots_ns']+row['finish_ns']
    result = dict(latency={}, kernel=manifest['kernel'],
        counts=dict(verified_sessions=len(rows),measured_sessions=240,failed=0,model_calls=0),
        protocol=manifest,
        sources={str(p):digest(p) for p in [args.input/'manifest.json',args.input/'complete.json',*paths]})
    for variant in VARIANTS:
        result['latency'][variant] = {}
        for depth in (1, 10, 30):
            group = [r for r in rows if r['phase']=='measure' and
                     r['variant']==variant and r['depth']==depth]
            assert len(group)==20 and {r['repeat'] for r in group}==set(range(20))
            result['latency'][variant][str(depth)] = dict(
                snapshot_ms=stats([r['snapshot_total_ns']/depth/1e6 for r in group]),
                lifecycle_ms=stats([r['lifecycle_ns']/1e6 for r in group]),
                finish_ms=stats([r['finish_ns']/1e6 for r in group]))
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({v:{d:{k:round(m['mean'],6) for k,m in metrics.items()}
                        for d,metrics in groups.items()} for v,groups in result['latency'].items()},indent=2))


if __name__ == '__main__':
    main()
