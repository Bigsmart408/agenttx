#!/usr/bin/env python3
"""Check an offline cross-host origin using base source and prior model patches."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from experiments.workloads import swe_bench_suite as swe


def main():
    data=Path('/mnt/data/agenttx-runtime')
    out=Path(os.environ.get('AGENTTX_REDIRECT_PREFLIGHT_OUT',str(data/'validation/swe-redirect-preflight-20261018-r3')));out.mkdir(exist_ok=False)
    cache=data/'cache/swe-monitored-20261018';tasks=swe.load_tasks(cache)
    original=subprocess.run;results=[];current=''
    def capture(*args,**kwargs):
        r=original(*args,**kwargs);cmd=args[0] if args else kwargs.get('args',[])
        if isinstance(cmd,list) and cmd[:2]==['docker','exec'] and 'bash' in cmd and '-lc' in cmd and kwargs.get('text'):
            (out/(current+'.stdout')).write_text(r.stdout or '')
            (out/(current+'.stderr')).write_text(r.stderr or '')
        return r
    subprocess.run=capture
    try:
        for task_id,policy,cohort in [('psf__requests-2148','temporal_checkpoint','sweagent-swe-local-monitored-20261018-full-r1'),('psf__requests-1963','clean_recovery','sweagent-swe-local-monitored-20261018-r3')]:
            task=tasks[task_id];instance=swe.fetch_instance(task_id,cache)
            instance={**instance,'_agenttx_eval_runtime':swe.official_runtime_info(task_id)}
            work=out/task_id
            shutil.copytree(cache/'swe_bench/repos'/task_id,work,ignore=shutil.ignore_patterns('.git','.venv','__pycache__','.pytest_cache'))
            if task_id.endswith('2148'):
                current=task_id+'-base';r=swe._verify_tests_docker(work,task,instance)
                text=(out/(current+'.stdout')).read_text() if (out/(current+'.stdout')).exists() else str(r)
                assert r['tests_rc']==1 and '1 failed, 9 passed' in text and 'FAILED test_requests.py::RequestsTestCase::test_iter_content_handles_socket_error' in text and 'FAILED test_requests.py::RequestsTestCase::test_auth_is_stripped' not in text,text
                results.append(dict(check=current,tests_rc=1,issue_test_only=True))
            folder=data/'results'/cohort/'cells'/task_id/policy
            patches=[(f,json.loads(f.read_text()).get('info',{}).get('submission','')) for f in folder.glob('trajectories/*/*/*.traj')]
            path,patch=max(patches,key=lambda x:len(x[1]))
            assert patch and 'diff --git a/requests/' in patch
            r=original(['git','apply','--whitespace=nowarn','-'],cwd=work,input=patch,text=True,capture_output=True)
            assert r.returncode==0,r.stderr
            current=task_id+'-prior-model';r=swe._verify_tests_docker(work,task,instance)
            assert r['tests_rc']==0,r
            results.append(dict(check=current,tests_rc=0,prior_model_patch=str(path),gold_patch_used=False))
        proof=dict(checked_at=datetime.now(timezone.utc).isoformat(),ok=True,model_calls=0,gold_patch_used=False,
                   http_fixture_image=os.environ['AGENTTX_HTTPBIN_IMAGE'],
                   swe_module_sha256=hashlib.sha256(Path(swe.__file__).read_bytes()).hexdigest(),checks=results)
        (out/'proof.json').write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof),flush=True)
    finally:subprocess.run=original


if __name__=='__main__':main()
