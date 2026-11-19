#!/usr/bin/env python3
"""Drain the old RQ1 dispatcher and resume its unchanged matrix with 12 workers."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
COHORT = ROOT.parent / 'agenttx-experiment-data/results/rq1-strategy-only-6000-20261110-r1'
RUNNER = 'agenttx-rq1-strategy-only-20261110-r1.service'
MONITOR = 'agenttx-rq1-strategy-monitor-20261110-r1.service'
DOCKER = ['docker', '--host', 'unix:///run/agenttx-home-docker/docker.sock']
PYTHON = '/home/bfq/.venvs/agenttx/bin/python'


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def systemctl(*args):
    return subprocess.check_output(['systemctl', '--user', *args], text=True).strip()


def name(cell):
    key = str(COHORT) + cell['phase'] + cell['key']
    return 'atx-rq1-' + hashlib.sha256(key.encode()).hexdigest()[:18]


def main():
    assert systemctl('is-active', RUNNER) == 'active'
    progress = read(COHORT / 'progress.json')
    assert progress['workers'] == 8 and progress['stop_reason'] is None
    operation = COHORT / 'orchestration' / datetime.now(timezone.utc).strftime('workers-12-%Y%m%dT%H%M%SZ')
    operation.mkdir(parents=True)
    coordinator = operation / 'coordinator.py'
    shutil.copy2(ROOT / 'experiments/scripts/run_rq1_strategy_only_20261110.py', coordinator)
    for service in (RUNNER, MONITOR):
        (operation / (service + '.before')).write_text(systemctl('cat', service) + '\n')
    amendment = dict(started_at=now(), reason='User requested twelve concurrent workers.',
                     previous_workers=8, workers=12, frozen_worker_source_unchanged=True,
                     coordinator=str(coordinator), coordinator_sha256=sha(coordinator),
                     original_started_at=progress['started_at'], phase='draining')
    save(operation / 'amendment.json', amendment)
    systemctl('stop', MONITOR)
    frozen = killed = False
    try:
        systemctl('kill', '--kill-whom=main', '--signal=SIGSTOP', RUNNER)
        frozen = True
        pid = systemctl('show', RUNNER, '--property=ExecMainPID', '--value')
        stages = read(COHORT / 'cells.json')
        save(operation / 'cells.before.json', stages)
        save(operation / 'progress.before.json', read(COHORT / 'progress.json'))
        baseline = {c['key']: c['result_sha256'] for c in stages['main'] if c['state'] == 'passed'}
        names = {name(c) for rows in stages.values() for c in rows}
        deadline = time.monotonic() + 900
        quiet_checks = 0
        while quiet_checks < 2:
            containers = set(subprocess.check_output(DOCKER + ['ps', '--format', '{{.Names}}'], text=True).splitlines()) & names
            children = subprocess.run(['ps', '--ppid', pid, '-o', 'pid=,stat=,comm='],
                                      capture_output=True, text=True, check=False)
            live_children = [line for line in children.stdout.splitlines()
                             if len(line.split()) >= 2 and not line.split()[1].startswith('Z')]
            quiet_checks = quiet_checks + 1 if not containers and not live_children else 0
            print(json.dumps(dict(at=now(), phase='draining', containers=len(containers),
                                  live_clients=len(live_children))), flush=True)
            if time.monotonic() > deadline:
                raise TimeoutError('Old workers did not drain in fifteen minutes')
            if quiet_checks < 2:
                time.sleep(5)

        adopted, reset = [], []
        for phase, cells in stages.items():
            for cell in cells:
                result = COHORT / phase / cell['key'] / 'result.json'
                if cell['state'] == 'passed':
                    assert sha(result) == cell['result_sha256']
                    continue
                assert cell['state'] in ('pending', 'running'), cell
                if result.exists():
                    row = read(result)
                    assert row['trace_id'] == cell['trace_id'] and row['policy'] == cell['policy']
                    assert row['manifest_hash'] == cell['manifest_hash']
                    assert row['runtime_only'] and row['model_calls'] == row['api_requests'] == 0
                    assert row['policy_contract_passed'] and all(row['policy_checks'].values())
                    finished = datetime.fromtimestamp(result.stat().st_mtime, timezone.utc)
                    started = datetime.fromisoformat(cell['started_at']) if cell.get('started_at') else None
                    cell.update(state='passed', result=str(result), result_sha256=sha(result),
                                finished_at=finished.isoformat(), error=None, model_calls=0, api_requests=0,
                                recovery_complete=row['recovery_complete'],
                                recovered_by_worker_resize=True)
                    if started:
                        cell.update(wall_s=(finished - started).total_seconds(),
                                    coordinator_wall_s_source='result-write-time; runtime timings unchanged')
                    adopted.append(dict(key=cell['key'], result_sha256=cell['result_sha256']))
                elif cell['state'] == 'running':
                    # A frozen thread may have been queued before starting Docker.
                    assert not result.parent.exists(), f'Unfinished output requires review: {result.parent}'
                    cell.update(state='pending')
                    cell.pop('started_at', None)
                    reset.append(cell['key'])
        assert all(c['state'] in ('passed', 'pending') for rows in stages.values() for c in rows)
        assert all(c['result_sha256'] == baseline[c['key']] for c in stages['main'] if c['key'] in baseline)

        # The only terminated process is the drained dispatcher; its containers
        # and Docker clients have already exited normally.
        systemctl('kill', '--kill-whom=main', '--signal=SIGKILL', RUNNER)
        killed = True
        save(COHORT / 'cells.json', stages)
        amendment.update(drained_at=now(), preserved_completed=len(baseline),
                         adopted=adopted, unstarted_cells_reset=reset,
                         state_counts=dict(Counter(c['state'] for c in stages['main'])))
        save(operation / 'amendment.json', amendment)

        user_units = Path('/run/user') / str(COHORT.stat().st_uid) / 'systemd/user'
        user_units.mkdir(parents=True, exist_ok=True)
        commands = {
            RUNNER: f'{PYTHON} -u {coordinator} run --cohort {COHORT} --workers 12',
            MONITOR: f'{PYTHON} -u {coordinator} monitor --cohort {COHORT} --service {RUNNER}',
        }
        for service, command in commands.items():
            # Keep a base unit after systemd releases the old transient unit.
            base = user_units / service
            if not base.exists():
                base.write_text((operation / (service + '.before')).read_text())
            folder = user_units / (service + '.d')
            folder.mkdir(parents=True, exist_ok=True)
            description = 'RQ1 fixed trajectories, twelve workers, no model' if service == RUNNER else 'RQ1 progress and result verification every five minutes'
            (folder / '50-workers-12.conf').write_text(
                f'[Unit]\nDescription={description}\n\n[Service]\nExecStart=\nExecStart={command}\n')
        systemctl('daemon-reload')
        systemctl('reset-failed', RUNNER)
        systemctl('daemon-reload')
        systemctl('start', RUNNER)
        systemctl('start', MONITOR)
        amendment.update(phase='resumed', resumed_at=now())
        save(operation / 'amendment.json', amendment)
        print(json.dumps(amendment, indent=2), flush=True)
    except BaseException:
        if frozen and not killed:
            systemctl('kill', '--kill-whom=main', '--signal=SIGCONT', RUNNER)
            systemctl('start', MONITOR)
        raise


if __name__ == '__main__':
    main()
