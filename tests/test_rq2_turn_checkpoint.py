from types import SimpleNamespace

import pytest

from experiments.scripts.bench_official_tasks import POLICY_MODES, _apply_blind_policy
from experiments.scripts.bench_tb_image_native import CONTEXTS


def test_turn_control_removes_whole_affected_turns_without_expanding_dependencies():
    calls = []
    tx = SimpleNamespace(
        conversation=SimpleNamespace(turns=[
            SimpleNamespace(turn_id=4, step_ids=[0, 1]),
            SimpleNamespace(turn_id=5, step_ids=[2, 3]),
            SimpleNamespace(turn_id=6, step_ids=[4, 5]),
            SimpleNamespace(turn_id=7, step_ids=[6]),
        ]),
        rollback_turns=lambda turn_ids: calls.append(turn_ids) or [0, 1, 4, 5],
    )
    agent = SimpleNamespace(harness=SimpleNamespace(tx=tx))
    targets, details = _apply_blind_policy(agent, {'oracle_invalid_steps': [1, 4]}, 'turn_checkpoint')
    assert calls == [[4, 6]]
    assert targets == [0, 1, 4, 5]
    assert details['minimum_complete_turn_set']
    assert details['hinted_affected_step_ids'] == [1, 4]
    assert not details['fault_location_disclosed']


def test_turn_control_rejects_unrepresented_affected_call():
    tx = SimpleNamespace(conversation=SimpleNamespace(turns=[SimpleNamespace(turn_id=0, step_ids=[0])]))
    agent = SimpleNamespace(harness=SimpleNamespace(tx=tx))
    with pytest.raises(RuntimeError, match='no recorded turn'):
        _apply_blind_policy(agent, {'oracle_invalid_steps': [1]}, 'turn_checkpoint')


def test_turn_control_is_registered_for_real_world_tasks_with_full_context():
    assert 'turn_checkpoint' in POLICY_MODES
    assert CONTEXTS['turn_checkpoint'] == 'full'
