import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from tb_shutdown_evidence_20261026 import shutdown_evidence


def test_only_successful_enforcement_of_exact_attempt_matches(tmp_path):
    directory = tmp_path / 'tb-live-monitor-20261026/shutdown-releases'
    directory.mkdir(parents=True)
    path = directory / 'one.json'
    proof = dict(affected_result_path='/attempt-one/host-result.json', enforcement_returncode=1)
    path.write_text(json.dumps(proof))
    row = dict(result_path=proof['affected_result_path'])
    assert shutdown_evidence(row, tmp_path) is None
    proof['enforcement_returncode'] = 0
    path.write_text(json.dumps(proof))
    before = path.read_bytes()
    result = shutdown_evidence(row, tmp_path)
    assert result['evidence_sha256'] and result['evidence_path'] == str(path)
    assert shutdown_evidence(dict(result_path='/attempt-two/host-result.json'), tmp_path) is None
    assert path.read_bytes() == before
