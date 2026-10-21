#!/usr/bin/env python3
"""Publish local verifier metadata without benchmark solution patches."""
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]

def main():
    from experiments.scripts.run_swe_monitored_local import CACHE,VERIFIER_CACHE
    from experiments.workloads.swe_bench_suite import fetch_all_instances
    rows=[{k:v for k,v in row.items() if k not in {'patch','hints_text'}} for row in fetch_all_instances(CACHE)]
    target=VERIFIER_CACHE/'swe_bench';target.mkdir(parents=True,exist_ok=True)
    for row in rows:(target/(row['instance_id']+'.json')).write_text(json.dumps(row,indent=2)+'\n')
    body=json.dumps(rows,indent=2)+'\n';(target/'lite_test_manifest.json').write_text(body)
    print(json.dumps(dict(tasks=len(rows),sha256=hashlib.sha256(body.encode()).hexdigest(),gold_patch_included=False,cache=str(VERIFIER_CACHE))))

if __name__=='__main__':main()
