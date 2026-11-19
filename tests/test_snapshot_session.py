import errno
import os
from pathlib import Path
import random
import shutil
import stat
from unittest.mock import patch

import pytest

from agenttx import snapshot as storage


@pytest.fixture
def session(tmp_path):
    workspace = tmp_path/'project'
    workspace.mkdir()
    (workspace/'original').write_text('baseline')
    try:
        session = storage.SnapshotSession(workspace, tmp_path/'session')
    except OSError as error:
        if error.errno in (errno.EPERM, errno.EACCES, errno.ENODEV):
            pytest.skip('requires real OverlayFS mount permissions')
        raise
    try:
        yield session
    finally:
        session.close(destroy=True)


def read_snapshot(session, point, path):
    view = session.state_dir/'history'
    session.mount_snapshot(point, view)
    try:
        return (view/path).read_text()
    finally:
        storage._unmount(view)


@pytest.fixture
def metadata_session(tmp_path):
    workspace = tmp_path/'project'
    workspace.mkdir()
    (workspace/'original').write_text('nonzero original contents')
    try:
        session = storage.SnapshotSession(workspace, tmp_path/'session', metadata_copy=True)
    except OSError as error:
        if error.errno in (errno.EPERM, errno.EACCES, errno.ENODEV):
            pytest.skip('requires real OverlayFS mount permissions')
        raise
    try:
        yield session
    finally:
        session.close(destroy=True)


def test_metacopy_rename_chain_retains_snapshots_and_publishes_real_data(metadata_session):
    session = metadata_session
    (session.view/'original').rename(session.view/'first')
    first = session.snapshot()
    (session.view/'first').rename(session.view/'second')
    second = session.snapshot()
    (session.view/'second').write_text('new current contents')
    assert read_snapshot(session, first, 'first') == 'nonzero original contents'
    assert read_snapshot(session, second, 'second') == 'nonzero original contents'
    session.commit()
    assert sorted(p.name for p in session.workspace.iterdir()) == ['second']
    assert (session.workspace/'second').read_text() == 'new current contents'


def test_metacopy_publication_materializes_metadata_only_file(metadata_session):
    session = metadata_session
    (session.view/'original').rename(session.view/'renamed')
    (session.view/'renamed').chmod(0o640)
    os.setxattr(session.view/'renamed', 'user.payload', b'metadata')
    os.utime(session.view/'renamed', ns=(1_500_000_000_000_000_000, 1_600_000_000_000_000_000))
    session.seal()
    session.commit()
    path = session.workspace/'renamed'
    assert path.read_text() == 'nonzero original contents'
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert path.stat().st_mtime_ns == 1_600_000_000_000_000_000
    assert os.getxattr(path, 'user.payload') == b'metadata'
    assert not any(name.startswith(storage._PRIVATE_XATTRS) for name in os.listxattr(path))


def test_metacopy_redirected_directory_preserves_untouched_descendants(metadata_session):
    session = metadata_session
    directory = session.workspace/'old/sub'
    directory.mkdir(parents=True)
    (directory/'untouched').write_text('keep this data')
    (directory/'changed').write_text('before')
    (directory/'link').symlink_to('untouched')
    os.mkfifo(directory/'fifo')
    (session.view/'old').rename(session.view/'new')
    (session.view/'new/sub/changed').write_text('after')
    (session.view/'new/sub').chmod(0o750)
    session.snapshot()
    (session.view/'new/sub/new-file').write_text('latest')
    session.commit()
    assert not (session.workspace/'old').exists()
    directory = session.workspace/'new/sub'
    assert (directory/'untouched').read_text() == 'keep this data'
    assert (directory/'changed').read_text() == 'after'
    assert (directory/'new-file').read_text() == 'latest'
    assert os.readlink(directory/'link') == 'untouched'
    assert stat.S_ISFIFO((directory/'fifo').stat().st_mode)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o750


@pytest.mark.parametrize('replacement', ['delete', 'directory', 'symlink'])
def test_obsolete_metacopy_does_not_publish_stale_data(metadata_session, replacement):
    session = metadata_session
    (session.view/'original').chmod(0o600)
    session.snapshot()
    (session.view/'original').unlink()
    if replacement == 'directory':
        (session.view/'original').mkdir()
        (session.view/'original/child').write_text('child')
    elif replacement == 'symlink':
        (session.view/'original').symlink_to('independent')
    (session.view/'independent').write_text('unrelated progress')
    session.commit()
    if replacement == 'directory':
        assert (session.workspace/'original/child').read_text() == 'child'
    elif replacement == 'symlink':
        assert (session.workspace/'original').is_symlink()
        assert (session.workspace/'original').read_text() == 'unrelated progress'
    else:
        assert not (session.workspace/'original').exists()


