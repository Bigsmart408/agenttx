from experiments.scripts import run_tb_dual_local as runner


def test_user_validation_hold_blocks_new_full_dispatch_even_with_invalid_metadata(tmp_path,monkeypatch):
    hold=tmp_path/'hold.json';monkeypatch.setattr(runner,'FULL_DISPATCH_HOLD',hold)
    assert not runner.full_dispatch_blocked()
    hold.write_text('invalid metadata cannot authorize a full run')
    assert runner.full_dispatch_blocked()
    assert not runner.full_dispatch_blocked(canary=True)


def test_worker_classification_does_not_require_task_success():
    row=dict(completed=True,success=False,context_variant='full',
             agent_runtime='official_task_container',verifier_runtime='same_official_task_container',
             conversation_matches_workspace=True,api_usage=dict(complete=True))
    assert runner.infrastructure_issue(row,'full') is None
    assert runner.infrastructure_issue(row,'compact')=='recovery_context_mismatch'
    row['api_usage']['complete']=False
    assert runner.infrastructure_issue(row,'full')=='incomplete_api_usage'


def test_both_harnesses_get_same_limits_and_six_arm_treatments(tmp_path):
    from pathlib import Path
    commands=[]
    for h in ('sweagent','codex'):
        command=runner.cell_command(tmp_path,tmp_path/'cell',dict(task='hello-world',harness=h,policy='ideal_oracle'),None)
        assert command[command.index('--timeout')+1]=='3600'
        assert command[command.index('--verifier-timeout')+1]=='1800'
        assert runner.CELL_TIMEOUT_S>2*3600+1800+300
        assert '--max-turns' not in command
        assert command[command.index('--policies')+1]=='ideal_oracle'
        commands.append(command)
    commands[0][commands[0].index('--harness')+1]='codex'
    assert commands[0]==commands[1]


def test_only_recognized_model_timeout_with_missing_usage_can_continue():
    result=dict(model_returncode=124,completed=True,status='task_or_model_failed')
    assert not runner.halts_dispatch('incomplete_api_usage',result)
    assert not runner.halts_dispatch('incomplete_api_usage',{**result,'status':'passed_after_model_timeout'})
    assert runner.halts_dispatch('incomplete_api_usage',{**result,'model_returncode':0})
    assert runner.halts_dispatch('conversation_workspace_mismatch',result)
    assert runner.halts_dispatch('worker_wall_timeout',result)
    assert runner.halts_dispatch('incomplete_api_usage',{})


def test_resume_keeps_success_failure_and_infrastructure_observations_without_retry(tmp_path):
    import pytest
    cells=[dict(task=str(i),harness='codex',policy='causal',state='pending',expected_context='compact') for i in range(4)]
    old=[]
    for i,state in enumerate(['completed','completed','infrastructure_error','pending']):
        row={**cells[i],'state':state}
        if state!='pending':
            p=tmp_path/str(i);p.write_text('{}')
            row.update(result_path=str(p),success=i==0)
        old.append(row)
    inherited=runner.inherit_terminal_cells(cells,old)
    assert [r['state'] for r in inherited]==['completed','completed','infrastructure_error','pending']
    assert inherited[:3]==old[:3]
    inherited[0]['success']=False
    assert old[0]['success'] is True
    old[-1]['state']='running'
    with pytest.raises(AssertionError,match='reconciled'):
        runner.inherit_terminal_cells(cells,old)


def test_provider_output_budget_exit_needs_structured_terminal_evidence(tmp_path):
    import json
    result=dict(harness='codex',completed=True,status='task_or_model_failed',
                model_returncode=1,container_returncode=0)
    path=tmp_path/'host-result.json'
    terminal=dict(type='turn.failed',error=dict(message='Incomplete response returned, reason: max_output_tokens'))
    model=tmp_path/'model-result.json'
    model.write_text(json.dumps(dict(returncode=1,events=[terminal])))
    evidence=runner.model_budget_failure_evidence(result,path)
    assert evidence['model_result_sha256']
    assert evidence['exclude_from_complete_cost_comparisons']
    assert not runner.halts_dispatch('incomplete_api_usage',result,evidence)
    assert runner.halts_dispatch('incomplete_api_usage',result)
    assert runner.halts_dispatch('conversation_workspace_mismatch',result,evidence)
    assert runner.model_budget_failure_evidence({**result,'container_returncode':1},path) is None
    for events in [
        [dict(type='item.completed',item=dict(aggregated_output=json.dumps(terminal)))],
        [terminal,dict(type='turn.completed')],
        [dict(type='turn.failed',error=dict(message='connection failed'))],
    ]:
        model.write_text(json.dumps(dict(returncode=1,events=events)))
        assert runner.model_budget_failure_evidence(result,path) is None


def test_changed_limits_and_contract_require_explicit_registered_transition():
    import pytest
    prior=dict(model='gpt-5.6-luna',reasoning_effort='max',fault_seed='20261019',contexts=runner.CONTEXTS,
               model_session_wall_s=900,verifier_wall_s=900,cell_wall_s=3000)
    with pytest.raises(AssertionError,match='allow-protocol-update'):
        runner.resume_protocol_changes(prior,False)
    changes=runner.resume_protocol_changes(prior,True)
    assert changes['model_session_wall_s']==dict(previous=900,current=3600)
    assert 'completion_contract' in changes
    with pytest.raises(AssertionError,match='model'):
        runner.resume_protocol_changes({**prior,'model':'different-model'},True)


def test_effects_rescore_timeout_without_pooling_incompatible_prompt_or_budget(tmp_path):
    import json
    cells=[]
    for i,(policy,current,timeout,tokens) in enumerate([
        ('causal',False,True,100),('temporal_checkpoint',False,False,200),
        ('whole_branch_abort',True,False,80)]):
        path=tmp_path/str(i)/'host-result.json';path.parent.mkdir()
        result=dict(completed=True,model_returncode=124 if timeout else 0,
            official_tests_rc=0,official_success=True,documents_valid=True,derived_removed=True,
            success=not timeout,api_usage=dict(total_tokens=tokens,complete=True))
        if current:
            result.update(runner.experiment_configuration())
            result['model_session_wall_s']=float(result['model_session_wall_s'])
            result['verifier_wall_s']=float(result['verifier_wall_s'])
        else:result.update(protocol='agenttx.tb-image-native-recovery/v4')
        path.write_text(json.dumps(result))
        cells.append(dict(task='task',policy=policy,harness='codex',state='completed',result_path=str(path)))
    original=[open(c['result_path']).read() for c in cells]
    report=runner.effects(cells,runner.experiment_configuration())['codex']
    assert report['by_policy']['causal']['success']==1
    assert len(report['configuration_groups'])==2
    assert 'temporal_checkpoint' not in report['paired_success']
    assert report['paired_success']['whole_branch_abort']['both_success_pairs']==0
    old=next(v for v in report['configuration_groups'].values() if v['configuration']['protocol'].endswith('/v4'))
    assert old['paired_success']['temporal_checkpoint']['saving_pct']==50.0
    assert [open(c['result_path']).read() for c in cells]==original
