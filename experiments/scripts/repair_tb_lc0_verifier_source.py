#!/usr/bin/env python3
"""Restore the exact signed LC0 verifier-tool commit from the upstream archive."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
from experiments.scripts.validate_tb_ready_local import DATA,save,digest,now
from experiments.workloads import terminal_bench_suite as tb

COMMIT='69fbfc6567a19da09793a4418dc2c533d30bd064'
TREE='758b7092c8196b2a50ba4088273111776adad50f'


def signed_commit_bytes(metadata):
    verification=metadata['verification'];assert verification['verified']
    headers,message=verification['payload'].split('\n\n',1)
    lines=headers.splitlines()
    for tail in ['', '\n']:
        signature='gpgsig '+(verification['signature'].rstrip('\n')+tail).replace('\n','\n ')
        for position in range(len(lines)+1):
            for ending in ['', '\n']:
                raw=('\n'.join(lines[:position]+[signature]+lines[position:])+'\n\n'+message+ending).encode()
                if hashlib.sha1(b'commit '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==metadata['sha']:
                    return raw
    raise RuntimeError('Cannot reproduce original signed commit object')


def main():
    output=DATA/'validation/tb-lc0-verifier-source-20261020-r2';output.mkdir(exist_ok=False)
    prior=DATA/'validation/tb-lc0-asset-repair-20261020-r1'
    metadata=json.loads((prior/'verifier-git-commit.json').read_text())
    assert metadata['sha']==COMMIT and metadata['tree']['sha']==TREE
    raw=signed_commit_bytes(metadata);(output/'commit.raw').write_bytes(raw)
    save(output/'commit.json',metadata)
    archive=prior/'verifier-lc0-head.tar.gz'
    extracted=output/'extracted';extracted.mkdir()
    with tarfile.open(archive) as stream:stream.extractall(extracted,filter='data')
    roots=list(extracted.iterdir());assert len(roots)==1 and roots[0].is_dir()
    repo=roots[0];assert not (repo/'.git').exists()
    subprocess.run(['git','init',str(repo)],check=True,capture_output=True)
    git=['git','-C',str(repo)]
    subprocess.run([*git,'-c','core.autocrlf=false','add','-f','-A'],check=True,timeout=60)
    assert subprocess.check_output([*git,'write-tree'],text=True).strip()==TREE
    restored=subprocess.check_output([*git,'hash-object','-t','commit','-w','--stdin'],input=raw).decode().strip()
    assert restored==COMMIT
    (repo/'.git/shallow').write_text(COMMIT+'\n')
    subprocess.run([*git,'checkout','-B','master',COMMIT],check=True,capture_output=True)
    subprocess.run([*git,'remote','add','origin','https://github.com/LeelaChessZero/lc0.git'],check=True)
    with (output/'fsck.log').open('w') as log:
        subprocess.run([*git,'fsck','--full','--strict'],check=True,stdout=log,stderr=subprocess.STDOUT)
    assert not subprocess.check_output([*git,'status','--porcelain','--ignored'],text=True).strip()
    cache=tb._asset_cache_dir();assert cache.resolve().is_relative_to(DATA)
    target=cache/'lc0-verifier-source';assert not target.exists();repo.rename(target)
    proof=dict(prepared_at=now(),commit=COMMIT,tree=TREE,exact_signed_commit_reproduced=True,
        archive_sha256=digest(archive),git_fsck_strict_passed=True,shallow_history=True,
        original_unpinned_tool_source_frozen=True,task_starting_image_unmodified=True,
        model_calls=0,agent_solution_supplied=False)
    save(output/'proof.json',proof);save(cache/'lc0-verifier-source-proof.json',proof)
    print(json.dumps(proof))


if __name__=='__main__':main()
