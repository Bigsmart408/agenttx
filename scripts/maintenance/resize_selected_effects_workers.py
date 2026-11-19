#!/usr/bin/env python3
"""Drain live Selected effects attempts and resume pending cells at a new concurrency."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    temporary = path.with_name(path.name + '.resize.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def systemctl(*arguments):
    return subprocess.check_output(['systemctl', '--user', *arguments], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', type=Path, required=True)
    parser.add_argument('--workers', type=int, required=True)
    args = parser.parse_args()
    if not 1 <= args.workers <= 32:
        raise ValueError('Worker count is outside the supported range')
    cohort = args.cohort.resolve()
    operation = cohort / 'maintenance' / (
        f'workers-{args.workers}-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    operation.mkdir(parents=True)
    services = read(cohort / 'services.json')
    registration = read(cohort / 'registration.json')
    assert systemctl('is-active', services['coordinator']) == 'active'
    assert systemctl('is-active', services['monitor']) == 'active'
    for name in ('registration.json', 'progress.json', 'cells.json', 'services.json'):
        shutil.copy2(cohort / name, operation / ('before-' + name))
    before_cells = read(operation / 'before-cells.json')
    evidence = {a['result_path']: a['result_sha256'] for c in before_cells
                for a in c['attempts'] if a.get('result_sha256')}
    original = dict(registration)
    registration.update(workers=args.workers, max_tb_workers=args.workers)
    assert {k for k in original if original[k] != registration[k]} <= {'workers', 'max_tb_workers'}
    save(cohort / 'registration.json', registration)
    shutil.copy2(cohort / 'registration.json', operation / 'after-registration.json')
    amendment = dict(started_at=now(), reason='User requested eight concurrent Selected effects workers.',
        previous_workers=original['workers'], previous_tb_workers=original['max_tb_workers'],
        workers=args.workers, max_tb_workers=args.workers,
        source_sha256=registration['source_sha256'], worker_source_changed=False,
        model_and_scoring_changed=False, completed_attempts_retried=0,
        phase='draining', operation=str(operation), coordinator_before=services['coordinator'])
    save(operation / 'amendment.json', amendment)
    monitor_paused = False
    try:
        # Pause only the supervisor while the separate drain watcher owns monitoring.
        systemctl('kill', '--kill-whom=main', '--signal=SIGSTOP', services['monitor'])
        monitor_paused = True
        subprocess.run(['docker', '--host', 'unix:///run/agenttx-docker/docker.sock',
            'exec', 'atx-selected-effects-run', '/usr/bin/kill', '-TERM', '1'],
            check=True, timeout=30)
        deadline = time.monotonic() + float(registration['tb_cell_timeout_s']) + 600
        while True:
            progress = read(cohort / 'progress.json')
            state = systemctl('show', services['coordinator'], '--property=ActiveState', '--value')
            snapshot = dict(checked_at=now(), state='draining_for_worker_resize',
                            target_workers=args.workers, progress=progress)
            save(operation / 'latest.json', snapshot)
            save(cohort / 'monitor/latest.json', snapshot)
            save(cohort / 'supervisor/latest.json', snapshot)
            if not progress['active'] and state not in ('active', 'activating', 'deactivating'):
                break
            if time.monotonic() > deadline:
                raise TimeoutError('Live attempts exceeded their registered deadline while draining')
            time.sleep(15)
        assert not any(c['state'] == 'running' for c in read(cohort / 'cells.json'))
        for name, expected in evidence.items():
            assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == expected, name
        assert read(cohort / 'registration.json') == registration
        shutil.copy2(cohort / 'cells.json', operation / 'after-drain-cells.json')
        shutil.copy2(cohort / 'progress.json', operation / 'after-drain-progress.json')
        if (cohort / 'execution.json').exists():
            (cohort / 'execution.json').rename(operation / 'previous-execution.json')
        service = 'agenttx-selected-effects-workers-' + str(args.workers) + '-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        subprocess.run(['systemd-run', '--user', '--unit=' + service,
            '--description=Selected effects pending tasks with eight workers',
            '--property=WorkingDirectory=' + registration['frozen_source'],
            '--property=StandardOutput=append:' + str(cohort / 'coordinator.log'),
            '--property=StandardError=append:' + str(cohort / 'coordinator.log'),
            '/home/bfq/.venvs/agenttx/bin/python', '-u', str(cohort / 'launch.py'), 'run'],
            check=True, timeout=30, capture_output=True)
        services['coordinator'] = service + '.service'
        save(cohort / 'services.json', services)
        amendment.update(phase='resumed', resumed_at=now(), coordinator=services['coordinator'],
                         prior_attempt_hashes_verified=len(evidence))
        save(operation / 'amendment.json', amendment)
        systemctl('kill', '--kill-whom=main', '--signal=SIGCONT', services['monitor'])
        monitor_paused = False
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            progress = read(cohort / 'progress.json')
            if progress['workers'] == args.workers and (len(progress['active']) == args.workers
                    or progress.get('stop_reason') or not progress['counts'].get('pending')):
                amendment.update(phase='verified', verified_at=now(), progress=progress)
                save(operation / 'amendment.json', amendment)
                print(json.dumps(amendment), flush=True)
                return
            time.sleep(5)
        raise TimeoutError('New coordinator did not expose the requested concurrency in time')
    except BaseException as error:
        amendment.update(phase='requires_review', error_type=type(error).__name__, error=str(error), checked_at=now())
        save(operation / 'amendment.json', amendment)
        raise
    finally:
        if monitor_paused:
            systemctl('kill', '--kill-whom=main', '--signal=SIGCONT', services['monitor'])


if __name__ == '__main__':
    main()
