"""User-authorized terminal-cell retention: summaries for all, Clean trajectories only."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,re,shutil,subprocess,time,signal,sys
BASE=Path('/mnt/data/agenttx-runtime')
AUDIT=BASE/'validation/tb-summary-retention-20261022'
ALLOWED=(BASE/'results',Path('/mnt/recovery-sdc/agenttx-archive/20261021'))
USAGE=re.compile(r'usage=Usage\(completion_tokens=(\d+),prompt_tokens=(\d+),total_tokens=(\d+)')

def now():return datetime.now(timezone.utc).isoformat()
def save(path,value):
 p=path.with_name(path.name+'.retention-tmp');p.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');p.replace(path)
def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as stream:
  for block in iter(lambda:stream.read(2**20),b''):h.update(block)
 return h.hexdigest()
def remove(path):
 if path.is_symlink():path.unlink()
 elif path.is_dir():shutil.rmtree(path)
 elif path.exists():path.unlink()
def tail(path,limit=65536):
 if not path.is_file():return ''
 with path.open('rb') as f:f.seek(max(0,path.stat().st_size-limit));return f.read().decode(errors='replace')
def diagnostic_excerpt(value,limit):
 text=str(value)
 text=re.sub(r'sk-[A-Za-z0-9_-]{12,}','[redacted key]',text)
 text=re.sub(r'(?i)Bearer\s+[A-Za-z0-9._-]+','Bearer [redacted]',text)
 return text[-limit:]
def bounded_lean_download_observations(text):
 """Keep at most two real dependency-error/redirect observations, not a trace."""
 selected=[]
 for block in re.findall(r'<observation>(.*?)</observation>',text,re.S):
  compact=re.sub(r'\s+','',block)
  error=all(s in compact for s in ('error:couldnotdownloadfilefrom',
   'https://releases.lean-lang.org/lean4/v4.21.0/lean-4.21.0-linux.tar.zst',
   'info:causedby:[28]Timeoutwasreached(Operationtooslow.'))
  redirect='location:https://github.com/leanprover/lean4/releases/download/v4.21.0/lean-4.21.0-linux.tar.zst' in compact
  if (error or redirect) and len(block)<=1800:
   kind='error' if error else 'redirect'
   selected=[(k,v) for k,v in selected if k!=kind]
   selected.append((kind,'<observation>'+diagnostic_excerpt(block,1800)+'</observation>'))
 return '\n'.join(value for _,value in selected)
def bounded_model_diagnostics(data):
 events=list(data.get('events') or [])
 for line in str(data.get('stdout','')).splitlines():
  try:event=json.loads(line)
  except ValueError:continue
  if isinstance(event,dict):events.append(event)
 seen=set();tools=[];assistant='';counts={}
 for event in events:
  if not isinstance(event,dict):continue
  encoded=json.dumps(event,sort_keys=True);digest=hashlib.sha256(encoded.encode()).hexdigest()
  if digest in seen:continue
  seen.add(digest)
  item=event.get('item',{}) if event.get('type')=='item.completed' else {}
  if item.get('type')=='agent_message':assistant=item.get('text','')
  if item.get('type')=='command_execution':
   rc=item.get('exit_code');label=str(rc);counts[label]=counts.get(label,0)+1
   command=str(item.get('command',''))
   tools.append(dict(exit_code=rc,status=item.get('status'),command_sha256=hashlib.sha256(command.encode()).hexdigest(),
                     output_excerpt=diagnostic_excerpt(item.get('aggregated_output',''),400)))
  payload=event.get('payload') or {}
  if event.get('type')=='event_msg' and payload.get('type')=='agent_message':assistant=payload.get('message','')
 return dict(schema='agenttx.bounded-model-diagnostics/v1',observed_command_results=sum(counts.values()),
             command_exit_counts=counts,last_tool_results=tools[-3:],
             final_assistant_excerpt=diagnostic_excerpt(assistant,1200),
             bounded_summary_only=True,full_trajectory_retained=False)
def model_summary(path):
 if not path.is_file():return None
 data=json.loads(path.read_text());summary={k:v for k,v in data.items() if k not in ('stdout','stderr','events')}
 summary['original_sha256']=sha(path)
 summary['stderr_excerpt']=str(data.get('stderr',''))[-16000:]
 summary['bounded_diagnostics']=bounded_model_diagnostics(data)
 summary['usage_and_terminal_events']=[]
 for event in data.get('events',[]):
  if not isinstance(event,dict):continue
  typ=event.get('type','')
  if 'usage' in typ or typ in ('turn.completed','turn.failed','error'):
   summary['usage_and_terminal_events'].append(event)
 return summary

def retain(cell,policy,harness,worker_log=None,allow_test=False):
 cell=Path(cell).resolve()
 if not allow_test:assert any(cell.is_relative_to(p) for p in ALLOWED)
 result_path=cell/'host-result.json';marker=cell/'retention.json'
 if marker.is_file():return None
 result=json.loads(result_path.read_text());host_sha=sha(result_path);clean=policy=='clean_recovery'
 accounting=cell/'accounting-summary.json'
 if not accounting.exists():
  logs={};records=[]
  for name in ('container.stdout.log','container.stderr.log'):
   f=cell/name
   if not f.is_file():continue
   logs[name]={'sha256':sha(f),'bytes':f.stat().st_size}
   # SWE wraps usage objects across lines; normalize exactly as the native reader.
   normalized=re.sub(r'\s+','',f.read_text(errors='replace'))
   records.extend(dict(completion_tokens=int(m[1]),prompt_tokens=int(m[2]),total_tokens=int(m[3]),source_log=name) for m in USAGE.finditer(normalized))
  model={name:model_summary(cell/name) for name in ('model-result.json','replay-result.json')}
  record=dict(created_at=now(),policy=policy,harness=harness,host_result_sha256=host_sha,
   api_usage=result.get('api_usage'),per_call_usage_records=records,original_logs=logs,model_summaries=model,
   original_result_unchanged=True,clean_full_trajectories_retained=clean)
  if harness=='sweagent' and result.get('api_usage',{}).get('complete'):
   assert sum(x['total_tokens'] for x in records)==result['api_usage']['total_tokens'],'SWE usage mismatch; retain original data'
   assert len(records)==result.get('model_calls',0)+result.get('doc_replay_model_calls',0)
  save(accounting,record)
 else:
  record=json.loads(accounting.read_text());assert record['host_result_sha256']==host_sha
 # Native Codex response-usage files stay at their original paths. Non-Clean
 # files contain accounting events only; Clean keeps the original full JSONL.
 session=cell/'session';old=cell/'session.retention-original';stage=cell/'session.retention-build'
 transcript_metadata=cell/'retained-session-records.json'
 if not transcript_metadata.is_file():
  remove(stage);stage.mkdir();evidence=[]
  workspace=Path(result.get('workspace','/'))
  if workspace.is_absolute() and '..' not in workspace.parts:
   source=session/'upperdir'/str(workspace).lstrip('/')
   for f in source.glob('.agenttx-codex-*/codex/sessions/*/*/*/*.jsonl'):
    if not f.is_file():continue
    relative=f.relative_to(session);dest=stage/relative;dest.parent.mkdir(parents=True,exist_ok=True)
    entry={'path':str(relative),'original_sha256':sha(f),'original_bytes':f.stat().st_size,'full_trajectory':clean}
    if clean:shutil.copy2(f,dest)
    else:
     with f.open(errors='replace') as src,dest.open('w') as out:
      for line in src:
       try:event=json.loads(line)
       except ValueError:continue
       typ=event.get('type');payload=event.get('payload',{})
       if typ=='token_usage_record' or (typ=='event_msg' and payload.get('type')=='token_count'):
        out.write(json.dumps(event)+'\n')
    os.chmod(dest,0o644);entry['retained_sha256']=sha(dest);evidence.append(entry)
  save(transcript_metadata,{'created_at':now(),'files':evidence,'retention':'full native transcript for Clean; accounting events only otherwise'})
  if session.exists() or session.is_symlink():session.rename(old)
  stage.rename(session)
 elif stage.exists():
  # Resume an interrupted directory swap before deleting the old snapshot.
  if session.exists() and not old.exists():session.rename(old)
  if not session.exists():stage.rename(session)
  else:remove(stage)
 removed=[]
 for name in ('session.retention-original','model' if not clean else '', 'replay' if not clean else '', 'cache','task-services/runtime-assets'):
  if not name:continue
  f=cell/name
  if f.exists() or f.is_symlink():remove(f);removed.append(name)
 for name in ('final-ledger.json','verification-ledger.json','recovered-ledger.json'):
  f=cell/name
  if f.is_file():
   stats=cell/(name+'.summary.json')
   if not stats.is_file():save(stats,{'original_bytes':f.stat().st_size,'original_sha256':sha(f),'detailed_ledger_removed':True})
   f.unlink();removed.append(name)
 if not clean:
  # Preserve essential failure diagnostics, without retaining agent trajectories.
  download_observations=''
  if harness=='sweagent' and result.get('task')=='lean4-proof' and (cell/'container.stdout.log').is_file():
   download_observations=bounded_lean_download_observations((cell/'container.stdout.log').read_text(errors='replace'))
  diagnostic={name:tail(cell/name) for name in ('verifier.stdout.log','verifier.stderr.log')}
  if download_observations:diagnostic['bounded_dependency_download_observations']=download_observations
  if not result.get('success') or not result.get('api_usage',{}).get('complete'):
   text=tail(cell/'container.stderr.log')+'\n'+tail(cell/'container.stdout.log')
   diagnostic['model_error_lines']=[line for line in text.splitlines() if re.search(r'Error|Exception|ERROR|WARN|Traceback|exit_status|timed.out|FAILED|Connection',line)][-100:]
  save(cell/'failure-diagnostics.json',diagnostic)
  for name in ('model-result.json','replay-result.json','precrash-conversation.json','recovered-conversation.json'):
   f=cell/name
   if f.is_file():f.unlink();removed.append(name)
  # Leave exact numeric usage records where the frozen SWE parser reads them.
  lines=['usage=Usage(completion_tokens={completion_tokens},prompt_tokens={prompt_tokens},total_tokens={total_tokens})'.format(**x) for x in record['per_call_usage_records']]
  if download_observations:lines.append(download_observations)
  (cell/'container.stdout.log').write_text('\n'.join(lines)+'\n' if lines else '')
  (cell/'container.stderr.log').write_text('Full model log removed by user-authorized summary retention; see failure-diagnostics.json.\n')
  if worker_log and worker_log.is_file():worker_log.write_text('Full worker log removed; see cell accounting-summary.json and failure-diagnostics.json.\n')
 assert sha(result_path)==host_sha
 metadata=json.loads(transcript_metadata.read_text())
 for entry in metadata['files']:assert sha(session/entry['path'])==entry['retained_sha256']
 marker_data=dict(completed_at=now(),schema='agenttx.terminal-summary-retention/v1',policy=policy,harness=harness,
  host_result_sha256=host_sha,host_result_unchanged=True,full_model_trajectory_retained=clean,
  accounting_summary='accounting-summary.json',session_records='retained-session-records.json',removed=removed)
 save(marker,marker_data);return marker_data

def main():
 AUDIT.mkdir(exist_ok=True)
 save(AUDIT/'authorization.json',dict(recorded_at=now(),user_instructions=['只保留数据摘要，其他占用磁盘很多的数据部分可以删掉，只保留关键信息就行，实验要用到的数据可以完整保留','轨迹只保留clean的轨迹'],terminal_only=True,clean_full_trajectories=True,keep_original_host_results=True,preserve_images_caches_inputs_environment_evidence=True))
 while True:
  pointer=json.loads((BASE/'validation/active-tb-dual-native.json').read_text());root=Path(pointer['root']);assert root.parent==BASE/'results'
  progress=json.loads((root/'progress.json').read_text());done=0;retained_count=0;errors=[];free_start=shutil.disk_usage(BASE).free/2**30
  for row in progress['tasks']+progress.get('retry_tasks',[]):
   if row['state'] not in ('completed','infrastructure_error'):continue
   result=Path(row['result_path'])
   if not result.is_file():continue
   if (result.parent/'retention.json').is_file():retained_count+=1;continue
   # Re-read state immediately before touching a cell; only terminal observations.
   current=json.loads((root/'progress.json').read_text())
   match=next(t for t in current['tasks']+current.get('retry_tasks',[]) if t.get('result_path')==row['result_path'] and (t['task'],t['harness'],t['policy'])==(row['task'],row['harness'],row['policy']))
   if match['state'] not in ('completed','infrastructure_error'):continue
   owner=result.parents[5]
   worker=owner/('network-retry-worker-logs' if result.parents[4].name=='network-retry-cells' else 'retry-worker-logs' if row.get('attempt',0)>0 else 'worker-logs')/row['task']/row['harness']/(row['policy']+'.log')
   try:
    res=retain(result.parent,row['policy'],row['harness'],worker)
    if res:
     with (AUDIT/'actions.jsonl').open('a') as stream:stream.write(json.dumps(dict(result_path=str(result),**res))+'\n')
     done+=1;retained_count+=1
   except Exception as exc:
    errors.append(dict(result_path=str(result),error=repr(exc)))
   save(AUDIT/'progress.json',dict(updated_at=now(),cohort=root.name,processed_this_sweep=done,retained_terminal_cells=retained_count,errors=errors,free_start_gib=round(free_start,1),disk_free_gib=round(shutil.disk_usage(BASE).free/2**30,1),phase='cleaning'))
  save(AUDIT/'progress.json',dict(updated_at=now(),cohort=root.name,processed_this_sweep=done,retained_terminal_cells=retained_count,errors=errors,disk_free_gib=round(shutil.disk_usage(BASE).free/2**30,1),phase='watching_completed_cells'))
  if not any(t['state'] in ('pending','running') for t in progress['tasks']+progress.get('retry_tasks',[])):return
  time.sleep(30)
if __name__=='__main__':
 signal.signal(signal.SIGTERM,lambda *_:sys.exit(0))
 main()
