import copy
import datetime
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import run_tb_lean_stalled_download_20261027 as q


def scenario(tmp_path):
    original = tmp_path/'original.json'
    original.write_text('{"success":false}')
    terminal = tmp_path/'host-result.json'
    terminal.write_text(json.dumps(dict(completed=False, api_usage=dict(complete=False, known_lower_bound_tokens=115177))))
    first = dict(task='lean4-proof', harness='sweagent', policy='crash_no_rollback',
                 state='completed', expected_context='full', result_path=str(original))
    running = dict(first, state='running', worker_pid=123, attempt=1, result_path=str(terminal))
    start = datetime.datetime(2026, 10, 26, 17, 47, tzinfo=datetime.timezone.utc)
    samples = [dict(at=(start+datetime.timedelta(seconds=s)).isoformat(), pid=2260,
                    bytes=6091201, io='unchanged counters', wchan='do_poll.constprop.0',
                    mtime=start.timestamp()-1810) for s in (0, 90)]
    proof = dict(dependency='leanprover/lean4:v4.21.0',
                 command=['elan','toolchain','install','leanprover/lean4:v4.21.0'],
                 enforcement_returncode=0, worker_stopped=True, samples=samples,
                 source_snapshots=[dict(path=str(terminal), sha256=q.sha(terminal))],
                 result_path=str(terminal), terminal_result_sha256=q.sha(terminal),
                 worker_pid=123, finished_at=samples[-1]['at'], worker_returncode=0)
    return [first], [running], proof


def test_only_stalled_worker_gets_one_scoped_retry_and_cost_stays(tmp_path):
    cells, retries, proof = scenario(tmp_path)
    unaffected = dict(task='other', harness='codex', policy='causal', state='running', worker_pid=999)
    retries.append(unaffected)
    raw = Path(proof['result_path']).read_bytes()
    q.apply_stall_interruption(cells, retries, proof)
    assert unaffected == dict(task='other', harness='codex', policy='causal', state='running', worker_pid=999)
    assert retries[0]['state'] == 'infrastructure_error'
    assert retries[-1]['download_proxy_retry'] and retries[-1]['attempt'] == 2
    assert retries[-1]['expected_context'] == 'full'
    assert Path(proof['result_path']).read_bytes() == raw
    assert json.loads(raw)['api_usage']['known_lower_bound_tokens'] == 115177


@pytest.mark.parametrize('change', ['recent_write', 'changing_io', 'already_proxy'])
def test_no_replacement_without_full_stall_and_finite_transport_proof(tmp_path, change):
    cells, retries, proof = scenario(tmp_path)
    if change == 'recent_write':
        proof['samples'][0]['mtime'] = proof['samples'][1]['mtime'] = datetime.datetime.fromisoformat(proof['samples'][0]['at']).timestamp()-100
    elif change == 'changing_io':
        proof['samples'][1]['io'] = 'more bytes'
    else:
        retries[0]['download_proxy_retry'] = True
    before = copy.deepcopy((cells, retries))
    with pytest.raises(AssertionError):
        q.apply_stall_interruption(cells, retries, proof)
    assert (cells, retries) == before
