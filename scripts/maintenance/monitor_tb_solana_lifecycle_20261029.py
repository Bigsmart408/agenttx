"""Read-only live service observations across the model/verifier boundary."""
import concurrent.futures
import datetime
import json
import os
from pathlib import Path
import subprocess
import time

D = Path('/mnt/data/agenttx-runtime')
OUT = D/'validation/tb-live-monitor-20261026/solana-service-lifecycle.jsonl'
ENV = dict(os.environ, DOCKER_HOST='unix:///run/agenttx-docker/docker.sock')
PROBE = '''import socket,json,pathlib
s=socket.socket();s.settimeout(1);rc=s.connect_ex(('127.0.0.1',5000));s.close()
rows=[]
for p in pathlib.Path('/proc').iterdir():
 if not p.name.isdigit(): continue
 try:
  name=(p/'comm').read_text().strip()
  if name.startswith(('python','node','sanic','uvicorn','gunicorn','flask')):
   rows.append({'pid':int(p.name),'name':name,'state':(p/'stat').read_text().rsplit(') ',1)[1].split()[0]})
 except OSError: pass
print(json.dumps({'port5000_connect_rc':rc,'processes':rows}))
'''


def sample():
    root=Path(json.loads((D/'validation/active-tb-dual-native.json').read_text())['root'])
    progress=json.loads((root/'progress.json').read_text())
    ids=subprocess.check_output(['docker','ps','-q'],env=ENV,text=True).split()
    inspection=subprocess.run(['docker','inspect',*ids],env=ENV,capture_output=True,text=True) if ids else None
    containers=json.loads(inspection.stdout) if inspection else []
    rows=[r for r in progress['tasks']+progress['retry_tasks'] if r['state']=='running' and r['task']=='solana-data']

    def check(row):
        folder=Path(row['result_path']).parent
        out={k:row[k] for k in ('harness','policy','result_path')}
        out['model_finished_file']=(folder/'model-result.json').exists()
        container=next((c for c in containers if any(m['Source']==str(folder) for m in c['Mounts'])),None)
        if not container:
            return dict(out,container_missing=True)
        result=subprocess.run(['docker','exec',container['Id'],'python3','-c',PROBE],env=ENV,capture_output=True,text=True,timeout=8)
        out.update(container=container['Id'],rc=result.returncode)
        if result.returncode==0:out.update(json.loads(result.stdout))
        for filename in ('model-result.json','host-result.json'):
            path=folder/filename
            if path.exists():
                data=json.loads(path.read_text())
                out[filename]={k:data.get(k) for k in ('returncode','official_success','model_returncode')}
        # Signal records can explain whether the harness or the task stopped a
        # service. Retain only bounded syscall/signal lines, never argv or env.
        traces=[]
        for path in (folder/'session').glob('worker-strace-*.log'):
            with path.open('rb') as f:
                f.seek(max(0,path.stat().st_size-262144))
                for line in f.read().decode(errors='replace').splitlines():
                    if '--- SIG' in line or '+++ killed by SIG' in line:
                        traces.append(line[:500])
        out['recent_signals']=traces[-30:]
        return out

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        workers=list(pool.map(check,rows))
    return dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(),cohort=root.name,
                readonly=True,host_forwarding_modified=False,workers=workers)


if __name__=='__main__':
    while True:
        try:
            data=sample()
            with OUT.open('a') as f:f.write(json.dumps(data)+'\n')
            print(json.dumps(dict(at=data['at'],workers=len(data['workers']))),flush=True)
        except Exception as exc:
            print(json.dumps(dict(error=type(exc).__name__)),flush=True)
        time.sleep(15)
