import os
from pathlib import Path
import stat
import pytest
from agenttx.layers import LayerStore
from agenttx.semisolate import SharedSemisolate, _WHITEOUT_DIGEST


def make_node(path,kind,device=0):
    try:
        os.mknod(path,kind|0o640,device)
    except PermissionError:
        pytest.skip('requires CAP_MKNOD for real device-node coverage')


@pytest.mark.parametrize('kind,device,replacement',[
    (stat.S_IFCHR,os.makedev(1,5),os.makedev(1,3)),
    (stat.S_IFBLK,os.makedev(7,0),os.makedev(7,1)),
    (stat.S_IFIFO,0,0),
])
def test_device_metadata_is_independent_across_full_incremental_and_surgical_rollback(tmp_path,kind,device,replacement):
    upper=tmp_path/'upper';directory=upper/'app/dev';directory.mkdir(parents=True)
    node=directory/'node';make_node(node,kind,device)
    layers=LayerStore(tmp_path/'layers')
    first=layers.snapshot_before(0,upper)
    saved=first/'app/dev/node'
    assert saved.stat().st_ino!=node.stat().st_ino
    node.chmod(0o600)
    assert stat.S_IMODE(saved.stat().st_mode)==0o640
    node.unlink();make_node(node,kind,replacement);node.chmod(0o600)
    if os.geteuid()==0:os.chown(node,1100,1100)
    second=layers.snapshot_before(1,upper,changed_paths=['/app/dev/node'])
    assert (second/'app/dev/node').stat().st_rdev==replacement
    (upper/'app/marker').write_text('independent')
    third=layers.snapshot_before(2,upper,changed_paths=['/app/marker'])
    assert (third/'app/dev/node').stat().st_rdev==replacement
    assert (saved.stat().st_rdev,stat.S_IMODE(saved.stat().st_mode))==(device,0o640)
    layers.restore_paths(0,upper,['/app/dev/node'])
    assert stat.S_IFMT(node.stat().st_mode)==kind
    assert (node.stat().st_rdev,stat.S_IMODE(node.stat().st_mode))==(device,0o640)
    assert (node.stat().st_uid,node.stat().st_gid)==(saved.stat().st_uid,saved.stat().st_gid)
    assert (upper/'app/marker').read_text()=='independent'
    layers.restore_before(1,upper)
    assert stat.S_IFMT(node.stat().st_mode)==kind
    assert (node.stat().st_rdev,stat.S_IMODE(node.stat().st_mode))==(replacement,0o600)
    if os.geteuid()==0:assert node.stat().st_uid==node.stat().st_gid==1100


def test_real_devices_are_not_whiteouts_and_fingerprints_track_device_and_mode(tmp_path):
    pool=object.__new__(SharedSemisolate);pool.sandbox_dir=tmp_path/'session';pool.workspace=Path('/app')
    upper=pool.sandbox_dir/'upperdir';(upper/'app').mkdir(parents=True)
    zero=upper/'app/zero';make_node(zero,stat.S_IFCHR,os.makedev(1,5))
    whiteout=upper/'app/deleted';make_node(whiteout,stat.S_IFCHR,os.makedev(0,0))
    before=pool.upperdir_digests()
    assert before['/app/deleted']==_WHITEOUT_DIGEST
    assert before['/app/zero']!=_WHITEOUT_DIGEST
    zero.chmod(0o600)
    assert pool.upperdir_digests()['/app/zero']!=before['/app/zero']
    zero.unlink();make_node(zero,stat.S_IFCHR,os.makedev(1,3))
    assert pool.upperdir_digests()['/app/zero']!=before['/app/zero']


@pytest.mark.skipif(os.geteuid()!=0,reason='requires a privileged task container')
def test_real_transaction_can_take_next_snapshot_and_rollback_after_mknod(tmp_path):
    from agenttx.runtime import AgentTX
    workspace=tmp_path/'app';workspace.mkdir()
    tx=AgentTX.begin(workdir=workspace,session_dir=tmp_path/'session',trace_reads=False)
    try:
        created=tx.run_tool('create-device',['bash','-c','mkdir dev; mknod -m 640 dev/zero c 1 5; mkfifo dev/pipe'])
        assert created.returncode==0,created.stderr
        checked=tx.run_tool('next-snapshot',['bash','-c','test -c dev/zero && test -p dev/pipe'])
        assert checked.returncode==0,checked.stderr
        changed=tx.run_tool('replace-device',['bash','-c','rm dev/zero; mknod -m 600 dev/zero c 1 3'])
        assert changed.returncode==0,changed.stderr
        tx.rollback(changed.step_id)
        checked=tx.run_tool('verify-rollback',['bash','-c',"stat -c '%t:%T:%a' dev/zero; test -p dev/pipe"])
        assert checked.returncode==0,checked.stderr
        assert checked.stdout.strip()=='1:5:640'
    finally:
        tx.close(destroy=True)
