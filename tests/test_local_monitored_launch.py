import pytest

from experiments.scripts.run_swe_monitored_local import infrastructure_issue, launch_window, inherit_terminal_cells


def test_continuation_reuses_valid_failures_and_repeats_environment_invalid_task_for_every_policy(tmp_path):
    cells=[dict(task=t,policy=p,state='pending') for t in ('valid','invalid','untouched')
           for p in ('causal','whole_branch_abort')]
    previous=dict(tasks=[dict(task=t,policy=p,state='completed',success=False)
                         for t in ('valid','invalid') for p in ('causal','whole_branch_abort')])
    assert inherit_terminal_cells(cells,previous,tmp_path/'prior',{'invalid'})==2
    assert all(r['state']=='completed' and r['success'] is False and r['inherited_from']=='prior'
               for r in cells if r['task']=='valid')
    assert all(r['state']=='pending' and 'inherited_from' not in r
               for r in cells if r['task'] in ('invalid','untouched'))


def test_full_suite_continues_past_five_canary_cells_at_eight_workers():
    cells=[dict(state='completed') for _ in range(5)]+[dict(state='pending') for _ in range(1495)]
    assert launch_window(True,5,cells)==(8,1500)
    assert launch_window(False,5,cells)==(4,5)


def test_default_launch_still_runs_clean_before_other_policies():
    cells=[dict(state='pending') for _ in range(1500)]
    assert launch_window(False,0,cells)==(1,1)
    cells[0]['state']='running'
    assert launch_window(False,1,cells)==(1,1)
    cells[0]['state']='completed'
    assert launch_window(False,1,cells)==(4,5)


def valid_result(**updates):
    row=dict(environment_error=False,harness_returncode=0,usage_source='sweagent_model_stats',
             total_tokens=3200,tests_rc=1,host_leak_before_commit=False,
             fault_location_disclosed=False,mode='causal',recovery_manifest_authoritative=True,
             success=False)
    return dict(row,**updates)


def test_real_task_failure_does_not_trigger_infrastructure_stop():
    assert infrastructure_issue(valid_result()) is None


@pytest.mark.parametrize('updates,reason',[
    ({'environment_error':True},'environment_error'),
    ({'harness_returncode':127},'harness_unavailable'),
    ({'usage_source':'none'},'missing_model_usage'),
    ({'total_tokens':0},'missing_model_usage'),
    ({'tests_rc':'error'},'official_verifier_missing'),
    ({'host_leak_before_commit':True},'host_write_leak'),
    ({'fault_location_disclosed':True},'fault_location_disclosed'),
    ({'recovery_manifest_authoritative':False},'recovery_state_not_authoritative'),
])
def test_monitor_stops_on_invalid_infrastructure_even_if_success_marked(updates,reason):
    assert infrastructure_issue(valid_result(success=True,**updates))==reason


def test_clean_control_does_not_require_crash_recovery_manifest():
    assert infrastructure_issue(valid_result(mode='clean_recovery',recovery_manifest_authoritative=False)) is None


def test_agent_side_verifier_error_stops_even_when_final_verifier_ran():
    row=valid_result(tests_rc=1,harness_returncode=1,error='RecoveryProtectionError: partial sandbox')
    assert infrastructure_issue(row,'Cannot connect to the Docker daemon')=='sandbox_docker_unavailable'
    assert infrastructure_issue(row,'strace did not produce dependency output')=='dependency_trace_missing'
    assert infrastructure_issue(valid_result(harness_stderr='SWEAgent exit_status=exit_error'))=='harness_integration_error'


def test_inferred_causal_location_is_allowed_but_oracle_labels_are_not():
    assert infrastructure_issue(valid_result(fault_location_disclosed=True,fault_location_source='agenttx_localizer')) is None
    assert infrastructure_issue(valid_result(fault_location_disclosed=True,fault_location_source='oracle'))=='fault_location_disclosed'


def test_ideal_truth_disclosure_is_allowed_only_for_registered_ideal_arm():
    row=valid_result(mode='ideal_oracle',fault_location_disclosed=True,
                     fault_location_source='oracle',oracle_fault_step_disclosed=True,
                     localization_validation='oracle_truth')
    assert infrastructure_issue(row) is None
    assert infrastructure_issue(dict(row,mode='causal'))=='fault_location_disclosed'
    assert infrastructure_issue(dict(row,oracle_fault_step_disclosed=False))=='fault_location_disclosed'


