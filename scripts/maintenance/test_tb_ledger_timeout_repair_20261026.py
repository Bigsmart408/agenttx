import json
from pathlib import Path
import sys
import subprocess

sys.path.insert(0, str(Path(__file__).parent))
import run_tb_ledger_timeout_repair_20261026 as q
from test_tb_registered_deadline_20261026 import fixture


def test_deadline_notice_does_not_reintroduce_broad_connection_halt(tmp_path, monkeypatch):
    row = fixture(tmp_path, dict(type='error', message='Reconnecting... 1/5 (stream disconnected before completion: stream closed before response.completed)'))
    monkeypatch.setattr(q.prior, 'connection_evidence', lambda row: dict(classification='explicit_model_connection_failure'))
    assert q.fresh_connection_evidence(row) is None
    model = tmp_path / 'model-result.json'
    data = json.loads(model.read_text())
    data['events'] = [dict(type='turn.failed', error=dict(message='Connection refused'))]
    model.write_text(json.dumps(data))
    assert q.fresh_connection_evidence(row)['classification'] == 'explicit_model_connection_failure'


def test_network_priority_and_recorded_shutdown_scope_remain():
    network = dict(task='network', state='pending', download_proxy_retry=True)
    assert q.select([], [network], {}) == (network, True)
    assert q.SHUTDOWN_AUDIT.name == 'tb-worker-shutdown-repair-20261026'
    assert q.REPAIR_FILES == ('src/agenttx/ledger.py',)


def test_adopting_dispatcher_with_no_children_is_valid():
    parent = subprocess.Popen(['sleep', '10'])
    try:
        assert q.spawned_worker_pids(parent.pid) == set()
    finally:
        parent.terminate()
        parent.wait(timeout=2)
