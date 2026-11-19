"""File reports, independent recovery checks, and real storage round trips."""
import json
from pathlib import Path

import pytest

from agenttx.conversation import ConversationLog, record_tool_record
from agenttx.file_recovery import FileRecoveryAudit, apply_file_recovery_plan
from agenttx.runtime import AgentTX


def _run(tx, command, *, reads=()):
    record = tx.run_tool('run_shell', ['bash', '-c', command],
                         extra_reads=list(reads), trace_reads=False)
    assert record.returncode == 0, record.stderr
    record_tool_record(tx.conversation, record, {'cmd': command})
    tx._persist()
    return record


@pytest.fixture
def history(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'problem.txt').write_text('base\n')
    tx = AgentTX.begin(workspace, session_dir=tmp_path / 'session', trace_reads=False)
    tx.conversation.seed('Use tools.', 'Complete the project.')
    try:
        checkpoint = _run(tx, "printf 'reference\n' > problem.txt")
        before = _run(tx, "printf 'before\n' > before.txt")
        root = _run(tx, "printf 'wrong\n' > problem.txt; printf 'result_channel_123456789'")
        independent = _run(tx, "printf 'independent\n' > keep.txt")
        derived = _run(tx, 'cat problem.txt > derived.txt', reads=[str(workspace/'problem.txt')])
        # No filesystem edge to root; the copied result establishes this edge.
        result_only = _run(tx, "printf 'result_channel_123456789' > result.txt")
        final = _run(tx, 'cat result.txt > final.txt', reads=[str(workspace/'result.txt')])
        yield tx, checkpoint, before, root, independent, derived, result_only, final
    finally:
        tx.close(destroy=True)


def test_file_report_uses_all_post_checkpoint_mutations_and_both_edge_types(history, monkeypatch):
    tx, checkpoint, before, root, independent, derived, result_only, final = history
    def no_search(*args, **kwargs):
        raise AssertionError('file recovery must not search step candidates or run a marker oracle')
    monkeypatch.setattr(AgentTX, 'plan_recovery', no_search)
    monkeypatch.setattr(AgentTX, 'run_verifier', no_search)
    outcome = tx.recover_file(['problem.txt'], checkpoint_step=checkpoint.step_id)
    assert outcome.applied and outcome.audit['valid']
    assert outcome.plan.root_step_ids == (root.step_id,)
    assert set(outcome.plan.required_step_ids) == {
        root.step_id, derived.step_id, result_only.step_id, final.step_id,
    }
    assert outcome.audit['independent_steps_discarded'] == []
    assert outcome.audit['residual_step_ids'] == []
    assert set(tx.conversation.active_step_ids()) == {checkpoint.step_id, before.step_id, independent.step_id}
    # Assert actual application state independently of the report/ledger graph.
    checked = tx.run_tool('inspect', ['bash', '-c',
        'cat problem.txt before.txt keep.txt; test ! -e derived.txt; '
        'test ! -e result.txt; test ! -e final.txt'], trace_reads=False)
    assert checked.returncode == 0
    assert checked.stdout == 'reference\nbefore\nindependent\n'


@pytest.mark.parametrize('policy', ['causal', 'temporal_checkpoint', 'whole_branch_abort', 'turn_checkpoint'])
def test_each_policy_is_audited_against_the_same_frozen_scope(history, policy):
    tx, checkpoint, *_ = history
    plan = tx.plan_file_recovery(['problem.txt'], checkpoint_step=checkpoint.step_id, policy=policy)
    audit = FileRecoveryAudit.capture(tx, plan)
    with tx.fork() as branch:
        apply_file_recovery_plan(branch, plan)
        checked = audit.check(branch)
    assert checked['valid'], checked
    assert all(step.status != 'rolled_back' for step in tx.ledger.steps)
    if policy in {'temporal_checkpoint', 'whole_branch_abort'}:
        assert checked['independent_steps_discarded']


def test_selected_effects_exposes_command_context_and_physical_residue(history):
    tx, checkpoint, _, root, _, derived, result_only, final = history
    plan = tx.plan_file_recovery(['problem.txt'], checkpoint_step=checkpoint.step_id,
                                policy='selected_effects')
    audit = FileRecoveryAudit.capture(tx, plan)
    with tx.fork() as branch:
        apply_file_recovery_plan(branch, plan)
        checked = audit.check(branch)
    assert not checked['valid']
    assert checked['residual_step_ids'] == [derived.step_id, result_only.step_id, final.step_id]
    assert checked['residual_context_step_ids'] == checked['residual_step_ids']
    assert str(tx.workspace/'derived.txt') in checked['file_state_mismatches']
    assert (root.step_id, derived.step_id) in checked['dangling_dependency_edges']
    outcome = tx.recover_file(['problem.txt'], checkpoint_step=checkpoint.step_id,
                              policy='selected_effects')
    assert not outcome.applied
    assert tx.ledger.steps[root.step_id].status != 'rolled_back'


