#!/usr/bin/env python3
"""Relocate completed, unmounted RQ2 sessions with checked rsync and receipts."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_tree(source):
    links, expected = Counter(), {}
    for root, dirs, files in os.walk(source, followlinks=False):
        for name in dirs + files:
            path = Path(root) / name
            value = path.lstat()
            if stat.S_ISREG(value.st_mode):
                key = (value.st_dev, value.st_ino)
                links[key] += 1
                expected[key] = value.st_nlink
            if stat.S_ISSOCK(value.st_mode):
                raise ValueError('socket in source tree')
    if any(links[k] != n for k, n in expected.items()):
        raise ValueError('source has hard links outside the session')
    return len(links)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--cell', required=True)
    args = parser.parse_args()
    cohort, archive = args.cohort.resolve(), args.archive.resolve()
    archive.mkdir(parents=True, exist_ok=True)
    assert os.geteuid() == 0
    assert cohort.stat().st_dev != archive.stat().st_dev
    cells = json.loads((cohort / 'cells.json').read_text())
    cell = next(c for c in cells if c['key'] == args.cell)
    assert cell['state'] in ('completed', 'infrastructure_error') and cell['benchmark'] == 'tb'
    assert len(cell['attempts']) == 1, 'select a settled single-attempt cell'
    result = Path(cell['result_path'])
    result_hash = sha(result)
    assert result_hash == cell['attempts'][0]['result_sha256']
    source = result.parent / 'session'
    assert source.is_dir() and not source.is_symlink()
    destination = archive / source.relative_to(cohort)
    assert not destination.exists()
    # Check mounted paths in the host namespace as well as the helper's own.
    for mountinfo in (Path('/proc/1/mountinfo'), Path('/proc/self/mountinfo')):
        for line in mountinfo.read_text().splitlines():
            mountpoint = Path(line.split()[4].replace('\\040', ' '))
            assert not mountpoint.is_relative_to(source), 'session contains a mount'
    inodes = inspect_tree(source)
    size = int(subprocess.check_output(['du', '-sx', '--block-size=1', str(source)], text=True).split()[0])
    assert shutil.disk_usage(archive).free > size + 100 * 2**30
    destination.mkdir(parents=True)
    command = ['rsync', '-aHAX', '--numeric-ids', '--one-file-system']
    subprocess.run([*command, str(source) + '/', str(destination) + '/'], check=True)
    verification = subprocess.run([*command, '--checksum', '--dry-run', '--itemize-changes', '--delete',
                                    str(source) + '/', str(destination) + '/'],
                                   check=True, capture_output=True, text=True)
    assert not verification.stdout and not verification.stderr, 'archive differs; original retained'
    assert inspect_tree(destination) == inodes
    assert sha(result) == result_hash
    current = next(c for c in json.loads((cohort / 'cells.json').read_text()) if c['key'] == args.cell)
    assert current['state'] == cell['state'] and current['attempts'] == cell['attempts']
    proof = dict(cell=args.cell, source=str(source), destination=str(destination), bytes=size,
                 regular_inodes=inodes, result_sha256=result_hash,
                 verified_at=datetime.now(timezone.utc).isoformat(),
                 verification='rsync full-file checksums, metadata, xattrs, hardlinks and special nodes')
    receipt = archive / 'receipts.jsonl'
    with receipt.open('a') as stream:
        stream.write(json.dumps(dict(proof, state='copy_verified')) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary = source.with_name('session.archive-link')
    backup = source.with_name('session.archive-source')
    assert not temporary.exists() and not temporary.is_symlink() and not backup.exists()
    temporary.symlink_to(destination, target_is_directory=True)
    source.rename(backup)
    try:
        temporary.replace(source)
    except BaseException:
        backup.rename(source)
        raise
    assert source.resolve() == destination and sha(result) == result_hash
    shutil.rmtree(backup)
    with receipt.open('a') as stream:
        stream.write(json.dumps(dict(proof, state='original_path_linked')) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps(dict(proof, moved_gib=round(size / 2**30, 3))))


if __name__ == '__main__':
    main()
