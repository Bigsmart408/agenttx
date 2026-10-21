#!/usr/bin/env python3
"""Recover only a hash-pinned input from its original public task-image layer."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import time
import urllib.parse
import urllib.request
from experiments.scripts.validate_tb_ready_local import DATA,digest,save,now
from experiments.workloads import terminal_bench_suite as tb


class RegistryRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        redirected=super().redirect_request(req,fp,code,msg,headers,newurl)
        if urllib.parse.urlsplit(newurl).netloc!=urllib.parse.urlsplit(req.full_url).netloc:
            redirected.remove_header('Authorization')
        return redirected


def recover_member(blob,member,target,expected):
    with tarfile.open(blob,'r|gz') as archive:
        for item in archive:
            if item.name.lstrip('./')!=member.lstrip('/'):
                continue
            assert item.isfile(),'asset must be a regular file'
            with archive.extractfile(item) as src,target.open('xb') as dst:
                shutil.copyfileobj(src,dst,1024*1024)
            tb._verify_local_asset(target,expected)
            return
    raise RuntimeError('original asset absent from pinned layer')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--task',choices=sorted(tb._CACHED_DOWNLOADS),required=True)
    p.add_argument('--reference',required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    assert args.output.resolve().is_relative_to(DATA/'validation')
    args.output.mkdir(exist_ok=False)
    downloads=tb._CACHED_DOWNLOADS[args.task];assert len(downloads)==1
    instruction,filename,member,expected=downloads[0]
    repo,tag=args.reference.rsplit(':',1)
    assert repo=='alexgshaw/'+args.task
    host='https://docker.1ms.run'
    opener=urllib.request.build_opener(RegistryRedirect())
    token_url=host+'/openapi/v1/auth/token?'+urllib.parse.urlencode(
        dict(service='docker.1ms.run',scope='repository:'+repo+':pull'))
    with opener.open(token_url,timeout=30) as response:
        token=json.load(response)
    headers={'Authorization':'Bearer '+(token.get('token') or token['access_token']),
        'Accept':'application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.oci.image.index.v1+json',
        'User-Agent':'agenttx-environment-repair'}
    base=host+'/v2/'+repo+'/'
    def fetch(path):
        with opener.open(urllib.request.Request(base+path,headers=headers),timeout=30) as response:
            raw=response.read()
        return raw
    raw=fetch('manifests/'+tag);manifest=json.loads(raw)
    save(args.output/'manifest.json',manifest)
    if 'manifests' in manifest:
        item=next(x for x in manifest['manifests'] if x['platform']['architecture']=='amd64' and x['platform']['os']=='linux')
        raw=fetch('manifests/'+item['digest'])
        assert 'sha256:'+hashlib.sha256(raw).hexdigest()==item['digest']
        manifest=json.loads(raw);save(args.output/'manifest-amd64.json',manifest)
    raw=fetch('blobs/'+manifest['config']['digest'])
    assert 'sha256:'+hashlib.sha256(raw).hexdigest()==manifest['config']['digest']
    config=json.loads(raw);save(args.output/'config.json',config)
    pairs=zip((h for h in config['history'] if not h.get('empty_layer')),manifest['layers'],strict=True)
    candidates=[(h,layer) for h,layer in pairs if instruction.removeprefix('RUN ') in h.get('created_by','')]
    assert len(candidates)==1,'require exact original download history'
    history,layer=candidates[0]
    save(args.output/'registration.json',dict(started_at=now(),task=args.task,
        reference=args.reference,config_digest=manifest['config']['digest'],layer=layer,
        history=history,member=member,expected_sha256=expected,model_calls=0,
        image_workspace_executed=False,task_solutions_used=False))
    blob=args.output/'original-asset-layer.tar.gz'
    request=urllib.request.Request(base+'blobs/'+layer['digest'],headers=headers)
    started=time.monotonic();received=0
    with opener.open(request,timeout=60) as response,blob.open('xb') as stream:
        while chunk:=response.read(1024*1024):
            stream.write(chunk);received+=len(chunk)
            save(args.output/'progress.json',dict(checked_at=now(),downloaded_bytes=received,
                expected_bytes=layer['size']))
            assert time.monotonic()-started<1800,'layer download wall deadline'
    assert received==layer['size']
    assert 'sha256:'+digest(blob)==layer['digest']
    asset=args.output/filename
    recover_member(blob,member,asset,expected)
    cache=tb._asset_cache_dir();assert cache.resolve().is_relative_to(DATA)
    cache.mkdir(parents=True,exist_ok=True);target=cache/filename
    if target.exists():tb._verify_local_asset(target,expected)
    else:shutil.copy2(asset,target)
    save(args.output/'proof.json',dict(finished_at=now(),task=args.task,sha256=digest(asset),
        bytes=asset.stat().st_size,layer_digest=layer['digest'],cache=str(target),
        model_calls=0,task_solutions_used=False,only_original_input_recovered=True))


if __name__=='__main__':main()
