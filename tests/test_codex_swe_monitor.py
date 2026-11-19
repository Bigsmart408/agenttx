from copy import deepcopy
from datetime import datetime
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
import pytest

from scripts.maintenance import monitor_codex_swe_recovery_20261105 as monitor
from scripts.maintenance.monitor_codex_swe_recovery_20261105 import (
    health_alerts, known_worker_response_failure, preservation_issues,
)


def test_prior_terminal_results_cannot_be_replaced_or_requeued():
    previous = {'task:causal': {'cell': {'state': 'completed'}, 'result_sha256': 'old'}}
    current = deepcopy(previous)
    current['new:causal'] = {'cell': {'state': 'completed'}, 'result_sha256': 'new'}
    assert preservation_issues(previous, current) == []
    current['task:causal']['result_sha256'] = 'replaced'
    assert preservation_issues(previous, current) == ['terminal_evidence_changed:task:causal']
    del current['task:causal']
    assert preservation_issues(previous, current) == ['terminal_evidence_changed:task:causal']


def test_only_uncommitted_known_worker_failures_allow_pending_continuation():
    cell = dict(state='infrastructure_error', infrastructure_issue='environment_error')
    result = dict(environment_error=True, committed=False, finished=False,
                  harness_stderr='_WorkerCommandUncertain: try worker failed after dispatch; refusing automatic replay',
                  error='RecoveryProtectionError: external session did not finish; refusing to commit its partial sandbox')
    assert known_worker_response_failure(cell, result)
    for field, value in [('committed', True), ('finished', True),
                         ('harness_stderr', 'other environment problem')]:
        assert not known_worker_response_failure(cell, dict(result, **{field: value}))
    assert not known_worker_response_failure(dict(cell, state='completed'), result)


def test_clean_worker_failure_is_recognized_without_recovery_commit_exception():
    cell = dict(state='infrastructure_error', infrastructure_issue='environment_error',
                policy='clean_recovery')
    result = dict(environment_error=True, committed=False, finished=False, error='',
                  harness_stderr='_WorkerCommandUncertain: try worker failed after dispatch; refusing automatic replay')
    assert known_worker_response_failure(cell, result)
    assert not known_worker_response_failure(dict(cell, policy='causal'), result)
    assert not known_worker_response_failure(cell, dict(result, harness_stderr='other error'))


def test_registered_model_timeout_keeps_strict_limit_context_and_safety_checks():
    cell = dict(state='infrastructure_error', infrastructure_issue='environment_error',
                task='registered-task', policy='temporal_checkpoint')
    registration = dict(model_wall_timeout_per_session_s=900, source_sha256='frozen', model='gpt-5.6-luna')
    result = dict(environment_error=True, harness_returncode=124, finished=False,
                  committed=False, official_model_wall_s=901.8, source_sha256='frozen',
                  task='registered-task', mode='temporal_checkpoint', model='gpt-5.6-luna', harness_backend='codex',
                  recovery_context_variant='full', harness_stderr='external harness timeout after 900.0s')
    assert monitor.registered_session_timeout(cell, result, registration)
    for field, value in [('official_model_wall_s', 800), ('official_model_wall_s', 940),
                         ('harness_returncode', 127), ('host_leak_before_commit', True),
                         ('source_sha256', 'changed'), ('recovery_context_variant', 'compact'),
                         ('task', 'different-task'), ('model', 'different-model'),
                         ('harness_stderr', 'other problem')]:
        assert not monitor.registered_session_timeout(cell, dict(result, **{field: value}), registration)


def test_container_pid_one_does_not_count_as_live_host_coordinator():
    progress = dict(updated_at='2026-11-05T01:00:00+00:00', coordinator_pid=1,
                    stop_reason=None)
    cells = [dict(task='x', policy='causal', state='running',
                  started_at='2026-11-05T00:00:00+00:00')]
    timestamp = datetime.fromisoformat('2026-11-05T01:01:01+00:00').timestamp()
    alerts = health_alerts(progress, cells, False, timestamp)
    assert 'coordinator_missing_with_unreconciled_workers' in alerts
    assert 'worker_exceeds_registered_wall_limit:x:causal' in alerts


