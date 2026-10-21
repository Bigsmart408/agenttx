#!/usr/bin/env python3
"""Repair recorded TB download failures with verified local build inputs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
DATA=Path('/mnt/data/agenttx-runtime')
OUT=DATA/'validation/tb-network-repairs-20261018-r1'
OLD=DATA/'validation/tb-images-local-20261018-r1'
STP='7a3fd493ae6f0a524f853946308f4f3c3ddcbe76'

def now():return datetime.now(timezone.utc).isoformat()
def save(path,value):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,indent=2)+'\n');tmp.replace(path)
def api(path):
    request=urllib.request.Request('https://api.github.com/'+path,headers={'User-Agent':'agenttx-local-environment-repair'})
    return json.loads(urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=20).read())
def download(url,path):
    started=time.monotonic()
    request=urllib.request.Request('https://gh-proxy.com/'+url,headers={'User-Agent':'agenttx-local-environment-repair'})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=15) as response,path.open('wb') as dest:
        while True:
            chunk=response.read(65536)
            if not chunk:break
            dest.write(chunk)
            if time.monotonic()-started>120:raise TimeoutError('asset download exceeded 120 seconds')
    return hashlib.sha256(path.read_bytes()).hexdigest()
def gitblob(body):return hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest()

def repair(task):
    from experiments.workloads import terminal_bench_suite as tb
    folder=OUT/task;folder.mkdir()
    row=dict(task=task,state='preparing',started_at=now(),original_attempt=str(OLD/task))
    save(folder/'result.json',row)
    try:
        source=tb.task_source(DATA/'cache',tb.load_tasks(DATA/'cache')[task])
        context=folder/'context';shutil.copytree(source,context)
        text=(OLD/task/'domestic-only.Dockerfile').read_text()
        if task=='bn-fit-modify':
            metadata=api('repos/gdreiman1/t_bench_bn_dataset/contents/bn_sample_10k.csv?ref=main')
            assert metadata['sha']=='9d2ff98d09f536bc6182baa1b58d8b726ee94db2'
            asset=context/'bn_sample_10k.csv'
            digest=download(metadata['download_url'],asset)
            assert gitblob(asset.read_bytes())==metadata['sha']
            anchor='RUN curl -fsSL "${BN_URL}" -o bn_sample_10k.csv'
            assert text.count(anchor)==1
            text=text.replace(anchor,'COPY bn_sample_10k.csv /app/bn_sample_10k.csv\n')
            row.update(asset_sha256=digest,asset_git_blob=metadata['sha'])
        elif task=='build-stp':
            ref=api('repos/stp/stp/git/ref/tags/2.3.3');assert ref['object']['sha']==STP
            tree=api('repos/stp/stp/git/trees/'+STP+'?recursive=1');assert not tree['truncated']
            wanted={r['path']:r['sha'] for r in tree['tree'] if r['type']=='blob'}
            asset=context/'stp-source.tar.gz';digest=download('https://github.com/stp/stp/archive/'+STP+'.tar.gz',asset)
            verified=set()
            with tarfile.open(asset) as archive:
                for member in archive.getmembers():
                    if member.isdir():continue
                    relative=member.name.split('/',1)[1]
                    body=member.linkname.encode() if member.issym() else archive.extractfile(member).read()
                    assert relative in wanted and gitblob(body)==wanted[relative],relative
                    verified.add(relative)
            assert verified==set(wanted)
            start=text.index('RUN git clone ')
            text=text[:start]+'COPY stp-source.tar.gz /tmp/stp-source.tar.gz\nRUN mkdir -p /app/stp && tar -xzf /tmp/stp-source.tar.gz --strip-components=1 -C /app/stp && rm /tmp/stp-source.tar.gz\n'
            row.update(asset_sha256=digest,source_commit=STP,git_blobs_verified=len(verified))
        else:raise ValueError(task)
        effective=folder/'Dockerfile';effective.write_text(text)
        tag=tb._official_image_tag(tb.load_tasks(DATA/'cache')[task],source)
        row.update(state='building',image_tag=tag,effective_dockerfile_sha256=hashlib.sha256(text.encode()).hexdigest());save(folder/'result.json',row)
        with (folder/'build.log').open('w') as log:
            build=subprocess.run(['docker','build','--progress','plain','--network','host','--file',str(effective),'--tag',tag,str(context)],stdout=log,stderr=subprocess.STDOUT,timeout=3600)
        assert build.returncode==0,'build failed; see '+str(folder/'build.log')
        info=json.loads(subprocess.check_output(['docker','image','inspect',tag],text=True))[0]
        row.update(state='image_ready',image_id=info['Id'],workdir=info['Config']['WorkingDir'],finished_at=now())
    except Exception as exc:row.update(state='environment_error',finished_at=now(),error=type(exc).__name__+': '+str(exc))
    save(folder/'result.json',row);print(json.dumps(row),flush=True);return row

def main():
    assert os.environ['DOCKER_HOST']=='unix:///run/agenttx-docker/docker.sock'
    OUT.mkdir(exist_ok=False)
    save(OUT/'registration.json',dict(registered_at=now(),tasks=['bn-fit-modify','build-stp'],workers=2,asset_transport='gh-proxy.com with GitHub API Git object identity checks',original_results_unchanged=True))
    with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(repair,['bn-fit-modify','build-stp']))
    save(OUT/'execution.json',dict(finished_at=now(),tasks=rows))

if __name__=='__main__':main()
