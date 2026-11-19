#!/usr/bin/env python3
"""Add kernel DeltaFS to RQ5's unchanged Rust file-commit workloads."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time

from rq5_deltafs_backend import session_type


VARIANTS = ('ext4', 'overlay-metacopy', 'agenttx-metacopy', 'deltafs')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--module', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=10)
    args = parser.parse_args()
    assert os.geteuid() == 0 and args.repeats > 0
    assert not args.root.exists() and not args.output.exists()
    assert 'nodev\tdeltafs' in Path('/proc/filesystems').read_text()
    spec = importlib.util.spec_from_file_location('rq5_deltafs_commit_port', args.port/'scripts/rq5.py')
    port = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = port
    spec.loader.exec_module(port)
    original_storage, original_mount_evidence = port.Storage, port.mount_evidence

    class DeltaStorage:
        execute = port.base.Storage.execute
        validate = port.Storage.validate

        def __init__(self, root, variant, case):
            self.root, self.variant, self.tag, self.case = root, variant, variant, case
            self.data = root/'b/data'
            self.data.mkdir(parents=True)
            if case['operation'] != 'create':
                port.base.rust_workload('write-files', self.data, case['files'])
            self.session = session_type(port.primitive)(self.data, root/'a', metadata_copy=True)
            self.dest = self.session.view
            self.snapshot_rows = []
            self.active = True

        def finish(self):
            started = time.perf_counter_ns()
            self.session.commit()
            self.session.close(destroy=True)
            self.active = False
            return time.perf_counter_ns()-started

        def discard(self):
            self.session.close(destroy=True)
            self.active = False

    def storage(root, variant, case):
        return (DeltaStorage(root, variant, case) if variant == 'deltafs' else
                original_storage(root, variant, case))

    def mount_evidence(item):
        result = original_mount_evidence(item)
        if item.tag == 'deltafs':
            assert ' - deltafs ' in result['mountinfo'], result
            assert all(option in result['mountinfo'] for option in ('metacopy=on', 'redirect_dir=on'))
        return result

    port.Storage, port.mount_evidence = storage, mount_evidence
    args.output.mkdir(parents=True)
    args.root.mkdir(parents=True)
    plan = []
    for operation in port.base.WORKLOADS:
        for variant in VARIANTS:
            plan.append(dict(phase='commit-canary', variant=variant, repeat=-2,
                case=dict(kind='commit', operation=operation, files=32)))
    rng = random.Random(20261117)
    for repeat in range(-1, args.repeats):
        batch = [dict(phase='warmup' if repeat == -1 else 'measured', variant=v,
                      repeat=repeat, case=dict(kind='commit', operation=op, files=10000))
                 for v in VARIANTS for op in port.base.WORKLOADS]
        rng.shuffle(batch)
        plan.extend(batch)
    for serial, item in enumerate(plan):
        item['serial'] = serial
    files = [Path(__file__), Path(__file__).with_name('rq5_deltafs_backend.py'),
             Path(port.primitive.__file__), args.module, port.base.PERF]
    files += [p for folder in ('scripts', 'bench_support') for p in (args.port/folder).rglob('*.py')]
    hashes = {str(p): port.sha(p) for p in files}
    port.base.atomic(args.output/'manifest.json', dict(plan=plan, kernel=os.uname().release,
        sources=hashes, pid=os.getpid(), model_calls=0, seed=20261117,
        filesystem=subprocess.check_output(['findmnt','-T',str(args.root),'-no','FSTYPE,SOURCE'],text=True).strip(),
        protocol=dict(files=10000, file_bytes=4096, warmups=1, repeats=args.repeats,
            workload='Unchanged upstream Rust exec-workload bodies; same initial files and full validation.',
            mount_options='metacopy=on,redirect_dir=on,index=off',
            timing='setup including initial files + workload + publication and session cleanup; validation excluded.',
            publication='Same final publication implementation for all layered filesystems.',
            durability='Buffered execution; no extra fsync for any system.',
            snapshots='None in file-commit workloads; explicit checkpoints measured separately.',
            samples='Randomized execution order; all formal samples retained.')))
    for index, item in enumerate(plan, 1):
        result = port.one(item, args.output, args.root)
        port.base.atomic(args.output/'progress.json', dict(done=index,total=len(plan),
            failed=int(result['status'] != 'passed'), latest=item))
        if result['status'] != 'passed':
            raise RuntimeError(result.get('error'))
    assert all(port.sha(p) == digest for p, digest in hashes.items()), 'Source changed during measurement'
    args.root.rmdir()
    port.base.atomic(args.output/'complete.json', dict(passed=len(plan), failed=0,
        measured=sum(i['phase']=='measured' for i in plan), model_calls=0, all_source_hashes_unchanged=True))
    print(json.dumps(dict(complete=True, passed=len(plan))), flush=True)


if __name__ == '__main__':
    main()