def test_context_mismatch_stops_launch_even_when_task_succeeds():
    row=valid_result(success=True,recovery_context_variant='full')
    assert infrastructure_issue(row,expected_context='full') is None
    assert infrastructure_issue(row,expected_context='compact')=='recovery_context_mismatch'


def test_full_suite_starts_eight_workers_without_serial_ideal_gate():
    cells=[dict(policy=p,state='completed') for p in
           ('clean_recovery','crash_no_rollback','temporal_checkpoint','whole_branch_abort','causal')]
    cells += [dict(policy='ideal_oracle',state='pending'),dict(policy='clean_recovery',state='pending')]
    assert launch_window(True,5,cells)==(8,7)
    cells[5]['state']='running'
    assert launch_window(True,6,cells)==(8,7)
    cells[5].update(state='completed',success=False)
    assert launch_window(True,6,cells)==(8,7)


def test_resume_preserves_original_result_path_across_multiple_continuations(tmp_path):
    cells=[dict(task='t',policy='causal',state='pending')]
    previous=dict(tasks=[dict(task='t',policy='causal',state='completed',success=False,
                             inherited_from='first',result_path='/first/result.json')])
    assert inherit_terminal_cells(cells,previous,tmp_path/'second',set())==1
    assert cells[0]['result_path']=='/first/result.json'
    assert cells[0]['success'] is False


def test_injection_repair_repeats_only_audited_cells_and_preserves_clean_result(tmp_path):
    cells=[dict(task='t',policy=p,state='pending') for p in ('clean_recovery','causal','ideal_oracle')]
    previous=dict(tasks=[dict(task='t',policy='clean_recovery',state='completed',success=True),
                         dict(task='t',policy='causal',state='infrastructure_error',success=False)])
    assert inherit_terminal_cells(cells,previous,tmp_path/'prior',set(),{('t','causal')})==1
    assert cells[0]['state']=='completed' and cells[0]['success'] is True
    assert cells[1]['state']==cells[2]['state']=='pending'


@pytest.mark.parametrize('changed_digest',['a'*64+'9','a'*16+'c'+'a'*47])
def test_agent_altered_image_argument_is_a_task_command_error(tmp_path,monkeypatch,changed_digest):
    import json
    from types import SimpleNamespace
    from experiments.scripts import run_swe_monitored_local as runner
    image='sha256:'+'a'*64
    supplied='sha256:'+changed_digest
    folder=tmp_path/'trajectories';folder.mkdir()
    path=folder/'main.traj'
    path.write_text(json.dumps(dict(trajectory=[dict(
        action='python verify_swe_workspace.py --image-id '+supplied,
        observation='No such image: '+supplied+'\nOfficial evaluator infrastructure error: missing image')])) )
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout=image+'\n'))
    audit=runner.verifier_command_audit(tmp_path,image)
    assert audit['classification']=='agent_authored_invalid_image_id'
    assert len(audit['episodes'])==1
    assert runner.infrastructure_issue(valid_result(), 'Official evaluator infrastructure error',command_audit=audit) is None
    # Ordinary task failures also count; absence of a final verifier does not.
    assert runner.infrastructure_issue(valid_result(tests_rc='error'), 'Official evaluator infrastructure error',command_audit=audit)=='agent_verifier_infrastructure_error'
    assert runner.infrastructure_issue(valid_result(), 'Cannot connect to the Docker daemon',command_audit=audit)=='sandbox_docker_unavailable'


def test_correct_image_missing_remains_an_infrastructure_error(tmp_path):
    import json
    from experiments.scripts import run_swe_monitored_local as runner
    image='sha256:'+'a'*64
    folder=tmp_path/'trajectories';folder.mkdir()
    (folder/'main.traj').write_text(json.dumps(dict(trajectory=[dict(
        action='python verify_swe_workspace.py --image-id '+image,
        observation='No such image: '+image+'\nOfficial evaluator infrastructure error: missing image')])))
    assert runner.verifier_command_audit(tmp_path,image) is None
    assert runner.infrastructure_issue(valid_result(), 'Official evaluator infrastructure error')=='agent_verifier_infrastructure_error'


