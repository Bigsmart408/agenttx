#!/usr/bin/env python3
"""Recover the exact shallow Flink commit from a verifiable source archive."""
from datetime import datetime,timezone
import argparse
import hashlib
import itertools
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request
from experiments.scripts.validate_tb_ready_local import DATA,digest,save,now
from experiments.workloads import terminal_bench_suite as tb

COMMIT='fe3613574f76201a8d55d572a639a4ce7e18a9db'
TREE='fe436b1700cddd42fad6821ecb43852d671097d9'
OUT=DATA/'validation/tb-flink-source-repair-20261020-r1'


def original_commit(metadata):
    # The API normalizes dates to UTC. Recover the original timezone spelling,
    # accepting bytes only when they reproduce the published commit object ID.
    assert metadata['verification']['reason']=='unsigned'
    zones=['+0000','-0000']+[f'{sign}{n//60:02}{n%60:02}' for n in range(15,14*60+1,15) for sign in ['+','-']]
    def identity(key,zone):
        obj=metadata[key];epoch=int(datetime.fromisoformat(obj['date'].replace('Z','+00:00')).timestamp())
        return f"{key} {obj['name']} <{obj['email']}> {epoch} {zone}\n"
    prefix='tree '+metadata['tree']['sha']+'\n'+''.join('parent '+p['sha']+'\n' for p in metadata['parents'])
    for author,committer in itertools.product(zones,repeat=2):
        for message in [metadata['message'],metadata['message']+'\n']:
            raw=(prefix+identity('author',author)+identity('committer',committer)+'\n'+message).encode()
            if hashlib.sha1(b'commit '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==COMMIT:return raw
    raise RuntimeError('Cannot reproduce the exact upstream commit; refuse substitute history')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OUT)
    parser.add_argument('--archive',type=Path)
    args=parser.parse_args();output=args.output
    assert output.resolve().is_relative_to(DATA/'validation')
    # Keep the original failed/slow transport attempt immutable.
    return repair(output,args.archive)


def repair(OUT,existing_archive=None):
    OUT.mkdir(exist_ok=False);cache=tb._asset_cache_dir()
    assert cache.resolve().is_relative_to(DATA)
    request=urllib.request.Request('https://api.github.com/repos/apache/flink/git/commits/'+COMMIT,
        headers={'User-Agent':'agenttx-environment-repair'})
    with urllib.request.urlopen(request,timeout=20) as response:metadata=json.load(response)
    assert metadata['sha']==COMMIT and metadata['tree']['sha']==TREE
    save(OUT/'commit.json',metadata);raw=original_commit(metadata);(OUT/'commit.raw').write_bytes(raw)
    archive=OUT/'source.tar.gz'
    urls=['https://gh-proxy.com/https://github.com/apache/flink/archive/'+COMMIT+'.tar.gz',
          'https://codeload.github.com/apache/flink/tar.gz/'+COMMIT]
    if existing_archive:
        assert existing_archive.resolve().is_relative_to(DATA/'validation')
        shutil.copy2(existing_archive,archive)
        urls=[];url='https://codeload.github.com/apache/flink/tar.gz/'+COMMIT
    for n,url in enumerate(urls):
        with (OUT/f'download-{n}.log').open('w') as log:
            result=subprocess.run(['curl','--fail','--location','--connect-timeout','15','--max-time','900',
                '--speed-time','90','--speed-limit','1024','--continue-at','-','--output',str(archive),url],
                stdout=log,stderr=subprocess.STDOUT,timeout=930)
        if result.returncode==0:break
    else:
        if not existing_archive:raise RuntimeError('Exact source archive transport unavailable')
    extracted=OUT/'extracted';extracted.mkdir()
    with tarfile.open(archive) as stream:stream.extractall(extracted,filter='data')
    roots=list(extracted.iterdir());assert len(roots)==1 and roots[0].is_dir();repo=roots[0]
    assert not (repo/'.git').exists()
    subprocess.run(['git','init',str(repo)],check=True,capture_output=True)
    git=['git','-C',str(repo)]
    # Upstream tracks fixture files covered by its own .gitignore patterns.
    subprocess.run([*git,'-c','core.autocrlf=false','add','-f','-A'],check=True,timeout=120)
    tree=subprocess.check_output([*git,'write-tree'],text=True,timeout=30).strip();assert tree==TREE,'source tree differs'
    sha=subprocess.check_output([*git,'hash-object','-t','commit','-w','--stdin'],input=raw,timeout=30).decode().strip()
    assert sha==COMMIT
    (repo/'.git/shallow').write_text(COMMIT+'\n')
    subprocess.run([*git,'update-ref','refs/tags/release-1.11.2',COMMIT],check=True)
    subprocess.run([*git,'checkout','--detach',COMMIT],check=True,capture_output=True)
    subprocess.run([*git,'remote','add','origin','https://github.com/apache/flink.git'],check=True)
    with (OUT/'fsck.log').open('w') as log:
        subprocess.run([*git,'fsck','--full','--strict'],check=True,stdout=log,stderr=subprocess.STDOUT,timeout=120)
    assert not subprocess.check_output([*git,'status','--porcelain'],text=True).strip()
    target=cache/'flink-source';assert not target.exists();repo.rename(target)
    proof=dict(prepared_at=now(),commit=COMMIT,tree=TREE,exact_commit_object_reproduced=True,
        archive_sha256=digest(archive),source_url=url,shallow_history=True,git_fsck_strict_passed=True,
        model_calls=0,gold_solution_used=False,task_source_modified=False)
    save(OUT/'proof.json',proof);save(cache/'flink-source-proof.json',proof)
    print(json.dumps(proof),flush=True)


if __name__=='__main__':main()