def test_metacopy_materialization_failure_keeps_live_data_and_allows_retry(metadata_session):
    session = metadata_session
    (session.view/'original').rename(session.view/'renamed')
    with patch.object(storage, '_matches_origin', return_value=False), \
            patch.object(storage.shutil, 'copyfile', side_effect=OSError(errno.EIO, 'copy failure')):
        with pytest.raises(OSError, match='copy failure'):
            session.commit()
    assert (session.view/'renamed').read_text() == 'nonzero original contents'
    session.commit()
    assert (session.workspace/'renamed').read_text() == 'nonzero original contents'


@pytest.mark.parametrize('moves', [
    [('original', 'first'), ('first', 'final')],
    [('original', 'other')],
    [('original', 'temporary'), ('other', 'original'), ('temporary', 'other')],
    [('original', 'temporary'), ('other', 'original'), ('third', 'other'), ('temporary', 'third')],
    [('original', 'moved'), ('other', 'original')],
])
def test_metacopy_rename_reuses_verified_inodes_without_copying(metadata_session, moves):
    session = metadata_session
    for index, name in enumerate(('other', 'third')):
        (session.workspace/name).write_bytes(bytes([index+1])*8193)
    expected = {path.name: (path.stat().st_ino, path.read_bytes())
                for path in session.workspace.iterdir()}
    for source, destination in moves:
        (session.view/source).rename(session.view/destination)
        expected[destination] = expected.pop(source)
    with patch.object(storage.shutil, 'copyfile', side_effect=AssertionError('rename copied data')):
        session.commit()
    assert {path.name: (path.stat().st_ino, path.read_bytes())
            for path in session.workspace.iterdir()} == expected


def test_metacopy_reuse_updates_metadata_and_preserves_directory_times(metadata_session):
    session = metadata_session
    original = session.workspace/'original'
    inode = original.stat().st_ino
    os.setxattr(original, 'user.old', b'remove')
    (session.view/'original').rename(session.view/'renamed')
    target = session.view/'renamed'
    target.chmod(0o640)
    os.removexattr(target, 'user.old')
    os.setxattr(target, 'user.new', b'keep')
    times = (1_500_000_000_000_000_000, 1_600_000_000_000_000_000)
    os.utime(target, ns=times)
    os.utime(session.view, ns=times)
    with patch.object(storage.shutil, 'copyfile', side_effect=AssertionError('metadata copied data')):
        session.commit()
    target = session.workspace/'renamed'
    assert target.stat().st_ino == inode
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert (target.stat().st_atime_ns, target.stat().st_mtime_ns) == times
    assert target.read_text() == 'nonzero original contents'
    assert os.getxattr(target, 'user.new') == b'keep'
    assert 'user.old' not in os.listxattr(target)
    assert session.workspace.stat().st_mtime_ns == times[1]


def test_metacopy_reuse_across_snapshots_keeps_history_until_commit(metadata_session):
    session = metadata_session
    inode = (session.workspace/'original').stat().st_ino
    (session.view/'original').rename(session.view/'first')
    first = session.snapshot()
    (session.view/'first').rename(session.view/'final')
    session.snapshot()
    assert read_snapshot(session, first, 'first') == 'nonzero original contents'
    assert (session.workspace/'original').stat().st_ino == inode
    session.commit()
    assert (session.workspace/'final').read_text() == 'nonzero original contents'
    assert sorted(p.name for p in session.workspace.iterdir()) == ['final']


def test_explicit_seal_keeps_base_unchanged_until_commit(metadata_session):
    session = metadata_session
    inode = (session.workspace/'original').stat().st_ino
    (session.view/'original').rename(session.view/'renamed')
    (session.view/'renamed').chmod(0o600)
    session.seal()
    assert (session.workspace/'original').stat().st_ino == inode
    assert stat.S_IMODE((session.workspace/'original').stat().st_mode) != 0o600
    assert not (session.workspace/'renamed').exists()
    session.commit()
    assert (session.workspace/'renamed').read_text() == 'nonzero original contents'


