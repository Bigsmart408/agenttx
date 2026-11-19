#!/usr/bin/env python3
"""Diagnose lower-file rename; exploratory mount options are not benchmarks."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0, '/opt/rq5/agenttx-snapshot-only-v10/src')
from agenttx import snapshot as primitive
import bench_rq5_storage_primitives as base

OPTIONS = {
    'default': '',
    'explicit-current': 'index=off,metacopy=off,redirect_dir=off',
    'userxattr': 'userxattr,index=off,metacopy=off,redirect_dir=nofollow',
    'metacopy': 'index=off,metacopy=on,redirect_dir=on',
    'metacopy-nofollow': 'index=off,metacopy=on,redirect_dir=nofollow',
}


def one(root, variant, count, tracing=False):
    workspace, view, upper, work = [root/name for name in ('b', 'm', 'u', 'w')]
    for path in (workspace, view, upper, work):
        path.mkdir(parents=True)
    base.rust_workload('write-files', workspace, count)
    options = f'lowerdir={workspace},upperdir={upper},workdir={work}'
    options += ','+OPTIONS[variant] if OPTIONS[variant] else ''
    started = time.perf_counter_ns()
    if primitive._LIBC.mount(b'overlay', os.fsencode(view), b'overlay', 0, options.encode()):
        error = ctypes.get_errno()
        shutil.rmtree(root)
        return dict(variant=variant, status='unsupported', error=os.strerror(error))
    mount_ns = time.perf_counter_ns()-started
    trace = Path('/sys/kernel/tracing/instances/agenttx-copyup-audit')
    try:
        if tracing:
            trace.mkdir(exist_ok=False)
            (trace/'buffer_size_kb').write_text('8192')
            (trace/'events/ext4/ext4_sync_file_enter/enable').write_text('1')
            (trace/'events/ext4/ext4_sync_file_exit/enable').write_text('1')
            (trace/'tracing_on').write_text('1')
        started = time.perf_counter_ns()
        base.rust_workload('rename-files', view, count)
        execution_ns = time.perf_counter_ns()-started
        if tracing:
            (trace/'tracing_on').write_text('0')
            text = (trace/'trace').read_text()
            (root.parent/f'trace-{variant}.txt').write_text(text)
        assert sorted(p.name for p in view.iterdir()) == [f'renamed-{i:06}.dat' for i in range(count)]
        assert all(p.read_bytes() == b'\0'*4096 for p in view.iterdir())
        assert sorted(p.name for p in workspace.iterdir()) == [f'file-{i:06}.dat' for i in range(count)]
        path = upper/'renamed-000000.dat'
        result = dict(variant=variant, status='passed', count=count, options=options,
                      mount_ns=mount_ns, execution_ns=execution_ns,
                      upper_blocks=path.stat().st_blocks,
                      upper_xattrs={name:os.getxattr(path,name).hex() for name in os.listxattr(path)})
        if tracing:
            result['ext4_sync_enter_events'] = text.count('ext4_sync_file_enter:')
        return result
    finally:
        if tracing and trace.exists():
            (trace/'tracing_on').write_text('0')
            trace.rmdir()
        primitive._unmount(view)
        shutil.rmtree(root)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    root = Path('/var/lib/agenttx-copyup-audit-20261115')
    assert not root.exists()
    rows = []
    for variant in OPTIONS:
        rows.append(one(root, variant, 1000, tracing=True))
        base.atomic(args.output/'diagnostic.json', dict(rows=rows, timing='diagnostic with tracing'))
    # Move trace files created beside the disposable session into the report.
    for variant in OPTIONS:
        source = root.parent/f'trace-{variant}.txt'
        if source.exists():
            shutil.move(source, args.output/source.name)
    print(json.dumps(rows, indent=2))


if __name__ == '__main__':
    main()