@pytest.mark.parametrize('supplied,extra,allowed',[
    ('a'*63,'',True), ('b'*64,'',True), ('a'*64,'',False),
    ('a'*63,'\nOfficial evaluator infrastructure error: RuntimeError: real failure',False),
])
def test_model_digest_argument_identity_errors_are_audited_without_hiding_real_errors(tmp_path,monkeypatch,supplied,extra,allowed):
    import json
    from types import SimpleNamespace
    from experiments.scripts import run_swe_monitored_local as runner
    image='sha256:'+'a'*64
    folder=tmp_path/'trajectories';folder.mkdir()
    observation='Official evaluator infrastructure error: RuntimeError: official evaluator image identity mismatch'+extra
    (folder/'main.traj').write_text(json.dumps(dict(trajectory=[dict(
        action='python verify_swe_workspace.py --image-id sha256:'+supplied,observation=observation)])))
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout=image+'\n'))
    proof=runner.verifier_command_audit(tmp_path,image)
    assert bool(proof) is allowed


@pytest.mark.parametrize('success,returncode',[(False,124),(False,0),(True,0)])
def test_reading_verifier_source_does_not_turn_task_outcome_into_infrastructure_error(tmp_path,monkeypatch,success,returncode):
    import json
    from types import SimpleNamespace
    from experiments.scripts import run_swe_monitored_local as runner
    image='sha256:'+'a'*64
    folder=tmp_path/'trajectories';folder.mkdir()
    literal='        print(f"Official evaluator infrastructure error: {type(exc).__name__}: {exc}", file=sys.stderr)'
    (folder/'main.traj').write_text(json.dumps(dict(trajectory=[dict(
        action="sed -n '1,90p' /frozen/experiments/scripts/verify_swe_workspace.py",observation=literal)])))
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout=image+'\n'))
    audit=runner.verifier_command_audit(tmp_path,image)
    assert audit['classification']=='verifier_source_literal'
    row=valid_result(success=success,harness_returncode=returncode)
    assert runner.infrastructure_issue(row,literal,command_audit=audit) is None


def test_real_error_cannot_be_hidden_by_reading_verifier_source(tmp_path):
    import json
    from experiments.scripts import run_swe_monitored_local as runner
    image='sha256:'+'a'*64
    folder=tmp_path/'trajectories';folder.mkdir()
    (folder/'main.traj').write_text(json.dumps(dict(trajectory=[dict(
        action="sed -n '1,90p' verify_swe_workspace.py && python verify_swe_workspace.py",
        observation='print(f"Official evaluator infrastructure error: {type(exc).__name__}: {exc}", file=sys.stderr)\nOfficial evaluator infrastructure error: RuntimeError: broken')])) )
    assert runner.verifier_command_audit(tmp_path,image) is None


def timeout_evidence(folder):
    import json
    row=valid_result(mode='clean_recovery',recovery_manifest_authoritative=False,
        harness_returncode=1,harness_stderr='SWEAgent exit_status=exit_error',
        error='RecoveryProtectionError: external session did not finish; refusing to commit its partial sandbox',
        finished=False,official_model_wall_s=900.35,model_calls=21,prompt_tokens=3000,completion_tokens=200)
    (folder/'result.json').write_text(json.dumps(row))
    (folder/'worker.log').write_text('experiments.scripts.sweagent_native_workload._SWEAgentWallTimeout: SWEAgent workload exceeded its wall-clock timeout')
    trajectory=folder/'trajectories';trajectory.mkdir()
    (trajectory/'main.traj').write_text(json.dumps(dict(
        info=dict(exit_status='exit_error',model_stats=dict(api_calls=21,tokens_sent=3000,tokens_received=200)),
        trajectory=[dict(action='',response='Exit due to unknown error: SWEAgent workload exceeded its wall-clock timeout')])) )
    return row


def test_registered_timeout_keeps_failure_and_usage_without_stopping_queue(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from experiments.scripts import run_swe_monitored_local as runner
    row=timeout_evidence(tmp_path)
    original=(tmp_path/'result.json').read_bytes()
    image='sha256:'+'a'*64
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout=image+'\n'))
    proof=runner.verifier_command_audit(tmp_path,image)
    assert proof['classification']=='registered_session_timeout'
    assert runner.infrastructure_issue(row,command_audit=proof) is None
    assert (tmp_path/'result.json').read_bytes()==original
    assert runner.infrastructure_issue(row,'Cannot connect to the Docker daemon',command_audit=proof)=='sandbox_docker_unavailable'
    assert runner.infrastructure_issue(row,'Official evaluator infrastructure error: broken',command_audit=proof)=='agent_verifier_infrastructure_error'
    assert runner.infrastructure_issue(dict(row,total_tokens=0),command_audit=proof)=='missing_model_usage'
    assert runner.infrastructure_issue(row,expected_context='full',command_audit=proof)=='recovery_context_mismatch'


