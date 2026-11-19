"""Recovery must preserve evidence and never rerun ordinary task failures."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.maintenance import resume_codex_controlled_20261107 as recovery
from scripts.maintenance.resume_codex_controlled_20261107 import archive_retry, transient


def interrupted():
    return dict(status='model_or_verification_failed', model_returncode=1,
                api_usage=dict(complete=False, known_total_tokens=123,
                               requests=[dict(total_tokens=123),
                                         dict(total_tokens=None, http_status=200)]))


def cohort(tmp_path, attempts=0):
    key = 'case/causal/repeat-00'
    folder = tmp_path / 'main' / key / 'causal'
    folder.mkdir(parents=True)
    result = folder / 'result.json'
    result.write_text(json.dumps(interrupted()))
    log = tmp_path / 'logs' / 'worker.log'
    log.parent.mkdir()
    log.write_text('original worker evidence')
    failed = dict(key=key, trace_id='case', manifest_hash='hash', family='family',
                  operation_count=16, policy='causal', repeat=0, harness='codex',
                  phase='main', state='infrastructure_error', result=str(result),
                  log=str(log), infrastructure_attempts=[{}] * attempts)
    ordinary = dict(key='other/turn_checkpoint/repeat-00', state='task_failed',
                    tokens=456, command_audit_passed=False)
    data = dict(main=[failed, ordinary], runtime_gate=[], model_canary=[])
    (tmp_path / 'cells.json').write_text(json.dumps(data))
    return result, ordinary


def test_archive_preserves_original_and_only_resets_infrastructure(tmp_path):
    result, ordinary = cohort(tmp_path)
    original = result.read_bytes()
    outcome = archive_retry(tmp_path)
    cells = json.loads((tmp_path / 'cells.json').read_text())['main']
    assert cells[1] == ordinary
    retry = cells[0]
    assert retry['state'] == 'pending' and 'result' not in retry
    prior = retry['infrastructure_attempts'][0]
    assert Path(prior['result']).read_bytes() == original
    assert Path(prior['log']).read_text() == 'original worker evidence'
    assert prior['known_api_tokens'] == 123 and prior['unknown_usage_requests'] == 1
    assert not result.exists()  # Fresh output path; original evidence is archived.
    commit = json.loads((Path(outcome['review']) / 'commit.json').read_text())
    assert commit['known_api_tokens'] == 123
    assert outcome['retained_completed'] == 1


def test_retry_limit_does_not_move_or_modify_evidence(tmp_path):
    result, _ = cohort(tmp_path, attempts=2)
    before = (tmp_path / 'cells.json').read_bytes()
    with pytest.raises(RuntimeError, match='retry limit'):
        archive_retry(tmp_path)
    assert (tmp_path / 'cells.json').read_bytes() == before
    assert result.exists()


def test_genuine_model_failures_and_budget_limits_are_not_transient():
    row = interrupted()
    assert transient(row)
    row['api_usage']['complete'] = True
    assert not transient(row)
    row['api_usage']['complete'] = False
    row['api_usage']['budget_exhausted'] = True
    assert not transient(row)


def test_recreates_unloaded_service_with_registered_frozen_source(tmp_path, monkeypatch):
    (tmp_path / 'registration.json').write_text(json.dumps(dict(
        frozen_source='/frozen/project', python='/registered/python')))
    calls = []
    monkeypatch.setattr(recovery.subprocess, 'check_output', lambda *a, **k: 'not-found\n')
    monkeypatch.setattr(recovery.subprocess, 'run', lambda args, **kw: calls.append(args))
    recovery.start_campaign(tmp_path, 'test.service')
    assert calls[0][0] == 'systemd-run'
    assert '/registered/python' in calls[0]
    assert '/frozen/project/experiments/scripts/run_system_microbenchmark.py' in calls[0]
    assert calls[0][-1] == str(tmp_path)


def test_monitor_keeps_archived_cost_and_unknown_usage_visible(tmp_path, monkeypatch):
    from scripts.maintenance import monitor_codex_controlled_20261107 as monitor
    commit = tmp_path / 'recovery' / 'event' / 'commit.json'
    commit.parent.mkdir(parents=True)
    commit.write_text(json.dumps(dict(attempts=[{}], known_api_tokens=123,
                                      unknown_usage_requests=1)))
    monkeypatch.setattr(monitor, 'original_sample', lambda *a: dict(
        known_finished_main_tokens=999, alerts=[]))
    row = monitor.sample(SimpleNamespace(cohort=tmp_path), {})
    assert row['known_finished_main_tokens'] == 999
    assert row['archived_infrastructure_known_tokens'] == 123
    assert row['archived_infrastructure_unknown_requests'] == 1
    assert row['archived_infrastructure_attempts'] == 1
