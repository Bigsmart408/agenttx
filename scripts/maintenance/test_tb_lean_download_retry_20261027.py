import copy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import tb_download_retry_20261026 as retry


FAILED_DOWNLOAD = """<observation>
 info: downloading https://releases.lean-lang.org/lean4/v4.21.0/lean-4.21.0-linux.tar.z
 st
 error: could not download file from
 'https://releases.lean-lang.org/lean4/v4.21.0/lean-4.21.0-linux.tar.
 zst' to '/root/.elan/tmp/archive_file'
 info: caused by: error during download
 info: caused by: [28] Timeout was reached (Operation too slow. Less
 than 10 bytes/sec transferred the last 30 seconds)
 </observation>
 <observation>
 HTTP/2 302
 location: https://github.com/leanprover/lean4/releases/download/v4.21.0/lean-4
 .21.0-linux.tar.zst
 </observation>"""


def row_at(tmp_path, text=FAILED_DOWNLOAD):
    result = tmp_path / 'host-result.json'
    result.write_text(json.dumps(dict(success=False, api_usage=dict(complete=True, total_tokens=38390))))
    (tmp_path / 'container.stdout.log').write_text(text)
    return dict(task='lean4-proof', harness='sweagent', policy='clean_recovery',
                state='completed', expected_context='compact', result_path=str(result))


def test_wrapped_real_elan_error_and_redirect_are_recognized(tmp_path):
    row = row_at(tmp_path)
    evidence = retry.download_evidence(row)
    assert evidence['classification'] == 'elan_tool_output_download_low_speed_timeout'
    assert evidence['domain'] == 'github.com'
    assert evidence['actual_tool_observation']
    assert evidence['source_sha256'] == hashlib.sha256((tmp_path/'container.stdout.log').read_bytes()).hexdigest()


def test_model_prose_or_missing_redirect_cannot_enable_proxy(tmp_path):
    row = row_at(tmp_path, FAILED_DOWNLOAD.replace('<observation>', '').replace('</observation>', ''))
    assert retry.download_evidence(row) is None
    (tmp_path/'container.stdout.log').write_text(FAILED_DOWNLOAD.split('</observation>', 1)[0]+'</observation>')
    assert retry.download_evidence(row) is None


def test_other_tasks_versions_successes_and_running_workers_unchanged(tmp_path):
    row = row_at(tmp_path)
    for changes in (dict(task='another-task'), dict(harness='codex'), dict(state='running')):
        assert retry.lean_toolchain_download_evidence(dict(row, **changes)) is None
    (tmp_path/'host-result.json').write_text('{"success":true}')
    assert retry.download_evidence(row) is None
    row = row_at(tmp_path, FAILED_DOWNLOAD.replace('v4.21.0', 'v4.22.0'))
    assert retry.download_evidence(row) is None


def test_finite_transport_retry_keeps_original_result_and_bill(tmp_path):
    row = row_at(tmp_path)
    before = copy.deepcopy(row)
    result_bytes = Path(row['result_path']).read_bytes()
    sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
    key = lambda value: (value['task'], value['harness'], value['policy'])
    rows = []
    added = retry.register_download_retries([row], rows, 'existing-transport-cohort', sha, key)
    assert len(added) == 1 and added[0]['download_proxy_retry']
    assert added[0]['previous_attempt_path'] == row['result_path']
    assert not retry.register_download_retries([row], rows, 'existing-transport-cohort', sha, key)
    assert row == before and Path(row['result_path']).read_bytes() == result_bytes
    assert json.loads(result_bytes)['api_usage']['total_tokens'] == 38390


def test_nonclean_summary_retains_bounded_download_proof_and_usage(tmp_path):
    from scripts.maintenance.retain_tb_summaries_20261022 import retain
    row = row_at(tmp_path)
    row['policy'] = 'temporal_checkpoint'
    result = dict(task='lean4-proof', workspace='/app', success=False, model_calls=1,
                  api_usage=dict(complete=True, total_tokens=38390))
    path = Path(row['result_path'])
    path.write_text(json.dumps(result))
    original = path.read_bytes()
    (tmp_path/'container.stdout.log').write_text(
        'private full trajectory\n'+FAILED_DOWNLOAD+
        '\nusage=Usage(completion_tokens=390,prompt_tokens=38000,total_tokens=38390)')
    retain(tmp_path, row['policy'], row['harness'], allow_test=True)
    retained = (tmp_path/'container.stdout.log').read_text()
    assert 'private full trajectory' not in retained and len(retained) < 4000
    assert 'total_tokens=38390' in retained
    assert path.read_bytes() == original
    assert retry.download_evidence(row)['actual_tool_observation']
