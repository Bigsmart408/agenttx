"""Reattach an existing native task; no model launch or container restart."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def save(path,value):
    tmp=path.with_name(path.name+'.recovery-tmp');tmp.write_text(json.dumps(value,indent=2)+'\n');tmp.replace(path)


def observed_exit(cid,started,environment):
    inspected=subprocess.run(['docker','inspect',cid],env=environment,capture_output=True,text=True,timeout=15)
    if inspected.returncode==0:
        state=json.loads(inspected.stdout)[0]['State']
        if not state['Running']:return state['ExitCode'],'docker-inspect'
        waited=subprocess.run(['docker','wait',cid],env=environment,capture_output=True,text=True)
        if waited.returncode==0 and waited.stdout.strip().isdigit():return int(waited.stdout.strip()),'docker-wait'
    until=datetime.datetime.now(datetime.timezone.utc).isoformat()
    events=subprocess.run(['docker','events','--since',started,'--until',until,'--filter','container='+cid,
        '--filter','event=die','--format','{{json .}}'],env=environment,text=True,capture_output=True,timeout=30)
    for line in reversed(events.stdout.splitlines()):
        try:event=json.loads(line);code=event['Actor']['Attributes']['exitCode']
        except (ValueError,KeyError,TypeError):continue
        if str(code).isdigit():return int(code),'docker-die-event'
    return None,'unknown-exit-code'


def finalize(row,folder,exit_code,exit_source,usage,provenance):
    target=folder/'host-result.json'
    if target.exists():return False
    result_path=folder/'result.json'
    result=json.loads(result_path.read_text()) if result_path.exists() else dict(completed=False,status='missing_native_result',success=False)
    result.update(container_returncode=exit_code,host_error=None,
        container_wall_s=time.time()-datetime.datetime.fromisoformat(row['started_at']).timestamp(),
        orchestrator_recovery=dict(provenance,container_exit_evidence=exit_source,
            host_cli_interrupted=True,task_container_restarted=False,model_restarted=False))
    registration=folder.parent/'registration.json'
    if registration.exists():
        reg=json.loads(registration.read_text());result['image_id']=reg.get('image',{}).get('id')
    result['policy']=row['policy']
    if exit_code not in (0,1):
        result.update(completed=False,native_success=result.get('success'),success=False,
            status='infrastructure_error',host_error='Recovered task exit was not a normal independently observed exit')
    result['api_usage']=usage(folder,row['harness'],result)
    cache=folder/'prerequisite-cache.json'
    if cache.exists():
        result['prerequisite_cache']=json.loads(cache.read_text())
        if result.get('prerequisite_install'):result['prerequisite_cache']['preparation']=result['prerequisite_install']['mode']
    proxy=folder/'task-services/download-proxy.json'
    if proxy.exists():result['download_network_retry']=json.loads(proxy.read_text())
    save(target,result)
    save(folder.parent/'summary.json',[result])
    return True


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--row',type=Path,required=True);parser.add_argument('--source',type=Path,required=True)
    args=parser.parse_args();row=json.loads(args.row.read_text());folder=Path(row['result_path']).parent
    if (folder/'host-result.json').exists():return 0
    sys.path[:0]=[str(args.source/'src'),str(args.source)]
    from experiments.scripts.bench_tb_image_native import logged_usage
    env=dict(os.environ,DOCKER_HOST='unix:///run/agenttx-docker/docker.sock')
    cid=(folder/'container.cid').read_text().strip()
    prior_logs={}
    for name in ('container.stdout.log','container.stderr.log'):
        path=folder/name
        if path.exists():prior_logs[name]=dict(bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    started=datetime.datetime.now(datetime.timezone.utc).isoformat()
    proof=dict(at=started,original_host_worker_pid=row.get('original_host_worker_pid',row['worker_pid']),
        task_container_id=cid,prior_log_hashes=prior_logs,model_calls_added=0)
    with (folder/'container.recovery.stdout.log').open('w') as stdout,(folder/'container.recovery.stderr.log').open('w') as stderr:
        logs=subprocess.Popen(['docker','logs','--follow',cid],env=env,stdout=stdout,stderr=stderr)
        code,evidence=observed_exit(cid,row['started_at'],env)
        try:logs.wait(timeout=30)
        except subprocess.TimeoutExpired:logs.terminate();logs.wait(timeout=10)
    proof['log_stream_returncode']=logs.returncode
    for suffix in ('stdout','stderr'):
        recovered=folder/('container.recovery.'+suffix+'.log');original=folder/('container.'+suffix+'.log')
        # Docker logs supplies the complete native stream from container start.
        # Preserve the original prefix check as provenance, without summing it twice.
        prior=original.read_bytes() if original.exists() else b''
        data=recovered.read_bytes();proof[suffix+'_original_prefix_matches']=data.startswith(prior)
        if logs.returncode==0 or len(data)>=len(prior):recovered.replace(original)
    services=folder/'task-services';owned=set()
    egress=services/'egress.json'
    if egress.exists():
        data=json.loads(egress.read_text());owned.update(data.get(k) for k in ('anchor_id','helper_id') if data.get(k))
        network=data.get('network')
        ids=subprocess.check_output(['docker','ps','-q'],env=env,text=True).split()
        if ids:
            for container in json.loads(subprocess.check_output(['docker','inspect',*ids],env=env,text=True)):
                if container['HostConfig']['NetworkMode']==network and container['Config']['Entrypoint']==['/mihomo']:
                    proxy_logs=subprocess.run(['docker','logs',container['Id']],env=env,capture_output=True,text=True,timeout=15)
                    (services/'download-proxy.log').write_text(proxy_logs.stdout+proxy_logs.stderr)
                    owned.add(container['Id'])
    proof['owned_services_cleanup']=[]
    for service in sorted(owned):
        try:
            cleaned=subprocess.run(['docker','rm','-f',service],env=env,text=True,capture_output=True,timeout=30)
            proof['owned_services_cleanup'].append(dict(id=service,returncode=cleaned.returncode))
        except subprocess.TimeoutExpired:proof['owned_services_cleanup'].append(dict(id=service,timeout=True))
    save(folder/'orchestrator-recovery.json',proof)
    finalize(row,folder,code,evidence,logged_usage,proof)
    print(json.dumps(dict(task=row['task'],harness=row['harness'],policy=row['policy'],exit_code=code,source=evidence)),flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
