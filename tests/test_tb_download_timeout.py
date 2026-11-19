"""Actual uv transfers and accounting separation for the dependency timeout repair."""
import functools
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import zipfile

from experiments.scripts import bench_tb_image_native as native
from experiments.scripts import run_tb_dual_local as runner
from scripts.maintenance.tb_cache_comparisons_20261026 import separate_comparisons


def test_real_uv_slow_wheel_uses_task_timeout(tmp_path,monkeypatch):
    uv=shutil.which('uv');assert uv
    public=tmp_path/'public';simple=public/'simple/slowfixture';simple.mkdir(parents=True)
    wheel=public/'slowfixture-1.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel,'w') as z:
        z.writestr('slowfixture.py','value = 42\n')
        z.writestr('slowfixture-1.0.dist-info/METADATA','Metadata-Version: 2.1\nName: slowfixture\nVersion: 1.0\n')
        z.writestr('slowfixture-1.0.dist-info/WHEEL','Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        z.writestr('slowfixture-1.0.dist-info/RECORD','')
    (simple/'index.html').write_text('<a href="/slowfixture-1.0-py3-none-any.whl">slowfixture</a>')
    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            if self.path.endswith('.whl'):time.sleep(2)
            try:super().do_GET()
            except (BrokenPipeError,ConnectionResetError):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(public)))
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    try:
        env=dict(os.environ,UV_HTTP_TIMEOUT='1',UV_HTTP_RETRIES='0',UV_NO_PROGRESS='1',NO_PROXY='*')
        for k in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):env.pop(k,None)
        command=[uv,'--no-config','--no-cache','pip','install','--no-deps','--no-build','--index-url',
            'http://127.0.0.1:'+str(server.server_port)+'/simple','slowfixture==1.0','--target']
        old=subprocess.run(command+[str(tmp_path/'old')],env=env,text=True,capture_output=True,timeout=15)
        assert old.returncode!=0 and 'timed out' in old.stderr.lower()
        monkeypatch.setattr(native,'DOWNLOAD_HTTP_TIMEOUT_S',4)
        monkeypatch.setenv('UV_HTTP_TIMEOUT','1')
        metadata=native.configure_task_download_timeout();assert metadata['uv_http_timeout_s']==4
        from experiments.scripts.validate_tb_bootstrap_batch import SETUP
        # Execute the actual bootstrap's environment exports as a child shell:
        # an explicit export must not silently undo the task-level repair.
        setup=native.configure_verifier_download_timeout(SETUP)
        exports=setup.split('export TEST_DIR=',1)[0]
        child=subprocess.run(['bash','-c',exports+'\nprintf "%s" "$UV_HTTP_TIMEOUT"'],
                             env=env,text=True,capture_output=True,check=True)
        assert child.stdout=='4'
        env['UV_HTTP_TIMEOUT']=child.stdout
        fixed=subprocess.run(command+[str(tmp_path/'fixed')],env=env,text=True,capture_output=True,timeout=15)
        assert fixed.returncode==0,fixed.stderr
        assert (tmp_path/'fixed/slowfixture.py').read_text()=='value = 42\n'
    finally:
        server.shutdown();server.server_close();worker.join(timeout=3)


def test_verifier_setup_only_changes_uv_timeout():
    from experiments.scripts.validate_tb_bootstrap_batch import SETUP
    updated=native.configure_verifier_download_timeout(SETUP)
    assert updated==SETUP.replace('export UV_HTTP_TIMEOUT=40','export UV_HTTP_TIMEOUT=180')


def test_timeout_contract_separates_histories_without_erasing_failure_cost(tmp_path):
    cells=[];retries=[]
    for i,(policy,fixed,tokens,target) in enumerate([
        ('causal',False,10,cells),('clean_recovery',True,50,cells),('causal',True,20,retries)]):
        path=tmp_path/str(i)/'host-result.json';path.parent.mkdir()
        result=dict(runner.experiment_configuration(),success=True,completed=True,api_usage=dict(complete=True,total_tokens=tokens))
        if fixed:result['download_timeout_contract']=native.DOWNLOAD_TIMEOUT_CONTRACT
        path.write_text(json.dumps(result));target.append(dict(task='fixture',harness='codex',policy=policy,state='completed',result_path=str(path)))
    report=separate_comparisons(dict(known_cumulative_tokens=80),cells,retries)
    assert report['known_cumulative_tokens']==80
    assert any(x['policy']=='causal' for x in report['cumulative_pair_exclusions'])
    assert all(v['both_success_pairs']==0 for g in report['cumulative_configuration_groups'].values()
        for policies in g['cumulative_paired_success'].values() for v in policies.values())