def test_audit_rejects_ledger_only_rollback(history):
    tx, checkpoint, *_ = history
    plan = tx.plan_file_recovery(['problem.txt'], checkpoint_step=checkpoint.step_id)
    audit = FileRecoveryAudit.capture(tx, plan)
    with tx.fork() as branch:
        branch.ledger.mark_rolled_back(plan.rollback_targets)
        branch.conversation.rewind(plan.rollback_targets, 'rollback', expand_dependencies=False)
        checked = audit.check(branch)
    assert not checked['valid']
    assert checked['residual_step_ids'] == []
    assert checked['file_state_mismatches']


def test_audit_rejects_stale_context_after_physical_rollback(history):
    tx, checkpoint, *_ = history
    plan = tx.plan_file_recovery(['problem.txt'], checkpoint_step=checkpoint.step_id)
    audit = FileRecoveryAudit.capture(tx, plan)
    with tx.fork() as branch:
        stale = branch.conversation.to_dict()
        apply_file_recovery_plan(branch, plan)
        branch.conversation = ConversationLog.from_dict(stale)
        checked = audit.check(branch)
    assert not checked['valid']
    assert checked['file_state_mismatches'] == []
    assert checked['residual_context_step_ids']


def test_multiple_writes_are_scoped_by_checkpoint_without_guessing_faulty_step(history):
    tx, checkpoint, *_ = history
    another = _run(tx, "printf 'another\n' > problem.txt")
    plan = tx.plan_file_recovery(['problem.txt'], checkpoint_step=checkpoint.step_id)
    assert another.step_id in plan.root_step_ids
    outcome = tx.recover_file(['problem.txt'], checkpoint_step=checkpoint.step_id)
    assert outcome.audit['valid'] and outcome.applied


def test_initial_reference_survives_discarded_infrastructure_probe(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'problem.txt').write_text('base\n')
    tx = AgentTX.begin(workspace, session_dir=tmp_path/'session', trace_reads=False)
    try:
        probe = _run(tx, "printf 'probe\n' > problem.txt")
        tx.rollback(probe.step_id)
        assert not (tx.pool.layers.root / 'before_0000').exists()
        root = _run(tx, "printf 'wrong\n' > problem.txt")
        keep = _run(tx, "printf 'keep\n' > keep.txt")
        count = len(list(tx.pool.layers.root.glob('before_*')))
        plan = tx.plan_file_recovery(['problem.txt'], checkpoint_step=-1)
        audit = FileRecoveryAudit.capture(tx, plan)
        assert len(list(tx.pool.layers.root.glob('before_*'))) == count
        assert plan.checkpoint_step == -1 and plan.rollback_targets == (root.step_id,)
        apply_file_recovery_plan(tx, plan)
        assert audit.check(tx)['valid']
        assert tx.conversation.active_step_ids() == [keep.step_id]
        checked = tx.run_tool('inspect', ['bash', '-c', 'cat problem.txt keep.txt'], trace_reads=False)
        assert checked.stdout == 'base\nkeep\n'
    finally:
        tx.close(destroy=True)


def test_unknown_file_is_inconclusive_and_stale_plan_is_rejected(history):
    tx, checkpoint, *_ = history
    outcome = tx.recover_file(['unknown.txt'], checkpoint_step=checkpoint.step_id)
    assert not outcome.applied and not outcome.audit['valid']
    assert outcome.status == 'inconclusive'
    plan = tx.plan_file_recovery(['problem.txt'], checkpoint_step=checkpoint.step_id)
    _run(tx, "printf 'later\n' > later.txt")
    with pytest.raises(ValueError, match='stale'):
        apply_file_recovery_plan(tx, plan)
    with pytest.raises(ValueError, match='inside the workspace'):
        tx.plan_file_recovery(['../escape'])


def test_file_only_user_message_and_cli(history):
    from agenttx.cli import build_parser
    tx, checkpoint, *_ = history
    plan = tx.plan_file_recovery(['problem.txt'], checkpoint_step=checkpoint.step_id)
    prompt = plan.user_message(tx.workspace)
    assert 'problem.txt' in prompt
    assert 'root_step_ids' not in prompt and str(plan.rollback_targets) not in prompt
    args = build_parser().parse_args(['recover-file', '--session', 'session',
                                     '--checkpoint-step', str(checkpoint.step_id), 'problem.txt'])
    assert args.paths == ['problem.txt'] and args.policy == 'causal'


