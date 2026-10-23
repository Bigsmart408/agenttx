"""Release strace waiting only on an orphan gpg-agent after the command exits."""
from pathlib import Path
from datetime import datetime,timezone
import json,subprocess,time
BASE=Path('/mnt/data/agenttx-runtime')
ROOT=BASE/'results/tb-dual-native-full-20261021-r16'
AUDIT=BASE/'validation/tb-gpg-daemon-repair-20261022'
TASK='decommissioning-service-with-sensitive-data'
PROBE=r'''
import os,json,time,signal
from pathlib import Path
states={}
for f in Path('/proc').glob('[0-9]*/status'):
 try:
  d={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in f.read_text().splitlines() if ':' in line}
  states[int(f.parent.name)]=d
 except (OSError,ValueError):pass
out=[]
for pid,d in states.items():
 if d.get('Name')!='gpg-agent':continue
 tracer=int(d.get('TracerPid','0'))
 if not tracer or states.get(tracer,{}).get('Name')!='strace':continue
 tracees=[p for p,s in states.items() if int(s.get('TracerPid','0'))==tracer]
 if tracees!=[pid]:continue
 stat=Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()
 age=float(Path('/proc/uptime').read_text().split()[0])-int(stat[19])/os.sysconf('SC_CLK_TCK')
 if age<60:continue
 cmd=Path('/proc',str(pid),'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
 if not cmd.startswith('gpg-agent ') or '--daemon' not in cmd:continue
 # Check again immediately before sending SIGTERM; the original process tree
 # must have exited and only this daemon may still be attached to this tracer.
 current=[]
 for f in Path('/proc').glob('[0-9]*/status'):
  try:
   s=f.read_text()
   if any(line.split(':',1)[0]=='TracerPid' and int(line.split(':',1)[1])==tracer for line in s.splitlines() if ':' in line):current.append(int(f.parent.name))
  except (OSError,ValueError):pass
 if current!=[pid]:continue
 os.kill(pid,signal.SIGTERM)
 out.append(dict(pid=pid,tracer_pid=tracer,only_remaining_tracee=True,command=cmd,age_s=round(age),signal='SIGTERM'))
print(json.dumps(out))
'''
def main():
 AUDIT.mkdir(exist_ok=True)
 (AUDIT/'registration.json').write_text(json.dumps(dict(started_at=datetime.now(timezone.utc).isoformat(),cohort=ROOT.name,task=TASK,scope='Only a gpg-agent --daemon which is the sole remaining strace tracee after its command tree has exited; no filesystem/test/prompt/model changes.',frozen_source_modified=False),indent=2)+'\n')
 while True:
  p=json.loads((ROOT/'progress.json').read_text());targets=[t for t in p['tasks'] if t['task']==TASK]
  for t in targets:
   if t['state']!='running':continue
   cell=ROOT/'cells'/TASK/t['harness']/t['policy']/t['policy'];cid=cell/'container.cid'
   if not cid.is_file():continue
   container=cid.read_text().strip()
   cmd=['docker','-H','unix:///run/agenttx-docker/docker.sock','exec','-i',container,'/home/bfq/.venvs/agenttx/bin/python','-']
   result=subprocess.run(cmd,input=PROBE,text=True,capture_output=True,timeout=20)
   if result.returncode:continue
   actions=json.loads(result.stdout)
   if actions:
    row=dict(checked_at=datetime.now(timezone.utc).isoformat(),task=TASK,harness=t['harness'],policy=t['policy'],container_id=container,actions=actions)
    with (AUDIT/'actions.jsonl').open('a') as stream:stream.write(json.dumps(row)+'\n')
    print(json.dumps(row),flush=True)
  counts={s:sum(t['state']==s for t in targets) for s in sorted({t['state'] for t in targets})}
  status=dict(checked_at=datetime.now(timezone.utc).isoformat(),counts=counts)
  (AUDIT/'progress.json').write_text(json.dumps(status,indent=2)+'\n')
  if all(t['state'] in ('completed','infrastructure_error') for t in targets):
   (AUDIT/'execution.json').write_text(json.dumps(status,indent=2)+'\n');return
  time.sleep(20)
if __name__=='__main__':main()
