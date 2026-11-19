import os
import stat

import pytest

from agenttx.layers import LayerStore


def test_incremental_snapshot_preserves_new_ancestor_directory_metadata(tmp_path):
    upper = tmp_path / 'upper'
    upper.mkdir()
    layers = LayerStore(tmp_path / 'layers')
    layers.snapshot_before(0, upper)
    home = upper / 'home' / 'alice'
    home.mkdir(parents=True)
    home.chmod(0o710)
    document = home / 'notes' / 'result'
    document.parent.mkdir(mode=0o750)
    document.write_text('retained')
    owner = 1100 if os.geteuid() == 0 else os.geteuid()
    if os.geteuid() == 0:
        os.chown(home, owner, owner)
        os.chown(document.parent, owner, owner)
    layers.snapshot_before(1, upper, changed_paths=['/home/alice/notes/result'])
    layers.restore_before(1, upper)
    assert document.read_text() == 'retained'
    assert stat.S_IMODE(home.stat().st_mode) == 0o710
    assert stat.S_IMODE(document.parent.stat().st_mode) == 0o750
    assert home.stat().st_uid == document.parent.stat().st_uid == owner


def test_surgical_restore_preserves_metadata_of_recreated_ancestors(tmp_path):
    upper = tmp_path / 'upper'
    home = upper / 'home' / 'alice'
    home.mkdir(parents=True)
    home.chmod(0o710)
    document = home / 'result'
    document.write_text('retained')
    layers = LayerStore(tmp_path / 'layers')
    layers.snapshot_before(0, upper)
    import shutil
    shutil.rmtree(upper / 'home')
    layers.restore_paths(0, upper, ['/home/alice/result'])
    assert document.read_text() == 'retained'
    assert stat.S_IMODE(home.stat().st_mode) == 0o710


@pytest.mark.skipif(os.geteuid()!=0,reason='requires preserving a different task uid/gid')
def test_full_and_surgical_rollback_preserve_task_ownership_and_setid_mode(tmp_path):
    upper=tmp_path/'upper';work=upper/'app';work.mkdir(parents=True)
    file=work/'result';file.write_text('original')
    link=work/'link';link.symlink_to('result')
    for path in (work,file,link):os.chown(path,1100,1100,follow_symlinks=False)
    file.chmod(0o4755)
    layers=LayerStore(tmp_path/'layers')
    layers.snapshot_before(0,upper)
    file.write_text('changed');file.chmod(0o600)
    os.chown(file,0,0)
    layers.restore_paths(0,upper,['/app/result','/app/link'])
    assert file.read_text()=='original'
    assert (file.stat().st_uid,file.stat().st_gid)==(1100,1100)
    assert stat.S_IMODE(file.stat().st_mode)==0o4755
    assert link.lstat().st_uid==1100
    os.chown(work,0,0)
    layers.restore_before(0,upper)
    assert (work.stat().st_uid,work.stat().st_gid)==(1100,1100)
    assert file.stat().st_uid==link.lstat().st_uid==1100
