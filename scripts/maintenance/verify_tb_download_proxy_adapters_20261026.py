"""Both official adapters keep private proxy routing; no model calls."""
import json
import os
from pathlib import Path
import subprocess
import sys

PROJECT=Path('/home/bfq/agenttx-workspace/agenttx')
sys.path[:0]=[str(PROJECT),str(PROJECT/'src')]
from experiments.scripts.run_tb_dual_local import cell_command

AUDIT=Path('/mnt/data/agenttx-runtime/validation/tb-selective-download-proxy-20261026')
EVIDENCE='/mnt/data/agenttx-runtime/validation/tb-basic-official-validation-20261019-r1/filter-js-from-html/result.json'


def main():
    rows=[]
    for harness in ('codex','sweagent'):
        folder=AUDIT/('adapter-'+harness)
        assert not folder.exists()
        command=cell_command(PROJECT,folder,dict(task='filter-js-from-html',harness=harness,policy='clean_recovery'),EVIDENCE)+['--preflight-only']
        env=dict(os.environ,AGENTTX_DOWNLOAD_PROXY_RETRY='1',AGENTTX_NO_PROXY='1',NO_PROXY='*',AGENTTX_SKIP_ORPHAN_REAP='1')
        for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):env.pop(name,None)
        with (AUDIT/('adapter-'+harness+'.log')).open('w') as log:
            result=subprocess.run(command,cwd=PROJECT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=240)
        row=json.loads((folder/'clean_recovery/host-result.json').read_text())
        rows.append(dict(harness=harness,returncode=result.returncode,status=row.get('status'),proxy=row.get('download_network_retry'),no_model_calls=True))
        print(json.dumps(rows[-1]),flush=True)
        assert result.returncode==0 and row['status']=='preflight_passed' and row['download_network_retry']['model_api_bypasses_local_proxy']
    (AUDIT/'adapter-network-preflights.json').write_text(json.dumps(dict(passed=2,rows=rows),indent=2)+'\n')


if __name__=='__main__':main()
