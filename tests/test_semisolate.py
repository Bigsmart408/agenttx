from pathlib import Path

import pytest

import agenttx.semisolate as semisolate_module
from agenttx.layers import LayerStore
from agenttx.semisolate import SharedSemisolate


def test_inactive_view_cleanup_does_not_follow_symlinks(tmp_path):
    pool = object.__new__(SharedSemisolate)
    pool.sandbox_dir = tmp_path / "session"
    view = pool.sandbox_dir / "temproot"
    view.mkdir(parents=True)
    outside = tmp_path / "keep"
    outside.write_text("independent")
    (view / "link").symlink_to(outside)
    (view / "stale").write_text("disposable")
    pool._clear_unmounted_temproot()
    assert outside.read_text() == "independent"
    assert not list(view.iterdir())


def test_view_cleanup_refuses_attached_mount(tmp_path, monkeypatch):
    pool = object.__new__(SharedSemisolate)
    pool.sandbox_dir = tmp_path / "session"
    view = pool.sandbox_dir / "temproot"
    view.mkdir(parents=True)
    sentinel = view / "keep"
    sentinel.write_text("mounted state")
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: f"1 0 0:1 / {view}/subtree rw - tmpfs tmpfs rw\n")
    with pytest.raises(RuntimeError, match="mounted sandbox"):
        pool._clear_unmounted_temproot()
    with sentinel.open() as handle:
        assert handle.read() == "mounted state"


def test_view_cleanup_refuses_symlink_root(tmp_path):
    pool = object.__new__(SharedSemisolate)
    pool.sandbox_dir = tmp_path / "session"
    pool.sandbox_dir.mkdir()
    (pool.sandbox_dir / "temproot").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(RuntimeError, match="symlink sandbox"):
        pool._clear_unmounted_temproot()


def test_commit_cleans_stale_view_without_deleting_independent_state(tmp_path):
    from agenttx.runtime import AgentTX

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "keep"
    outside.write_text("independent")
    tx = AgentTX.begin(workdir=workspace, session_dir=tmp_path / "session", trace_reads=False)
    try:
        tx.run_tool("write", ["bash", "-c", "printf valid > result.txt"])
        view = tx.pool.session_dir / "temproot"
        view.mkdir(exist_ok=True)
        (view / "stale-link").symlink_to(outside)
        tx.commit()
        assert (workspace / "result.txt").read_text() == "valid"
        assert outside.read_text() == "independent"
    finally:
        tx.close(destroy=True)


def test_empty_filtered_commit_is_a_noop(tmp_path: Path) -> None:
    pool = SharedSemisolate(workspace=tmp_path, sandbox_dir=tmp_path / "session")
    result = pool.commit(paths=[])
    assert result.returncode == 0
    assert list(tmp_path.iterdir()) == [tmp_path / "session"]


def test_selective_commit_rejects_filesystem_root(tmp_path: Path) -> None:
    pool = SharedSemisolate(workspace=tmp_path, sandbox_dir=tmp_path / "session")
    with pytest.raises(ValueError, match="filesystem root"):
        pool.commit(paths=["/"])


def test_include_patterns_are_exact_suffixes_and_include_parents(tmp_path: Path) -> None:
    pool = SharedSemisolate(workspace=tmp_path, sandbox_dir=tmp_path / "session")
    target = tmp_path / "pkg" / "sub" / "data.txt"
    patterns = pool._include_patterns([str(target)])
    assert patterns
    assert all(pattern.endswith("$") for pattern in patterns)
    assert any(r"data\.txt$" in pattern for pattern in patterns)
    assert not any("sibling" in pattern for pattern in patterns)


def test_read_tracing_fails_closed_when_strace_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(semisolate_module.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="requires strace"):
        SharedSemisolate(
            workspace=tmp_path,
            sandbox_dir=tmp_path / "traced-session",
        )

    pool = SharedSemisolate(
        workspace=tmp_path,
        sandbox_dir=tmp_path / "untraced-session",
        trace_reads=False,
    )
    pool.close(destroy=True)


