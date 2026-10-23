#!/usr/bin/env python3
"""Move a stopped cohort's cells to the data archive after checksum comparison.

Keep its original absolute cell paths working through one directory symlink.
Run as the local maintenance container user, never inside a model task.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from datetime import datetime,timezone


DATA=Path('/mnt/data/agenttx-runtime')
ARCHIVE=Path('/mnt/recovery-sdc/agenttx-archive/20261021')


def save(path,value):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,indent=2));temp.replace(path)


def now():return datetime.now(timezone.utc).isoformat()


def run(command,log,audit,phase):
    with log.open('w') as stream:
        proc=subprocess.Popen(command,stdout=stream,stderr=subprocess.STDOUT)
        while proc.poll() is None:
            save(audit/'progress.json',dict(updated_at=now(),phase=phase,pid=proc.pid,
                disk_free_gib=round(shutil.disk_usage(DATA).free/2**30,1)))
            time.sleep(10)
    if proc.returncode:raise RuntimeError(f'{phase} failed with {proc.returncode}: {log}')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort',required=True)
    args=parser.parse_args()
    assert Path(args.cohort).name==args.cohort and args.cohort.startswith('tb-dual-native-full-')
    root=DATA/'results'/args.cohort;source=root/'cells'
    assert (root/'execution.json').is_file(), 'Only stopped cohorts may be archived'
    progress=json.loads((root/'progress.json').read_text())
    assert not any(c['state']=='running' for c in progress['tasks'])
    assert source.is_dir() and not source.is_symlink()
    assert os.path.ismount('/mnt/recovery-sdc')
    assert os.path.ismount(DATA) or os.path.ismount('/mnt/data')
    assert Path('/mnt/recovery-sdc').stat().st_dev!=source.stat().st_dev
    audit=DATA/'validation'/('tb-cold-archive-20261021-'+args.cohort)
    audit.mkdir(exist_ok=False)
    target=ARCHIVE/args.cohort/'cells';target.mkdir(parents=True,exist_ok=False)
    registered={}
    for c in progress['tasks']:
        path=Path(c.get('result_path',''))
        if path.is_file() and path.is_relative_to(source):
            registered[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
    save(audit/'registration.json',dict(started_at=now(),source=str(source),target=str(target),
        stopped_cohort=args.cohort,registered_results_sha256=registered,
        source_progress_sha256=hashlib.sha256((root/'progress.json').read_bytes()).hexdigest(),
        original_paths_preserved=True,copy_before_release=True))
    flags=['-aHAXS','--numeric-ids']
    run(['rsync',*flags,'--stats',str(source)+'/',str(target)+'/'],audit/'copy.log',audit,'copying')
    # Read every file on both disks, compare checksums and metadata without
    # modifying either tree. Unexpected/missing files must also block release.
    run(['rsync',*flags,'--checksum','--checksum-choice=sha1','--dry-run','--delete',
         '--itemize-changes',str(source)+'/',str(target)+'/'],audit/'compare.log',audit,'verifying')
    assert not (audit/'compare.log').read_text().strip(), 'Archive differs; keep the original tree'
    for path,expected in registered.items():
        dest=target/Path(path).relative_to(source)
        assert hashlib.sha256(dest.read_bytes()).hexdigest()==expected
    backup=root/'cells.ssd-before-archive-20261021'
    assert not backup.exists()
    # This is a stopped cohort. Preserve both copies until the new entrypoint
    # is installed, then verify every registered result through that entrypoint.
    source.rename(backup)
    try:source.symlink_to(target,target_is_directory=True)
    except BaseException:backup.rename(source);raise
    for path,expected in registered.items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==expected
    save(audit/'verified-before-release.json',dict(verified_at=now(),checksum_comparison='all file data and metadata equal',
        registered_results=len(registered),original_paths_sha256_unchanged=True))
    shutil.rmtree(backup)
    save(audit/'execution.json',dict(finished_at=now(),source=str(source),target=str(target),
        registered_results=len(registered),original_paths_sha256_unchanged=True,
        disk_free_gib=round(shutil.disk_usage(DATA).free/2**30,1)))
    save(audit/'progress.json',dict(updated_at=now(),phase='complete',
        disk_free_gib=round(shutil.disk_usage(DATA).free/2**30,1)))


if __name__=='__main__':main()
