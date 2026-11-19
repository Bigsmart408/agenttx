import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts/maintenance'))
import run_tb_rollback_ancestors_20261028 as repair


@pytest.mark.parametrize('summary', [None, {}])
def test_absent_model_summary_is_not_a_repair_or_dispatcher_crash(tmp_path, summary):
    path = tmp_path/'host-result.json'
    path.write_text(json.dumps(dict(original_task_user='alice',
        task_user_workspace_contract='original-task-user-git-and-mounted-test-restore/v1')))
    accounting = dict(host_result_sha256=repair.base.sha(path),
                      model_summaries={'model-result.json': summary})
    (tmp_path/'accounting-summary.json').write_text(json.dumps(accounting))
    row = dict(task='privilege-escalation', state='infrastructure_error', result_path=str(path))
    assert repair.failure_evidence(row) is None


def test_archived_permission_failure_keeps_original_result_and_model_hashes(tmp_path):
    path = tmp_path/'host-result.json'
    path.write_text(json.dumps(dict(original_task_user='alice')))
    raw = path.read_bytes()
    accounting = dict(host_result_sha256=repair.base.sha(path), model_summaries={
        'model-result.json': dict(returncode=1, original_sha256='original-model-hash',
            stderr_excerpt='mkdir: cannot create directory /home/alice/.agenttx-codex-test: Permission denied')})
    (tmp_path/'accounting-summary.json').write_text(json.dumps(accounting))
    row = dict(task='privilege-escalation', state='infrastructure_error', result_path=str(path))
    evidence = repair.failure_evidence(row)
    assert evidence['original_model_result_sha256'] == 'original-model-hash'
    assert path.read_bytes() == raw
