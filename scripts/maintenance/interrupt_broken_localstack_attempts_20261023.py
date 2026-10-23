from pathlib import Path
import os,signal,json,subprocess,time,datetime,sys,hashlib
sys.path[:0]=['/home/bfq/agenttx','/home/bfq/agenttx/src']
from experiments.scripts.bench_tb_image_native import logged_usage
D=Path('/mnt/data/agenttx-runtime');pointer=json.loads((D/'validation/active-tb-dual-native.json').read_text());assert pointer['cohort']=='tb-dual-native-full-20261023-r19-hosts-resume'
root=Path(pointer['root']);p=json.loads((root/'progress.json').read_text());audit=D/'validation/tb-original-service-resume-20261023';audit.mkdir(exist_ok=True)
now=lambda:datetime.datetime.now(datetime.timezone.utc).isoformat()
proof=json.loads((D/'validation/tb-api-connection-repair-20261023/original-localstack-verification.json').read_text());assert proof['private_localstack_health']==200 and proof['initial_s3_buckets_empty']
os.kill(pointer['coordinator_pid'],signal.SIGSTOP)
rows=[r for r in p['retry_tasks'] if r['state']=='running' and r['task']=='create-bucket' and r['harness']=='sweagent']
(audit/'interruption-plan.json').write_text(json.dumps(dict(at=now(),previous_coordinator_pid=pointer['coordinator_pid'],rows=rows,reason='Original LocalStack ENTRYPOINT service is not preserved outside tool process groups; stop affected attempts and record unknown in-flight cost before environment repair'),indent=2)+'\n')
records=[]
for row in rows:
 pid=row['worker_pid'];cell=Path(row['result_path']).parent;result=Path(row['result_path'])
 if result.exists():records.append(dict(pid=pid,result_already_finished=True));continue
 proc=Path('/proc')/str(pid)
 if proc.exists():
  args=(proc/'cmdline').read_bytes().replace(b'\0',b' ').decode();assert 'bench_tb_image_native.py host --task create-bucket --harness sweagent' in args
  assert os.getpgid(pid)==pid
  os.killpg(pid,signal.SIGTERM)
 cid=(cell/'container.cid').read_text().strip()
 subprocess.run(['docker','rm','-f',cid],capture_output=True,text=True,check=True,timeout=30)
 egress=json.loads((cell/'task-services/egress.json').read_text())
 for owned in (egress['helper_id'],egress['anchor_id']):
  assert len(owned)==64 and all(c in '0123456789abcdef' for c in owned)
  subprocess.run(['docker','rm','-f',owned],capture_output=True,text=True,timeout=30)
 for _ in range(50):
  if not proc.exists() or (proc/'stat').read_text().rsplit(')',1)[1].split()[0]=='Z':break
  time.sleep(.1)
 else:raise RuntimeError('Affected worker did not exit')
 usage=logged_usage(cell,'sweagent',{});usage.update(complete=False,lower_bound=True,reason='Interrupted for confirmed environment repair; unbilled in-flight requests may add cost; all logged main/replay usage retained')
 data=dict(task=row['task'],harness=row['harness'],policy=row['policy'],completed=False,success=False,
  status='interrupted_for_environment_repair',environment_failure='original_localstack_service_not_preserved',
  interrupted_at=now(),worker_pid=pid,api_usage=usage,worker_interrupted=True,
  reason='Restore original task-image LocalStack ENTRYPOINT service outside tool process groups; preserve this attempt, do not count it as an algorithm failure')
 if not result.exists():
  with result.open('x') as f:json.dump(data,f,indent=2);f.write('\n')
 records.append(dict(pid=pid,result_path=str(result),api_usage=usage,host_result_sha256=hashlib.sha256(result.read_bytes()).hexdigest()))
(audit/'interrupted-attempts.json').write_text(json.dumps(dict(at=now(),records=records,unaffected_workers_interrupted=False,host_8080_rules_modified=False),indent=2)+'\n');print(json.dumps(records,indent=2))