def test_try_commit_error_text_is_not_treated_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = SharedSemisolate(
        workspace=tmp_path,
        sandbox_dir=tmp_path / "session",
        trace_reads=False,
    )
    monkeypatch.setattr(
        semisolate_module.subprocess,
        "run",
        lambda *args, **kwargs: semisolate_module.subprocess.CompletedProcess(
            args[0],
            0,
            "",
            "try: couldn't commit /tmp/example",
        ),
    )

    result = pool.commit()

    assert result.returncode == 1


def test_try_starts_in_workspace_when_inherited_pwd_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    caller = tmp_path / "caller"
    workspace = tmp_path / "workspace"
    caller.mkdir()
    workspace.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setenv("PWD", str(caller))
    pool = SharedSemisolate(
        workspace=workspace,
        trace_reads=False,
        persistent_worker=False,
    )

    try:
        result = pool.run(["bash", "-c", "pwd"])
    finally:
        pool.close(destroy=True)

    assert result.returncode == 0
    assert result.stdout.strip() == str(workspace)


def test_snapshots_deduplicate_unchanged_files_without_aliasing(
    tmp_path: Path,
) -> None:
    upper = tmp_path / "upper"
    upper.mkdir()
    target = upper / "target.txt"
    target.write_text("old\n", encoding="utf-8")
    layers = LayerStore(tmp_path / "layers")

    before_zero = layers.snapshot_before(0, upper)
    snapshot_target = before_zero / "target.txt"
    assert snapshot_target.read_text(encoding="utf-8") == "old\n"
    assert snapshot_target.stat().st_ino != target.stat().st_ino

    target.write_text("new\n", encoding="utf-8")
    before_one = layers.snapshot_before(1, upper)
    assert (before_one / "target.txt").read_text(encoding="utf-8") == "new\n"
    assert snapshot_target.read_text(encoding="utf-8") == "old\n"
    assert len(list((tmp_path / "layers" / "blobs").glob("*/*"))) == 2

    layers.drop_from([0])
    assert snapshot_target.exists() is False
    assert len(list((tmp_path / "layers" / "blobs").glob("*/*"))) == 1


def test_worker_startup_diagnostics_are_preserved(tmp_path, monkeypatch):
    from agenttx.runtime import AgentTX
    workspace = tmp_path/'workspace'; workspace.mkdir()
    tx = AgentTX.begin(workdir=workspace, session_dir=tmp_path/'session', trace_reads=False)
    monkeypatch.setenv('AGENTTX_WORKER_PYTHON', '/missing-agenttx-worker-python')
    try:
        with pytest.raises(RuntimeError, match='refusing automatic replay'):
            tx.run_tool('must-not-run', ['bash','-c','touch unintended'])
        assert 'missing-agenttx-worker-python' in (tmp_path/'session/worker.stderr.log').read_text()
        assert not (workspace/'unintended').exists()
    finally:
        tx.close(destroy=True)


def test_declared_readonly_infrastructure_survives_overlay_and_rejects_writes(tmp_path, monkeypatch):
    import os
    from agenttx.runtime import AgentTX
    workspace = tmp_path/'workspace'; workspace.mkdir()
    infrastructure = tmp_path/'immutable-tool-config'
    infrastructure.write_text('original')
    monkeypatch.setenv('TRY_BIND_READONLY', ':'.join(filter(None,
        [os.environ.get('TRY_BIND_READONLY'),str(infrastructure)])))
    tx = AgentTX.begin(workdir=workspace, session_dir=tmp_path/'session', trace_reads=True)
    try:
        record = tx.run_tool('read', ['cat', str(infrastructure)])
        assert record.returncode == 0 and record.stdout == 'original'
        denied = tx.run_tool('write', ['bash','-c',f'printf changed > {infrastructure}'])
        assert denied.returncode != 0
        written = tx.run_tool('task-write', ['bash','-c','printf repair > task.txt'])
        assert written.returncode == 0
        tx.rollback(written.step_id)
        assert infrastructure.read_text() == 'original'
        assert not tx.path_exists(workspace/'task.txt')
    finally:
        tx.close(destroy=True)


