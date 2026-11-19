#!/usr/bin/env python3
"""Compare v12/v14 publication with matched OverlayFS and unchanged YoloFS."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--one')
    parser.add_argument('--publisher-only', action='store_true')
    parser.add_argument('--sync-before-sample', action='store_true')
    args = parser.parse_args()
    if args.one:
        item = json.loads(args.one)
        sys.path.insert(0, str(Path(item['runtime'])/'src'))
        from agenttx import snapshot
        import bench_rq5_yolofs_followup as followup
        bench = followup.current
        assert Path(snapshot.__file__).is_relative_to(item['runtime'])
        bench.ROOT = Path('/var/lib/agenttx-publisher-reuse-final-20261115')
        if item['phase'] == 'history-canary':
            followup.fairness.ROOT = bench.ROOT
            followup.fairness.Storage = followup.YoloStorage
            okay = followup.fairness.one(item, args.output)
        else:
            okay = bench.one(item, args.output)
        raise SystemExit(0 if okay else 1)

    assert not args.output.exists()
    args.output.mkdir(parents=True)
    old, new = [f'/opt/rq5/agenttx-snapshot-only-v{version}' for version in (12, 14)]
    configurations = [
        (old, 'agenttx-metacopy', 'AgentTX v12 metacopy'),
        (old, 'overlay-metacopy', 'OverlayFS v12 metacopy'),
        (new, 'agenttx-metacopy', 'AgentTX v14 metacopy'),
        (new, 'overlay-metacopy', 'OverlayFS v14 metacopy'),
        (new, 'agenttx', 'AgentTX ordinary'),
        (new, 'overlay', 'OverlayFS ordinary'),
        (new, 'yolofs-api', 'YoloFS'),
        (new, 'ext4', 'Ext4'),
    ]
    current = configurations[2:7]
    plan = []
    def add(phase, configuration, case, repeat):
        runtime, variant, label = configuration
        plan.append(dict(serial=len(plan), phase=phase, runtime=runtime,
                         variant=variant, label=label, case=case, repeat=repeat))
    if not args.publisher_only:
        for configuration in current:
            add('history-canary', configuration, dict(kind='snapshot', depth=3), -2)
            for operation in ('create', 'overwrite', 'rename', 'unlink'):
                add('commit-canary', configuration,
                    dict(kind='commit', operation=operation, files=32), -2)
        for configuration in configurations[:2]:
            add('commit-canary', configuration, dict(kind='commit', operation='rename', files=32), -2)
    else:
        # Confirm the changed publication path without ordinary copy-up syncs
        # from the other regression workloads between these measurements.
        configurations = configurations[:4]+configurations[6:]
    cases = [(dict(kind='commit', operation='rename', files=10000), configurations)]
    if not args.publisher_only:
        cases += [(dict(kind='commit', operation=operation, files=10000), current[:2])
                  for operation in ('create', 'overwrite', 'unlink')]
        cases += [(dict(kind='snapshot', depth=depth), current) for depth in (1, 10, 30)]
    randomizer = random.Random(20261115)
    for case, available in cases:
        for repeat in range(-1, 5):
            ordered = list(available)
            randomizer.shuffle(ordered)
            for configuration in ordered:
                add('warmup' if repeat == -1 else 'measured', configuration, case, repeat)
    sources = [Path(__file__), *Path(__file__).parent.glob('bench_rq5_*.py')]
    manifest = dict(plan=plan, model_calls=0, formal_campaign_paused=True,
        runtime_sha256={runtime: hashlib.sha256((Path(runtime)/'src/agenttx/snapshot.py').read_bytes()).hexdigest()
                        for runtime in (old, new)},
        source_sha256={path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources},
        native_sha256=hashlib.sha256((Path(new)/'src/agenttx/_snapshot_native.so').read_bytes()).hexdigest(),
        yolo_binary_sha256=hashlib.sha256(Path('/usr/local/bin/yolo').read_bytes()).hexdigest(),
        settings=dict(filesize=4096, files=10000, warmups=1, repeats=5,
            randomized_order_seed=20261115, all_samples_retained=True, volatile=False,
            publisher_only=args.publisher_only, sync_before_sample=args.sync_before_sample,
            finish='Materialization, publication, unmount, reference release, staging destruction.',
            lifecycle='Initial population/setup, operation and complete finish; validation is outside timing.',
            comparison='AgentTX and bare OverlayFS use identical mount and publication primitives.',
            yolo='Unchanged native binary/module, permissions enabled, paths preallowed, automatic snapshots off.',
            snapshots='In-process APIs; original fixed upstream workloads; explicit snapshot boundaries.'))
    def save(name, value):
        path = args.output/name
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, indent=2)+'\n')
        temporary.replace(path)
    save('manifest.json', manifest)
    for item in plan:
        save('progress.json', dict(state='running', completed=item['serial'], total=len(plan), current=item))
        if args.sync_before_sample:
            started = time.perf_counter_ns()
            os.sync()
            item['precondition_sync_ns'] = time.perf_counter_ns()-started
        result = subprocess.run([sys.executable, __file__, '--output', str(args.output), '--one', json.dumps(item)])
        if result.returncode:
            save('progress.json', dict(state='failed', completed=item['serial'], total=len(plan), current=item))
            raise SystemExit(result.returncode)
    save('progress.json', dict(state='complete', completed=len(plan), total=len(plan)))


if __name__ == '__main__':
    main()
