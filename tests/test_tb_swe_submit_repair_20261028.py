import hashlib
import json

from scripts.maintenance import run_tb_swe_task_user_submit_20261028 as repair


def test_broken_environment_retries_all_policies_once_and_keeps_raw_outcomes(tmp_path, monkeypatch):
    policies = ('clean_recovery', 'crash_no_rollback', 'temporal_checkpoint',
                'whole_branch_abort', 'causal', 'ideal_oracle')
    cells = []
    original = {}
    for i, policy in enumerate(policies):
        path = tmp_path/(policy+'.json')
        path.write_text(json.dumps({'official_success': i != 2,
                                    'api_usage': {'total_tokens': i+10}}))
        original[str(path)] = path.read_bytes()
        cells.append(dict(task='task', harness='sweagent', policy=policy,
                          expected_context='compact' if policy in ('clean_recovery','causal','ideal_oracle') else 'full',
                          state='completed' if i != 2 else 'infrastructure_error',
                          result_path=str(path)))
    monkeypatch.setattr(repair, 'failure_evidence', lambda row:
                        {'result_sha256': hashlib.sha256(original[row['result_path']]).hexdigest()})
    retries = []
    added = repair.register_repair(cells, retries)
    assert len(added) == 6
    assert {r['policy'] for r in added} == set(policies)
    assert all(r['state'] == 'pending' and r['attempt'] == 1 for r in added)
    assert repair.register_repair(cells, retries) == []
    for row in cells:
        from pathlib import Path
        assert Path(row['result_path']).read_bytes() == original[row['result_path']]
    assert [r['expected_context'] for r in retries] == [r['expected_context'] for r in cells]