def test_worker_uses_current_command_after_reusable_script_is_copied_up(tmp_path):
    from agenttx.runtime import AgentTX
    workspace=tmp_path/'workspace';workspace.mkdir()
    tx=AgentTX.begin(workdir=workspace,session_dir=tmp_path/'session',trace_reads=True)
    try:
        assert tx.run_tool('first',['bash','-c','printf initial']).stdout=='initial'
        script=tx.pool._cmd_script
        assert script is not None
        copied=tx.run_tool('copyup-command-script',['bash','-c',f'touch {script}; printf copied'])
        assert copied.returncode==0 and copied.stdout=='copied'
        current=tx.run_tool('current-command',['bash','-c','printf current'])
        assert current.returncode==0 and current.stdout=='current'
        tx.rollback(current.step_id)
        assert tx.run_tool('after-rollback',['bash','-c','printf next']).stdout=='next'
    finally:tx.close(destroy=True)


def test_large_file_writes_keep_exact_content_and_rollback_under_persistent_worker(tmp_path):
    from agenttx.harness import CodingAgentHarness
    workspace=tmp_path/'workspace';workspace.mkdir()
    harness=CodingAgentHarness(workspace,session_dir=tmp_path/'session',trace_backend='strace')
    content='中文 recovery content\n' * 12000
    try:
        first=harness.call_tool('write_file',dict(path='large.txt',content=content))
        assert first.returncode==0,first.stderr
        assert not (workspace/'large.txt').exists()
        read=harness.call_tool('read_file',dict(path='large.txt'))
        assert read.returncode==0 and read.stdout==content+'\n'
        assert first.step_id in read.parents
        second=harness.call_tool('write_file',dict(path='large.txt',content=content+'next'))
        assert second.returncode==0,second.stderr
        assert harness.call_tool('read_file',dict(path='large.txt')).stdout==content+'next\n'
        harness.tx.rollback(second.step_id)
        assert harness.call_tool('read_file',dict(path='large.txt')).stdout==content+'\n'
        assert harness.call_tool('run_shell',dict(cmd='printf next')).stdout=='next'
        harness.tx.commit()
        assert (workspace/'large.txt').read_text()==content+'\n'
    finally:harness.close(destroy=True)


def test_task_user_requires_root_coordinator_and_cannot_select_root(tmp_path,monkeypatch):
    import os,pwd
    monkeypatch.setattr(os,'geteuid',lambda:1003)
    with pytest.raises(RuntimeError,match='root namespace coordinator'):
        SharedSemisolate(workspace=tmp_path,tool_user='nobody')
    monkeypatch.setattr(os,'geteuid',lambda:0)
    with pytest.raises(ValueError,match='original non-root'):
        SharedSemisolate(workspace=tmp_path,tool_user='root')


def test_task_user_environment_uses_original_account_home():
    import pwd
    pool=object.__new__(SharedSemisolate);pool.tool_user='nobody'
    account=pwd.getpwnam('nobody')
    assert pool._task_user_environment()==['HOME='+account.pw_dir,'USER=nobody','LOGNAME=nobody','SHELL='+account.pw_shell]


@pytest.mark.skipif(__import__('os').geteuid()!=0,reason='task-user execution needs root coordinator')
def test_task_user_rejects_bpf_before_dispatching_any_command(tmp_path,monkeypatch):
    pool=SharedSemisolate(workspace=tmp_path,sandbox_dir=tmp_path/'session',
                          tool_user='nobody',trace_backend='strace')
    monkeypatch.setattr(pool,'_resolve_step_backend',lambda:('bpf','available'))
    monkeypatch.setattr(pool,'_run_step_bpf_persistent',lambda *a:pytest.fail('must not dispatch as root'))
    try:
        with pytest.raises(RuntimeError,match='requires strace'):
            pool.run(['true'])
    finally:pool.close(destroy=True)
