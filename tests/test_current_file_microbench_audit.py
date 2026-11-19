"""The independent scorer must catch physical, ledger and context residue."""
import json
from types import SimpleNamespace

import pytest

from experiments.scripts.audit_current_file_microbench import audit_cell


@pytest.fixture
def saved_case(tmp_path):
    def write(relative, value):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    steps = tuple(SimpleNamespace(index=i, relative_path=f'step_{i:03d}.txt',
                                  parent_indices=(0,) if i == 1 else (),
                                  role='independent' if i == 2 else 'affected') for i in range(3))
    plan = SimpleNamespace(steps=steps, oracle_closure=(0, 1))
    write('session/agenttx.json', dict(workspace='/results/workspace'))
    write('recovered-ledger.json', dict(steps=[dict(step_id=i,
          status='active' if i == 2 else 'rolled_back') for i in range(3)]))
    write('recovered-conversation.json', dict(turns=[dict(calls=[dict(step_id=i,
          status='active' if i == 2 else 'rolled_back') for i in range(3)])]))
    write('result.json', dict(captured_parents={'0': [], '1': [0], '2': []},
          model_calls=0, api_requests=0, policy_input=dict(kind='file', files=['step_000.txt'],
          checkpoint_step=-1), timings=dict(policy_wall_s=.1)))
    upper = tmp_path / 'session/upperdir/results/workspace'
    upper.mkdir(parents=True)
    (upper / 'step_002.txt').write_bytes(b'artifact-002-independent\n')
    (tmp_path / 'workspace').mkdir()
    return tmp_path, upper, plan, write


def test_independent_audit_accepts_exact_recovery(saved_case):
    output, _, plan, _ = saved_case
    assert audit_cell(output, plan)['valid']


@pytest.mark.parametrize('corruption', ['residual_file', 'retained_bytes', 'ledger', 'context', 'missing_edge'])
def test_independent_audit_rejects_corruption(saved_case, corruption):
    output, upper, plan, write = saved_case
    if corruption == 'residual_file':
        (upper / 'step_001.txt').write_text('stale result')
    elif corruption == 'retained_bytes':
        (upper / 'step_002.txt').write_text('changed')
    else:
        filename = {'ledger': 'recovered-ledger.json', 'context': 'recovered-conversation.json',
                    'missing_edge': 'result.json'}[corruption]
        data = json.loads((output / filename).read_text())
        if corruption == 'ledger':
            data['steps'][0]['status'] = 'active'
        elif corruption == 'context':
            data['turns'][0]['calls'][0]['status'] = 'active'
        else:
            data['captured_parents']['1'] = []
        write(filename, data)
    assert not audit_cell(output, plan)['valid']
