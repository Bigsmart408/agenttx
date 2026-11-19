#!/usr/bin/env python3
"""Paired check of the materialization metadata-copy optimization."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--one')
    args = p.parse_args()
    if args.one:
        item = json.loads(args.one)
        sys.path.insert(0, str(Path(item['runtime'])/'src'))
        from agenttx import snapshot
        import bench_rq5_metacopy_try as bench
        assert Path(bench.primitive.__file__).is_relative_to(item['runtime'])
        bench.ROOT = Path('/var/lib/agenttx-materialization-copy-20261115')
        raise SystemExit(0 if bench.one(item,args.output) else 1)
    assert not args.output.exists()
    args.output.mkdir(parents=True)
    runtimes = [Path('/opt/rq5')/f'agenttx-snapshot-only-v{version}' for version in (11,12)]
    plan = []
    randomizer = random.Random(20261115)
    for repeat in range(-1,5):
        pairs = [(str(runtime),variant) for runtime in runtimes
                 for variant in ['agenttx-metacopy','overlay-metacopy']]
        randomizer.shuffle(pairs)
        for runtime,variant in pairs:
            plan.append(dict(serial=len(plan),runtime=runtime,variant=variant,
                phase='warmup' if repeat==-1 else 'measured',repeat=repeat,
                case=dict(kind='commit',operation='rename',files=10000)))
    (args.output/'manifest.json').write_text(json.dumps(dict(plan=plan,
        runtime_sha256={str(runtime):hashlib.sha256((runtime/'src/agenttx/snapshot.py').read_bytes()).hexdigest()
                        for runtime in runtimes},
        change='copyfile + chown + copystat replaces copy2 + chown + copystat; unchanged non-materializing paths.',
        sample_policy='One warmup then five measured repeats per configuration; fixed randomized order.'),indent=2)+'\n')
    for item in plan:
        (args.output/'progress.json').write_text(json.dumps(dict(state='running',completed=item['serial'],total=len(plan))))
        result = subprocess.run([sys.executable,__file__,'--output',str(args.output),'--one',json.dumps(item)])
        if result.returncode:
            raise SystemExit(result.returncode)
    (args.output/'progress.json').write_text(json.dumps(dict(state='complete',completed=len(plan),total=len(plan))))


if __name__ == '__main__':
    main()
