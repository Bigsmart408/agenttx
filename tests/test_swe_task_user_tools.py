import os
import pwd
import subprocess
from pathlib import Path

import pytest

from agenttx.agents.swe_task_user_tools import AUTOSUBMIT_COMMAND, TaskUserSWETools
from experiments.scripts.run_tb_dual_local import configuration_id, result_configuration


def installed_tools(tmp_path, monkeypatch):
    # Ordinary-user fixture; native checks separately exercise real root/alice.
    account = pwd.getpwuid(os.getuid())
    assert account.pw_uid != 0
    monkeypatch.setattr(os, 'geteuid', lambda: 0)
    root = tmp_path/'protected-root'
    bundle = root/'tools/submit/bin'
    bundle.mkdir(parents=True)
    (bundle/'submit').write_text('cd "$ROOT"\n'
        'if [ -s "/root/test.patch" ]; then git apply -R < "/root/test.patch"; fi\n'
        'git add -A\ngit diff --cached > /root/model.patch\n'
        'echo "<<SWE_AGENT_SUBMISSION>>"\n')
    parent = tmp_path/'private'
    parent.mkdir()
    env = dict(os.environ, PATH=str(bundle)+':/root/bin:'+os.defpath, HOME='/root')
    return account, root, parent, env


def test_submit_writes_and_reads_patch_without_access_to_root(tmp_path, monkeypatch):
    account, root, parent, env = installed_tools(tmp_path, monkeypatch)
    work = tmp_path/'work'
    work.mkdir()
    subprocess.run(['git', 'init', '-q', str(work)], check=True)
    (work/'changed').write_text('real change\n')
    env['ROOT'] = str(work)
    before = root.stat()
    tools = TaskUserSWETools(account.pw_name, env, tools_root=root/'tools', private_parent=parent)
    try:
        result = subprocess.run(['bash', '-c', 'submit'], cwd=work, env=env,
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert '<<SWE_AGENT_SUBMISSION>>' in result.stdout
        patch = tools.path('/root/model.patch')
        assert '+real change' in patch.read_text()
        assert env['HOME'] == account.pw_dir and env['USER'] == account.pw_name
        assert '/root/bin' not in env['PATH'].split(':')
        fallback = subprocess.run(['bash', '-c', tools.command(AUTOSUBMIT_COMMAND)],
                                  cwd=work, env=env, capture_output=True)
        assert fallback.returncode == 0 and '+real change' in patch.read_text()
        assert tools.path('/root/protected_file.txt') == Path('/root/protected_file.txt')
        assert (root.stat().st_mode, root.stat().st_uid) == (before.st_mode, before.st_uid)
        assert not (root/'model.patch').exists()
    finally:
        tools.close()
    assert not tools.root.exists()


def test_nonempty_test_patch_is_not_exposed(tmp_path, monkeypatch):
    account, root, parent, env = installed_tools(tmp_path, monkeypatch)
    (root/'test.patch').write_text('private task content')
    with pytest.raises(RuntimeError, match='protected root test.patch'):
        TaskUserSWETools(account.pw_name, env, tools_root=root/'tools', private_parent=parent)
    assert not list(parent.iterdir())


def test_other_root_bundles_are_not_silently_broken(tmp_path, monkeypatch):
    account, root, parent, env = installed_tools(tmp_path, monkeypatch)
    env['PATH'] += ':'+str(root/'tools/edit/bin')
    with pytest.raises(RuntimeError, match='only the submit bundle'):
        TaskUserSWETools(account.pw_name, env, tools_root=root/'tools', private_parent=parent)
    assert not list(parent.iterdir())


def test_submit_repair_does_not_pair_with_old_tools(tmp_path):
    path = tmp_path/'result.json'
    old = result_configuration({'task_user_workspace_contract': 'v3'}, path)
    new = result_configuration({'task_user_workspace_contract': 'v3',
                               'task_user_swe_tools_contract': 'v1'}, path)
    assert configuration_id(old) != configuration_id(new)