def test_metacopy_changed_payload_is_not_replaced_by_base_inode(metadata_session):
    session = metadata_session
    (session.view/'original').rename(session.view/'renamed')
    (session.view/'renamed').write_text('changed payload')
    (session.view/'independent').write_text('keep')
    session.commit()
    assert (session.workspace/'renamed').read_text() == 'changed payload'
    assert (session.workspace/'independent').read_text() == 'keep'


def test_metacopy_base_hardlink_alias_remains_independent(metadata_session):
    session = metadata_session
    os.link(session.workspace/'original', session.workspace/'alias')
    (session.view/'original').rename(session.view/'renamed')
    (session.view/'renamed').chmod(0o600)
    alias_mode = stat.S_IMODE((session.view/'alias').stat().st_mode)
    session.commit()
    assert stat.S_IMODE((session.workspace/'alias').stat().st_mode) == alias_mode
    assert (session.workspace/'alias').read_text() == 'nonzero original contents'
    assert (session.workspace/'renamed').read_text() == 'nonzero original contents'


@pytest.mark.parametrize('corruption', ['uuid', 'fid', 'flags', 'version'])
def test_metacopy_origin_mismatch_uses_safe_copy(metadata_session, corruption):
    session = metadata_session
    (session.view/'original').rename(session.view/'renamed')
    original_getxattr = storage.os.getxattr
    def altered(path, name, **kwargs):
        value = original_getxattr(path, name, **kwargs)
        if name == 'trusted.overlay.origin':
            value = bytearray(value)
            offset = {'uuid': 5, 'fid': -1, 'flags': 3, 'version': 0}[corruption]
            value[offset] ^= 4 if corruption == 'flags' else 1
            return bytes(value)
        return value
    with patch.object(storage.os, 'getxattr', side_effect=altered), \
            patch.object(storage.shutil, 'copyfile', wraps=storage.shutil.copyfile) as copied:
        session.commit()
    assert copied.call_count == 1
    assert (session.workspace/'renamed').read_text() == 'nonzero original contents'


def test_failed_extraction_retains_originals_in_staging(metadata_session):
    session = metadata_session
    (session.workspace/'other').write_text('second payload')
    (session.view/'original').rename(session.view/'renamed')
    (session.view/'other').rename(session.view/'moved')
    rename = storage.os.rename
    extracted = []
    def fail_second(source, destination, **kwargs):
        if kwargs.get('src_dir_fd') == session._base:
            if extracted:
                raise OSError(errno.EIO, 'extraction failed')
            extracted.append(str(destination))
        return rename(source, destination, **kwargs)
    with patch.object(storage.os, 'rename', side_effect=fail_second):
        with pytest.raises(OSError, match='extraction failed'):
            session.commit()
    assert Path(extracted[0]).is_file()
    retained = [path.read_text() for path in session.workspace.iterdir()]
    retained += [path.read_text() for path in (session.state_dir/'materialized').iterdir()]
    assert sorted(retained) == ['nonzero original contents', 'second payload']


def test_reuse_preparation_failure_keeps_base_intact(metadata_session):
    session = metadata_session
    (session.workspace/'linked').write_text('hardlinked payload')
    os.link(session.workspace/'linked', session.workspace/'alias')
    expected = {p.name: (p.stat().st_ino, p.read_bytes()) for p in session.workspace.iterdir()}
    (session.view/'original').rename(session.view/'renamed')
    (session.view/'linked').rename(session.view/'moved')
    with patch.object(storage.shutil, 'copyfile', side_effect=OSError(errno.EIO, 'copy failed')):
        with pytest.raises(OSError, match='copy failed'):
            session.commit()
    assert {p.name: (p.stat().st_ino, p.read_bytes())
            for p in session.workspace.iterdir()} == expected
    assert not (session.state_dir/'materialized').exists()
    session.commit()
    assert (session.workspace/'renamed').read_text() == 'nonzero original contents'
    assert (session.workspace/'moved').read_text() == 'hardlinked payload'


def test_reused_inode_survives_failure_during_layer_publication(metadata_session):
    session = metadata_session
    (session.view/'original').rename(session.view/'renamed')
    with patch.object(storage, '_merge', side_effect=OSError(errno.EIO, 'merge failed')):
        with pytest.raises(OSError, match='merge failed'):
            session.commit()
    retained = list((session.state_dir/'materialized').iterdir())
    assert len(retained) == 1
    assert retained[0].read_text() == 'nonzero original contents'


