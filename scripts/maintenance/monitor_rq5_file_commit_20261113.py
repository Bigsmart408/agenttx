#!/usr/bin/env python3
"""Read-only host monitor for the isolated RQ5 VM; never retries samples."""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import time

RUN = Path('/home/bfq/agenttx-workspace/rq5-20261113')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, default=RUN / 'results')
    parser.add_argument('--output', type=Path, default=RUN / 'monitor.json')
    parser.add_argument('--guest-service', default='agenttx-rq5-file-commit')
    args = parser.parse_args()
    while True:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            progress = json.loads((args.results / 'progress.json').read_text())
        except (OSError, ValueError) as exc:
            progress = {'state': 'unavailable', 'error': str(exc)}
        try:
            cp = subprocess.run([
                'ssh', '-i', str(RUN / 'id_ed25519'), '-p', '19225',
                '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                '-o', 'UserKnownHostsFile=' + str(RUN / 'known_hosts'),
                'root@127.0.0.1', 'systemctl show ' + args.guest_service + ' '
                '-p ActiveState -p ExecMainStatus'],
                text=True, capture_output=True, timeout=15)
            service = cp.stdout.strip() if cp.returncode == 0 else cp.stderr[-500:]
        except (OSError, subprocess.TimeoutExpired) as exc:
            service = str(exc)
        value = {'checked_at_utc': now, 'progress': progress, 'guest_service': service,
                 'policy': 'observe only; preserve failures; no automatic sample retries'}
        tmp = args.output.with_suffix('.tmp')
        tmp.write_text(json.dumps(value, indent=2) + '\n')
        tmp.replace(args.output)
        print(json.dumps({'checked_at_utc': now, 'state': progress.get('state'),
                          'completed': progress.get('completed'), 'service': service}), flush=True)
        if progress.get('state') in ('complete', 'failed'):
            break
        time.sleep(30)


if __name__ == '__main__':
    main()