def test_llm_tool_accepts_a_file_report_and_defers_recovery(tmp_path):
    from agenttx.agents.llm_agent import LLMToolAgent, TOOLS
    workspace = tmp_path / 'model-workspace'
    workspace.mkdir()
    agent = LLMToolAgent(workspace, session_dir=tmp_path/'model-session', api_key='test')
    try:
        _run(agent.harness.tx, "printf 'wrong\n' > problem.txt")
        tool = next(t for t in TOOLS if t['function']['name'] == 'rollback_file')
        assert tool['function']['parameters']['required'] == ['paths']
        assert 'step_id' not in tool['function']['parameters']['properties']
        call = {'id': 'recover', 'type': 'function', 'function': {
            'name': 'rollback_file', 'arguments': json.dumps({'paths': ['problem.txt']})}}
        batch = agent._run_call_batch([call])
        assert batch['deferred_rollback'][0] == call
        result = json.loads(agent._dispatch('rollback_file', {'paths': ['problem.txt']}))
        assert result['ok'] and result['applied']
    finally:
        agent.close(destroy=True)


def test_official_causal_protocol_takes_only_file_report(history):
    from types import SimpleNamespace
    from experiments.scripts.bench_official_tasks import _file_report_policy
    tx, checkpoint, *_ = history
    with tx.fork() as branch:
        agent = SimpleNamespace(harness=SimpleNamespace(tx=branch))
        _, evidence = _file_report_policy(agent, ['problem.txt'], 'causal',
                                         checkpoint_step=checkpoint.step_id)
        assert evidence['file_recovery_audit']['valid']
        assert evidence['candidate_probe_count'] == 0
        assert evidence['fault_step_disclosed'] is False
        assert evidence['localized_root_steps'] == []
        assert evidence['problem_files'] == ['problem.txt']
        with pytest.raises(ValueError, match='exclusive'):
            _file_report_policy(agent, ['problem.txt'], 'root_only')


@pytest.mark.parametrize('mode,valid', [('root_only', False), ('crash_no_rollback', False),
    ('temporal_checkpoint', True), ('turn_checkpoint', True), ('ideal_oracle', True),
    ('whole_branch_abort', False)])
def test_controls_keep_registered_step_inputs_and_receive_a_precontinuation_audit(history, mode, valid):
    from types import SimpleNamespace
    from experiments.scripts.bench_official_tasks import _reported_recovery_policy
    tx, checkpoint, _, root, _, derived, result_only, final = history
    injected = dict(root_step=root.step_id,
                    oracle_invalid_steps=[root.step_id, derived.step_id, result_only.step_id, final.step_id])
    with tx.fork() as branch:
        agent = SimpleNamespace(harness=SimpleNamespace(tx=branch))
        targets, evidence = _reported_recovery_policy(agent, ['problem.txt'], mode,
            injected=injected, checkpoint_step=checkpoint.step_id)
        assert evidence['recovery_input'] == 'step'
        assert evidence['fault_step_disclosed']
        assert evidence['hinted_fault_step_ids'] == [root.step_id]
        assert evidence['candidate_probe_count'] == 0
        assert evidence['file_recovery_audit']['valid'] is valid
        if mode == 'root_only':
            assert targets == [root.step_id]
        if mode == 'whole_branch_abort':
            # This explicit reference is inside the uncommitted branch; branch
            # abort returns farther back and the checkpoint audit detects that.
            assert evidence['file_recovery_audit']['checkpoint_file_mismatches']


def test_causal_dispatch_does_not_read_injection_truth(history):
    from types import SimpleNamespace
    from experiments.scripts.bench_official_tasks import _reported_recovery_policy
    class ForbiddenTruth(dict):
        def __getitem__(self, key):
            raise AssertionError('causal must not read injection truth')
        def get(self, *args):
            raise AssertionError('causal must not read injection truth')
    tx, checkpoint, *_ = history
    agent = SimpleNamespace(harness=SimpleNamespace(tx=tx))
    _, evidence = _reported_recovery_policy(agent, ['problem.txt'], 'causal',
        injected=ForbiddenTruth(), checkpoint_step=checkpoint.step_id)
    assert evidence['recovery_input'] == 'file' and evidence['file_recovery_audit']['valid']


def test_step_selector_uses_supplied_hint_instead_of_file_roots(history):
    from types import SimpleNamespace
    from experiments.scripts.bench_official_tasks import _step_report_policy
    tx, checkpoint, _, root, independent, *_ = history
    agent = SimpleNamespace(harness=SimpleNamespace(tx=tx))
    targets, evidence = _step_report_policy(agent, dict(root_step=independent.step_id),
        'root_only', audit_files=['problem.txt'], checkpoint_step=checkpoint.step_id)
    assert targets == [independent.step_id]
    assert root.step_id in evidence['file_recovery_audit']['residual_step_ids']


