import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import run_tb_worker_shutdown_repair_20261026 as q


def test_only_proven_shutdown_failure_gets_one_repair_with_costs_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(q, 'AUDIT', tmp_path)
    original = tmp_path / 'original.json'
    original.write_text(json.dumps(dict(success=False, api_usage=dict(complete=True, total_tokens=111))))
    failed = tmp_path / 'failed.json'
    failed.write_text(json.dumps(dict(api_usage=dict(complete=False, total_tokens=222))))
    first = dict(task='browser', harness='sweagent', policy='ideal_oracle',
                 state='completed', expected_context='compact', result_path=str(original))
    old = dict(first, state='infrastructure_error', attempt=3, result_path=str(failed),
               download_proxy_retry=True, download_retry_cohort='existing-download-treatment')
    retries = [old]
    before = [original.read_bytes(), failed.read_bytes()]
    q.register_shutdown_repairs([first], retries)
    assert len(retries) == 1
    (tmp_path / 'shutdown-enforcement.json').write_text(json.dumps(dict(affected_result_path=str(failed))))
    q.register_shutdown_repairs([first], retries)
    assert len(retries) == 2
    new = retries[-1]
    assert new['attempt'] == 4 and new['state'] == 'pending'
    assert new['runtime_shutdown_repair_of'] == str(failed)
    assert new['download_proxy_retry'] is True
    assert new['download_retry_cohort'] == 'existing-download-treatment'
    q.register_shutdown_repairs([first], retries)
    assert len(retries) == 2
    assert [original.read_bytes(), failed.read_bytes()] == before
    report = q.prior.report([first], [old])
    assert report['attempts_with_incomplete_usage'] == 1


def test_running_attempt_is_adopted_without_duplicate_repair(tmp_path, monkeypatch):
    monkeypatch.setattr(q, 'AUDIT', tmp_path)
    row = dict(task='browser', harness='sweagent', policy='ideal_oracle',
               state='running', result_path=str(tmp_path / 'running.json'))
    (tmp_path / 'shutdown-enforcement.json').write_text(json.dumps(dict(affected_result_path=row['result_path'])))
    retries = [row]
    q.register_shutdown_repairs([], retries)
    assert retries == [row]


def test_network_backlog_keeps_eight_slot_priority():
    first = dict(task='ordinary', state='pending')
    network = dict(task='network', state='pending', download_proxy_retry=True)
    for count in range(8):
        assert q.select([first], [network], {i: dict(retry=True) for i in range(count)}) == (network, True)
