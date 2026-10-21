#!/usr/bin/env python3
"""Prove scoped SymPy verification using base source and an existing model patch."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from experiments.workloads import swe_bench_suite as swe


def main():
    data=Path('/mnt/data/agenttx-runtime')
    out=data/'validation/swe-sympy-selection-preflight-20261018-r2'
    out.mkdir(exist_ok=False)
    cache=data/'cache/swe-monitored-20261018'
    task_id='sympy__sympy-11870'
    task=swe.load_tasks(cache)[task_id]
    instance=swe.fetch_instance(task_id,cache)
    instance={**instance,'_agenttx_eval_runtime':swe.official_runtime_info(task_id)}
    work=out/'workspace'
    shutil.copytree(cache/'swe_bench/repos'/task_id,work,
                    ignore=shutil.ignore_patterns('.git','.venv','__pycache__','.pytest_cache'))
    folder=data/'results/sweagent-swe-local-monitored-20261018-full-r5/cells'/task_id/'ideal_oracle'
    patches=[(f,json.loads(f.read_text()).get('info',{}).get('submission','')) for f in folder.glob('trajectories/*/*/*.traj')]
    path,patch=max(patches,key=lambda x:len(x[1]))
    assert patch and 'diff --git a/sympy/' in patch
    checks=[];original=subprocess.run;current='base'
    def capture(*args,**kwargs):
        result=original(*args,**kwargs)
        cmd=args[0] if args else kwargs.get('args',[])
        if isinstance(cmd,list) and cmd[:2]==['docker','exec'] and 'bash' in cmd and '-lc' in cmd and kwargs.get('text'):
            (out/(current+'.stdout')).write_text(result.stdout or '')
            (out/(current+'.stderr')).write_text(result.stderr or '')
        return result
    subprocess.run=capture
    try:
        for current in ('base','prior-model'):
            if current=='prior-model':
                applied=original(['git','apply','--whitespace=nowarn','-'],cwd=work,input=patch,text=True,capture_output=True)
                assert applied.returncode==0,applied.stderr
            started=time.monotonic()
            verdict=swe._verify_tests_docker(work,task,instance,apply_test_patch=True)
            stdout=(out/(current+'.stdout')).read_text()
            assert verdict['tests_rc']==(1 if current=='base' else 0),verdict
            assert 'sympy/functions/elementary/tests/test_trigonometric.py' in stdout and 'test_sinc' in stdout
            assert 'sympy/printing/tests/test_octave.py' not in stdout, 'file scope was lost'
            checks.append(dict(check=current,tests_rc=verdict['tests_rc'],elapsed_s=round(time.monotonic()-started,2)))
            print(json.dumps(checks[-1]),flush=True)
        proof=dict(checked_at=datetime.now(timezone.utc).isoformat(),ok=True,model_calls=0,gold_patch_used=False,
                   task=task_id,selected_tests=swe.fail_to_pass(instance),
                   command=swe.test_command(task,instance,swe.DOCKER_TESTBED_PYTHON),
                   prior_model_patch=str(path),prior_model_patch_sha256=hashlib.sha256(patch.encode()).hexdigest(),
                   swe_module_sha256=hashlib.sha256(Path(swe.__file__).read_bytes()).hexdigest(),checks=checks)
        (out/'proof.json').write_text(json.dumps(proof,indent=2)+'\n')
    finally:subprocess.run=original


if __name__=='__main__':main()
