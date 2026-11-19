#!/usr/bin/env python3
"""RQ5 snapshot-only API, with the unchanged YoloFS workload/control code."""
import os
from pathlib import Path
import shutil
import time

import bench_rq5_try_comparison as comparison
import bench_rq5_storage_primitives as base


class Storage(comparison.Storage):
    def __init__(self, root, variant, case):
        if variant != 'agenttx':
            super().__init__(root, variant, case)
            return
        from agenttx.snapshot import SnapshotSession
        self.root, self.variant, self.case = root, variant, case
        self.base = root/'b'
        self.data = self.base/'data'
        self.data.mkdir(parents=True)
        if case['kind'] == 'commit' and case['operation'] != 'create':
            base.rust_workload('write-files', self.data, case['files'])
        self.sandbox = root/'a'
        self.session = SnapshotSession(self.data, self.sandbox)
        self.view = self.dest = self.session.view
        self.upper = self.session.upperdir
        self.pool = None
        self.active = True
        self.layers, self.generations, self.snapshot_rows = [], [], []

    def snapshot(self, index):
        if self.variant != 'agenttx':
            return super().snapshot(index)
        started = time.perf_counter_ns()
        point = self.session.snapshot()
        elapsed = time.perf_counter_ns()-started
        assert point.generation == index
        self.snapshot_rows.append(dict(index=index, snapshot_ns=elapsed,
            capture_ns=elapsed, history_prepare_ns=0))
        return elapsed

    def check_history(self, canary=False):
        if self.variant != 'agenttx':
            return super().check_history(canary)
        if self.case['kind'] != 'snapshot':
            return []
        from agenttx.snapshot import _unmount
        depth, rows = self.case['depth'], []
        for index in sorted({1, max(1, depth//2), depth}):
            view = self.root/'history-view'
            self.session.mount_snapshot(self.session.snapshots[index-1], view)
            try:
                if canary:
                    comparison.check_distinct(view, index)
                    checked = dict(distinct_generation=index)
                else:
                    checked = self.validate(view)
                rows.append(dict(index=index, **checked))
            finally:
                _unmount(view)
        return rows

    def commit(self):
        if self.variant != 'agenttx':
            return super().commit()
        seal_started = time.perf_counter_ns()
        self.session.seal()
        self.seal_ns = time.perf_counter_ns()-seal_started
        self.active = False
        started = time.perf_counter_ns()
        self.session.commit()
        return time.perf_counter_ns()-started

    def close(self, destroy):
        if self.variant != 'agenttx':
            return super().close(destroy)
        self.session.close(destroy=destroy)
        self.active = False
        if destroy:
            shutil.rmtree(self.root)


if __name__ == '__main__':
    comparison.Storage = Storage
    comparison.VARIANTS = comparison.SNAPSHOT_VARIANTS = ('agenttx', 'overlayfs', 'yolofs')
    comparison.RUNTIME = Path(os.environ['AGENTTX_SNAPSHOT_RUNTIME'])
    comparison.SHARE = Path('/hostshare/snapshot-only-20261115')
    comparison.GUEST = Path(os.environ['AGENTTX_SNAPSHOT_ROOT'])
    comparison.main(runner=__file__, mode='snapshot-and-commit-only')
