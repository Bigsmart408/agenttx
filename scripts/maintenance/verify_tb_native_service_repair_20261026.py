"""Exercise original service binaries with both adapters; zero model calls."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys

PROJECT = Path('/home/bfq/agenttx-workspace/agenttx')
sys.path[:0] = [str(PROJECT),str(PROJECT/'src')]
from experiments.scripts.run_tb_dual_local import cell_command

AUDIT = Path('/mnt/data/agenttx-runtime/validation/tb-socket-service-repair-20261026')
EVIDENCE = {
 'install-windows-xp':'/mnt/data/agenttx-runtime/validation/tb-xp-image-repair-20261020-r1/bootstrap/install-windows-xp/result.json',
 'home-server-https':'/mnt/data/agenttx-runtime/validation/tb-sidecar-official-20261020-r4/home-server-https/result.json',
}

def run(task, harness):
    folder=AUDIT/('final-native-'+task+'-'+harness)
    assert not folder.exists(), folder
    row=dict(task=task,harness=harness,policy='clean_recovery')
    command=cell_command(PROJECT,folder,row,EVIDENCE[task])+['--preflight-only']
    env=dict(os.environ,AGENTTX_NO_PROXY='1',NO_PROXY='*',AGENTTX_SKIP_ORPHAN_REAP='1')
    for k in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):env.pop(k,None)
    with (AUDIT/(folder.name+'-host.log')).open('w') as log:
        p=subprocess.run(command,cwd=PROJECT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=240)
    result=json.loads((folder/'clean_recovery/host-result.json').read_text())
    proof=dict(task=task,harness=harness,returncode=p.returncode,status=result.get('status'),service_lifecycle_preflight=result.get('service_lifecycle_preflight'))
    print(json.dumps(proof),flush=True)
    assert p.returncode==0 and result['status']=='preflight_passed'
    assert result['service_lifecycle_preflight']['trusted_verifier_connected']
    return proof

if __name__=='__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(run,t,h) for t in EVIDENCE for h in ('codex','sweagent')]
        proofs=[f.result() for f in futures]
    (AUDIT/'final-native-service-proofs.json').write_text(json.dumps(proofs,indent=2)+'\n')