@pytest.mark.parametrize('seed', range(10))
def test_metacopy_mixed_generations_match_direct_filesystem(metadata_session, tmp_path, seed):
    session = metadata_session
    oracle = tmp_path/'oracle'
    shutil.copytree(session.workspace, oracle)
    rng = random.Random(seed)
    for step in range(40):
        names = sorted(path.name for path in oracle.iterdir())
        action = rng.choice(('write', 'rename', 'delete', 'mode', 'attribute')) if names else 'write'
        source = rng.choice(names) if names else 'original'
        destination = f'file{rng.randrange(8)}'
        payload = bytes([step+1])*(97+step)
        for root in (session.view, oracle):
            if action == 'write':
                (root/destination).write_bytes(payload)
            elif action == 'rename':
                (root/source).rename(root/destination)
            elif action == 'delete':
                (root/source).unlink()
            elif action == 'mode':
                (root/source).chmod(0o600 if step % 2 else 0o640)
            else:
                os.setxattr(root/source, 'user.payload', payload[:13])
        if step % 10 == 9:
            session.snapshot()
    session.commit()
    def contents(root):
        return {path.name: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode),
                           {name: os.getxattr(path, name) for name in os.listxattr(path)
                            if not name.startswith(storage._PRIVATE_XATTRS)})
                for path in root.iterdir()}
    assert contents(session.workspace) == contents(oracle)


def test_discard_does_not_materialize_metacopy_data(metadata_session):
    session = metadata_session
    (session.view/'original').rename(session.view/'renamed')
    with patch.object(storage.shutil, 'copyfile', side_effect=AssertionError('discard must not copy data')):
        session.close(destroy=True)
    assert (session.workspace/'original').read_text() == 'nonzero original contents'


def test_metacopy_publication_preserves_live_hardlink_identity(metadata_session):
    session = metadata_session
    (session.view/'original').chmod(0o640)
    os.link(session.view/'original', session.view/'alias')
    expected_same_inode = ((session.view/'original').stat().st_ino ==
                           (session.view/'alias').stat().st_ino)
    assert expected_same_inode
    session.commit()
    assert (session.workspace/'original').read_text() == 'nonzero original contents'
    assert (session.workspace/'alias').read_text() == 'nonzero original contents'
    assert (session.workspace/'original').stat().st_ino == (session.workspace/'alias').stat().st_ino


def test_metacopy_directory_move_after_multiple_snapshots_matches_oracle(metadata_session, tmp_path):
    session = metadata_session
    source = session.workspace/'source'
    source.mkdir()
    for index in range(7):
        (source/f'f{index}').write_bytes(bytes([index+1])*8193)
    oracle = tmp_path/'oracle'
    shutil.copytree(session.workspace, oracle)
    def modify(root, generation):
        if generation == 0:
            (root/'source/f0').chmod(0o600)
        elif generation == 1:
            (root/'source').rename(root/'moved')
            (root/'moved/f1').unlink()
            (root/'moved/f2').write_text('changed after move')
        else:
            (root/'moved').rename(root/'final')
            (root/'final/f3').rename(root/'independent')
            (root/'final/new').write_text('newest')
    for generation in range(3):
        modify(session.view, generation)
        modify(oracle, generation)
        session.snapshot()
    session.commit()
    def tree(root):
        result = {}
        for path in root.rglob('*'):
            info = path.lstat()
            result[str(path.relative_to(root))] = (stat.S_IMODE(info.st_mode),
                path.read_bytes() if path.is_file() else None)
        return result
    assert tree(session.workspace) == tree(oracle)


def test_snapshot_versions_remain_immutable_and_publish_current_state(session):
    (session.view/'original').write_text('one')
    first = session.snapshot()
    (session.view/'original').write_text('two')
    (session.view/'independent').write_text('keep')
    second = session.snapshot()
    (session.view/'original').unlink()
    assert read_snapshot(session, first, 'original') == 'one'
    assert read_snapshot(session, second, 'original') == 'two'
    assert read_snapshot(session, second, 'independent') == 'keep'
    session.commit()
    assert not (session.workspace/'original').exists()
    assert (session.workspace/'independent').read_text() == 'keep'
    with pytest.raises(RuntimeError):
        session.snapshot()


