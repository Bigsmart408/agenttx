from unittest.mock import Mock

import pytest

from agenttx.ledger import Ledger, Step
from agenttx.runtime import AgentTX


@pytest.mark.parametrize("targets", [[-1], [9], [0], [2]])
def test_exact_oracle_rejects_invalid_targets_before_mutation(tmp_path, targets):
    ledger = Ledger(steps=[Step(i, "tool") for i in range(4)], committed_frontier=0)
    ledger.steps[2].status = "rolled_back"
    tx = AgentTX(workspace=tmp_path, ledger=ledger)
    tx._rollback_selected_targets = Mock()
    before = ledger.to_dict()
    with pytest.raises(ValueError):
        tx.rollback_exact(targets)
    tx._rollback_selected_targets.assert_not_called()
    assert ledger.to_dict() == before


def test_exact_oracle_deduplicates_without_expanding_captured_edges(tmp_path):
    tx = AgentTX(workspace=tmp_path, ledger=Ledger(steps=[Step(i, "tool") for i in range(4)]))
    tx._rollback_selected_targets = Mock(return_value=[0, 2])
    tx._unified_causal_targets = Mock(side_effect=AssertionError("oracle must not expand captured edges"))
    assert tx.rollback_exact([2, 0, 2]) == [0, 2]
    tx._rollback_selected_targets.assert_called_once_with([0, 2], mode="oracle")
    tx._unified_causal_targets.assert_not_called()


def test_empty_oracle_is_noop(tmp_path):
    tx = AgentTX(workspace=tmp_path)
    assert tx.rollback_exact([]) == []


def test_exact_oracle_does_not_expand_false_captured_message_edge(tmp_path):
    tx = AgentTX(workspace=tmp_path, ledger=Ledger(steps=[Step(i, "tool") for i in range(2)]))
    tx.conversation.seed("system", "task")
    tx.conversation.append_turn([
        {"role": "assistant", "content": "two operations", "tool_calls": [
            {"id": f"call-{i}", "type": "function", "function": {"name": "tool", "arguments": "{}"}}
            for i in range(2)]},
        {"role": "tool", "tool_call_id": "call-0", "content": "faulty"},
        {"role": "tool", "tool_call_id": "call-1", "content": "independent"},
    ], step_ids=[0, 1], tool_names=["tool", "tool"], kind="effect")
    tx.conversation.turns[-1].calls[1].parents = (0,)
    tx.conversation.conversation_closure = Mock(side_effect=AssertionError("oracle must not expand captured message edges"))
    tx._rewind_conversation([0], mode="oracle")
    assert tx.conversation.active_step_ids() == [1]
    tx.conversation.conversation_closure.assert_not_called()
