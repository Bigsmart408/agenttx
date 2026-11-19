#!/usr/bin/env python3
"""Complete the YoloFS comparison against current ordinary/metacopy storage."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import subprocess
import sys

sys.path.insert(0,'/opt/rq5/agenttx-snapshot-only-v12/src')
from agenttx import snapshot
import bench_rq5_metacopy_try as current

base, fairness = current.base, current.fairness
OriginalStorage = fairness.snapshot_bench.Storage
_spec = importlib.util.spec_from_file_location('rq5_yolo_original_fairness',fairness.__file__)
_audited = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _audited
_spec.loader.exec_module(_audited)
AuditedStorage = _audited.Storage


class YoloStorage(current.Storage):
    def __init__(self, root, variant, case):
        if variant!='yolofs-api':
            super().__init__(root,variant,case)
            return
        self.tag, self.bare = variant,None
        OriginalStorage.__init__(self,root,'yolofs',case)

    def snapshot(self, index):
        if self.tag=='yolofs-api':
            return AuditedStorage.snapshot(self,index)
        return super().snapshot(index)

    def finish(self):
        if self.tag=='yolofs-api':
            return AuditedStorage.finish(self)
        return super().finish()

    def discard(self):
        if self.tag=='yolofs-api':
            return AuditedStorage.discard(self)
        return super().discard()

    def check_history(self,canary=False):
        if self.tag=='yolofs-api':
            return OriginalStorage.check_history(self,canary)
        if self.tag.startswith('agenttx'):
            rows=[]
            for index in sorted({1,max(1,self.case['depth']//2),self.case['depth']}):
                target=self.root/'history-view'
                self.session.mount_snapshot(self.session.snapshots[index-1],target)
                try:
                    if canary:
                        current.fairness.comparison.check_distinct(target,index)
                        rows.append(dict(distinct_generation=index))
                    else:
                        rows.append(self.validate(target))
                finally:
                    snapshot._unmount(target)
            return rows
        depth,rows = self.case['depth'],[]
        for index in sorted({1,max(1,depth//2),depth}):
            target=self.root/'history-view'
            target.mkdir(exist_ok=True)
            snapshot._mount(target,[*(fd for _,fd in reversed(self.bare._layers[:index])),self.bare._base],
                            metadata_copy=self.bare.metadata_copy)
            try:
                if canary:
                    current.fairness.comparison.check_distinct(target,index)
                    rows.append(dict(distinct_generation=index))
                else:
                    rows.append(self.validate(target))
            finally:
                snapshot._unmount(target)
        return rows


current.Storage = YoloStorage
current.ROOT = Path('/var/lib/agenttx-yolofs-followup-20261115')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--one')
    args=p.parse_args()
    if args.one:
        item=json.loads(args.one)
        if item['phase']=='history-canary':
            # Reuse the audited history check before measured execution.
            fairness.ROOT=current.ROOT
            fairness.Storage=YoloStorage
            raise SystemExit(0 if fairness.one(item,args.output) else 1)
        raise SystemExit(0 if current.one(item,args.output) else 1)
    assert not args.output.exists() and not current.ROOT.exists()
    args.output.mkdir(parents=True)
    ordinary=['agenttx','overlay','yolofs-api']
    metadata=['agenttx-metacopy','overlay-metacopy']
    plan=[]
    def add(phase,variant,case,repeat):
        plan.append(dict(serial=len(plan),phase=phase,variant=variant,case=case,repeat=repeat))
    for variant in ordinary:
        add('history-canary',variant,dict(kind='snapshot',depth=3),-2)
    for operation in base.WORKLOADS:
        for variant in ordinary:
            add('commit-canary',variant,dict(kind='commit',operation=operation,files=32),-2)
    cases=[dict(kind='commit',operation=op,files=10000) for op in base.WORKLOADS]
    cases += [dict(kind='snapshot',depth=depth) for depth in (1,10,30)]
    rng=random.Random(20261115)
    for case in cases:
        for repeat in range(-1,5):
            variants=list(ordinary)
            if case.get('operation')=='rename':variants+=metadata
            rng.shuffle(variants)
            for variant in variants:
                add('warmup' if repeat==-1 else 'measured',variant,case,repeat)
    files=[Path(__file__),Path(current.__file__),Path(fairness.__file__),
           Path(base.__file__),Path(fairness.snapshot_bench.__file__),Path(fairness.comparison.__file__)]
    base.atomic(args.output/'manifest.json',dict(plan=plan,
        runtime='/opt/rq5/agenttx-snapshot-only-v12',
        runtime_sha256=hashlib.sha256(Path(snapshot.__file__).read_bytes()).hexdigest(),
        script_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        yolo_binary_sha256=hashlib.sha256(Path('/usr/local/bin/yolo').read_bytes()).hexdigest(),
        settings=dict(files=10000,filesize=4096,snapshot_depths=[1,10,30],warmups=1,repeats=5,
            snapshot_entry='All three systems use in-process APIs; YoloFS uses the audited snapshot ioctl.',
            finish='Publication, unmount, reference release and all staging destruction included.',
            validation='Full contents, names and modes checked after timing; immutable nonzero history canaries.',
            yolo_config='permission=true,staging=true,auto_snapshot=false; paths allowed upfront.',
            sample_policy='Fixed randomized system order; all measured samples retained.'),model_calls=0))
    for item in plan:
        base.atomic(args.output/'progress.json',dict(state='running',completed=item['serial'],total=len(plan),current=item))
        result=subprocess.run([sys.executable,__file__,'--output',str(args.output),'--one',json.dumps(item)])
        if result.returncode:
            base.atomic(args.output/'progress.json',dict(state='failed',completed=item['serial'],total=len(plan),current=item))
            raise SystemExit(result.returncode)
    base.atomic(args.output/'progress.json',dict(state='complete',completed=len(plan),total=len(plan)))


if __name__=='__main__':
    main()
