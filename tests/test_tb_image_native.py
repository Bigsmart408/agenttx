"""Task-image isolation, immutable image IDs, and no gold/projected workspaces."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.scripts import bench_tb_image_native as native


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
    assert '--user' not in command
    assert '--max-turns' not in command


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
