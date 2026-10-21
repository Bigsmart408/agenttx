#!/usr/bin/env python3
"""Fetch the registered original input through a byte-identical alternate URL."""
import argparse
from concurrent.futures import ThreadPoolExecutor,wait
from pathlib import Path
import re
import shutil
import subprocess
from experiments.scripts.validate_tb_ready_local import DATA,save,now
from experiments.scripts.repair_tb_missing_images_local import download
from experiments.workloads import terminal_bench_suite as tb


def segmented_download(url,name,expected,size,connections,output,cache,segments=None,reuse=None):
    assert 1<connections<=16 and size>connections
    segments=segments or connections
    assert connections<=segments<=64
    parts=[]
    for i in range(segments):
        start=size*i//segments;end=size*(i+1)//segments-1
        parts.append((i,start,end,output/f'part-{i:02d}.data'))
    def fetch(part):
        i,start,end,path=part
        if reuse:
            prior=reuse/path.name
            if prior.exists() and prior.stat().st_size==end-start+1:
                shutil.copy2(prior,path)
                return
        header=output/f'part-{i:02d}.headers'
        cmd=['curl','--fail','--location','--connect-timeout','15','--max-time','1200',
            '--speed-time','90','--speed-limit','1024','--retry','2','--retry-delay','10',
            '--range',f'{start}-{end}','--dump-header',str(header),'--output',str(path),url]
        with (output/f'part-{i:02d}.log').open('w') as log:
            subprocess.run(cmd,check=True,stdout=log,stderr=subprocess.STDOUT,timeout=3700)
        ranges=re.findall(r'(?im)^content-range:\s*bytes (\d+)-(\d+)/(\d+)',header.read_text())
        assert ranges and tuple(map(int,ranges[-1]))==(start,end,size),'range response mismatch'
        assert path.stat().st_size==end-start+1
    with ThreadPoolExecutor(max_workers=connections) as pool:
        pending={pool.submit(fetch,part) for part in parts}
        while pending:
            done,pending=wait(pending,timeout=5)
            for future in done:future.result()
            save(output/'progress.json',dict(checked_at=now(),state='segmented_download',
                expected_bytes=size,downloaded_bytes=sum(p.stat().st_size for _,_,_,p in parts if p.exists()),
                remaining_parts=len(pending),connections=connections))
    joined=output/'verified-input.data'
    with joined.open('xb') as dst:
        for _,_,_,path in parts:
            with path.open('rb') as src:shutil.copyfileobj(src,dst,1024*1024)
    tb._verify_local_asset(joined,expected)
    target=cache/name
    if target.exists():tb._verify_local_asset(target,expected)
    else:shutil.copy2(joined,target)
    return dict(file=name,url=url,sha256=expected,bytes=size,connections=connections)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--task',choices=sorted(tb._CACHED_DOWNLOADS),required=True)
    p.add_argument('--url',required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--connections',type=int,default=1)
    p.add_argument('--bytes',type=int)
    p.add_argument('--segments',type=int)
    p.add_argument('--reuse-parts',type=Path)
    args=p.parse_args()
    assert args.url.startswith('https://')
    assert args.output.resolve().is_relative_to(DATA/'validation')
    if args.reuse_parts:assert args.reuse_parts.resolve().is_relative_to(DATA/'validation')
    args.output.mkdir(exist_ok=False)
    assets=tb._CACHED_DOWNLOADS[args.task];assert len(assets)==1
    instruction,name,destination,expected=assets[0]
    cache=tb._asset_cache_dir();assert cache.resolve().is_relative_to(DATA)
    cache.mkdir(parents=True,exist_ok=True)
    save(args.output/'registration.json',dict(started_at=now(),task=args.task,
        original_instruction=instruction,alternate_url=args.url,expected_sha256=expected,
        model_calls=0,task_solutions_used=False,connections=args.connections,
        segments=args.segments,reused_parts_from=str(args.reuse_parts) if args.reuse_parts else None))
    if args.connections>1:
        assert args.bytes
        result=segmented_download(args.url,name,expected,args.bytes,args.connections,args.output,cache,args.segments,args.reuse_parts)
    else:
        result=download(name,args.url,expected,cache,args.output,
            lambda **fields:save(args.output/'progress.json',dict(checked_at=now(),**fields)))
    save(args.output/'proof.json',dict(finished_at=now(),task=args.task,
        original_asset_identity_verified=True,model_calls=0,**result))


if __name__=='__main__':main()