@pytest.mark.parametrize('failure', ['timeout', 'invalid_json', 'container_exit'])
def test_worker_diagnostic_failure_does_not_interrupt_supervision(monkeypatch, failure):
    def run(command, **kwargs):
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        return SimpleNamespace(returncode=1 if failure == 'container_exit' else 0,
                               stdout='incomplete diagnostic output')

    monkeypatch.setattr(monitor.subprocess, 'run', run)
    snapshot = monitor.worker_snapshot(None, [dict(task='x', policy='causal')])
    assert snapshot['unavailable'] is True
    assert snapshot['reason']
    assert monitor.worker_snapshot(None, []) == []


def test_missing_failed_result_requires_review_without_crashing(tmp_path):
    (tmp_path / 'registration.json').write_text('{}')
    cell = dict(task='x', policy='causal', state='infrastructure_error',
                result_path=str(tmp_path / 'absent-result.json'))
    control = dict(reviewed_errors=[])
    assert monitor.continue_pending(tmp_path, {}, [cell], control) == (
        'unclassified_infrastructure_error_requires_review')
    assert not control['reviewed_errors']


@pytest.mark.parametrize('failure', ['worker_response', 'registered_timeout'])
def test_automatic_continuation_preserves_cells_and_failure_evidence(tmp_path, monkeypatch, failure):
    cohort = tmp_path / 'cohort'
    cohort.mkdir()
    (cohort / 'maintenance').mkdir()
    (cohort / 'monitor').mkdir()
    frozen = tmp_path / 'frozen'
    frozen.mkdir()
    (frozen / 'source.py').write_text('frozen source')
    failed = cohort / 'failed-result.json'
    result = dict(environment_error=True, committed=False, finished=False,
                  harness_stderr='_WorkerCommandUncertain: try worker failed after dispatch; refusing automatic replay',
                  error='RecoveryProtectionError: external session did not finish; refusing to commit its partial sandbox')
    if failure == 'registered_timeout':
        result.update(harness_returncode=124, official_model_wall_s=901.8,
                      task='actual-failed-task', mode='causal', model='gpt-5.6-luna', harness_backend='codex',
                      recovery_context_variant='compact', source_sha256='frozen',
                      harness_stderr='external harness timeout after 900.0s')
    failed.write_text(json.dumps(result))
    error = dict(task='actual-failed-task', policy='causal', state='infrastructure_error',
                 infrastructure_issue='environment_error', result_path=str(failed))
    cells = [dict(task='completed', policy='causal', state='completed'), error,
             dict(task='pending', policy='causal', state='pending')]
    (cohort / 'cells.json').write_text(json.dumps(cells))
    (cohort / 'progress.json').write_text('{}')
    (cohort / 'execution.json').write_text('{"stopped": true}')
    (cohort / 'registration.json').write_text(json.dumps(dict(frozen_source=str(frozen),
        model_wall_timeout_per_session_s=900, source_sha256='frozen', model='gpt-5.6-luna')))
    (cohort / 'source-manifest.json').write_text(json.dumps(dict(files=[
        dict(path='source.py', sha256=monitor.digest(frozen / 'source.py'))])))
    (cohort / 'preflight-container-command.json').write_text(json.dumps([
        'docker', '--name', 'old-probe', '-c', "probe('psf__requests-1963')", 'old-output']))
    before = (cohort / 'cells.json').read_bytes(), failed.read_bytes()
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[0] == 'docker':
            assert command[command.index('-c') + 1] == "probe('actual-failed-task')"
            output = Path(command[-1])
            output.mkdir()
            (output / 'proof.json').write_text(json.dumps(dict(model_calls=0, valid=True,
                original_source_restored=True, independent_documents_retained=True,
                derived_removed=True, fault_write_succeeded=True)))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(monitor.subprocess, 'run', run)
    monkeypatch.setattr(monitor.shutil, 'disk_usage', lambda _: SimpleNamespace(free=300 * 2**30))
    recent_worker_stops = [monitor.time.time()] * 3 if failure == 'registered_timeout' else []
    control = dict(reviewed_errors=[], automatic_continuations=recent_worker_stops)
    assert monitor.continue_pending(cohort, {}, [error], control).startswith('resumed_')
    assert before == ((cohort / 'cells.json').read_bytes(), failed.read_bytes())
    assert not (cohort / 'execution.json').exists()
    assert calls[-1] == ['systemctl', '--user', 'start', monitor.SERVICE]
    assert control['reviewed_errors'] == ['actual-failed-task:causal']
    assert list((cohort / 'maintenance').glob('*/prior-execution-marker.json'))
