#!/usr/bin/env python3
"""Prepare read-only coordinator Git without repairing task-owned networking/APT."""
from pathlib import Path
import json
import os
import subprocess
import tarfile
import posixpath
from experiments.scripts.validate_tb_ready_local import DATA,save,digest,now
from experiments.scripts.validate_tb_sidecars_local import pull

DEST=DATA/'validation/tb-validation-tools-20261018/portable-git-r3'


def verified_prerequisite():
    """Verify the cached bundle and keep its loader in its private prefix."""
    proof=json.loads((DEST/'proof.json').read_text())
    assert digest(DEST/'git')==proof['wrapper_sha256']
    for name,expected in proof['files'].items():
        path=DEST/'root'/name
        if 'symlink' in expected:
            assert path.is_symlink() and os.readlink(path)==expected['symlink']
        else:
            assert digest(path)==expected['sha256']
    content=(DEST/'git').read_text().replace(
        'exec /lib/ld-musl-x86_64.so.1 ',
        'exec /opt/agenttx-portable-git/lib/ld-musl-x86_64.so.1 ')
    wrapper=DEST/'git-private-loader'
    if not wrapper.exists():
        temporary=DEST/('git-private-loader.'+str(os.getpid())+'.tmp')
        temporary.write_text(content);temporary.chmod(0o755);temporary.replace(wrapper)
    assert wrapper.read_text()==content and os.access(wrapper,os.X_OK)
    return DEST,wrapper,dict(source='hash-verified read-only fallback when image Git is absent',
        proof_sha256=digest(DEST/'proof.json'),wrapper_sha256=digest(wrapper),
        original_image_git_preserved=True,task_network_apt_curl_unmodified=True,
        loader='private /opt prefix; original task libc/loader unchanged')


def main():
    assert os.environ['DOCKER_HOST']=='unix:///run/agenttx-docker/docker.sock'
    DEST.mkdir(exist_ok=False);root=DEST/'root';root.mkdir()
    image=pull('alpine/git:v2.47.2',DEST)
    archive=DEST/'tools.tar'
    with archive.open('wb') as stream:
        subprocess.run(['docker','run','--rm','--pull=never','--network=none','--entrypoint','tar',image,
            '-C','/','-cf','-','lib','usr/lib','usr/libexec/git-core','usr/bin/git','usr/share/git-core','etc/ssl'],
            stdout=stream,check=True,timeout=120)
    def safe_filter(member,destination):
        if member.issym() and member.linkname.startswith('/'):
            member.linkname=posixpath.relpath(member.linkname.lstrip('/'),posixpath.dirname(member.name))
        return tarfile.data_filter(member,destination)
    with tarfile.open(archive) as stream:stream.extractall(root,filter=safe_filter)
    wrapper=DEST/'git'
    wrapper.write_text('''#!/bin/sh
export LD_LIBRARY_PATH=/opt/agenttx-portable-git/lib:/opt/agenttx-portable-git/usr/lib
export GIT_EXEC_PATH=/opt/agenttx-portable-git/usr/libexec/git-core
export GIT_TEMPLATE_DIR=/opt/agenttx-portable-git/usr/share/git-core/templates
export GIT_SSL_CAINFO=/opt/agenttx-portable-git/etc/ssl/cert.pem
exec /lib/ld-musl-x86_64.so.1 /opt/agenttx-portable-git/usr/bin/git "$@"
''');wrapper.chmod(0o755)
    manifest={str(p.relative_to(root)):dict(symlink=os.readlink(p)) if p.is_symlink()
              else dict(sha256=digest(p)) for p in root.rglob('*') if p.is_symlink() or p.is_file()}
    proof=dict(prepared_at=now(),image_id=image,reference='alpine/git:v2.47.2',
        files=manifest,wrapper_sha256=digest(wrapper),model_calls=0,
        purpose='Read-only agent prerequisite; task APT/DNS/curl/Python are unchanged.')
    save(DEST/'proof.json',proof)
    print(json.dumps({k:proof[k] for k in ['image_id','reference','model_calls']}),flush=True)


if __name__=='__main__':main()
