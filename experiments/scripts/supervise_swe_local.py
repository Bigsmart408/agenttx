#!/usr/bin/env python3
"""Persistent local queue supervision and evidence-based continuation.

Never retry a model task outcome. A proven model-authored image argument
mistake retains its outcome and cost, and only pending cells are launched.
Unclassified infrastructure problems remain visible for diagnosis.
"""
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from experiments.scripts.run_swe_monitored_local import infrastructure_issue, verifier_command_audit

DATA = Path('/mnt/data/agenttx-runtime')
STATE = DATA / 'validation/swe-persistent-supervisor'
PYTHON = '/home/bfq/.venvs/agenttx/bin/python'


def read(path):
    return json.loads(path.read_text()) if path.exists() else {}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)
    os.chown(path, 1003, 1003)


def alive(pid):
    try:
        return (Path('/proc') / str(pid) / 'stat').read_text().split(') ', 1)[1][0] != 'Z'
    except (OSError, IndexError):
        return False


def worker_wall_timeout_audit(folder, cell, registration):
    """Preserve a truncated clean attempt; authorize pending work only, never a retry."""
    if not (cell.get('policy')=='clean_recovery'
            and cell.get('infrastructure_issue')=='worker_terminated_without_result'
            and cell.get('returncode')==124 and not (folder/'result.json').exists()
            and registration.get('cell_wall_timeout_s')==3600):return None
    try:
        elapsed=(datetime.fromisoformat(cell['finished_at'])-datetime.fromisoformat(cell['started_at'])).total_seconds()
        if not 3600<=elapsed<=3635:return None
        paths=list((folder/'trajectories').rglob('*.traj'))
        if len(paths)!=1:return None
        trajectory=read(paths[0]);stats=trajectory['info']['model_stats']
        if trajectory['info'].get('exit_status') is not None:return None
        raw=(folder/'worker.log').read_bytes()
        compact=re.sub(r'\s+','',raw.decode(errors='replace'))
        records=re.findall(r'usage=Usage\(completion_tokens=(\d+),prompt_tokens=(\d+),total_tokens=(\d+)',compact)
        if not records or len(records)!=stats['api_calls'] or len(trajectory['trajectory'])!=len(records):return None
        if not all(int(c)+int(p)==int(t) for c,p,t in records):return None
        return dict(classification='registered_worker_timeout_without_result',wall_s=elapsed,
            registered_limit_s=3600,completed_api_responses=len(records),
            logged_api_tokens=sum(int(t) for c,p,t in records),usage_complete=False,
            in_flight_request_usage_unknown=True,official_verdict_available=False,
            log_sha256=hashlib.sha256(raw).hexdigest(),trajectory=str(paths[0]),
            trajectory_sha256=hashlib.sha256(paths[0].read_bytes()).hexdigest(),
            raw_result_unchanged=True,task_retried=False)
    except (OSError,ValueError,KeyError,TypeError):return None


def audit_terminal_errors(root, progress, registration):
    records = []
    images = {r['task']: r['image_id'] for r in registration['images']}
    for cell in progress['tasks']:
        if cell['state'] != 'infrastructure_error':
            continue
        path = Path(cell.get('result_path') or root / 'cells' / cell['task'] / cell['policy'] / 'result.json')
        if cell.get('infrastructure_issue')=='worker_terminated_without_result':
            proof=worker_wall_timeout_audit(path.parent,cell,registration)
            if not proof:return None
            records.append(dict(task=cell['task'],policy=cell['policy'],proof=proof,
                                preserve_infrastructure_error=True))
            continue
        if cell.get('infrastructure_issue') not in {'agent_verifier_infrastructure_error', 'harness_integration_error'}:
            return None
        row = read(path)
        proof = verifier_command_audit(path.parent, images[cell['task']])
        log = (path.parent / 'worker.log').read_text(errors='replace')
        if not proof or infrastructure_issue(row, log, registration['context_by_policy'][cell['policy']], proof):
            return None
        records.append(dict(task=cell['task'], policy=cell['policy'],
                            result_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), proof=proof))
    return dict(cohort=root.name, cells=records, raw_results_unchanged=True,
                task_retried=False) if records else None


def start_pending(root, audit):
    assert not any(alive(r['pid']) for r in read(root / 'progress.json').get('active', []))
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    name = 'sweagent-swe-local-monitored-20261018-selective-auto-' + stamp
    proof = STATE / (stamp + '-command-audit.json')
    if audit:
        save(proof, audit)
    # Reuse the frozen execution code and all six registered context settings.
    template_path = ROOT / 'scripts/launch-local-swe-selective-auto-template-20261018.sh'
    if not template_path.exists():
        template_path = ROOT / 'scripts/launch-local-swe-selective-resume-r3-20261018.sh'
    template = template_path.read_text()
    prefix = template.split('exec /home/bfq/.venvs/agenttx/bin/python', 1)[0]
    command = [PYTHON, '-u', 'experiments/scripts/run_swe_monitored_local.py', '--full-suite',
               '--continue-from', root.name, '--resume-all-valid']
    if audit:
        verifier_records=[r for r in audit['cells'] if not r.get('preserve_infrastructure_error')]
        if verifier_records:
            verifier_proof=STATE/(stamp+'-verifier-command-audit.json')
            save(verifier_proof,dict(audit,cells=verifier_records))
            command += ['--verifier-command-audit', str(verifier_proof)]
    launcher = Path('/home/bfq/agenttx-executions/swe-local-auto-launchers') / (name + '.sh')
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(prefix + '\nexport AGENTTX_SWE_MONITORED_RUN=' + shlex.quote(name)
                        + '\nexec ' + shlex.join(command) + '\n')
    log = DATA / 'logs' / (name + '.log')
    with log.open('x') as stream:
        child = subprocess.Popen(['/bin/bash', str(launcher)], cwd=ROOT,
                                 stdin=subprocess.DEVNULL, stdout=stream,
                                 stderr=subprocess.STDOUT, start_new_session=True)
    return dict(previous_cohort=root.name, next_cohort=name, pid=child.pid,
                started_at=datetime.now(timezone.utc).isoformat(),
                audit=str(proof) if audit else None, task_outcomes_retried=False)


