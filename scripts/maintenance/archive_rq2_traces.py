#!/usr/bin/env python3
"""Move finished RQ2 syscall traces to another disk, verifying bytes first.

Leave an absolute symlink at every original path. Results, runtime layers and
active/retry-pending cells are untouched. Every move has an append-only receipt.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 2**20), b''):
            digest.update(block)
    return digest.hexdigest()


def move_verified(source, destination, receipt):
    original = source.lstat()
    if not stat.S_ISREG(original.st_mode) or original.st_nlink != 1:
        raise ValueError('only ordinary files with a single hard link can be archived')
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    expected = sha(source)
    with source.open('rb') as reader, destination.open('xb') as writer:
        shutil.copyfileobj(reader, writer, 8 * 2**20)
        writer.flush()
        os.fsync(writer.fileno())
    shutil.copystat(source, destination)
    if sha(destination) != expected:
        raise ValueError('archive copy differs; original retained')
    current = source.lstat()
    if (original.st_dev, original.st_ino, original.st_size, original.st_mtime_ns) != (
            current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns):
        raise ValueError('source changed during copy; original retained')
    proof = dict(source=str(source), destination=str(destination), bytes=original.st_size,
                 sha256=expected, recorded_at=datetime.now(timezone.utc).isoformat())
    with receipt.open('a') as stream:
        stream.write(json.dumps(dict(proof, state='copy_verified')) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    link = source.with_name(source.name + '.archive-link')
    link.symlink_to(destination)
    link.replace(source)
    with receipt.open('a') as stream:
        stream.write(json.dumps(dict(proof, state='original_path_linked')) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    return original.st_size


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    args = parser.parse_args()
    cohort, archive = args.cohort.resolve(), args.archive.resolve()
    archive.mkdir(parents=True, exist_ok=True)
    assert cohort.stat().st_dev != archive.stat().st_dev, 'archive must be on another filesystem'
    candidates = []
    cells = json.loads((cohort / 'cells.json').read_text())
    for cell in cells:
        if cell['state'] not in ('completed', 'infrastructure_error') or cell['benchmark'] != 'tb':
            continue
        for attempt in cell['attempts']:
            folder = Path(attempt['folder']) / 'native/turn_checkpoint/session'
            for source in folder.glob('worker-strace-*.log'):
                entry = source.lstat()
                if stat.S_ISREG(entry.st_mode) and entry.st_nlink == 1 and entry.st_size > 100 * 2**20:
                    assert source.resolve().is_relative_to(cohort)
                    candidates.append(source)
    total = sum(p.stat().st_size for p in candidates)
    assert shutil.disk_usage(archive).free > total + 100 * 2**30, 'insufficient archive headroom'
    receipt = archive / 'receipts.jsonl'
    moved = 0
    for source in sorted(candidates):
        moved += move_verified(source, archive / source.relative_to(cohort), receipt)
    print(json.dumps(dict(files=len(candidates), moved_gib=round(moved / 2**30, 3),
                          free_gib=round(shutil.disk_usage(cohort).free / 2**30, 3), receipt=str(receipt))))


if __name__ == '__main__':
    main()
