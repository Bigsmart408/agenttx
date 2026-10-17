#!/usr/bin/env python3
"""Move recovered local experiment data to /mnt/data, with a verified copy first."""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DATA = Path('/mnt/data/agenttx-runtime')


def main():
    assert socket.gethostname() == 'r760-ssd'
    assert Path('/mnt/data').is_mount()
    assert DATA.stat().st_dev != Path('/').stat().st_dev
    lock = (DATA / 'validation/data-migration.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    report = DATA / 'validation/data-migration.json'
    rows = []
    for name in ('cache', 'logs', 'results'):
        source = ROOT / 'experiments' / name
        dest = DATA / name
        if source.is_symlink():
            assert source.resolve() == dest.resolve()
            rows.append(dict(kind=name, state='already_on_data_disk'))
            continue
        assert source.is_dir() and dest.is_dir()
        if any(dest.iterdir()):
            # Existing destinations are allowed only when all same-named bytes agree.
            # rsync's dry-run catches conflicts before overwriting recovered evidence.
            conflicts = subprocess.run(['rsync', '-rcn', '--existing', '--out-format=%n',
                                        str(source)+'/', str(dest)+'/'], capture_output=True, text=True, check=True)
            if conflicts.stdout.strip():
                raise RuntimeError('Conflicting destination data: ' + conflicts.stdout[:1000])
        subprocess.run(['rsync', '-aH', '--no-owner', '--no-group', str(source)+'/', str(dest)+'/'], check=True)
        checked = subprocess.run(['rsync', '-rcn', '--out-format=%n', str(source)+'/', str(dest)+'/'],
                                 capture_output=True, text=True, check=True)
        if checked.stdout.strip():
            raise RuntimeError('Copy verification failed: ' + checked.stdout[:1000])
        retired = source.with_name(name + '.verified-data-copy')
        assert not retired.exists()
        # Stop experiment writers before using this script. No model launch is included.
        source.rename(retired)
        source.symlink_to(dest, target_is_directory=True)
        # This is the verified source of the move; unrelated recovery originals are retained.
        shutil.rmtree(retired)
        rows.append(dict(kind=name, state='moved_and_content_verified', source=str(source), destination=str(dest)))
        report.write_text(json.dumps(dict(updated_at=datetime.now(timezone.utc).isoformat(), host=socket.gethostname(), rows=rows), indent=2)+'\n')
        print(json.dumps(rows[-1]), flush=True)
    print('Local cache, logs and results now resolve to the data disk.', flush=True)


if __name__ == '__main__':
    main()
