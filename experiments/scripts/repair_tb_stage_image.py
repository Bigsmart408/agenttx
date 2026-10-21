#!/usr/bin/env python3
"""Rebuild the recorded Slurm failure with stage-aware domestic image rewriting."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from experiments.workloads import terminal_bench_suite as tb
from experiments.scripts.experiment_provenance import source_manifest

DATA=Path('/mnt/data/agenttx-runtime')
OUT=DATA/'validation/tb-stage-repair-20261018-r1'


def now():return datetime.now(timezone.utc).isoformat()


def save(name,value):
    tmp=OUT/(name+'.tmp');tmp.write_text(json.dumps(value,indent=2)+'\n');tmp.replace(OUT/name)


def main():
    assert os.environ['DOCKER_HOST']=='unix:///run/agenttx-docker/docker.sock'
    assert os.environ['AGENTTX_DOMESTIC_ONLY']=='1'
    assert shutil.disk_usage(DATA).free>200*2**30
    OUT.mkdir(exist_ok=False)
    task_id='slurm-simple-node-monitoring'
    manifest=source_manifest(ROOT)
    save('source-manifest.json',manifest)
    save('registration.json',dict(registered_at=now(),task=task_id,workers=1,
         original_attempt=str(DATA/'validation/tb-images-local-20261018-r1'/task_id),
         original_results_unchanged=True,source_sha256=manifest['sha256'],model_calls=0))
    row=dict(task=task_id,state='preparing',started_at=now(),coordinator_pid=os.getpid())
    save('progress.json',row)
    try:
        task=tb.load_tasks(DATA/'cache')[task_id];source=tb.task_source(DATA/'cache',task)
        context,original=tb._docker_build_paths(source)
        original=original or source/'Dockerfile'
        text=tb._domestic_dockerfile(original.read_text())
        dockerfile=OUT/'Dockerfile';dockerfile.write_text(text)
        image=tb._official_image_tag(task,source)
        row.update(state='building',image_tag=image,original_dockerfile_sha256=hashlib.sha256(original.read_bytes()).hexdigest(),
                   effective_dockerfile_sha256=hashlib.sha256(text.encode()).hexdigest())
        save('progress.json',row)
        with (OUT/'build.log').open('w') as log:
            process=subprocess.Popen(['docker','build','--progress','plain','--network','host',
                        '--file',str(dockerfile),'--tag',image,str(context)],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            row['child_pid']=process.pid;save('progress.json',row)
            try:rc=process.wait(timeout=3600)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGTERM)
                try:process.wait(timeout=15)
                except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
                raise TimeoutError('build exceeded registered 3600 seconds')
        if rc:raise RuntimeError('build failed; see '+str(OUT/'build.log'))
        info=json.loads(subprocess.check_output(['docker','image','inspect',image],text=True,timeout=30))[0]
        assert info['Architecture']=='amd64' and info['Os']=='linux'
        row.update(state='image_ready',image_id=info['Id'],workdir=info['Config']['WorkingDir'])
    except Exception as exc:row.update(state='environment_error',error=type(exc).__name__+': '+str(exc))
    row['finished_at']=now();save('progress.json',row);save('execution.json',dict(finished_at=now(),tasks=[row]))
    print(json.dumps(row),flush=True)


if __name__=='__main__':main()