def test_opaque_directory_recreation_removes_old_children(session):
    directory = session.workspace/'directory'
    directory.mkdir()
    (directory/'old').write_text('old')
    shutil.rmtree(session.view/'directory')
    (session.view/'directory').mkdir()
    (session.view/'directory/new').write_text('new')
    session.snapshot()
    session.commit()
    assert sorted(p.name for p in directory.iterdir()) == ['new']


def test_metadata_and_hardlinks_survive_publication(session):
    file = session.view/'file'
    file.write_text('same inode')
    file.chmod(0o400)
    os.utime(file, ns=(1_500_000_000_000_000_000, 1_600_000_000_000_000_000))
    os.link(file, session.view/'alias')
    session.view.chmod(0o710)
    os.setxattr(session.view, 'user.snapshot-test', b'root metadata')
    session.snapshot()
    session.snapshot()
    session.commit()
    committed = session.workspace/'file'
    assert committed.stat().st_ino == (session.workspace/'alias').stat().st_ino
    assert stat.S_IMODE(committed.stat().st_mode) == 0o400
    assert committed.stat().st_mtime_ns == 1_600_000_000_000_000_000
    assert stat.S_IMODE(session.workspace.stat().st_mode) == 0o710
    assert os.getxattr(session.workspace, 'user.snapshot-test') == b'root metadata'


@pytest.mark.parametrize('old_kind,new_kind', [('file','dir'), ('dir','file'), ('link','dir'), ('dir','link')])
def test_publication_handles_object_type_changes_without_following_links(session, old_kind, new_kind):
    destination = session.workspace/'target'
    outside = session.workspace.parent/'outside'
    outside.mkdir()
    (outside/'keep').write_text('untouched')
    if old_kind == 'dir':
        destination.mkdir()
        (destination/'old').write_text('old')
        shutil.rmtree(session.view/'target')
    elif old_kind == 'link':
        destination.symlink_to(outside)
        (session.view/'target').unlink()
    else:
        destination.write_text('old')
        (session.view/'target').unlink()
    staged = session.view/'target'
    if new_kind == 'dir':
        staged.mkdir()
        (staged/'new').write_text('new')
    elif new_kind == 'link':
        staged.symlink_to('original')
    else:
        staged.write_text('new')
    session.commit()
    assert (outside/'keep').read_text() == 'untouched'
    if new_kind == 'dir':
        assert (destination/'new').read_text() == 'new'
    elif new_kind == 'link':
        assert destination.is_symlink()
    else:
        assert destination.read_text() == 'new'


def test_failed_remount_restores_writable_view_and_allows_retry(session):
    original_mount = storage._mount
    calls = 0
    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError(errno.EIO, 'injected mount failure')
        return original_mount(*args, **kwargs)
    with patch.object(storage, '_mount', side_effect=fail_once):
        with pytest.raises(OSError, match='injected mount failure'):
            session.snapshot()
    (session.view/'original').write_text('after failed snapshot')
    point = session.snapshot()
    assert point.generation == 1
    assert read_snapshot(session, point, 'original') == 'after failed snapshot'


def test_thirty_layers_do_not_exceed_mount_option_path_limit(session):
    first = None
    for generation in range(30):
        (session.view/'original').write_text(str(generation))
        point = session.snapshot()
        first = first or point
    assert read_snapshot(session, first, 'original') == '0'
    assert read_snapshot(session, point, 'original') == '29'


def test_state_directory_must_not_be_inside_workspace(tmp_path):
    with pytest.raises(ValueError, match='outside'):
        storage.SnapshotSession(tmp_path, tmp_path/'state')


def test_failed_layer_preparation_cleans_resources_and_can_retry(session):
    with patch.object(storage, '_directory_metadata', side_effect=OSError(errno.EIO, 'metadata failure')):
        with pytest.raises(OSError, match='metadata failure'):
            session.snapshot()
    assert not (session.state_dir/'u1').exists()
    assert not (session.state_dir/'w1').exists()
    assert session.snapshot().generation == 1


def test_failed_journal_write_preserves_files_and_rejects_new_operations(session):
    (session.view/'original').write_text('new')
    with patch.object(storage.os, 'write', side_effect=OSError(errno.EIO, 'journal failure')):
        with pytest.raises(OSError, match='journal failure'):
            session.snapshot()
    assert (session.view/'original').read_text() == 'new'
    with pytest.raises(RuntimeError):
        session.snapshot()
    with pytest.raises(RuntimeError):
        session.commit()