def maintain_reports(children):
    for key, script in [('comparison', 'watch_swe_full_comparison.py')]:
        process = children.get(key)
        if process is not None and process.poll() is None:
            continue
        # flock in the comparison script protects against any existing observer.
        with (DATA / 'logs/swe-persistent-comparison.log').open('a') as log:
            children[key] = subprocess.Popen([PYTHON, '-u', str(ROOT / 'experiments/scripts' / script)],
                                             cwd=ROOT, stdin=subprocess.DEVNULL,
                                             stdout=log, stderr=subprocess.STDOUT)
    subprocess.run(['runuser', '-u', 'bfq', '--', 'env', 'XDG_RUNTIME_DIR=/run/user/1003',
                    'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1003/bus',
                    'systemctl', '--user', 'start', 'agenttx-swe-halfhour.timer'],
                   capture_output=True, timeout=15)


def main():
    assert os.geteuid() == 0 and socket.gethostname() == 'r760-ssd'
    assert os.environ['DOCKER_HOST'] == 'unix:///run/agenttx-docker/docker.sock'
    STATE.mkdir(parents=True, exist_ok=True)
    lock = (STATE / 'supervisor.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    children = {}
    launched = {}
    last_usage_audit = 0
    while True:
        try:
            pointer = read(DATA / 'validation/active-swe-full-cohort.json')
            name = pointer['cohort']
            assert Path(name).name == name and name.startswith('sweagent-swe-local-monitored-')
            root = DATA / 'results' / name
            progress = read(root / 'progress.json')
            registration = read(root / 'registration.json')
            counts = progress.get('counts', {})
            finished = (root / 'execution.json').exists()
            alerts = []
            if not finished and not alive(progress.get('coordinator_pid')):
                alerts.append('coordinator_missing')
            missing = [r for r in progress.get('active', []) if not alive(r['pid'])]
            if missing:
                alerts.append('workers_exited_awaiting_coordinator_reconciliation')
            if progress.get('stop_reason'):
                alerts.append('queue_stopped:' + progress['stop_reason'])
            docker = subprocess.run(['docker', 'info', '--format', '{{.DockerRootDir}}'],
                                    capture_output=True, text=True, timeout=20)
            if docker.returncode:
                alerts.append('dedicated_docker_unavailable')
                # Start only the dedicated daemon if its service is stopped.
                # Never restart the shared daemon or interrupt active workers.
                service = subprocess.run(['systemctl', 'is-active', 'agenttx-docker.service'], capture_output=True)
                if service.returncode and not progress.get('active'):
                    subprocess.run(['systemctl', 'start', 'agenttx-containerd.service', 'agenttx-docker.service'],
                                   capture_output=True, timeout=45)
            elif docker.stdout.strip() != '/mnt/data/agenttx-runtime/docker':
                alerts.append('docker_storage_mismatch')
            free = shutil.disk_usage(DATA).free / 2**30
            if free < 250:
                alerts.append('data_disk_near_dispatch_guard')
            if finished and counts.get('pending', 0) and name not in launched:
                audit = audit_terminal_errors(root, progress, registration)
                normal_end = not counts.get('infrastructure_error') and not progress.get('stop_reason')
                if free > 250 and docker.returncode == 0 and (audit or normal_end):
                    launched[name] = start_pending(root, audit)
                    save(STATE / ('continuation-' + name + '.json'), launched[name])
                else:
                    alerts.append('infrastructure_diagnosis_required_before_continuation')
            maintain_reports(children)
            if time.monotonic() - last_usage_audit > 300:
                with (DATA / 'logs/swe-persistent-api-audit.log').open('a') as log:
                    subprocess.run([PYTHON, str(ROOT / 'experiments/scripts/audit_swe_token_usage.py')],
                                   cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=60)
                last_usage_audit = time.monotonic()
            state = dict(checked_at=datetime.now(timezone.utc).isoformat(), supervisor_pid=os.getpid(),
                         cohort=name, counts=counts, finished=finished,
                         coordinator_alive=alive(progress.get('coordinator_pid')),
                         active_workers=len(progress.get('active', [])), disk_free_gib=round(free, 1),
                         alerts=alerts, continuation=launched.get(name),
                         poll_seconds=30, api_usage_audit_seconds=300,
                         raw_results_preserved=True, no_outcome_based_retry=True)
            save(STATE / 'latest.json', state)
            with (STATE / 'history.jsonl').open('a') as stream:
                stream.write(json.dumps(state) + '\n')
        except Exception as exc:
            save(STATE / 'last-error.json', dict(checked_at=datetime.now(timezone.utc).isoformat(),
                                               error=type(exc).__name__ + ': ' + str(exc)))
        time.sleep(30)


if __name__ == '__main__':
    main()
