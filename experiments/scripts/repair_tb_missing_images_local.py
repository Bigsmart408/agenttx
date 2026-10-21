#!/usr/bin/env python3
"""Repair missing TB images using verified assets, then run model-free tests."""
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback

from experiments.scripts.validate_tb_bootstrap_batch import run, effective_script, TOOLS
from experiments.scripts.validate_tb_ready_local import DATA, digest, save
from experiments.scripts.experiment_provenance import source_manifest
from experiments.workloads import terminal_bench_suite as tb

ROOT=Path(__file__).resolve().parents[2]


def now():
    return datetime.now(timezone.utc).isoformat()


def assets_for(task):
    assets=[]
    for instruction, name, destination, expected in tb._CACHED_DOWNLOADS.get(task, ()):
        url=re.search(r'https://[^\s"\\]+', instruction)[0]
        assets.append((name,url,expected))
    if task=='llm-spec-decoding':
        for model, files in tb._SMOLLM2_ASSETS.items():
            model_id, revision = (('SmolLM2-360M','f8027fd0') if model=='smollm2-360m' else ('SmolLM2-135M','93efa2f0'))
            for name, expected in files.items():
                assets.append((model+'/'+name,'https://huggingface.co/HuggingFaceTB/'+model_id+'/resolve/'+revision+'/'+name,expected))
    dataset_repos={
        'mnist-learning-fix': ('ylecun/mnist','77f32790'),
        'word2vec-from-scratch': ('Salesforce/wikitext','b08601e0'),
        'reshard-c4-data': ('allenai/c4','1588ec45'),
    }
    if task in dataset_repos:
        repo, revision=dataset_repos[task]
        for name, expected in tb._DATASET_ASSETS[task].items():
            # Cache layout prefixes identify datasets; upstream filenames do not.
            remote=name.split('/',1)[1]
            assets.append((name,'https://huggingface.co/datasets/'+repo+'/resolve/'+revision+'/'+remote,expected))
    return assets


