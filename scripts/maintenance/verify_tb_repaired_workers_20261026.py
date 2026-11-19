"""Read durable model evidence for the initial eight r23 model workers."""
import datetime
import json
import os
from pathlib import Path
import re
import subprocess

D=Path('/mnt/data/agenttx-runtime')
AUDIT=D/'validation/tb-socket-service-repair-20261026'
ROOT=D/'results/tb-dual-native-full-20261026-r23-runtime-repair'

def main():
    progress=json.loads((ROOT/'progress.json').read_text())
    dispatches=[json.loads(s) for s in (AUDIT/'dispatches.jsonl').read_text().splitlines()][:8]
    evidence=[]
    for a in dispatches:
        rows=progress['retry_tasks'] if a['retry'] else progress['tasks']
        row=next(r for r in rows if (r['task'],r['harness'],r['policy'])==(a['task'],a['harness'],a['policy']) and r.get('execution_cohort')==ROOT.name)
        folder=Path(row['result_path']).parent
        total=0;records=0;responses=0;paths=[]
        if a['harness']=='codex':
            files=list(folder.glob('session/upperdir/app/.agenttx-codex-*/codex/sessions/*/*/*/*.jsonl'))
            files+=list(folder.glob('**/retained-trajectories/**/*.jsonl'))
            for path in files:
                last=0
                for line in path.read_text(errors='replace').splitlines():
                    try:e=json.loads(line)
                    except ValueError:continue
                    payload=e.get('payload',{})
                    if e.get('type')=='response_item' and payload.get('role')=='assistant':responses+=1
                    if e.get('type')=='event_msg' and payload.get('type')=='token_count':
                        info=payload.get('info') or {}
                        last=max(last,(info.get('total_token_usage') or {}).get('total_tokens',0));records+=1
                total+=last;paths.append(str(path))
        else:
            path=folder/'container.stdout.log'
            if path.exists():
                text=path.read_text(errors='replace')
                # SWE's rich logger wraps Usage fields across padded lines.
                for match in re.finditer(r'Usage\((.*?)\)',text,re.S):
                    m=re.search(r'total_tokens\s*=\s*(\d+)',match.group(1))
                    if m:total+=int(m.group(1));records+=1
                responses=text.count('🎬 ACTION')
                paths.append(str(path))
        terminal=folder/'host-result.json'
        if terminal.exists():
            u=json.loads(terminal.read_text()).get('api_usage',{})
            if u.get('complete'):
                total=max(total,u.get('total_tokens') or 0);records=max(records,1);responses=max(responses,1)
        evidence.append(dict(task=a['task'],harness=a['harness'],policy=a['policy'],state=row['state'],
            response_events=responses,usage_records=records,recorded_tokens=total,
            actual_model_data=responses>0 and total>0,provisional=not terminal.exists(),paths=paths))
    # Read-only container inspection: task source mounts identify ownership.
    env=dict(os.environ,DOCKER_HOST='unix:///run/agenttx-docker/docker.sock')
    ids=subprocess.check_output(['docker','ps','-q'],env=env,text=True).split()
    containers=[]
    if ids:
        for c in json.loads(subprocess.check_output(['docker','inspect',*ids],env=env,text=True)):
            if any(str(ROOT) in m.get('Source','') for m in c.get('Mounts',[])):
                containers.append(dict(id=c['Id'],name=c['Name'],network=c['HostConfig']['NetworkMode']))
    proof=dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(),cohort=ROOT.name,
        workers=evidence,actual_model_workers=sum(r['actual_model_data'] for r in evidence),
        stop_reason=progress['stop_reason'],active_count=len(progress['active']),
        containers=containers,task_containers_private=bool(containers) and all(c['network']!='host' for c in containers))
    temp=AUDIT/'eight-worker-data.tmp';temp.write_text(json.dumps(proof,indent=2)+'\n');temp.replace(AUDIT/'eight-worker-data.json')
    print(json.dumps({k:v for k,v in proof.items() if k not in ('workers','containers')}))
    for row in evidence: print(json.dumps({k:v for k,v in row.items() if k!='paths'}))

if __name__=='__main__':main()
