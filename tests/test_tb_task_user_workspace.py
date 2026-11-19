"""Non-root task setup must preserve mountpoints, ownership and cost groups."""
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

from experiments.scripts.bench_tb_image_native import restore_official_test_assets
from experiments.scripts.run_tb_dual_local import configuration_id, result_configuration


def test_test_assets_replace_hidden_entries_without_following_symlinks(tmp_path):
    assets = tmp_path / 'assets'
    assets.mkdir()
    (assets / '.pinned').write_text('official')
    destination = tmp_path / 'destination'
    destination.mkdir()
    (destination / '.stale').write_text('modified')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'retained').write_text('unrelated')
    (destination / 'link').symlink_to(outside, target_is_directory=True)
    inode = destination.stat().st_ino

    class View:
        pool = SimpleNamespace(tool_user='alice')

        def run_tool(self, name, command, **kwargs):
            assert self.pool.tool_user is None
            script = command[-1].replace('/opt/agenttx-verifier/tests', shlex.quote(str(assets)))
            script = script.replace('/tests', shlex.quote(str(destination)))
            return subprocess.run(['bash', '-c', script], capture_output=True, text=True)

    view = View()
    result = restore_official_test_assets(view, preserve_directory=True)
    assert result.returncode == 0, result.stderr
    assert destination.stat().st_ino == inode
    assert sorted(p.name for p in destination.iterdir()) == ['.pinned']
    assert (destination / '.pinned').read_text() == 'official'
    assert (outside / 'retained').read_text() == 'unrelated'
    assert view.pool.tool_user == 'alice'


def test_test_directory_symlink_cannot_redirect_official_asset_restore(tmp_path):
    assets = tmp_path / 'assets'
    assets.mkdir()
    (assets / 'pinned').write_text('official')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'retained').write_text('unrelated')
    destination = tmp_path / 'destination'
    destination.symlink_to(outside, target_is_directory=True)

    class View:
        def run_tool(self, name, command, **kwargs):
            script = command[-1].replace('/opt/agenttx-verifier/tests', shlex.quote(str(assets)))
            script = script.replace('/tests', shlex.quote(str(destination)))
            return subprocess.run(['bash', '-c', script], capture_output=True, text=True)

    result = restore_official_test_assets(View(), preserve_directory=True)
    assert result.returncode == 0, result.stderr
    assert not destination.is_symlink()
    assert (destination / 'pinned').read_text() == 'official'
    assert (outside / 'retained').read_text() == 'unrelated'


def test_nonroot_workspace_repair_cannot_pair_with_legacy_environment(tmp_path):
    path = tmp_path / 'attempt' / 'policy' / 'host-result.json'
    legacy = result_configuration({}, path)
    repaired = result_configuration({'task_user_workspace_contract': 'repair/v1'}, path)
    assert configuration_id(legacy) != configuration_id(repaired)
