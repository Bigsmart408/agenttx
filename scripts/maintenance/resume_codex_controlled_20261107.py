"""Resume registered Codex cells after bounded, archived upstream interruptions."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
import tomllib
import urllib.request
import uuid


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    temporary.replace(path)


def transient(row):
    usage = row.get('api_usage', {})
    if (row.get('status') != 'model_or_verification_failed'
            or row.get('model_returncode') != 1 or usage.get('complete')
            or usage.get('budget_exceeded') or usage.get('budget_exhausted')):
        return False
    unknown = [r for r in usage.get('requests', []) if r.get('total_tokens') is None]
    return bool(unknown) and all(
        r.get('http_status') in (200, 408, 429, 500, 502, 503, 504)
        or r.get('error_type') in ('RemoteDisconnected', 'ConnectionResetError',
                                  'TimeoutError', 'IncompleteRead', 'ConnectionRefusedError')
        for r in unknown)


def archive_retry(cohort, max_retries=2):
    """Preserve attempts and reset only reviewed infrastructure failures."""
    cells = json.loads((cohort / 'cells.json').read_text())
    failures = [c for c in cells['main'] if c['state'] == 'infrastructure_error']
    if not failures:
        raise RuntimeError('No infrastructure failures to recover')
    results = {}
    for cell in failures:
        if len(cell.get('infrastructure_attempts', [])) >= max_retries:
            raise RuntimeError('Infrastructure retry limit reached for ' + cell['key'])
        result = json.loads(Path(cell['result']).read_text())
        if not transient(result):
            raise RuntimeError('Non-transient failure requires review: ' + cell['key'])
        results[cell['key']] = result
    review = cohort / 'recovery' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                                   + '-' + uuid.uuid4().hex[:6])
    review.mkdir(parents=True)
    for name in ('cells.json', 'progress.json', 'execution.json', 'registration.json'):
        if (cohort / name).exists():
            shutil.copy2(cohort / name, review / name)
    moves = []
    archived = []
    try:
        for cell in failures:
            old = deepcopy(cell)
            source = cohort / 'main' / cell['key']
            target = review / 'attempts' / cell['key']
            assert Path(cell['result']).is_relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
            moves.append((source, target))
            log = Path(cell['log'])
            target_log = review / 'logs' / log.name
            target_log.parent.mkdir(exist_ok=True)
            log.rename(target_log)
            moves.append((log, target_log))
            old['original_result_path'] = old['result']
            old['result'] = str(target / Path(old['result']).relative_to(source))
            old['original_log_path'] = old['log']
            old['log'] = str(target_log)
            old.pop('infrastructure_attempts', None)
            old['result_sha256'] = hashlib.sha256(Path(old['result']).read_bytes()).hexdigest()
            usage = results[cell['key']]['api_usage']
            old['known_api_tokens'] = usage.get('known_total_tokens', 0)
            old['unknown_usage_requests'] = sum(r.get('total_tokens') is None
                                              for r in usage['requests'])
            previous = cell.get('infrastructure_attempts', [])
            identity = {k: cell[k] for k in ('key', 'trace_id', 'manifest_hash', 'family',
                        'operation_count', 'policy', 'repeat', 'harness', 'phase')}
            cell.clear()
            cell.update(identity, state='pending', infrastructure_attempts=previous + [old])
            archived.append(old)
        save(review / 'review.json', dict(at=now(), state='prepared', attempts=archived,
             ordinary_task_failures_retried=0, experiment_conditions_changed=False))
        save(cohort / 'cells.json', cells)
    except Exception:
        for source, target in reversed(moves):
            target.rename(source)
        raise
    save(review / 'commit.json', dict(at=now(), attempts=archived,
         known_api_tokens=sum(a['known_api_tokens'] for a in archived),
         unknown_usage_requests=sum(a['unknown_usage_requests'] for a in archived)))
    return dict(review=str(review), reset_cells=len(archived),
                retained_completed=sum(c['state'] in ('passed', 'task_failed') for c in cells['main']))


def verify_environment(cohort):
    registration = json.loads((cohort / 'registration.json').read_text())
    source = Path(registration['frozen_source'])
    manifest = json.loads((cohort / 'source-manifest.json').read_text())
    for entry in manifest['files']:
        if hashlib.sha256((source / entry['path']).read_bytes()).hexdigest() != entry['sha256']:
            raise RuntimeError('Frozen source changed')
    profile = Path('/home/bfq/.config/agenttx/experiment-20261017')
    for name, expected in registration['profile_sha256'].items():
        if hashlib.sha256((profile / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Registered profile changed')
    binary = Path('/home/bfq/.local/lib/node_modules/@openai/codex/node_modules/'
                  '@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex')
    if hashlib.sha256(binary.read_bytes()).hexdigest() != registration['codex_binary_sha256']:
        raise RuntimeError('Registered Codex binary changed')
    config = tomllib.loads((profile / 'config.toml').read_text())
    endpoint = config['model_providers'][config['model_provider']]['base_url']
    key = json.loads((profile / 'auth.json').read_text())['OPENAI_API_KEY']
    request = urllib.request.Request(endpoint.rstrip('/') + '/models',
                                     headers={'Authorization': 'Bearer ' + key})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError('Upstream unavailable')


def start_campaign(cohort, service):
    # Transient units are unloaded after exiting; recreate the exact launch.
    loaded = subprocess.check_output(['systemctl', '--user', 'show', service,
             '--property=LoadState', '--value'], text=True).strip()
    if loaded == 'loaded':
        subprocess.run(['systemctl', '--user', 'start', service], check=True, timeout=30)
        return
    registration = json.loads((cohort / 'registration.json').read_text())
    source = registration['frozen_source']
    log = str(cohort / 'coordinator.log')
    subprocess.run(['systemd-run', '--user', '--unit=' + service,
        '--description=Codex canonical-400 five-policy controlled experiment (6000 runs)',
        '--property=WorkingDirectory=' + source,
        '--property=Environment=AGENTTX_DOCKER_HOST=unix:///run/agenttx-home-docker/docker.sock',
        '--property=StandardOutput=append:' + log,
        '--property=StandardError=append:' + log,
        registration['python'], '-u', source + '/experiments/scripts/run_system_microbenchmark.py',
        'run', '--cohort', str(cohort)], check=True, timeout=30)


def tick(args):
    progress = json.loads((args.cohort / 'progress.json').read_text())
    state = subprocess.check_output(['systemctl', '--user', 'show', args.service,
           '--property=ActiveState', '--value'], text=True).strip()
    if progress['phase'] == 'finished':
        return dict(at=now(), state='finished')
    if state in ('active', 'activating', 'deactivating'):
        return dict(at=now(), state='running', coordinator_state=state)
    if progress.get('stop_reason') != 'infrastructure_error_review_required':
        return dict(at=now(), state='review_required', reason=progress.get('stop_reason'))
    elapsed = time.time() - datetime.fromisoformat(progress['updated_at']).timestamp()
    if elapsed < args.cooldown:
        return dict(at=now(), state='cooldown', seconds_remaining=round(args.cooldown - elapsed))
    verify_environment(args.cohort)
    with (args.cohort / '.infrastructure-recovery.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        cells = json.loads((args.cohort / 'cells.json').read_text())['main']
        if any(c['state'] == 'infrastructure_error' for c in cells):
            result = archive_retry(args.cohort, args.max_retries)
        elif any(c['state'] == 'pending' for c in cells) and any((args.cohort / 'recovery').glob('*/commit.json')):
            result = dict(reset_cells=0, reason='Retry service launch after committed archival')
        else:
            raise RuntimeError('No reviewed infrastructure recovery to resume')
        start_campaign(args.cohort, args.service)
    return dict(at=now(), state='resumed', **result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', type=Path, required=True)
    parser.add_argument('--service', required=True)
    parser.add_argument('--max-retries', type=int, default=2)
    parser.add_argument('--cooldown', type=int, default=300)
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    policy = args.cohort / 'infrastructure-retry-policy.json'
    if not policy.exists():
        save(policy, dict(recorded_at=now(), ordinary_task_retries=0,
             max_infrastructure_retries=args.max_retries, cooldown_s=args.cooldown,
             preserve_every_attempt=True, missing_usage_remains_unknown=True,
             recovery_runtime_or_model_conditions_changed=False,
             reason='Recover upstream connection disruptions; do not retry genuine task or audit failures.'))
    while True:
        try:
            row = tick(args)
        except Exception as exc:
            # Do not print private endpoint/authentication exception strings.
            row = dict(at=now(), state='review_required', error_type=type(exc).__name__)
            if type(exc) is RuntimeError:
                row['reason'] = str(exc)
        save(args.cohort / 'recovery-supervisor.json', row)
        print(json.dumps(row), flush=True)
        if not args.watch or row['state'] == 'finished':
            break
        time.sleep(60)


if __name__ == '__main__':
    main()