def download(name,url,expected,cache,folder,update):
    target=cache/name
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        tb._verify_local_asset(target,expected)
        return dict(file=name,sha256=expected,bytes=target.stat().st_size,reused_verified=True)
    sources=[url.replace('https://huggingface.co/','https://hf-mirror.com/'),url] if 'huggingface.co/' in url else [url]
    failures=[]
    partial=target.with_name(target.name+'.partial')
    for n, source in enumerate(dict.fromkeys(sources)):
        log=folder/('download-'+re.sub(r'[^a-zA-Z0-9_.-]','_',name)+'-'+str(n)+'.log')
        command=['curl','--fail','--location','--continue-at','-', '--connect-timeout','15',
                 '--max-time','1800','--speed-time','90','--speed-limit','1024',
                 '--output',str(partial),source]
        with log.open('w') as stream:
            process=subprocess.Popen(command,stdout=stream,stderr=subprocess.STDOUT)
            while process.poll() is None:
                update(state='downloading',asset=name,url=source,
                       downloaded_bytes=partial.stat().st_size if partial.exists() else 0)
                time.sleep(5)
        if process.returncode==0:
            try:
                tb._verify_local_asset(partial,expected)
            except RuntimeError:
                # Preserve bad bytes as diagnostic evidence, never install them.
                partial.rename(folder/(re.sub(r'[^a-zA-Z0-9_.-]','_',name)+'.invalid-'+str(n)))
                failures.append(dict(url=source,error='checksum_mismatch'));continue
            partial.replace(target)
            return dict(file=name,url=source,sha256=expected,bytes=target.stat().st_size)
        failures.append(dict(url=source,returncode=process.returncode,log=str(log)))
    raise RuntimeError('Asset transport exhausted: '+json.dumps(failures))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--tasks',nargs='+',required=True)
    args=parser.parse_args()
    assert socket.gethostname()=='r760-ssd'
    assert os.environ['DOCKER_HOST']=='unix:///run/agenttx-docker/docker.sock'
    assert os.environ['AGENTTX_DOMESTIC_ONLY']=='1'
    assert args.output.resolve().is_relative_to(DATA/'validation')
    cache=tb._asset_cache_dir();assert cache.resolve().is_relative_to(DATA)
    cache.mkdir(parents=True,exist_ok=True)
    args.output.mkdir(exist_ok=False);(args.output/'bootstrap').mkdir()
    tasks=tb.load_tasks(DATA/'cache')
    manifest=source_manifest(ROOT);save(args.output/'source-manifest.json',manifest)
    save(args.output/'registration.json',dict(registered_at=now(),tasks=args.tasks,workers=2,
        model_calls=0,gold_solution_used=False,original_results_unchanged=True,
        source_sha256=manifest['sha256'],asset_cache=str(cache),
        download_idle_s=90,download_limit_s=1800,build_limit_s=4200,bootstrap_limit_s=900))
    rows={name:dict(task=name,state='pending') for name in args.tasks};mutex=threading.Lock()
    def update(name,**fields):
        with mutex:
            rows[name].update(fields)
            save(args.output/'progress.json',dict(updated_at=now(),coordinator_pid=os.getpid(),
                counts=dict(Counter(r['state'] for r in rows.values())),tasks=list(rows.values())))
    def repair(name):
        folder=args.output/name;folder.mkdir()
        try:
            assert shutil.disk_usage(DATA).free>=350*2**30,'data disk below guard'
            task=tasks[name];source=tb.task_source(DATA/'cache',task)
            update(name,state='preparing',started_at=now(),assets=[])
            assets=[]
            for filename,url,expected in assets_for(name):
                assets.append(download(filename,url,expected,cache,folder,
                    lambda **fields:update(name,**fields)))
                update(name,assets=assets)
            if name in {'path-tracing','path-tracing-reverse'}:
                binary=TOOLS/'bin/uv'
                assert digest(binary)=='04e7399b45054f5ae4239ed60cd579311daafdd8d43e0e6ac01003436f19eaac'
                # Both tasks share exactly the same upstream installer.
                with mutex:
                    if not (cache/'uv-0.7.13').exists():shutil.copy2(binary,cache/'uv-0.7.13')
                effective,versions=effective_script((source/'install.sh').read_text())
                assert versions==[dict(upstream='0.7.13',resolved='0.7.13')]
                script=cache/(name+'-install.sh');script.write_text(effective)
                update(name,bootstrap_asset_sha256=digest(script),uv_sha256=digest(binary))
            update(name,state='building')
            env=dict(os.environ,AGENTTX_TB_BUILD_EVIDENCE_DIR=str(folder),
                     PYTHONPATH=str(ROOT/'src')+':'+str(ROOT))
            with (folder/'build.log').open('w') as stream:
                process=subprocess.Popen([sys.executable,'-u',str(ROOT/'experiments/scripts/prebuild_missing_tb_images.py'),
                    '--cache-root',str(DATA/'cache'),'--one-task',name],cwd=ROOT,env=env,
                    stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
                update(name,child_pid=process.pid)
                try:rc=process.wait(timeout=4200)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGTERM)
                    try:process.wait(timeout=10)
                    except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
                    raise TimeoutError('build exceeded 4200 seconds')
            assert rc==0,'build failed; see '+str(folder/'build.log')
            tag=tb._official_image_tag(task,source)
            info=json.loads(subprocess.check_output(['docker','image','inspect',tag],text=True,timeout=30))[0]
            update(name,state='image_ready',image_id=info['Id'],image_tag=tag,
                original_dockerfile_sha256=digest(tb._docker_build_paths(source)[1]))
            update(name,state='validating')
            result=run(dict(task=name,local_image_id=info['Id']),args.output/'bootstrap',900,
                       'https://mirrors.aliyun.com/pypi/simple',download_timeout=180)
            update(name,state='official_tests_executed' if result['official_tests_executed'] else 'bootstrap_blocked',
                baseline_classification=result['baseline_classification'],
                validation_result=str(args.output/'bootstrap'/name/'result.json'),finished_at=now())
        except Exception as exc:
            (folder/'error.txt').write_text(traceback.format_exc())
            update(name,state='environment_error',error=repr(exc),finished_at=now())
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(repair,args.tasks))
    save(args.output/'execution.json',dict(finished_at=now(),tasks=list(rows.values()),model_calls=0))


if __name__=='__main__':main()