@pytest.mark.parametrize('change',['early_exit','missing_traceback','other_exception','mismatched_calls'])
def test_generic_exit_error_cannot_masquerade_as_registered_timeout(tmp_path,change):
    import json
    from experiments.scripts import run_swe_monitored_local as runner
    row=timeout_evidence(tmp_path)
    if change=='early_exit':
        row['official_model_wall_s']=40
        (tmp_path/'result.json').write_text(json.dumps(row))
    elif change=='missing_traceback':
        (tmp_path/'worker.log').write_text('SWEAgent workload exceeded its wall-clock timeout')
    else:
        path=tmp_path/'trajectories/main.traj';trajectory=json.loads(path.read_text())
        if change=='other_exception':trajectory['trajectory'][-1]['response']='Exit due to unknown error: broken integration'
        else:trajectory['info']['model_stats']['api_calls']=20
        path.write_text(json.dumps(trajectory))
    assert runner.session_timeout_audit(tmp_path) is None
    assert runner.infrastructure_issue(row)=='harness_integration_error'


def test_tb_domestic_build_preserves_source_and_has_no_origin_fallback(tmp_path,monkeypatch):
    from experiments.workloads import terminal_bench_suite as tb
    monkeypatch.setenv('AGENTTX_DOMESTIC_ONLY','1')
    monkeypatch.setenv('AGENTTX_TB_HUB_MIRROR','docker.1ms.run')
    monkeypatch.setenv('AGENTTX_TB_GHCR_MIRRORS','ghcr.nju.edu.cn')
    source=tmp_path/'ordinary-task';source.mkdir()
    original='FROM ghcr.io/example/base:v1\nRUN echo unchanged\n'
    (source/'Dockerfile').write_text(original)
    candidates=tb._dockerfile_candidates(source)
    try:
        assert len(candidates)==1 and candidates[0][1]=='domestic-only'
        assert candidates[0][0].read_text()=='FROM ghcr.nju.edu.cn/example/base:v1\nRUN echo unchanged\n'
        assert (source/'Dockerfile').read_text()==original
    finally:
        for path,_,_ in candidates:path.unlink()


def test_tb_dockerhub_base_uses_explicit_mirror(tmp_path,monkeypatch):
    from experiments.workloads import terminal_bench_suite as tb
    monkeypatch.setenv('AGENTTX_DOMESTIC_ONLY','1')
    monkeypatch.setenv('AGENTTX_TB_HUB_MIRROR','docker.1ms.run')
    source=tmp_path/'ordinary-task';source.mkdir()
    (source/'Dockerfile').write_text('FROM ubuntu:24.04\nRUN echo unchanged\n')
    candidates=tb._dockerfile_candidates(source)
    try:assert candidates[0][0].read_text().startswith('FROM docker.1ms.run/library/ubuntu:24.04\n')
    finally:
        for path,_,_ in candidates:path.unlink()


def test_coordinator_prepares_test_patch_before_reusing_hydrated_source(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from experiments.scripts import run_swe_monitored_local as runner
    from experiments.workloads import swe_bench_suite as swe
    task='example__repo-1'
    repo=tmp_path/'swe_bench/repos'/task
    repo.mkdir(parents=True)
    repo.with_name(task+'.source-verified.json').write_text(json.dumps(dict(image_id='pinned',base_commit='base')))
    monkeypatch.setattr(runner,'CACHE',tmp_path)
    monkeypatch.setattr(runner.subprocess,'check_output',lambda *a,**k:'base\n')
    monkeypatch.setattr(swe,'load_tasks',lambda *a:{task:'task-object'})
    calls=[]
    monkeypatch.setattr(swe,'ensure_repo',lambda *args:calls.append(args))
    runner.hydrate(task,'pinned','base')
    assert calls==[('task-object',tmp_path)]
