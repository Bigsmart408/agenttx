#!/usr/bin/env python3
"""Monitor full Selected effects and resume pending cells after verified provider recovery; keep terminal attempts."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src'), str(Path(__file__).parent)]
from experiments.scripts.experiment_provenance import source_manifest
from tb_provider_health_20261026 import probe_inference, probe_accounting


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def terminal_provider_error(events):
    if not events or not isinstance(events[-1], dict):
        return False
    event = events[-1]
    return (event.get('type') == 'turn.failed'
            and re.search(r'unexpected status (502|503|504)\b',
                          str((event.get('error') or {}).get('message', ''))) is not None)


def classify(cell):
    path = Path(cell['result_path'])
    if not path.exists():
        return None
    data = read(path)
    if data.get('recovery_context_variant') != 'full':
        return None
    if cell['benchmark'] == 'swe':
        if not data.get('environment_error') or data.get('host_leak_before_commit'):
            return None
        events = []
        for line in data.get('harness_stdout', '').splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                events.append(event)
        if data.get('harness_returncode') == 1 and terminal_provider_error(events):
            return 'explicit_terminal_provider_http_5xx'
        if data.get('harness_returncode') == 124 and 'external harness timeout after 900.0s' in data.get('harness_stderr', ''):
            return 'registered_model_session_timeout'
        return None
    if (not data.get('completed') or data.get('container_returncode') != 0
            or not data.get('conversation_matches_workspace')
            or data.get('api_usage', {}).get('complete')):
        return None
    model = read(path.parent / 'model-result.json')
    if (data.get('model_returncode') == model.get('returncode') == 124
            and data.get('model_timed_out') and data.get('model_session_wall_s') == 3600):
        if 'external harness timeout after 3600.0s' in model.get('stderr', ''):
            return 'registered_model_session_timeout'
        # The native SWE-agent adapter catches its own deadline and emits a
        # structured exit status rather than the outer harness timeout text.
        metadata = model.get('metadata') or {}
        if (cell['harness'] == 'sweagent' and model.get('harness') == 'sweagent_native'
                and metadata.get('harness_backend') == 'sweagent_native'
                and metadata.get('native_adapter') == 'SWEAgentNativeAdapter'
                and metadata.get('sweagent_exit_status') == '_SWEAgentWallTimeout'
                and 3600 <= float(model.get('duration_s') or 0) <= 3660
                and '_SWEAgentWallTimeout: SWEAgent workload exceeded its wall-clock timeout'
                    in model.get('stderr', '')):
            return 'registered_native_model_session_timeout'
    if data.get('model_returncode') != 1 or model.get('returncode') != 1:
        return None
    if cell['harness'] == 'codex' and terminal_provider_error(model.get('events', [])):
        return 'explicit_terminal_provider_http_5xx'
    if cell['harness'] == 'codex':
        events = model.get('events', [])
        if events and isinstance(events[-1], dict):
            event = events[-1]
            message = str((event.get('error') or {}).get('message', ''))
            if (event.get('type') == 'turn.failed'
                    and re.search(r'\b429 Too Many Requests\b', message)):
                return 'explicit_terminal_provider_rate_limit_429'
    if cell['harness'] == 'sweagent':
        log = path.parent / 'container.stdout.log'
        text = log.read_text(errors='replace') if log.exists() else ''
        if ('Exit due to unknown error: litellm.BadGatewayError' in text
                and 'OpenAIException - Upstream service temporarily unavailable' in text):
            return 'explicit_native_provider_bad_gateway'
        if ('Exit due to unknown error: litellm.RateLimitError' in text
                and 'Error code: 429' in text
                and 'Too many pending requests' in text):
            return 'explicit_native_provider_rate_limit_429'
    return None


def verify_sources(cohort):
    registration = read(cohort / 'registration.json')
    assert source_manifest(Path(registration['frozen_source']))['sha256'] == registration['source_sha256']
    profile = Path('/home/bfq/.config/agenttx/experiment-20261017')
    for name, expected in registration['profile_sha256'].items():
        assert sha(profile / name) == expected, 'profile changed: ' + name


def fresh_preflight(cohort, audit):
    clone = audit / 'environment-preflight'
    clone.mkdir()
    shutil.copy2(cohort / 'registration.json', clone / 'registration.json')
    command = read(cohort / 'preflight-container-command.json')
    command[command.index('--name') + 1] = 'atx-selected-effects-supervisor-preflight'
    command[command.index('--cohort') + 1] = str(clone)
    save(audit / 'preflight-command.json', command)
    with (audit / 'preflight.log').open('w') as stream:
        subprocess.run(command, check=True, stdout=stream, stderr=subprocess.STDOUT, timeout=600)
    assert read(clone / 'preflight/proof.json')['passed']


def resume(cohort, audit):
    assert not read(cohort / 'progress.json')['active']
    assert not any(c['state'] == 'running' for c in read(cohort / 'cells.json'))
    verify_sources(cohort)
    for name in ('progress.json', 'cells.json', 'execution.json'):
        if (cohort / name).exists():
            shutil.copy2(cohort / name, audit / ('before-' + name))
    if (cohort / 'execution.json').exists():
        (cohort / 'execution.json').rename(audit / 'previous-execution.json')
    service = 'agenttx-selected-effects-resume-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    registration = read(cohort / 'registration.json')
    try:
        subprocess.run(['systemd-run', '--user', '--unit=' + service,
            '--description=Continue pending Selected effects tasks after verified service recovery',
            '--property=WorkingDirectory=' + registration['frozen_source'],
            '--property=StandardOutput=append:' + str(cohort / 'coordinator.log'),
            '--property=StandardError=append:' + str(cohort / 'coordinator.log'),
            '/home/bfq/.venvs/agenttx/bin/python', '-u', str(cohort / 'launch.py'), 'run'],
            check=True, timeout=30, capture_output=True)
    except Exception:
        if (audit / 'previous-execution.json').exists():
            shutil.copy2(audit / 'previous-execution.json', cohort / 'execution.json')
        raise
    services = read(cohort / 'services.json')
    services['coordinator'] = service + '.service'
    save(cohort / 'services.json', services)
    save(audit / 'resume.json', dict(resumed_at=now(), coordinator=service + '.service',
        pending_only=True, terminal_attempts_retried=0, runtime_changed=False, model_configuration_changed=False))
    return service + '.service'


def tick(cohort, control):
    progress = read(cohort / 'progress.json')
    cells = read(cohort / 'cells.json')
    for cell in cells:
        for attempt in cell['attempts']:
            if attempt.get('result_sha256'):
                assert sha(attempt['result_path']) == attempt['result_sha256'], 'terminal evidence changed'
    if progress['active']:
        age = time.time() - datetime.fromisoformat(progress['updated_at']).timestamp()
        return dict(state='running' if age < 120 else 'stale_progress_requires_review', progress=progress)
    if not any(c['state'] == 'pending' for c in cells):
        return dict(state='finished', progress=progress)
    service = read(cohort / 'services.json')['coordinator']
    state = subprocess.run(['systemctl', '--user', 'show', service, '--property=ActiveState', '--value'],
                           capture_output=True, text=True, timeout=15).stdout.strip()
    if state in ('active', 'activating', 'deactivating'):
        return dict(state='coordinator_starting_or_draining', progress=progress)
    errors = [c for c in cells if c['state'] == 'infrastructure_error'
              and (not Path(c['result_path']).exists() or control.get('reviewed_errors', {}).get(c['key']) != sha(c['result_path']))]
    classified = []
    for cell in errors:
        category = classify(cell)
        if category is None:
            return dict(state='unclassified_failure_requires_review', key=cell['key'], progress=progress)
        classified.append(dict(key=cell['key'], classification=category,
                               result_path=cell['result_path'], result_sha256=sha(cell['result_path'])))
    if not classified:
        return dict(state='stopped_without_new_reviewed_failure', progress=progress)
    if time.time() - control.get('last_probe', 0) < 300:
        return dict(state='waiting_for_provider_probe', progress=progress)
    verify_sources(cohort)
    audit = cohort / 'maintenance' / ('automatic-provider-review-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    audit.mkdir(parents=True)
    save(audit / 'reviewed-errors.json', classified)
    control['last_probe'] = time.time()
    urllib.request.urlopen = urllib.request.build_opener(urllib.request.ProxyHandler({})).open
    proof = probe_inference(audit)
    save(audit / 'inference-health.json', proof)
    save(audit / 'probe-accounting.json', probe_accounting(audit))
    if not proof['healthy']:
        return dict(state='waiting_for_provider_recovery', audit=str(audit), progress=progress)
    fresh_preflight(cohort, audit)
    service = resume(cohort, audit)
    control.setdefault('reviewed_errors', {}).update({r['key']: r['result_sha256'] for r in classified})
    return dict(state='pending_resumed', audit=str(audit), coordinator=service, progress=progress)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', type=Path, required=True)
    args = parser.parse_args()
    directory = args.cohort / 'supervisor'
    directory.mkdir(exist_ok=True)
    (args.cohort / 'monitor').mkdir(exist_ok=True)
    control_path = directory / 'control.json'
    while True:
        control = read(control_path) if control_path.exists() else {}
        try:
            status = tick(args.cohort, control)
        except Exception as exc:
            status = dict(state='supervision_error_requires_review', error_type=type(exc).__name__, error=str(exc))
        status['checked_at'] = now()
        save(control_path, control)
        save(directory / 'latest.json', status)
        save(args.cohort / 'monitor/latest.json', dict(checked_at=status['checked_at'],
            progress=read(args.cohort / 'progress.json'), supervisor_state=status['state'],
            terminal_evidence_errors=[status['error']] if status.get('state') == 'supervision_error_requires_review' else []))
        with (directory / 'history.jsonl').open('a') as stream:
            stream.write(json.dumps(status) + '\n')
        if status['state'] == 'finished':
            return
        time.sleep(60)


if __name__ == '__main__':
    main()
