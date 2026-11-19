import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from tb_registered_deadline_20261026 import registered_deadline_evidence


def fixture(tmp_path, event):
    path = tmp_path / 'host-result.json'
    path.write_text(json.dumps(dict(harness='codex', completed=True, container_returncode=0,
        model_returncode=124, official_tests_rc=124, status='verifier_timeout',
        model_session_wall_s=3600, verifier_wall_s=1800, api_usage=dict(complete=False))))
    (tmp_path / 'model-result.json').write_text(json.dumps(dict(returncode=124,
        stderr='external harness timeout after 3600.0s', events=[event])))
    (tmp_path / 'verifier.stderr.log').write_text('external harness timeout after 1800.0s\n')
    return dict(result_path=str(path))


def test_reconnect_notice_and_proven_deadlines_do_not_claim_provider_health(tmp_path):
    row = fixture(tmp_path, dict(type='error', message='Reconnecting... 1/5 (stream disconnected before completion: stream closed before response.completed)'))
    before = Path(row['result_path']).read_bytes()
    proof = registered_deadline_evidence(row)
    assert proof['reconnect_notices'] == 1
    assert proof['provider_health_not_inferred'] and proof['incomplete_cost_comparison_exclusion']
    assert proof['automatic_retry'] is False and proof['task_success'] is False
    assert Path(row['result_path']).read_bytes() == before


def test_terminal_connection_failure_still_requires_review(tmp_path):
    for event in [dict(type='turn.failed', error=dict(message='Connection refused')),
                  dict(type='error', message='Connection refused'),
                  dict(type='error', message='Reconnecting... 5/5 (stream disconnected before completion: stream closed before response.completed)')]:
        assert registered_deadline_evidence(fixture(tmp_path, event)) is None


def test_both_actual_timeout_signatures_are_required(tmp_path):
    row = fixture(tmp_path, dict(type='turn.completed'))
    (tmp_path / 'verifier.stderr.log').write_text('Connection refused')
    assert registered_deadline_evidence(row) is None
