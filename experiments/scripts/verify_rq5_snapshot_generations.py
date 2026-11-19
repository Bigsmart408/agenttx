#!/usr/bin/env python3
"""Check every one of three distinct historical generations on each backend."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import traceback

RUNTIME = Path('/opt/rq5/agenttx-snapshot-only-v14')
ROOT = Path('/var/lib/agenttx-snapshot-all-generations-20261115')


def main():
    sys.path.insert(0, str(RUNTIME/'src'))
    from agenttx import snapshot
    import bench_rq5_yolofs_followup as adapters
    base = adapters.base
    assert not ROOT.exists()
    results = []
    for variant in ('agenttx-metacopy', 'overlay-metacopy', 'yolofs-api'):
        storage = None
        root = ROOT/variant
        record = dict(variant=variant, checks=[], model_calls=0)
        try:
            storage = adapters.YoloStorage(root, variant, dict(kind='snapshot', depth=3))
            for i in range(100):
                (storage.dest/f'target-{i:06}.dat').write_bytes(b'\0'*4096)
            for generation in range(1, 4):
                for i in range(10):
                    (storage.dest/f'seed-{i:06}.dat').write_bytes(bytes([generation])*4096)
                storage.snapshot(generation)
            for i in range(10):
                (storage.dest/f'seed-{i:06}.dat').write_bytes(b'\xfe'*4096)
            adapters.fairness.comparison.check_distinct(storage.dest, 254)
            for generation in range(1, 4):
                if variant == 'yolofs-api':
                    actual = storage.generations[generation-1]
                    base.command(['yolo', 'travel', str(actual)], cwd=storage.base)
                    adapters.fairness.comparison.check_distinct(storage.dest, generation)
                else:
                    target = root/'check-history'
                    if variant == 'agenttx-metacopy':
                        storage.session.mount_snapshot(storage.session.snapshots[generation-1], target)
                    else:
                        target.mkdir(exist_ok=True)
                        lowers = [*(fd for _, fd in reversed(storage.bare._layers[:generation])), storage.bare._base]
                        snapshot._mount(target, lowers, metadata_copy=True)
                    try:
                        adapters.fairness.comparison.check_distinct(target, generation)
                    finally:
                        snapshot._unmount(target)
                    adapters.fairness.comparison.check_distinct(storage.dest, 254)
                record['checks'].append(dict(generation=generation, validated_files=110,
                                             distinct_seed_byte=generation))
            if variant == 'yolofs-api':
                base.command(['yolo', 'travel', str(storage.generations[-1])], cwd=storage.base)
                adapters.fairness.comparison.check_distinct(storage.dest, 3)
            record['status'] = 'passed'
        except BaseException:
            record.update(status='failed', error=traceback.format_exc())
        finally:
            if storage and storage.active:
                try:
                    storage.discard()
                except BaseException:
                    record.update(status='failed', cleanup_error=traceback.format_exc())
            if record['status'] == 'passed':
                shutil.rmtree(root)
        results.append(record)
    result = dict(runtime=str(RUNTIME),
                  runtime_sha256=hashlib.sha256(Path(snapshot.__file__).read_bytes()).hexdigest(),
                  results=results)
    Path(__file__).with_name('all-generation-verification.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))
    assert all(r['status'] == 'passed' and len(r['checks']) == 3 for r in results)


if __name__ == '__main__':
    main()
