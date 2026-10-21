#!/usr/bin/env python3
"""Durable local TB build queue; retain every attempt, never launch models."""
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from datetime import datetime, timezone
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import threading

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from experiments.workloads import terminal_bench_suite as tb
from experiments.scripts.experiment_provenance import source_manifest
DATA=Path('/mnt/data/agenttx-runtime')
OUT=DATA/'validation/tb-images-local-20261018-r1'

def now():return datetime.now(timezone.utc).isoformat()
def save(path, obj):
    tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(obj,indent=2)+'\n');tmp.replace(path)

def main():
    global OUT
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OUT)
    parser.add_argument('--tasks',nargs='+')
    args=parser.parse_args()
    OUT=args.output.resolve()
    assert OUT.is_relative_to(DATA/'validation')
    assert socket.gethostname()=='r760-ssd'
    assert os.environ['DOCKER_HOST']=='unix:///run/agenttx-docker/docker.sock'
    assert os.environ['AGENTTX_DOMESTIC_ONLY']=='1'
    cache=Path(os.environ['AGENTTX_CACHE_DIR'])
    lock=(DATA/'validation/local-tb-images.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    OUT.mkdir(exist_ok=False)
    tasks=tb.load_tasks(cache)
    queue=['hello-world']+[t for t in sorted(tasks) if t!='hello-world']
    if args.tasks:
        assert set(args.tasks)<=set(tasks), 'unknown task'
        queue=[t for t in queue if t in set(args.tasks)]
    rows={t:dict(task=t,state='pending') for t in queue}
    manifest=source_manifest(ROOT)
    save(OUT/'source-manifest.json',manifest)
    save(OUT/'registration.json',dict(registered_at=now(),host=socket.gethostname(),workers=2,
         tasks=queue,domestic_only=True,hub_mirror=os.environ.get('AGENTTX_TB_HUB_MIRROR','docker.1ms.run'),
         ghcr_mirror=os.environ.get('AGENTTX_TB_GHCR_MIRRORS','ghcr.nju.edu.cn'),
         min_free_gib=200,model_experiments_started=0,source_manifest_sha256=manifest['sha256']))
    mutex=threading.Lock()
    def update(task,**fields):
        with mutex:
            rows[task].update(fields)
            save(OUT/'progress.json',dict(updated_at=now(),coordinator_pid=os.getpid(),
                 counts=dict(Counter(r['state'] for r in rows.values())),tasks=list(rows.values())))
            with (OUT/'events.jsonl').open('a') as f:f.write(json.dumps(dict(task=task,at=now(),**fields))+'\n')
    def build(task):
        if shutil.disk_usage(DATA).free<200*2**30:
            update(task,state='deferred_disk_guard');return
        folder=OUT/task;folder.mkdir()
        update(task,state='preparing',started_at=now())
        try:
            source=tb.task_source(cache,tasks[task]);tb._docker_build_paths(source)
            env=os.environ.copy();env['AGENTTX_TB_BUILD_EVIDENCE_DIR']=str(folder)
            env['PYTHONPATH']=str(ROOT/'src')+':'+str(ROOT)
            with (folder/'build.log').open('w') as log:
                child=subprocess.Popen([sys.executable,'-u',str(ROOT/'experiments/scripts/prebuild_missing_tb_images.py'),
                    '--cache-root',str(cache),'--one-task',task],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                update(task,child_pid=child.pid)
                try:rc=child.wait(timeout=4200)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid,signal.SIGTERM)
                    try:child.wait(timeout=10)
                    except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
                    update(task,state='environment_error',error='build_wall_timeout',finished_at=now());return
            if rc:raise RuntimeError('build child exited '+str(rc)+'; see '+str(folder/'build.log'))
            tag=tb._official_image_tag(tasks[task],source)
            info=json.loads(subprocess.check_output(['docker','image','inspect',tag],text=True,timeout=30))[0]
            assert info['Architecture']=='amd64' and info['Os']=='linux'
            update(task,state='image_ready',finished_at=now(),image_tag=tag,image_id=info['Id'],
                   workdir=info['Config']['WorkingDir'],user=info['Config'].get('User'),
                   original_dockerfile_sha256=hashlib.sha256(tb._docker_build_paths(source)[1].read_bytes()).hexdigest())
        except (FileNotFoundError,ValueError) as exc:
            update(task,state='unsupported_environment',finished_at=now(),error=f'{type(exc).__name__}: {exc}')
        except Exception as exc:
            update(task,state='environment_error',finished_at=now(),error=f'{type(exc).__name__}: {exc}')
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(build,queue))
    save(OUT/'execution.json',dict(finished_at=now(),tasks=list(rows.values()),model_experiments_started=0))

if __name__=='__main__':main()