def test_model_handoff_only_our_method_receives_file_hint_and_controls_receive_steps():
    from experiments.workloads.recovery_inject import recovery_prompt
    for mode in ['causal', 'root_only', 'turn_checkpoint', 'temporal_checkpoint',
                 'crash_no_rollback', 'whole_branch_abort', 'ideal_oracle']:
        manifest = dict(authoritative=True, policy=mode,
                        recovery_input='file' if mode == 'causal' else 'step',
                        problem_files=['src/parser.py'], hinted_fault_step_ids=[123456789],
                        localized_fault_step_ids=[987654321], oracle_fault_step_id=987654321,
                        invalidated=[dict(path='hidden-derived-file', reason='secret')],
                        post_recovery_verified=True, retained=[], recreate_required=[])
        prompt = recovery_prompt(title='Original task', context='Repository', instruction='Fix parser',
                                 docs=[], test_cmd='pytest test_parser.py', mode=mode,
                                 recovery_manifest=manifest)
        if mode == 'causal':
            assert 'src/parser.py' in prompt and '123456789' not in prompt
        else:
            assert '123456789' in prompt and 'src/parser.py' not in prompt
        assert '987654321' not in prompt and 'hidden-derived-file' not in prompt
        assert 'does not certify' in prompt
        assert 'Run the named official verifier' in prompt
        assert 'recovery_build/derived.txt' not in prompt


def test_model_receives_inconclusive_file_recovery_result(tmp_path, monkeypatch):
    from agenttx.agents.llm_agent import LLMToolAgent
    workspace = tmp_path/'workspace'
    workspace.mkdir()
    agent = LLMToolAgent(workspace, session_dir=tmp_path/'session', api_key='test', max_turns=2)
    monkeypatch.setattr(agent, '_client', lambda: None)
    observed = []
    def turn(client, messages):
        observed.append(messages)
        if len(observed) == 1:
            return '', [{'id': 'recover', 'type': 'function', 'function': {
                'name': 'rollback_file', 'arguments': '{"paths":["unknown.txt"]}'}}], {}
        return 'The file has no recorded mutation; recovery is inconclusive.', [], {}
    monkeypatch.setattr(agent, '_one_turn', turn)
    try:
        result = agent.run('There is a problem with unknown.txt.')
        assert result.finished
        payloads = [json.loads(m['content']) for m in observed[1] if m['role'] == 'tool']
        assert payloads[-1]['status'] == 'inconclusive'
        assert payloads[-1]['ok'] is False and not payloads[-1]['applied']
    finally:
        agent.close(destroy=True)


@pytest.mark.parametrize('input_kind', ['file', 'step'])
def test_report_manifest_reseals_input_and_never_certifies_task_success(input_kind):
    import hashlib
    from experiments.scripts.bench_official_tasks import _reported_recovery_manifest
    evidence = dict(recovery_input=input_kind, reference_checkpoint_step=-1,
        file_recovery_audit=dict(valid=True), fault_step_disclosed=input_kind == 'step',
        problem_files=['parser.py'], hinted_fault_step_ids=[42], hinted_affected_step_ids=[])
    manifest = _reported_recovery_manifest(dict(state_id='old', authoritative=True,
        post_recovery_verified=True, oracle_post_recovery_verified=True), evidence)
    assert not manifest['post_recovery_verified'] and not manifest['oracle_post_recovery_verified']
    assert ('problem_files' in manifest) == (input_kind == 'file')
    assert ('hinted_fault_step_ids' in manifest) == (input_kind == 'step')
    payload = {k: v for k, v in manifest.items() if k != 'state_id'}
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    assert manifest['state_id'] == hashlib.sha256(canonical.encode()).hexdigest()


def test_independent_workflow_reference_catches_a_missing_dependency(monkeypatch):
    from agenttx.ledger import EffectKind
    from experiments.scripts.bench_file_recovery import demo
    original = AgentTX.plan_file_recovery
    def missing_edge(tx, *args, **kwargs):
        derived = {s.step_id for s in tx.ledger.steps if any(
            e.path.endswith('/derived.txt') and e.kind == EffectKind.WRITE for e in s.effects)}
        for i in derived:
            tx.ledger.steps[i].parents.clear()
        for turn in tx.conversation.turns:
            for span in turn.calls:
                if span.step_id in derived:
                    span.parents.clear()
        return original(tx, *args, **kwargs)
    monkeypatch.setattr(AgentTX, 'plan_file_recovery', missing_edge)
    result = demo(['causal'])['results'][0]
    # A graph-relative audit alone cannot establish capture completeness.
    assert result['audit']['valid']
    assert not result['independent_reference']['valid']
    assert result['independent_reference']['residual_step_ids']
