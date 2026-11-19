"""Check that the factorial changes original context without altering replay."""
import copy
import json
from types import SimpleNamespace

from agenttx.conversation import ConversationLog
from experiments.scripts.recovery_cross_ablation import (
    NOTICE, Treatment, handoff, history_prompt, neutral_manifest, treatment_conversation,
)


def recorded(log, step):
    log.append_turn([
        dict(role='assistant', tool_calls=[dict(id=f'c{step}',type='function',
             function=dict(name='run_shell',arguments=json.dumps(dict(cmd=f'operation-{step}'))))]),
        dict(role='tool',tool_call_id=f'c{step}',content=f'original-output-{step}')],
        step_ids=[step],tool_names=['run_shell'])


def logs():
    log=ConversationLog()
    log.seed('system','original task')
    for step in range(4):
        recorded(log,step)
    before=copy.deepcopy(log.to_dict())
    log.rewind([1,3],notice='internal policy notice',expand_dependencies=False)
    recorded(log,4)  # replay after restoring the filesystem
    return before,log.to_dict()


def test_stale_only_restores_original_observations_and_keeps_replay():
    before,aligned=logs()
    sync=treatment_conversation(before,aligned,True)
    stale=treatment_conversation(before,aligned,False)
    assert sync.active_step_ids()==[0,2,4]
    assert stale.active_step_ids()==[0,1,2,3,4]
    assert stale.generation==sync.generation==1
    assert [t.messages for t in sync.turns if t.kind=='recovery']==[
        t.messages for t in stale.turns if t.kind=='recovery']==[[dict(role='user',content=NOTICE)]]
    assert len({t.turn_id for t in stale.turns})==len(stale.turns)
    assert before['turns'][1]['status']=='active'
    assert aligned['turns'][1]['status']=='invalidated'


def test_codex_receives_full_actual_treatment_history():
    before,aligned=logs()
    sync=history_prompt(treatment_conversation(before,aligned,True),'task')
    stale=history_prompt(treatment_conversation(before,aligned,False),'task')
    assert 'original-output-1' not in sync and 'original-output-1' in stale
    assert 'original-output-3' not in sync and 'original-output-3' in stale
    assert 'original-output-4' in sync and 'original-output-4' in stale


def test_handoff_has_no_policy_or_localization_prompt_treatment():
    manifest=dict(authoritative=True,policy='causal',state_id='policy-specific-state',
        localized_fault_step_ids=[42],retained=[dict(path='notes/a',contract='A-001',origin='retained')])
    other=copy.deepcopy(manifest)
    other.update(policy='temporal_checkpoint',state_id='another-state',localized_fault_step_ids=[])
    other['retained'][0]['origin']='regenerated'
    assert handoff(manifest)==handoff(other)
    neutral=neutral_manifest(manifest)
    assert neutral['policy']=='comparison' and neutral['localized_fault_step_ids']==[]
    assert manifest['policy']=='causal'


def test_treatment_records_identical_files_and_expected_original_step_visibility(tmp_path):
    before,aligned=logs()
    target=tmp_path/'data'
    target.write_text('valid preserved file')
    for synchronized in (True,False):
        treatment=Treatment('agenttx' if synchronized else 'filesystem_only',tmp_path)
        tx=SimpleNamespace(conversation=ConversationLog.from_dict(aligned),workspace=tmp_path,
            _overlay_entry=lambda p: target,_persist=lambda:None)
        treatment.tx=tx
        treatment.before=before
        treatment.injected=dict(faulty_path='data',derived_paths=[],independent_steps=[0,2],
            oracle_invalid_steps=[1,3],docs_before=[],docs_after=[])
        treatment.targets=[1,3]
        treatment.rollback_ids=[1,3]
        treatment.apply()
        proof=json.loads((tmp_path/'cross-treatment.json').read_text())
        assert proof['filesystem_before']==proof['filesystem_after']
        assert proof['visible_original_step_ids']==([0,2] if synchronized else [0,1,2,3])
        assert target.read_text()=='valid preserved file'


def test_contribution_attribution_preserves_interaction_and_negative_effects():
    from experiments.scripts.analyze_recovery_cross_ablation import decompose
    values=dict(no_rollback=100,context_only=80,filesystem_only=70,agenttx=40)
    d=decompose(values)
    assert d['filesystem_saved_tokens']==35
    assert d['context_saved_tokens']==25
    assert d['filesystem_saved_tokens']+d['context_saved_tokens']==d['total_saved_tokens']==60
    assert d['nonadditivity_tokens']==10
    values.update(context_only=130,agenttx=90)
    d=decompose(values)
    assert d['context_saved_tokens']==-25
    assert d['filesystem_share_pct']+d['context_share_pct']==100