@pytest.mark.parametrize('native', [False, True])
def test_bulk_deletion_handles_directory_symlink_and_non_ascii_names(session, native):
    if native and storage._NATIVE is None:
        pytest.skip('native storage helper not built')
    directory = session.workspace/'tree'
    directory.mkdir()
    (directory/'child').write_text('child')
    (session.workspace/'空 格').write_text('file')
    (session.workspace/'link').symlink_to('original')
    fd = os.open(session.workspace, storage._DIRECTORY)
    try:
        with patch.object(storage, '_NATIVE', storage._NATIVE if native else None):
            storage._remove_many(fd, ['空 格', 'tree', 'link', 'missing'])
    finally:
        os.close(fd)
    assert sorted(path.name for path in session.workspace.iterdir()) == ['original']


def test_character_device_is_not_mistaken_for_whiteout(session):
    os.mknod(session.view/'device', stat.S_IFCHR | 0o600, os.makedev(1, 3))
    (session.view/'original').unlink()
    session.commit()
    assert (session.workspace/'device').stat().st_rdev == os.makedev(1, 3)
    assert not (session.workspace/'original').exists()


def test_snapshot_is_readonly_even_through_its_retained_mount(session):
    point = session.snapshot()
    target = Path(f'/proc/self/fd/{point.mount_fd}/original')
    with pytest.raises(OSError) as error:
        target.write_text('must not mutate snapshot')
    assert error.value.errno == errno.EROFS
    (session.view/'original').write_text('current only')
    assert target.read_text() == 'baseline'


def test_seal_releases_retained_mount_references(session):
    points = [session.snapshot() for _ in range(3)]
    session.seal()
    assert session._pins == []
    for point in points:
        with pytest.raises(OSError):
            os.fstat(point.mount_fd)


def test_parallel_delete_resumes_after_a_directory_error(session):
    if storage._NATIVE is None:
        pytest.skip('native storage helper not built')
    names = [f'file-{i:06}' for i in range(4000)]
    for name in names:
        (session.workspace/name).touch()
    (session.workspace/'directory').mkdir()
    (session.workspace/'directory/child').touch()
    names.insert(2000, 'directory')
    fd = os.open(session.workspace, storage._DIRECTORY)
    try:
        storage._remove_many(fd, names)
    finally:
        os.close(fd)
    assert sorted(p.name for p in session.workspace.iterdir()) == ['original']


def test_native_delete_reports_invalid_directory_without_success(session):
    if storage._NATIVE is None:
        pytest.skip('native storage helper not built')
    with pytest.raises(OSError) as error:
        storage._remove_many(-1, ['original'])
    assert error.value.errno == errno.EBADF
    assert (session.workspace/'original').read_text() == 'baseline'


def test_owner_change_does_not_clear_directory_setgid(session):
    session.workspace.chmod(0o2755)
    os.chown(session.view, 0, 1234)
    session.view.chmod(0o2755)
    session.commit()
    assert session.workspace.stat().st_gid == 1234
    assert stat.S_IMODE(session.workspace.stat().st_mode) == 0o2755


def test_repeated_updates_keep_a_bounded_active_lower_stack(session):
    original_mount = storage._mount
    widths = []
    def observe(view, lowers, upper=None, work=None):
        if upper is not None:
            widths.append(len(lowers))
        return original_mount(view, lowers, upper, work)
    points = []
    with patch.object(storage, '_mount', side_effect=observe):
        for generation in range(30):
            (session.view/'original').write_text(str(generation))
            points.append(session.snapshot())
    assert max(widths) <= 2
    for index in (0, 14, 29):
        assert read_snapshot(session, points[index], 'original') == str(index)
    session.commit()
    assert (session.workspace/'original').read_text() == '29'


def test_compaction_retains_independent_files_and_whiteout_history(session):
    (session.view/'independent').write_text('keep')
    (session.view/'original').write_text('first')
    first = session.snapshot()
    (session.view/'original').unlink()
    deleted = session.snapshot()
    (session.view/'original').write_text('latest')
    session.snapshot()
    assert read_snapshot(session, first, 'original') == 'first'
    history = session.state_dir/'history'
    session.mount_snapshot(deleted, history)
    try:
        assert not (history/'original').exists()
        assert (history/'independent').read_text() == 'keep'
    finally:
        storage._unmount(history)
    session.commit()
    assert (session.workspace/'original').read_text() == 'latest'
    assert (session.workspace/'independent').read_text() == 'keep'


