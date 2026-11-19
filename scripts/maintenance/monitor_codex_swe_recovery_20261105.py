#!/usr/bin/env python3
"""Supervise the frozen Codex/SWE queue without retrying terminal attempts."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
from zoneinfo import ZoneInfo


SERVICE = 'agenttx-codex-swe-full-recovery-20261104-r1.service'
CONTAINER = 'atx-codex-swe-20261104-run'
DOCKER = ['/usr/bin/docker', '--host', 'unix:///run/agenttx-docker/docker.sock']
POLICIES = ('causal', 'crash_no_rollback', 'temporal_checkpoint',
            'whole_branch_abort', 'clean_recovery', 'ideal_oracle')
CONTEXTS = {p: 'compact' if p in ('causal', 'ideal_oracle', 'clean_recovery') else 'full'
            for p in POLICIES}


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def key(cell):
    return cell['task'] + ':' + cell['policy']


def terminal_evidence(cells):
    evidence = {}
    for cell in cells:
        if cell['state'] not in ('completed', 'infrastructure_error'):
            continue
        result = Path(cell['result_path']) if cell.get('result_path') else None
        evidence[key(cell)] = dict(cell=cell, result_sha256=digest(result)
                                  if result and result.is_file() else None)
    return evidence


def preservation_issues(previous, current):
    return ['terminal_evidence_changed:' + name for name, evidence in previous.items()
            if current.get(name) != evidence]


def known_worker_response_failure(cell, result):
    """A dispatch with uncertain effects remains a failed, un-replayed attempt."""
    # Clean has no recovery commit, so the same worker failure produces an
    # empty top-level error instead of RecoveryProtectionError. The explicit
    # after-dispatch stderr and unfinished/uncommitted flags are still needed.
    protected_error = (result.get('error') ==
        'RecoveryProtectionError: external session did not finish; refusing to commit its partial sandbox')
    clean_error = cell.get('policy') == 'clean_recovery' and result.get('error') == ''
    return (cell['state'] == 'infrastructure_error'
            and cell.get('infrastructure_issue') == 'environment_error'
            and result.get('environment_error') is True
            and result.get('committed') is False
            and result.get('finished') is False
            and not result.get('host_leak_before_commit')
            and '_WorkerCommandUncertain: try worker failed after dispatch; refusing automatic replay'
            in result.get('harness_stderr', '')
            and (protected_error or clean_error))


def registered_session_timeout(cell, result, registration):
    """Keep a limit-exhausted attempt; continuing pending work is not a retry."""
    limit = registration.get('model_wall_timeout_per_session_s')
    elapsed = result.get('official_model_wall_s')
    return (type(limit) in (int, float) and limit > 0
            and type(elapsed) in (int, float) and limit <= elapsed <= limit + 30
            and cell['state'] == 'infrastructure_error'
            and cell.get('infrastructure_issue') == 'environment_error'
            and result.get('environment_error') is True
            and result.get('harness_returncode') == 124
            and f'external harness timeout after {limit:.1f}s' in result.get('harness_stderr', '')
            and result.get('finished') is False and result.get('committed') is False
            and not result.get('host_leak_before_commit')
            and result.get('task') == cell.get('task')
            and result.get('mode') == cell.get('policy')
            and result.get('model') == registration.get('model')
            and result.get('harness_backend') == 'codex'
            and result.get('source_sha256') == registration.get('source_sha256')
            and result.get('recovery_context_variant') == CONTEXTS.get(cell.get('policy')))


def quality_issues(cell, result, registration):
    issues = []
    if result.get('task') != cell['task'] or result.get('mode') != cell['policy']:
        issues.append('result_identity')
    if result.get('source_sha256') != registration['source_sha256']:
        issues.append('frozen_source')
    if result.get('model') != registration['model'] or result.get('harness_backend') != 'codex':
        issues.append('model_or_harness')
    if result.get('recovery_context_variant') != CONTEXTS[cell['policy']]:
        issues.append('context')
    if result.get('environment_error'):
        issues.append('completed_environment_error')
    if result.get('host_leak_before_commit'):
        issues.append('host_leak')
    prompt = result.get('prompt_tokens')
    completion = result.get('completion_tokens')
    total = result.get('total_tokens')
    replay = result.get('doc_replay_tokens')
    if not all(type(n) is int and n >= 0 for n in (prompt, completion, total, replay)):
        issues.append('usage_missing')
    elif total != prompt + completion:
        issues.append('usage_arithmetic')
    return [key(cell) + ':' + issue for issue in issues]


def health_alerts(progress, cells, active, timestamp):
    counts = Counter(c['state'] for c in cells)
    alerts = []
    if counts['running'] > 8:
        alerts.append('worker_limit_exceeded')
    age = timestamp - datetime.fromisoformat(progress['updated_at']).timestamp()
    if active and age > 120:
        alerts.append('coordinator_progress_stale')
    if not active and counts['running']:
        alerts.append('coordinator_missing_with_unreconciled_workers')
    if progress.get('stop_reason'):
        alerts.append('dispatch_stopped:' + progress['stop_reason'])
    for cell in cells:
        if cell['state'] == 'running' and cell.get('started_at'):
            age = timestamp - datetime.fromisoformat(cell['started_at']).timestamp()
            if age > 3660:
                alerts.append('worker_exceeds_registered_wall_limit:' + key(cell))
    return alerts


def service_active():
    return subprocess.run(['systemctl', '--user', 'is-active', '--quiet', SERVICE],
                          timeout=15).returncode == 0


def worker_snapshot(cohort, active):
    if not active:
        return []
    # Read namespace diagnostics, not model prompts or responses. Redact with
    # the same frozen credential helper before anything leaves the container.
    code = '''
import json
from pathlib import Path
from experiments.scripts.experiment_credentials import private_values,redact_credentials
active=json.loads(__import__('sys').argv[1]); root=Path('/mnt/data/agenttx-runtime/tmp'); rows=[]
for cell in active:
 prefix='agenttx-session-swe-'+cell['task']+'-'+cell['policy']+'-0-'
 for session in root.glob(prefix+'*'):
  log=session/'worker.stderr.log'
  tail=''
  if log.is_file():
   with log.open('rb') as stream:
    stream.seek(max(0,log.stat().st_size-4096));tail=stream.read().decode(errors='replace')
  traces=list(session.glob('worker-strace-*.log'))
  rows.append(dict(task=cell['task'],policy=cell['policy'],session=str(session),
   worker_stderr_tail=tail,trace_bytes=sum(p.stat().st_size for p in traces),
   last_trace_write=max((p.stat().st_mtime for p in traces),default=None)))
print(json.dumps(redact_credentials(rows,private_values())))
'''
    command = [*DOCKER, 'exec', CONTAINER, '/home/bfq/.venvs/agenttx/bin/python',
               '-c', code, json.dumps(active)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=25)
    except subprocess.TimeoutExpired:
        return dict(unavailable=True, reason='worker diagnostic read timed out after 25 seconds')
    if result.returncode:
        return dict(unavailable=True, reason='coordinator exited or diagnostic read failed')
    try:
        return json.loads(result.stdout)
    except ValueError:
        return dict(unavailable=True, reason='worker diagnostic response was not valid JSON')


def render(document, cohort, state, cells):
    stamp = datetime.fromisoformat(state['checked_at']).astimezone(ZoneInfo('Asia/Shanghai'))
    lines = ['# Codex × SWE-Bench Lite 恢复实验进度', '',
             f'北京时间 {stamp:%Y-%m-%d %H:%M:%S}；每 60 秒检查，健康汇总每 30 分钟保存。', '',
             f'批次：`{cohort.name}`。300 题 × 6 策略；最多 8 路。', '',
             f"监控状态：`{state['status']}`；协调器服务：`{state['coordinator_active']}`。", '',
             '| 策略 | 已完成 | 成功 | 基础设施异常 | 运行中 | 待运行 |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for policy in POLICIES:
        group = [c for c in cells if c['policy'] == policy]
        counts = Counter(c['state'] for c in group)
        successes = sum(c.get('success') is True for c in group if c['state'] == 'completed')
        lines.append(f"| {policy} | {counts['completed']} | {successes} | {counts['infrastructure_error']} | {counts['running']} | {counts['pending']} |")
    lines += ['', f"停止原因：`{state['stop_reason']}`。", '',
              '告警：' + ('；'.join(state['alerts']) if state['alerts'] else '无新增运行告警。'), '',
              '已有结果与基础设施异常保留，不按成功率或 token 收益重跑。',
              '执行器派发后响应丢失的异常用量不完整，日志中的 0 token 不视为实际零成本。',
              '已知执行器异常仅在零模型环境检查通过后继续待运行单元；未知异常停止派发并持续记录。',
              '`formal_eligible=false`；成功率和 token 比较须另做完整实验审计。', '',
              f"机器可读状态：`{cohort}/monitor/latest.json`。", '']
    temporary = document.with_suffix('.tmp')
    temporary.write_text('\n'.join(lines))
    temporary.replace(document)


def continue_pending(cohort, state, errors, control):
    """Check known infrastructure failures, retain them, then dispatch new cells."""
    if control.get('halt_requested'):
        return 'quality_halt_requires_review'
    if control.get('continuation_block_reason'):
        return control['continuation_block_reason']
    unreviewed = [c for c in errors if key(c) not in control['reviewed_errors']]
    if not unreviewed:
        return 'stopped_without_new_classified_infrastructure_error'
    registration = read(cohort / 'registration.json')
    classified = []
    for cell in unreviewed:
        if not cell.get('result_path') or not Path(cell['result_path']).is_file():
            return 'unclassified_infrastructure_error_requires_review'
        result = read(Path(cell['result_path']))
        if known_worker_response_failure(cell, result):
            classification = 'worker_response_uncertain'
        elif registered_session_timeout(cell, result, registration):
            classification = 'registered_model_session_timeout'
        else:
            return 'unclassified_infrastructure_error_requires_review'
        classified.append(dict(task=cell['task'], policy=cell['policy'], classification=classification,
                               result_sha256=digest(Path(cell['result_path'])),
                               main_usage_complete=False, reported_main_tokens=result.get('total_tokens'),
                               known_replay_tokens=result.get('doc_replay_tokens')))
    worker_failure = any(c['classification'] == 'worker_response_uncertain' for c in classified)
    worker_continuations = control.setdefault('worker_failure_continuations',
                                             list(control['automatic_continuations']))
    recent = [t for t in worker_continuations if time.time() - t < 3600]
    if worker_failure and len(recent) >= 3:
        return 'repeated_worker_failures_require_diagnosis'
    if shutil.disk_usage(cohort).free < 250 * 2**30:
        return 'insufficient_disk_for_continuation'
    directory = cohort / 'maintenance' / ('automatic-pending-continuation-' +
                                         datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    directory.mkdir()
    frozen = Path(registration['frozen_source'])
    for entry in read(cohort / 'source-manifest.json')['files']:
        if digest(frozen / entry['path']) != entry['sha256']:
            return 'frozen_source_changed'
    command = read(cohort / 'preflight-container-command.json')
    command[command.index('--name') + 1] = 'atx-codex-swe-monitor-preflight'
    script = command[command.index('-c') + 1]
    checks = []
    for index, cell in enumerate(unreviewed):
        probe = list(command)
        probe[probe.index('-c') + 1] = script.replace("'psf__requests-1963'", repr(cell['task']))
        probe[-1] = str(directory / f'preflight-{index}')
        try:
            with (directory / f'preflight-{index}.log').open('w') as log:
                result = subprocess.run(probe, stdout=log, stderr=subprocess.STDOUT, timeout=300)
        except subprocess.TimeoutExpired:
            # Only this diagnostic container is stopped; campaign workers
            # have already drained before continuation is considered.
            subprocess.run([*DOCKER, 'stop', '--time=10', 'atx-codex-swe-monitor-preflight'],
                           capture_output=True, timeout=25)
            return 'zero_model_preflight_timeout'
        proof_path = Path(probe[-1]) / 'proof.json'
        if result.returncode or not proof_path.exists():
            return 'zero_model_preflight_failed'
        proof = read(proof_path)
        if proof.get('model_calls') != 0 or not all(proof.get(k) for k in (
                'valid', 'original_source_restored', 'independent_documents_retained',
                'derived_removed', 'fault_write_succeeded')):
            return 'zero_model_preflight_invalid'
        checks.append(dict(task=cell['task'], proof=str(proof_path), sha256=digest(proof_path)))
    activity_file = cohort / 'monitor/session-model-activity.jsonl'
    if activity_file.exists():
        for line in activity_file.open():
            try:
                activity = json.loads(line)
            except ValueError:
                continue
            for row in activity.get('rows', []):
                usage = row.get('last_total_usage') or {}
                for entry in classified:
                    if (row.get('task'), row.get('policy')) == (entry['task'], entry['policy']):
                        entry['observed_session_token_lower_bound'] = max(
                            entry.get('observed_session_token_lower_bound', 0), usage.get('total_tokens', 0))
    audit = dict(checked_at=now(), errors=unreviewed, classifications=classified, preflights=checks,
                 failed_attempts_retried=False, pending_only=True,
                 failure_usage_complete=False, source_unchanged=True)
    for name in ('progress.json', 'cells.json', 'execution.json'):
        shutil.copy2(cohort / name, directory / name)
    save(directory / 'audit.json', audit)
    for cell in unreviewed:
        control['reviewed_errors'].append(key(cell))
    control['automatic_continuations'].append(time.time())
    if worker_failure:
        worker_continuations.append(time.time())
    save(cohort / 'monitor/control.json', control)
    # Archive the old completion marker only after its evidence is saved.
    (cohort / 'execution.json').rename(directory / 'prior-execution-marker.json')
    subprocess.run(['systemctl', '--user', 'reset-failed', SERVICE], check=True, timeout=15)
    subprocess.run(['systemctl', '--user', 'start', SERVICE], check=True, timeout=30)
    return 'resumed_pending_after_zero_model_preflight'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', type=Path, required=True)
    parser.add_argument('--document', type=Path, required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    cohort = args.cohort
    folder = cohort / 'monitor'
    folder.mkdir(exist_ok=True)
    lock = (folder / 'monitor.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    registration = read(cohort / 'registration.json')
    baseline_file = folder / 'terminal-evidence.json'
    baseline = read(baseline_file) if baseline_file.exists() else {}
    control_file = folder / 'control.json'
    if control_file.exists():
        control = read(control_file)
    else:
        audit_path = Path(read(cohort / 'maintenance/active-monitored-resume.json')['directory'])
        reviewed = read(audit_path / 'resume-audit.json')['prior_infrastructure_errors']
        control = dict(reviewed_errors=[key(c) for c in reviewed], automatic_continuations=[])
        save(control_file, control)
    last_health = 0
    while True:
        try:
            progress = read(cohort / 'progress.json')
            cells = read(cohort / 'cells.json')
            active = service_active()
            current = terminal_evidence(cells)
            issues = preservation_issues(baseline, current)
            for cell in cells:
                if cell['state'] == 'completed':
                    try:
                        result = read(Path(cell['result_path']))
                    except (OSError, ValueError, KeyError):
                        issues.append(key(cell) + ':missing_or_invalid_result')
                    else:
                        issues.extend(quality_issues(cell, result, registration))
            alerts = health_alerts(progress, cells, active, time.time())
            alerts.extend(issues)
            if not issues:
                baseline = current
                save(baseline_file, baseline)
            elif active and not control.get('halt_requested'):
                # Ask the frozen coordinator to stop dispatching and drain;
                # never kill live workers to hide a validation problem.
                subprocess.run([*DOCKER, 'kill', '--signal=TERM', CONTAINER],
                               capture_output=True, timeout=15, check=True)
                control['halt_requested'] = True
                save(control_file, control)
            counts = dict(Counter(c['state'] for c in cells))
            state = dict(checked_at=now(), cohort=cohort.name, counts=counts,
                         coordinator_active=active, progress_updated_at=progress['updated_at'],
                         active=progress.get('active', []), stop_reason=progress.get('stop_reason'),
                         alerts=alerts, status='running' if active else 'needs_attention',
                         formal_eligible=False, completed_quality_issues=issues,
                         disk_free_gib=round(shutil.disk_usage(cohort).free / 2**30, 1),
                         prior_results_preserved=not preservation_issues(baseline, current),
                         known_infrastructure_error_count=counts.get('infrastructure_error', 0))
            state['worker_activity'] = worker_snapshot(cohort, state['active']) if active else []
            if isinstance(state['worker_activity'], dict) and state['worker_activity'].get('unavailable'):
                state['alerts'].append('worker_diagnostics_unavailable')
            if not counts.get('running') and not counts.get('pending'):
                state['status'] = 'finished_with_infrastructure_errors' if counts.get('infrastructure_error') else 'finished'
            elif (not active and not counts.get('running') and counts.get('pending')
                  and (cohort / 'execution.json').exists() and not issues and not args.once):
                errors = [c for c in cells if c['state'] == 'infrastructure_error']
                state['continuation'] = continue_pending(cohort, state, errors, control)
                if state['continuation'].startswith('resumed_'):
                    state['status'] = 'continuing_pending'
                else:
                    state['alerts'].append(state['continuation'])
                    control['continuation_block_reason'] = state['continuation']
                    save(control_file, control)
            save(folder / 'latest.json', state)
            with (folder / 'history.jsonl').open('a') as log:
                log.write(json.dumps(state, ensure_ascii=False) + '\n')
            if time.monotonic() - last_health >= 1800:
                save(folder / 'halfhour-latest.json', state)
                with (folder / 'halfhour-history.jsonl').open('a') as log:
                    log.write(json.dumps(state, ensure_ascii=False) + '\n')
                last_health = time.monotonic()
            render(args.document, cohort, state, cells)
            print(json.dumps({k: state[k] for k in ('checked_at', 'status', 'counts', 'alerts')}, ensure_ascii=False), flush=True)
            if args.once or state['status'].startswith('finished'):
                return
        except Exception as exc:
            save(folder / 'monitor-error.json', dict(checked_at=now(), error=type(exc).__name__,
                                                    message=str(exc)[:1000]))
            raise
        time.sleep(60)


if __name__ == '__main__':
    main()