def test_file_disabled_arms_retain_dirty_files_and_only_context_arm_localizes(tmp_path):
    original,_=logs()
    path=tmp_path/'data'
    path.write_text('injected faulty state')
    injected=dict(faulty_path='data',derived_paths=[],independent_steps=[0,2],
                  oracle_invalid_steps=[1,3],docs_before=[],docs_after=[])
    for variant in ('no_rollback','context_only'):
        tx=SimpleNamespace(conversation=ConversationLog.from_dict(original),workspace=tmp_path,
            _overlay_entry=lambda p:path,_persist=lambda:None)
        applied=[]
        def apply(plan):
            applied.append(plan)
            path.write_text('restored file')
            return list(plan.rollback_targets)
        tx.apply_recovery_plan=apply
        calls=[]
        def localize(agent, injected):
            calls.append(True)
            return tx.apply_recovery_plan(SimpleNamespace(verified=True,rollback_targets=[1,3])),{}
        treatment=Treatment(variant,tmp_path/variant)
        treatment.localize=localize
        result=treatment.capture(lambda *args:([],{}))(SimpleNamespace(harness=SimpleNamespace(tx=tx)),injected)
        assert result[0]==[] and applied==[]
        assert calls==([True] if variant=='context_only' else [])
        assert tx.apply_recovery_plan is apply
        # Native file/context consistency checks happen before this handoff.
        assert tx.conversation.active_step_ids()==[0,1,2,3]
        treatment.apply()
        assert tx.conversation.active_step_ids()==([0,2] if variant=='context_only' else [0,1,2,3])
        assert path.read_text()=='injected faulty state'
        assert treatment.proof['file_disabled_preserves_injected_files']
        assert treatment.proof['rollback_ids']==[]
        assert treatment.proof['selected_causal_ids']==([1,3] if variant=='context_only' else [])


def test_neutral_handoff_does_not_claim_dirty_files_were_restored():
    prompt=handoff(dict(authoritative=True,retained=[],recreate_required=[]))
    assert 'restoration completed' not in prompt.lower()
    assert 'recovery has already run' not in prompt.lower()
    assert 'restor' not in NOTICE.lower()


def test_file_component_handoff_preserves_the_same_file_report_without_a_step_hint():
    prompt=handoff(dict(authoritative=True,recovery_input='file',problem_files=['src/a.py'],
                        reference_checkpoint_step=-1,retained=[],recreate_required=[]))
    assert 'src/a.py' in prompt and 'Reference checkpoint: -1' in prompt
    assert 'faulty step' not in prompt and 'Known faulty' not in prompt


def test_three_arm_cost_comparison_excludes_failures_and_preserves_negative_benefits():
    from experiments.scripts.analyze_rq5_components import compare
    def row(tokens,success=True):
        return dict(success=success,complete_usage=True,state='completed',tokens=tokens,duration_s=1.)
    rows={'a':dict(context_only=row(100),filesystem_only=row(60),agenttx=row(80)),
          'failed':dict(context_only=row(1000),filesystem_only=row(1000),agenttx=row(10,False))}
    fs=compare(rows,'context_only')
    context=compare(rows,'filesystem_only')
    assert fs['pairs']==context['pairs']==1
    assert fs['saved_tokens']==20 and fs['saving_pct']==20.
    assert context['saved_tokens']==-20


def test_native_swe_projector_sees_registered_context_treatment():
    from agenttx.agents.swe_agent import SWEAgentHistoryProjector, STEP_IDS_KEY
    before,aligned=logs()
    history=[dict(role='user',content=f'observation-{i}',**{STEP_IDS_KEY:[i]}) for i in range(4)]
    for sync in (True,False):
        conversation=treatment_conversation(before,aligned,sync)
        adapter=SimpleNamespace(harness=SimpleNamespace(tx=SimpleNamespace(conversation=conversation)),
                                recovery_context_variant='full',agent=SimpleNamespace(name='test'))
        visible=SWEAgentHistoryProjector(adapter)(history)
        assert any(m.get('content')=='observation-1' for m in visible)==(not sync)
        assert any(m.get('content')=='observation-2' for m in visible)


def test_micro_launch_preserves_original_native_namespace_contract(tmp_path):
    from experiments.scripts.run_recovery_cross_ablation import micro_command
    (tmp_path/'registration.json').write_text(json.dumps(dict(micro_reference=dict(
        python_mounts=[],image_id='pinned-reference-image'))))
    command=micro_command(tmp_path,dict(trace_id='trace',harness='sweagent',variant='agenttx'),
                          tmp_path/'probe')
    assert 'AGENTTX_MOTIVATION_BWRAP=1' in command
    assert 'SWE_AGENT_LOG_STREAM_LEVEL=DEBUG' in command
    assert 'pinned-reference-image' in command
