import copy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import run_tb_common_root_20261028 as repair
from experiments.scripts.run_tb_dual_local import configuration_id, result_configuration


def fixture_row(tmp_path, **extra):
    path = tmp_path/'host-result.json'
    path.write_text(json.dumps(dict(success=False, **extra)))
    (tmp_path/'verifier.stdout.log').write_text(
        'os.rename("mystery", "/tmp/mystery")\n'
        "OSError: [Errno 18] Invalid cross-device link: 'mystery' -> '/tmp/mystery'")
    return dict(task='path-tracing-reverse', harness='codex', policy='causal',
                state='completed', expected_context='compact', result_path=str(path))


def test_repair_requires_exact_original_verifier_filesystem_failure(tmp_path):
    row = fixture_row(tmp_path)
    assert repair.filesystem_fault(row)
    assert repair.filesystem_fault(dict(row, task='unrelated')) is None
    assert repair.filesystem_fault(dict(row, state='running')) is None
    (tmp_path/'verifier.stdout.log').write_text('FAILED: candidate source missing')
    assert repair.filesystem_fault(row) is None


def test_repaired_failure_and_success_are_not_unlimited_environment_retries(tmp_path):
    row = fixture_row(tmp_path, app_tmp_filesystem_contract=repair.CONTRACT)
    assert repair.filesystem_fault(row) is None
    row = fixture_row(tmp_path, success_flag=True)
    data = json.loads(Path(row['result_path']).read_text())
    data['success'] = True
    Path(row['result_path']).write_text(json.dumps(data))
    assert repair.filesystem_fault(row) is None


def test_one_finite_repair_preserves_old_result_and_all_charged_attempts(tmp_path):
    row = fixture_row(tmp_path)
    before = copy.deepcopy(row)
    raw = Path(row['result_path']).read_bytes()
    retries = []
    assert len(repair.register_filesystem_repairs([row], retries)) == 1
    assert row == before and Path(row['result_path']).read_bytes() == raw
    assert retries[0]['previous_attempt_path'] == row['result_path']
    assert repair.register_filesystem_repairs([row], retries) == []
    retries[0].update(state='completed', result_path=row['result_path'])
    assert repair.register_filesystem_repairs([row], retries) == []


def test_running_retry_kept_and_existing_pending_retry_reused(tmp_path):
    row = fixture_row(tmp_path)
    retry = dict(row, state='running', attempt=1)
    retries = [retry]
    assert repair.register_filesystem_repairs([row], retries) == []
    retry['state'] = 'pending'
    assert len(repair.register_filesystem_repairs([row], retries)) == 1
    assert len(retries) == 1 and retry['app_tmp_repair_of'] == row['result_path']


def test_old_and_repaired_environments_do_not_share_cost_pair_configuration(tmp_path):
    path = tmp_path/'cell'/'host-result.json'
    old = result_configuration({}, path)
    new = result_configuration(dict(app_tmp_filesystem_contract=repair.CONTRACT), path)
    assert configuration_id(old) != configuration_id(new)
    assert result_configuration({}, path) == old
