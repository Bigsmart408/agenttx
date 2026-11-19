"""Read-only evidence for official dependency installers, without model probes."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import time

from tb_hf_prerequisite_evidence_20261026 import D, EVIDENCE, PINNED


def sample():
    pointer=json.loads((D/'validation/active-tb-dual-native.json').read_text())
    progress=json.loads((Path(pointer['root'])/'progress.json').read_text())
    env=dict(os.environ,DOCKER_HOST='unix:///run/agenttx-docker/docker.sock')
    EVIDENCE.mkdir(exist_ok=True,parents=True)
    now=datetime.datetime.now(datetime.timezone.utc);summaries=[]
    for row in progress['tasks']+progress['retry_tasks']:
        if row['state']!='running' or row['task']!='hf-train-lora-adapter':continue
        folder=Path(row['result_path']).parent;cidfile=folder/'container.cid'
        if not cidfile.exists():continue
        cid=cidfile.read_text().strip()
        top=subprocess.run(['docker','top',cid,'-eo','pid,ppid,etime,args'],env=env,text=True,capture_output=True,timeout=15)
        if top.returncode:continue
        processes={}
        for line in top.stdout.splitlines()[1:]:
            parts=line.split(None,3)
            if len(parts)==4:processes[int(parts[0])]=dict(pid=int(parts[0]),ppid=int(parts[1]),elapsed=parts[2],args=parts[3])
        installers=[]
        for process in processes.values():
            argv=process['args'].split()
            if len(argv)!=8 or Path(argv[0]).name!='uv' or argv[1:3]!=['pip','install'] or set(argv[3:])!=PINNED:continue
            parent=process['ppid'];seen=set();ancestors=[]
            while parent in processes and parent not in seen:
                seen.add(parent);ancestor=processes[parent]
                if ancestor['args']=='bash /opt/agenttx-verifier/verify.sh':ancestors.append(dict(pid=parent,args=ancestor['args'],elapsed=ancestor['elapsed']))
                parent=ancestor['ppid']
            installers.append(dict(process,official_verifier_ancestors=ancestors))
        if not installers:continue
        model=folder/'model-result.json';model=json.loads(model.read_text()) if model.exists() else {}
        proof=dict(at=now.isoformat(),task=row['task'],harness=row['harness'],policy=row['policy'],
            result_path=row['result_path'],container=cid,installers=installers,model_returncode=model.get('returncode'),no_model_calls=True)
        name=row['harness']+'-'+row['policy']+'-'+now.strftime('%Y%m%dT%H%M%SZ')+'.json'
        path=EVIDENCE/name
        with path.open('x') as handle:handle.write(json.dumps(proof,indent=2)+'\n')
        summaries.append(dict(harness=row['harness'],policy=row['policy'],installers=len(installers),model_returncode=model.get('returncode')))
    return dict(at=now.isoformat(),workers=summaries,no_model_calls=True)


if __name__=='__main__':
    previous=None;last=0
    while True:
        try:
            result=sample();signature=json.dumps(result['workers'],sort_keys=True)
            if signature!=previous or time.monotonic()-last>=300:
                print(json.dumps(result),flush=True);last=time.monotonic()
            previous=signature
        except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
            print(json.dumps(dict(error_type=type(exc).__name__,no_model_calls=True)),flush=True)
        time.sleep(30)
