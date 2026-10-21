#!/usr/bin/env python3
"""Restore the vanished fix-git clone using its original Git objects."""
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import subprocess
import urllib.request

from experiments.scripts.validate_tb_ready_local import DATA,digest,save
from experiments.workloads import terminal_bench_suite as tb

TIP='b0d59cded7105fea91a4ea0008098295d8895786'
PIN='d7d3e4ba9350f634d92d39ace2b471433ee57d50'
UPSTREAM='https://github.com/TheMikeMerrill/personal-site.git'
MIRROR='https://gh-proxy.com/https://github.com/wangsikuan/personal-site.git'

def recover_prebuilt(output, source):
    # The deleted upstream's objects survive in an unmodified task image.
    # Inspect only source/setup assets; never execute or reuse its workspace.
    from experiments.scripts.validate_tb_sidecars_local import pull
    image=pull('alexgshaw/fix-git:20251031',output)
    container=subprocess.check_output(['docker','create','--pull=never',image],text=True,timeout=30).strip()
    try:
        for remote,local in [('/app/personal-site/.git','mirror.git'),
                             ('/app/setup.sh','upstream-setup.sh'),('/app/resources','resources')]:
            subprocess.run(['docker','cp',container+':'+remote,str(output/local)],check=True,timeout=120)
    finally:
        subprocess.run(['docker','rm',container],check=True,capture_output=True,timeout=30)
    assert digest(output/'upstream-setup.sh')==digest(source/'setup.sh'),'setup mismatch'
    def files(root):
        return {str(p.relative_to(root)):digest(p) for p in root.rglob('*') if p.is_file()}
    assert files(output/'resources')==files(source/'resources'),'resource mismatch'
    save(output/'source-commit-identities.json',dict(prebuilt_image_id=image,
        prebuilt_reference='alexgshaw/fix-git:20251031',setup_identical=True,
        resources=files(source/'resources'),workspace_executed=False))
    return output/'mirror.git'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--prebuilt',action='store_true')
    args=parser.parse_args();OUT=args.output
    assert OUT.resolve().is_relative_to(DATA/'validation')
    assert os.environ['DOCKER_HOST']=='unix:///run/agenttx-docker/docker.sock'
    cache=tb._asset_cache_dir();assert cache.resolve().is_relative_to(DATA)
    OUT.mkdir(exist_ok=False)
    row=dict(task='fix-git',started_at=datetime.now(timezone.utc).isoformat(),model_calls=0,
             gold_solution_used=False,original_results_preserved=True,clone_tip=TIP,pinned_commit=PIN)
    save(OUT/'registration.json',row)
    try:
        source=tb.task_source(DATA/'cache',tb.load_tasks(DATA/'cache')['fix-git'])
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        metadata={}
        for sha in ([] if args.prebuilt else [TIP,PIN]):
            url='https://api.github.com/repos/wangsikuan/personal-site/git/commits/'+sha
            request=urllib.request.Request(url,headers={'User-Agent':'agenttx-environment-repair'})
            with opener.open(request,timeout=20) as response:obj=json.load(response)
            assert obj['sha']==sha
            metadata[sha]=obj
        if args.prebuilt:
            repo=recover_prebuilt(OUT,source)
        else:
            save(OUT/'source-commit-identities.json',metadata)
            repo=OUT/'mirror.git'
            with (OUT/'clone.log').open('w') as stream:
                subprocess.run(['git','clone','--mirror',MIRROR,str(repo)],stdout=stream,
                    stderr=subprocess.STDOUT,check=True,timeout=600)
        git=['git','--git-dir='+str(repo)]
        with (OUT/'fsck.log').open('w') as stream:
            subprocess.run([*git,'fsck','--strict'],stdout=stream,stderr=subprocess.STDOUT,check=True,timeout=90)
        subprocess.run([*git,'merge-base','--is-ancestor',PIN,TIP],check=True,timeout=20)
        subprocess.run([*git,'update-ref','refs/heads/agenttx-original-master',TIP],check=True,timeout=20)
        # Export only the original master history, not later fork commits.
        export=OUT/'export.git'
        subprocess.run(['git','init','--bare',str(export)],check=True,capture_output=True,timeout=20)
        subprocess.run(['git','--git-dir='+str(export),'fetch',str(repo),
            'refs/heads/agenttx-original-master:refs/heads/master'],check=True,capture_output=True,timeout=60)
        subprocess.run(['git','--git-dir='+str(export),'bundle','create',str(OUT/'personal-site.bundle'),
            'refs/heads/master'],check=True,timeout=90)
        heads=subprocess.check_output(['git','bundle','list-heads',str(OUT/'personal-site.bundle')],text=True,timeout=20)
        assert heads.strip()==TIP+' refs/heads/master'
        original=(source/'setup.sh').read_text();anchor='git clone '+UPSTREAM
        assert original.count(anchor)==1
        replacement=('git clone /tmp/personal-site.bundle personal-site && '
            "sed -i 's#clone: from /tmp/personal-site.bundle#clone: from "+UPSTREAM+
            "#' personal-site/.git/logs/HEAD personal-site/.git/logs/refs/heads/master && rm /tmp/personal-site.bundle")
        effective=original.replace(anchor,replacement,1)
        (OUT/'fix-git-setup.sh').write_text(effective)
        proof=dict(original_setup_sha256=digest(source/'setup.sh'),
            effective_setup_sha256=digest(OUT/'fix-git-setup.sh'),bundle_sha256=digest(OUT/'personal-site.bundle'),
            clone_tip=TIP,pinned_commit=PIN,git_fsck_strict_passed=True,ancestry_verified=True,
            source_metadata=str(OUT/'source-commit-identities.json'),
            changes='Only source transport; preserve reset, detached commit, branch and original clone reflog URL.')
        for name in ['personal-site.bundle','fix-git-setup.sh']:
            target=cache/name
            assert not target.exists(),'do not overwrite an existing asset'
            target.write_bytes((OUT/name).read_bytes())
        save(cache/'fix-git-source-proof.json',proof);save(OUT/'proof.json',proof)
        row.update(state='verified_source_ready',proof=str(OUT/'proof.json'))
    except Exception as exc:
        row.update(state='environment_error',error=repr(exc))
    row['finished_at']=datetime.now(timezone.utc).isoformat();save(OUT/'execution.json',row)
    print(json.dumps(row),flush=True)
    return 0 if row['state']=='verified_source_ready' else 1


if __name__=='__main__':raise SystemExit(main())
