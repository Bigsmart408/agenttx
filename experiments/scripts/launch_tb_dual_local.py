#!/usr/bin/env python3
"""Expand infrastructure-checked TB canary to both complete registered suites."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from experiments.scripts.run_tb_dual_local import DATA, PYTHON, save, infrastructure_issue


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--canary',required=True)
    parser.add_argument('--full',required=True)
    args=parser.parse_args()
    root=DATA/'validation'/args.canary
    assert root.parent==DATA/'validation'
    while not (root/'execution.json').exists():time.sleep(5)
    progress=json.loads((root/'progress.json').read_text())
    assert len(progress['tasks'])==12
    checked=[]
    for cell in progress['tasks']:
        assert cell['state']=='completed', str(cell)
        path=Path(cell['result_path']);result=json.loads(path.read_text())
        issue=infrastructure_issue(result,cell['expected_context'])
        assert issue is None, issue
        checked.append(dict(task=cell['task'],harness=cell['harness'],policy=cell['policy'],
            task_success=result.get('success'),context=result['context_variant'],api_usage=result['api_usage'],
            result=str(path),result_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    proof=DATA/'validation'/('tb-dual-native-launch-proof-'+args.full+'.json')
    save(proof,dict(checked_at=datetime.now(timezone.utc).isoformat(),canary=args.canary,full=args.full,
        gate='runtime_context_and_api_usage_only',task_success_required=False,checks=checked,
        canary_results_fill_full_suite=False))
    os.execv(str(PYTHON),[str(PYTHON),'-u',str(ROOT/'experiments/scripts/run_tb_dual_local.py'),
                           '--name',args.full])


if __name__=='__main__':main()