def test_metadata_only_snapshots_keep_their_own_root_state(session):
    session.view.chmod(0o710)
    os.setxattr(session.view, 'user.version', b'first')
    first = session.snapshot()
    session.view.chmod(0o700)
    os.setxattr(session.view, 'user.version', b'second')
    second = session.snapshot()
    history = session.state_dir/'history'
    for point, mode, value in [(first, 0o710, b'first'), (second, 0o700, b'second')]:
        session.mount_snapshot(point, history)
        try:
            assert stat.S_IMODE(history.stat().st_mode) == mode
            assert os.getxattr(history, 'user.version') == value
        finally:
            storage._unmount(history)
    session.commit()
    assert stat.S_IMODE(session.workspace.stat().st_mode) == 0o700
    assert os.getxattr(session.workspace, 'user.version') == b'second'


def test_large_and_nested_layers_fall_back_without_losing_history(session):
    for i in range(300):
        (session.view/f'file-{i}').write_text(str(i))
    directory = session.view/'directory'
    directory.mkdir()
    (directory/'old').write_text('old')
    first = session.snapshot()
    shutil.rmtree(directory)
    directory.mkdir()
    (directory/'new').write_text('new')
    (session.view/'file-0').unlink()
    second = session.snapshot()
    assert read_snapshot(session, first, 'directory/old') == 'old'
    assert read_snapshot(session, first, 'file-0') == '0'
    assert read_snapshot(session, second, 'directory/new') == 'new'
    session.commit()
    assert not (session.workspace/'file-0').exists()
    assert sorted(p.name for p in (session.workspace/'directory').iterdir()) == ['new']
    assert (session.workspace/'file-299').read_text() == '299'


def test_mixed_versions_and_final_commit_match_an_independent_tree(session):
    reference = session.state_dir.parent/'reference'
    shutil.copytree(session.workspace, reference)
    def capture(root):
        result = {'.': stat.S_IMODE(root.stat().st_mode)}
        def visit(directory, prefix=''):
            with os.scandir(directory) as entries:
                for entry in entries:
                    name = prefix+entry.name
                    if entry.is_symlink():
                        result[name] = ('link', os.readlink(entry.path))
                    elif entry.is_dir(follow_symlinks=False):
                        result[name] = ('dir', stat.S_IMODE(entry.stat().st_mode))
                        visit(Path(entry.path), name+'/')
                    else:
                        result[name] = ('file', Path(entry.path).read_bytes(),
                                        stat.S_IMODE(entry.stat().st_mode))
        visit(root)
        return result
    def mutate(root, step):
        (root/f'file-{step%5}').write_text(f'version {step}')
        if step%4 == 1:
            (root/f'file-{(step+2)%5}').unlink(missing_ok=True)
        directory = root/'nested'
        if step%6 == 2:
            if directory.exists():
                shutil.rmtree(directory)
            directory.mkdir()
            (directory/'child').write_text(f'new tree {step}')
        if step%6 == 3:
            (directory/'child').write_text(f'updated tree {step}')
        if step%6 == 4:
            (root/f'file-{step%5}').rename(root/f'renamed-{step%3}')
        if step%8 == 5:
            (root/'alias').unlink(missing_ok=True)
            (root/'alias').symlink_to('original')
        if step%7 == 0:
            root.chmod(0o750 if step%2 else 0o710)
        if step%5 == 0:
            shape = root/'shape'
            if shape.is_dir():
                shutil.rmtree(shape)
            else:
                shape.unlink(missing_ok=True)
            if step%10 == 0:
                shape.mkdir()
                (shape/'child').write_text(str(step))
            else:
                shape.write_text(str(step))
    points = []
    expected = []
    for step in range(24):
        mutate(reference, step)
        mutate(session.view, step)
        expected.append(capture(reference))
        assert capture(session.view) == expected[-1]
        points.append(session.snapshot())
    history = session.state_dir/'history'
    for index in (0, 7, 15, 23):
        session.mount_snapshot(points[index], history)
        try:
            assert capture(history) == expected[index]
        finally:
            storage._unmount(history)
    session.commit()
    assert capture(session.workspace) == capture(reference)
