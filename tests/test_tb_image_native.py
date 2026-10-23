"""Task-image isolation, immutable image IDs, and no gold/projected workspaces."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.scripts import bench_tb_image_native as native


def test_timeout_usage_keeps_deduplicated_lower_bound_and_unknown_total(tmp_path):
    folder=tmp_path/'session/upperdir/app/.agenttx-codex-probe/codex/sessions/2026/10/20'
    folder.mkdir(parents=True)
    record=dict(type='token_usage_record',payload=dict(response_id='response-1',
        usage=dict(input_tokens=100,output_tokens=20,total_tokens=120)))
    (folder/'rollout.jsonl').write_text(json.dumps(record)+'\n'+json.dumps(record)+'\n')
    row=dict(workspace='/app',model_calls=0,total_tokens=0,usage_source='stdout_jsonl')
    usage=native.logged_usage(tmp_path,'codex',row)
    assert not usage['complete'] and usage['total_tokens'] is None
    assert usage['partial_session_usage']['recorded_tokens']==120
    assert usage['partial_session_usage']['records']==1
    assert usage['partial_session_usage']['evidence'][0]['sha256']


def test_verifier_observes_transaction_state_and_preserves_failed_verdict(tmp_path):
    class PrivateView:
        def run_tool(self,name,command,**kwargs):
            assert name=='official-verifier' and command==['verifier']
            assert kwargs['timeout_s']==7
            return SimpleNamespace(stdout='private view verdict',stderr='target test failed',returncode=1)
    assert native.verify_transaction_view(PrivateView(),['verifier'],tmp_path,7)==1
    assert (tmp_path/'verifier.stdout.log').read_text()=='private view verdict'
    assert (tmp_path/'verifier.stderr.log').read_text()=='target test failed'


def test_verifier_timeout_preserves_output_and_remains_a_task_observation(tmp_path):
    class TimeoutView:
        def run_tool(self,*args,**kwargs):
            raise native.subprocess.TimeoutExpired('verifier',7,output=b'partial output',stderr=b'time limit')
    rc=native.verify_transaction_view(TimeoutView(),['verifier'],tmp_path,7)
    assert rc==124
    assert (tmp_path/'verifier.stdout.log').read_text()=='partial output'
    assert native.outcome_status(0,rc,True)==(True,'verifier_timeout')


def test_trusted_verifier_restores_original_agent_user_even_after_error():
    pool=SimpleNamespace(tool_user='original-task-user')
    class View:
        def run_tool(self,*args,**kwargs):
            assert pool.tool_user is None
            raise RuntimeError('grading error')
    tx=View();tx.pool=pool
    with pytest.raises(RuntimeError,match='grading error'):
        native.run_trusted_verifier_tool(tx,'official-verifier',['verifier'],7)
    assert pool.tool_user=='original-task-user'


def test_image_identity_rejects_nonroot_without_overriding_task_user(monkeypatch):
    monkeypatch.setattr(native.subprocess,'run',lambda *a,**k: SimpleNamespace(stdout=json.dumps([
        dict(Id='sha256:pinned',Config=dict(User='1000:1000',WorkingDir='/original'))])))
    with pytest.raises(RuntimeError,match='do not override task user'):
        native.image_identity('mutable:tag')


def test_container_preserves_image_workdir_task_interpreter_and_uses_pinned_id(tmp_path,monkeypatch):
    for name in ('profile','venv','base','swe','tiktoken','cell/causal','tests','libs'):
        (tmp_path/name).mkdir(parents=True)
    files=['python','tools/codex','tools/codex-code-mode-host','task.json','run-tests.sh','uv',
           'strace','git','libs/libunwind-ptrace.so.0','libs/libunwind-x86_64.so.8','libs/libunwind.so.8']
    for f in files:
        p=tmp_path/f;p.parent.mkdir(exist_ok=True);p.write_text('tool')
    args=argparse.Namespace(python=tmp_path/'python',profile_dir=tmp_path/'profile',
        swe_agent_root=tmp_path/'swe',codex_bin=tmp_path/'tools/codex',tiktoken_cache=tmp_path/'tiktoken',
        harness='sweagent',model='test-model',timeout=240,verifier_timeout=600,preflight_only=False)
    monkeypatch.setattr(native.subprocess,'run',lambda *a,**k: SimpleNamespace(
        stdout=json.dumps([str(tmp_path/'venv'),str(tmp_path/'base')])))
    monkeypatch.setattr(native.shutil,'which',lambda name: str(tmp_path/name))
    monkeypatch.setenv('AGENTTX_TB_PORTABLE_STRACE',str(tmp_path/'portable'))
    portable=tmp_path/'portable'
    for f in ['usr/bin/strace','usr/lib/x86_64-linux-gnu/libunwind-ptrace.so.0',
              'usr/lib/x86_64-linux-gnu/libunwind-x86_64.so.8','usr/lib/x86_64-linux-gnu/libunwind.so.8']:
        p=portable/f;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('portable')
    mounts=[]
    original=native.mount
    def mounted(cmd,src,dst=None,readonly=True):
        # Library locations are platform dependent; record those as well.
        mounts.append((str(src),str(dst or src),readonly))
        if str(src).startswith('/usr/lib/'):
            cmd.extend(['--mount',f'type=bind,src={src},dst={dst or src},readonly'])
        else: original(cmd,src,dst,readonly)
    monkeypatch.setattr(native,'mount',mounted)
    command=native.build_command(args,dict(id='sha256:immutable',workdir='/original',user='root'),
        tmp_path/'cell/causal',tmp_path/'task.json',tmp_path/'run-tests.sh',tmp_path/'tests',tmp_path/'uv')
    assert command[command.index('--workdir')+1]=='/original'
    assert 'sha256:immutable' in command
    assert not any(dst=='/original' for _,dst,_ in mounts)
    assert any(dst=='/opt/agenttx-verifier/tests' and ro for _,dst,ro in mounts)
    assert not any('solution.sh' in source for source,_,_ in mounts)
    assert not any(value.startswith('PATH=') for value in command)
    assert f'AGENTTX_WORKER_PYTHON={args.python}' in command
    assert not any(dst=='/usr/bin/git' for _,dst,_ in mounts)
    assert any('portable/usr/bin/strace' in src for src,_,_ in mounts)
    assert 'AGENTTX_TRACE_SPOOL_ROOT=/var/tmp' in command
    assert '--user' not in command
    assert '--max-turns' not in command

    args.portable_git = tmp_path/'git-bundle'
    (args.portable_git/'root').mkdir(parents=True)
    args.portable_git_wrapper = args.portable_git/'git-private-loader'
    args.portable_git_wrapper.write_text('local git')
    mounts.clear()
    command=native.build_command(args,dict(id='sha256:immutable',workdir='/original',user='root'),
        tmp_path/'cell/causal',tmp_path/'task.json',tmp_path/'run-tests.sh',tmp_path/'tests',tmp_path/'uv')
    assert any(dst=='/opt/agenttx-portable-git' and ro for _,dst,ro in mounts)
    assert any(dst=='/opt/agenttx-prerequisites/git' and ro for _,dst,ro in mounts)
    assert not any(dst in ('/lib/ld-musl-x86_64.so.1','/usr/local/bin/git','/usr/bin/git') for _,dst,_ in mounts)
    assert 'AGENTTX_TB_GIT_FALLBACK=/opt/agenttx-prerequisites/git' in command


def test_runtime_git_preserves_available_image_git(tmp_path,monkeypatch):
    monkeypatch.delenv('AGENTTX_TB_PORTABLE_GIT',raising=False)
    monkeypatch.setenv('AGENTTX_TB_GIT_FALLBACK','/missing-fallback')
    monkeypatch.setattr(native.shutil,'which',lambda _: '/usr/bin/git')
    monkeypatch.setattr(native.subprocess,'run',lambda *a,**kw:pytest.fail('must not install or replace available image Git'))
    assert native.prepare_runtime_tools(tmp_path)==dict(git='image-provided')


def test_official_verifier_exposes_redirected_diagnostics_and_preserves_exit(tmp_path):
    import subprocess
    import shlex
    setup=tmp_path/'setup.sh'
    setup.write_text('echo bootstrap\nprintf "assertion failed\\n" > '+shlex.quote(str(tmp_path/'verifier.stdout'))+
                     '\nprintf "test diagnostic\\n" > '+shlex.quote(str(tmp_path/'verifier.stderr'))+'\nexit 7\n')
    (tmp_path/'verifier.stdout').write_text('stale passing verdict')
    wrapper=tmp_path/'verify.sh'
    wrapper.write_text(native.verifier_entrypoint_script(True,tmp_path,setup))
    verdict=subprocess.run(['bash',str(wrapper)],capture_output=True,text=True)
    assert verdict.returncode==7
    assert verdict.stdout=='bootstrap\nassertion failed\n'
    assert verdict.stderr=='test diagnostic\n'
    first=next((tmp_path/'verifier-invocations').iterdir())
    assert (first/'previous.stdout').read_text()=='stale passing verdict'
    assert (first/'test.stdout').read_text()=='assertion failed\n'
    setup.write_text('echo bootstrap-failed >&2\nexit 3\n')
    verdict=subprocess.run(['bash',str(wrapper)],capture_output=True,text=True)
    assert verdict.returncode==3 and verdict.stdout==''
    assert verdict.stderr=='bootstrap-failed\n'
    assert (first/'test.stdout').read_text()=='assertion failed\n'


def test_model_handoff_replaces_internal_controller_notice_without_changing_history():
    from agenttx.conversation import ConversationLog
    log=ConversationLog();log.seed('system','original')
    log.append_turn([dict(role='assistant',content='recorded action'),dict(role='tool',content='retained observation')],
                    step_ids=[1],tool_names=['run_shell'],kind='effect')
    log.append_turn([dict(role='user',content='verifier:auto-localization-crash-verifier')],kind='recovery')
    before=log.to_dict()
    for policy in native.POLICIES:
        prompt=native.codex_recovery_prompt(log,policy,native.completion_prompt('official task'))
        assert 'retained observation' in prompt
        assert 'auto-localization-crash-verifier' not in prompt
        assert native.OFFICIAL_VERIFIER_COMMAND in prompt
        assert prompt.index('retained observation')<prompt.index('Current authoritative task')
        assert 'not shell commands' in prompt
    assert log.to_dict()==before


def test_runtime_missing_git_uses_only_local_fallback(tmp_path,monkeypatch):
    fallback=tmp_path/'fallback';fallback.write_text('local executable')
    target=tmp_path/'git'
    monkeypatch.delenv('AGENTTX_TB_PORTABLE_GIT',raising=False)
    monkeypatch.setenv('AGENTTX_TB_GIT_FALLBACK',str(fallback))
    monkeypatch.setattr(native.shutil,'which',lambda _:None)
    monkeypatch.setattr(native,'COORDINATOR_GIT',target)
    commands=[]
    monkeypatch.setattr(native.subprocess,'run',lambda command,**kw:commands.append(command))
    native.prepare_runtime_tools(tmp_path)
    assert target.is_symlink() and target.resolve()==fallback
    assert commands==[[str(fallback),'--version']]
    target.unlink();target.symlink_to(tmp_path/'absent')
    with pytest.raises(RuntimeError,match='occupied'):native.prepare_runtime_tools(tmp_path)
    assert target.is_symlink() and not target.exists()


def test_missing_official_tests_records_infrastructure_failure_before_models(tmp_path,monkeypatch):
    source=tmp_path/'task'; source.mkdir()
    args=argparse.Namespace(output_dir=tmp_path/'out',model='m',timeout=240,verifier_timeout=600,
        harness='codex',task='missing-assets',policies=['causal'],preflight_only=False,cache_dir=tmp_path)
    monkeypatch.setattr(native.tb,'load_tasks',lambda _: {'missing-assets':object()})
    monkeypatch.setattr(native.tb,'task_source',lambda *a: source)
    monkeypatch.setattr(native.tb,'ensure_official_image',lambda *a: pytest.fail('must not build or execute without official tests'))
    assert native.run_host(args)==1
    row=json.loads((args.output_dir/'summary.json').read_text())[0]
    assert row['status']=='infrastructure_error'
    assert 'assets missing' in row['error']


def test_compact_view_keeps_six_live_calls_without_changing_audit_history():
    from agenttx.conversation import ConversationLog
    conversation=ConversationLog();conversation.seed('system','original task')
    for i in range(9):
        conversation.append_turn([
            dict(role='assistant',content=f'action {i}',tool_calls=[dict(id=f'call-{i}',type='function',
                function=dict(name='run_shell',arguments=json.dumps({'cmd':f'op {i}'})))]),
            dict(role='tool',tool_call_id=f'call-{i}',content=f'result {i}')],
            step_ids=[i],tool_names=['run_shell'],kind='effect')
    conversation.rewind([7],'Recovery notice',mode='oracle',expand_dependencies=False)
    before=conversation.to_dict()
    view=native.compact_recovery_messages(conversation)
    results=[m['content'] for m in view if m.get('role')=='tool']
    assert results==[f'result {i}' for i in [2,3,4,5,6,8]]
    assert conversation.to_dict()==before


def test_named_nonroot_identity_requires_explicit_adapter_opt_in(monkeypatch):
    monkeypatch.setattr(native.subprocess,'run',lambda *a,**k:SimpleNamespace(stdout=json.dumps([
        dict(Id='sha256:pinned',Config=dict(User='alice',WorkingDir='/home/alice'))])))
    with pytest.raises(RuntimeError):native.image_identity('tag')
    assert native.image_identity('tag',allow_task_user=True)==dict(id='sha256:pinned',workdir='/home/alice',user='alice')


def test_official_inline_patch_is_preserved_without_inventing_missing_test_files(tmp_path):
    source=tmp_path/'task';source.mkdir();output=tmp_path/'out';output.mkdir()
    script="cat > /app/test_patch.diff << 'EOF'\npublic test diff\nEOF\npatch --fuzz=5 -p1 -i /app/test_patch.diff\npytest tests/test_module.py\n"
    (source/'run-tests.sh').write_text(script)
    tests,layout=native.official_tests_directory(source,output)
    assert layout=='inline_patch_in_official_script' and tests.is_dir()
    assert not list(tests.iterdir())
    assert (source/'run-tests.sh').read_text()==script


def test_task_failure_finishes_cell_without_becoming_infrastructure_failure():
    assert native.outcome_status(0,1,True)==(True,'task_or_model_failed')
    assert native.outcome_status(124,1,False)==(True,'task_or_model_failed')
    assert native.outcome_status(127,1,True)==(False,'harness_error')
    assert native.outcome_status(0,0,True)==(True,'passed')


def test_verified_timeout_is_success_but_cannot_bypass_tests_or_recovery():
    row=dict(completed=True,model_returncode=124,official_tests_rc=0,
             documents_valid=True,derived_removed=True,success=False)
    assert native.result_success(row)
    assert native.outcome_status(124,0,True)==(True,'passed_after_model_timeout')
    assert not native.result_success({**row,'official_tests_rc':1})
    assert not native.result_success({**row,'official_tests_rc':124})
    assert not native.result_success({**row,'derived_removed':False})
    assert not native.result_success({**row,'documents_valid':False})
    assert not native.result_success({**row,'model_returncode':127})
    assert row['success'] is False  # Historical result is never overwritten.


@pytest.mark.parametrize('policy',native.POLICIES)
def test_native_prompt_discloses_cleanup_even_when_fault_location_is_hidden(policy):
    from experiments.workloads.recovery_inject import render_recovery_manifest_prompt
    manifest=dict(authoritative=True,policy=policy,state_id='state',retained=[],
        recreate_required=[],invalidated=[dict(path='secret/fault.py',reason='fault',current_state='absent',must_stay_absent=False)],
        pending=['implement task','run verifier'],localized_fault_step_ids=[],oracle_fault_step_id=7)
    prompt=native.completion_prompt('Official task\n'+render_recovery_manifest_prompt(manifest))
    assert 'ensure `recovery_build/derived.txt` is absent' in prompt
    assert 'remove it if it exists' in prompt
    assert 'Preserve the protected independent recovery notes' in prompt
    if policy in ('crash_no_rollback','temporal_checkpoint','whole_branch_abort'):
        assert 'secret/fault.py' not in prompt


def test_six_arm_context_projection_preserves_full_control_history():
    from agenttx.conversation import ConversationLog
    log=ConversationLog();log.seed('system','original')
    for i in range(10):
        log.append_turn([dict(role='assistant',content='run',tool_calls=[dict(id=str(i),type='function',function=dict(name='run_shell',arguments='{}'))]),
                         dict(role='tool',tool_call_id=str(i),content=f'result {i}')],step_ids=[i],tool_names=['run_shell'],kind='effect')
    assert set(native.POLICIES)==set(native.CONTEXTS)
    for p in native.POLICIES:
        messages=native.recovery_messages(log,p)
        expected=6 if p in ('causal','ideal_oracle','clean_recovery') else 10
        assert sum(m.get('role')=='tool' for m in messages)==expected
    assert len(log.active_step_ids())==10


def test_logged_api_usage_does_not_substitute_text_estimate(tmp_path):
    (tmp_path/'container.stdout.log').write_text('usage=Usage(completion_tokens=12, prompt_tokens=88, total_tokens=100)')
    usage=native.logged_usage(tmp_path,'sweagent',dict(model_calls=1,total_tokens=76))
    assert usage['complete'] and usage['total_tokens']==100
    assert not native.logged_usage(tmp_path,'sweagent',dict(model_calls=2))['complete']


def test_network_baseline_preserves_hosts_before_localhost_preflight(tmp_path,monkeypatch):
    calls=[]
    pool=SimpleNamespace(try_bin=Path('/try'),session_dir=tmp_path/'session',
        refresh_summary=lambda:None,upperdir_digests=lambda:{'baseline':'preserved'})
    harness=SimpleNamespace(tx=SimpleNamespace(pool=pool),workdir=tmp_path)
    monkeypatch.setattr(native.shutil,'copyfile',lambda *args:None)
    monkeypatch.setattr(native,'file_hash',lambda path:'source-hash')
    def run(command,**kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0,stdout='127.0.0.1 STREAM localhost',stderr='')
    monkeypatch.setattr(native.subprocess,'run',run)
    evidence=native.prime_resolver(harness)
    assert [c[-1] for c in calls[:2]]==['/etc/resolv.conf','/etc/hosts']
    assert calls[-1][-3:]==['getent','ahostsv4','localhost']
    assert evidence['localhost_resolution'] and set(evidence['files_sha256'])=={'hosts','resolv.conf'}
    assert pool._cached_digests=={'baseline':'preserved'}
