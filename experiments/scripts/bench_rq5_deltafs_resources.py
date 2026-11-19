#!/usr/bin/env python3
"""Add kernel DeltaFS to the unchanged RQ5 paired resource protocol."""
from pathlib import Path
import time

import bench_rq5_snapshot_resources as common
from rq5_deltafs_backend import session_type


original_adapters = common.adapters


def adapters(args):
    port, primitive, previous_make = original_adapters(args)
    lifetime = common.load('rq5_deltafs_history', args.lifetime)

    class DeltaStorage:
        execute = port.base.Storage.execute
        validate = port.Storage.validate

        def __init__(self, root, variant, case):
            self.root, self.variant, self.case = root, variant, case
            self.data = root/'b/data'
            self.data.mkdir(parents=True)
            self.session = session_type(primitive)(self.data, root/'a', metadata_copy=True)
            self.dest = self.session.view
            self.snapshot_rows = []
            self.active = True

        def snapshot(self, index):
            begin = time.perf_counter_ns()
            point = self.session.snapshot()
            elapsed = time.perf_counter_ns()-begin
            assert point.generation == index
            self.snapshot_rows.append(dict(index=index, snapshot_ns=elapsed))
            return elapsed

        def finish(self):
            self.session.commit()
            self.session.close(destroy=True)
            self.active = False

        def discard(self):
            self.session.close(destroy=True)
            self.active = False

        def check_history(self):
            checks = []
            for index, point in enumerate(self.session.snapshots, 1):
                target = self.root/'history-view'
                self.session.mount_snapshot(point, target)
                try:
                    lifetime.validate(target, index)
                    checks.append(dict(generation=index, distinct_seed_byte=index, validated_files=110))
                finally:
                    primitive._unmount(target)
            return checks

    def make(root, variant, case):
        return (DeltaStorage(root, variant, case) if variant == 'deltafs' else
                previous_make(root, variant, case))

    return port, primitive, make


if __name__ == '__main__':
    common.adapters = adapters
    common.VARIANTS = ('overlay-sync', 'agenttx-overlay', 'deltafs')
    # The existing parent launches a fresh copy of its __file__ for each
    # isolated cgroup worker. Route those copies through this adapter too.
    common.__file__ = str(Path(__file__).resolve())
    common.main()
