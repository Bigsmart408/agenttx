#!/usr/bin/env python3
"""Prepare and probe original TB sidecars on isolated task networks, without gold/models."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import argparse,hashlib,json,os,re,shlex,shutil,subprocess,time,traceback
from pathlib import Path
import yaml
from experiments.workloads import terminal_bench_suite as tb
from experiments.scripts.validate_tb_ready_local import DATA,save,digest

MIRRORS=('docker.1ms.run','hub.rat.dev','docker.1panel.live')
SETUP='''ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple PIP_DEFAULT_TIMEOUT=40 PIP_RETRIES=1
RUN for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources /etc/apt/sources.list.d/*.list; do [ ! -f "$f" ] || sed -i -E 's|https?://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g; s|https?://security.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' "$f"; done
'''


def pull(reference,folder):
    # Resolve only through domestic registries, then pin the local immutable ID.
    image=reference.removeprefix('docker.io/')
    if '/' not in image:image='library/'+image
    for mirror in MIRRORS:
        ref=mirror+'/'+image
        p=subprocess.run(['docker','image','inspect',ref],capture_output=True,text=True)
        if p.returncode:
            from experiments.scripts.docker_pull_progress import pull_image
            def observe(progress):
                save(folder/('pull-progress-'+image.replace('/','_').replace(':','_')+'.json'),
                     dict(progress,source=ref,checked_at=datetime.now(timezone.utc).isoformat()))
            attempt=pull_image(ref,folder/('pull-'+mirror+'-'+image.replace('/','_').replace(':','_')+'.jsonl'),
                               env=os.environ.copy(),observe=observe)
            save(folder/('pull-result-'+mirror+'-'+image.replace('/','_').replace(':','_')+'.json'),attempt)
            p.returncode=attempt['returncode']
        if p.returncode==0:
            return json.loads(subprocess.check_output(['docker','image','inspect',ref],text=True))[0]['Id']
    raise RuntimeError('All domestic sources failed for '+reference)


def prepare_image(service,source,folder,key):
    build=service.get('build')
    if build:
        build={'context':build} if isinstance(build,str) else build
        context=(source/build.get('context','.')).resolve()
        dockerfile=context/build.get('dockerfile','Dockerfile')
        original=dockerfile.read_text()
        effective=tb._domestic_dockerfile(original)
        lines=effective.splitlines(True)
        for n,line in enumerate(lines):
            match=re.match(r'^(\s*FROM\s+(?:--platform=\S+\s+)?)(\S+)(.*)',line,re.I)
            if match:
                reference=match.group(2)
                if reference.split('/',1)[0] in MIRRORS:
                    # Pull with observable byte/idle deadlines before building;
                    # docker build's wall cap must not truncate a live base pull.
                    pinned=pull(reference.split('/',1)[1],folder)
                    line=match.group(1)+pinned+match.group(3)+'\n'
                lines[n]=line+SETUP
        effective=''.join(lines)
    else:
        base=pull(service['image'],folder)
        context=source
        effective='FROM '+base+'\n'+SETUP
    # Preinstall precisely the pip requirement expression in upstream startup.
    command=service.get('command','')
    command=' '.join(command) if isinstance(command,list) else command
    pattern=r'pip install ([^&;\n]+)\s*&&'
    installs=re.findall(pattern,command)
    for expression in installs:
        effective+='RUN python -m pip install '+expression.strip()+'\n'
    if not build and not installs:
        return base
    path=folder/(key+'.Dockerfile');path.write_text(effective)
    tag='agenttx-tb-sidecar:'+folder.name+'-'+key+'-'+hashlib.sha256(effective.encode()).hexdigest()[:12]
    with (folder/(key+'.build.log')).open('w') as log:
        p=subprocess.run(['docker','build','--network=host','--pull=false','-f',str(path),'-t',tag,str(context)],stdout=log,stderr=subprocess.STDOUT,timeout=900)
    if p.returncode:raise RuntimeError('sidecar image build failed: '+key)
    if installs:
        command=re.sub(pattern,'true &&',command)
        service['command']=['sh','-c',command[command.find('sh -c ')+6:].strip().strip('"')] if command.startswith('sh -c ') else ['sh','-c',command]
    return json.loads(subprocess.check_output(['docker','image','inspect',tag],text=True))[0]['Id']


PROBE=r'''import socket,json,sys,time
checks=json.loads(sys.argv[1]);out=[]
for host,port in checks:
 try:
  ips=socket.gethostbyname_ex(host)[2]
  if port:
   with socket.create_connection((host,port),timeout=3):pass
  out.append(dict(host=host,port=port,ok=True,ips=ips))
 except Exception as e:out.append(dict(host=host,port=port,ok=False,error=str(e)))
print(json.dumps(out))
sys.exit(0 if all(x['ok'] for x in out) else 1)
'''


def run(record,output):
    task=record['task'];folder=output/task;folder.mkdir()
    project='agenttx-tb-sc-'+task[:32]+'-'+output.name[-2:]
    compose=['docker','compose','--project-name',project,'-f',str(folder/'compose.yaml')]
    row=dict(task=task,started_at=datetime.now(timezone.utc).isoformat(),model_calls=0,gold_solution_used=False,
             readiness_scope='service startup plus client-to-service DNS/TCP only; no task verdict')
    try:
        original=tb.task_source(DATA/'cache',tb.load_tasks(DATA/'cache')[task])
        source=folder/'runtime-assets'
        shutil.copytree(original,source,ignore=shutil.ignore_patterns('solution*','tests','.git','task.yaml','run-tests.sh'))
        config=yaml.safe_load((original/'docker-compose.yaml').read_text())
        client='client' if 'client' in config['services'] else 'webserver'
        checks=[]
        for key,service in config['services'].items():
            service.pop('container_name',None)
            if key==client:
                service.pop('build',None);service['image']=record['local_image_id']
                service['command']=['sh','-c','sleep infinity']
            else:
                service['image']=prepare_image(service,source,folder,key);service.pop('build',None)
                ports=service.get('ports',[])
                if ports:
                    for p in ports:
                        checks.append([key,int(str(p).split(':')[-1].split('/')[0])])
                else:checks.append([key,None])
            service['pull_policy']='never'
            service.pop('ports',None) # Private DNS/ports preserved; no global port collisions.
            service.pop('links',None)
            service['networks']=['task']
            env=service.get('environment',{})
            if isinstance(env,list):env=dict(x.split('=',1) if '=' in x else (x,'') for x in env)
            service['environment']={k:('/tests' if '${T_BENCH_TEST_DIR}' in str(v) else v) for k,v in env.items()}
            volumes=[]
            for volume in service.get('volumes',[]):
                if '${T_BENCH' in volume:continue
                parts=volume.split(':')
                if parts[0].startswith('.'):
                    parts[0]=str((source/parts[0]).resolve())
                volumes.append(':'.join(parts))
            service['volumes']=volumes
        # Each compose project owns its network and volumes; no daemon changes.
        config['networks']={'task':{'driver':'bridge','internal':True}}
        (folder/'compose.yaml').write_text(yaml.safe_dump(config,sort_keys=False))
        row['effective_compose_sha256']=digest(folder/'compose.yaml')
        row['original_compose_sha256']=digest(original/'docker-compose.yaml')
        row['images']={k:s['image'] for k,s in config['services'].items()}
        row['transport_changes']=['Preinstalled upstream startup pip requirements using TUNA.',
            'Private task bridge with original service DNS and container ports; no host publications.',
            'Source bind assets copied without reference solutions/tests; independent named volumes.']
        save(folder/'registration.json',row)
        with (folder/'up.log').open('w') as log:
            p=subprocess.run([*compose,'up','-d','--wait','--wait-timeout','150'],stdout=log,stderr=subprocess.STDOUT,timeout=200)
        row['compose_up_rc']=p.returncode
        # db_init is a completed one-shot initializer, not a long-running server.
        checks=[c for c in checks if c[0]!='db_init']
        for attempt in range(15):
            p=subprocess.run([*compose,'exec','-T',client,'python3','-c',PROBE,json.dumps(checks)],capture_output=True,text=True,timeout=80)
            (folder/'connectivity.stdout').write_text(p.stdout);(folder/'connectivity.stderr').write_text(p.stderr)
            if p.returncode==0:break
            time.sleep(3)
        row['client_connectivity_ok']=p.returncode==0
        if p.stdout.strip().startswith('['):row['connectivity']=json.loads(p.stdout)
        row['passed']=row['compose_up_rc']==0 and row['client_connectivity_ok']
    except Exception as exc:
        row.update(passed=False,error=repr(exc));(folder/'error.txt').write_text(traceback.format_exc())
    finally:
        if (folder/'compose.yaml').exists():
            for label,command in [('services',['ps','--all','--format','json']),('logs',['logs','--no-color'])]:
                p=subprocess.run([*compose,*command],capture_output=True,text=True,timeout=30)
                (folder/(label+'.log')).write_text(p.stdout+p.stderr)
            p=subprocess.run([*compose,'down','--volumes','--remove-orphans','--timeout','5'],capture_output=True,text=True,timeout=60)
            row['cleanup_returncode']=p.returncode
    row['finished_at']=datetime.now(timezone.utc).isoformat();save(folder/'result.json',row)
    print(json.dumps({k:row.get(k) for k in ['task','passed','error']}),flush=True)
    return row


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--tasks',nargs='+');args=p.parse_args()
    assert os.environ['DOCKER_HOST']=='unix:///run/agenttx-docker/docker.sock'
    args.output.mkdir(exist_ok=False)
    summary=json.loads((DATA/'validation/tb-ready-validation-summary-20261018.json').read_text())
    tasks=args.tasks or [r['task'] for r in summary['checks'] if r['classification']=='additional_services_required']
    checks=json.loads((DATA/'validation/tb-readiness-recheck-20261018.json').read_text())['checks']
    records=[r for r in checks if r['task'] in tasks and r['identity_verified']]
    save(args.output/'registration.json',dict(tasks=tasks,source_sha256=digest(Path(__file__)),models=0,gold=False,workers=2))
    rows=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        for result in as_completed([pool.submit(run,r,args.output) for r in records]):
            rows.append(result.result());save(args.output/'progress.json',dict(completed=len(rows),total=len(records),tasks=rows))
    save(args.output/'execution.json',dict(attempted=len(rows),passed=sum(r['passed'] for r in rows),tasks=rows))

if __name__=='__main__':main()
