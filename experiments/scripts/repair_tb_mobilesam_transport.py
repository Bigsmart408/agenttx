#!/usr/bin/env python3
"""Verify a pinned MobileSAM archive against Git blobs, then retry only bootstrap."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import time
import urllib.request

from experiments.scripts.validate_tb_ready_local import DATA, save, digest

COMMIT='34bbbfdface3c18e5221aa7de6032d7220c6c6a1'
OUTPUT=DATA/'validation/tb-bootstrap-sam-archive-repair-20261019-r5'


def blob_hash(body):
    return hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest()


def verify_source(source, tree):
    assert not tree['truncated']
    wanted={r['path']:r for r in tree['tree'] if r['type']=='blob'}
    assert not any(r['type']=='commit' for r in tree['tree']), 'Unverified submodule'
    found={str(p.relative_to(source)) for p in source.rglob('*') if p.is_file() or p.is_symlink()}
    assert found==set(wanted), 'Source file inventory mismatch'
    for name,record in wanted.items():
        path=source/name
        body=os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
        assert blob_hash(body)==record['sha'], 'Git blob mismatch: '+name
        assert path.is_symlink()==(record['mode']=='120000'), 'Git file mode mismatch: '+name
    return len(wanted)


def fetch(url, path, wall=600):
    started=time.monotonic();last=0;size=0
    request=urllib.request.Request(url,headers={'User-Agent':'agenttx-local-environment-repair'})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request,timeout=40) as response,path.open('wb') as out:
        while True:
            chunk=response.read(65536)
            if not chunk:break
            out.write(chunk);size+=len(chunk)
            elapsed=time.monotonic()-started
            if elapsed-last>=15:
                print(json.dumps(dict(stage='archive_download',bytes=size,wall_s=round(elapsed,1))),flush=True);last=elapsed
            if elapsed>wall:raise TimeoutError('Archive source wall deadline exceeded')
            if size>150*1024*1024:raise ValueError('Unexpected archive size')
    return digest(path)


def prepare():
    folder=OUTPUT/'source';folder.mkdir()
    treefile=folder/'github-tree.json'
    fetch('https://api.github.com/repos/ChaoningZhang/MobileSAM/git/trees/'+COMMIT+'?recursive=1',treefile,60)
    tree=json.loads(treefile.read_text());assert not tree['truncated']
    errors=[]
    for index,proxy in enumerate(['https://gh-proxy.com/','https://ghfast.top/']):
        archive=folder/f'archive-{index}.tar.gz'
        source=folder/f'extracted-{index}';source.mkdir()
        try:
            url=proxy+'https://github.com/ChaoningZhang/MobileSAM/archive/'+COMMIT+'.tar.gz'
            checksum=fetch(url,archive)
            with tarfile.open(archive) as tar:
                members=tar.getmembers()
                # extract with Python's data filter and reject hardlinks/devices.
                assert all(m.isfile() or m.isdir() or m.issym() for m in members)
                tar.extractall(source,filter='data')
            roots=list(source.iterdir());assert len(roots)==1 and roots[0].is_dir()
            blobs=verify_source(roots[0],tree)
            dest=folder/'verified-source';roots[0].rename(dest)
            save(folder/'source-proof.json',dict(commit=COMMIT,archive_sha256=checksum,
                url=url,git_blobs_verified=blobs,tree_sha256=digest(treefile),
                original_reference_preserved=True,prior_transport_errors=errors))
            return dest
        except Exception as exc:
            errors.append(dict(url=proxy,error=repr(exc)))
            save(folder/f'archive-{index}-error.json',errors[-1])
    raise RuntimeError('Pinned archive transports failed: '+repr(errors))


def main():
    assert os.environ['DOCKER_HOST']=='unix:///run/agenttx-docker/docker.sock'
    OUTPUT.mkdir(exist_ok=False)
    prior=DATA/'validation/tb-bootstrap-sam-transport-repair-20261019-r4/sam-cell-seg/result.json'
    save(OUTPUT/'registration.json',dict(tasks=['sam-cell-seg'],timeout_s=1800,model_calls=0,
        gold_solution_used=False,source_commit=COMMIT,previous_result=str(prior),
        previous_result_sha256=digest(prior),original_results_preserved=True,
        purpose='Environment-only retry after Git fetch early EOF; verify every source blob before local install.'))
    try:
        source=prepare()
        from experiments.scripts.validate_tb_bootstrap_batch import run
        evidence=json.loads((DATA/'validation/tb-readiness-recheck-20261018.json').read_text())
        record=next(r for r in evidence['checks'] if r['task']=='sam-cell-seg')
        assert record['identity_verified']
        row=run(record,OUTPUT,1800,'https://mirrors.aliyun.com/pypi/simple','https://gh-proxy.com/',source)
        rows=[row]
    except Exception as exc:
        save(OUTPUT/'preparation-error.json',dict(error=repr(exc),model_calls=0));rows=[]
        print(repr(exc),flush=True)
    save(OUTPUT/'execution.json',dict(finished_at=datetime.now(timezone.utc).isoformat(),tasks=rows,
        attempted=len(rows),official_tests_executed=sum(r['official_tests_executed'] for r in rows)))


if __name__=='__main__':main()
