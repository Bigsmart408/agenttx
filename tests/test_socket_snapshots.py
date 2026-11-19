import os
import socket
import stat

from agenttx.layers import LayerStore
from agenttx.semisolate import SharedSemisolate


def test_socket_snapshots_preserve_type_metadata_without_mutating_live_endpoint(tmp_path):
    upper = tmp_path / 'upper'
    upper.mkdir()
    endpoint = upper / 'monitor.sock'
    layers = LayerStore(tmp_path / 'layers')
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
        server.bind(str(endpoint))
        endpoint.chmod(0o640)
        saved = layers.snapshot_before(0, upper) / endpoint.name
        assert stat.S_ISSOCK(saved.stat().st_mode)
        assert saved.stat().st_ino != endpoint.stat().st_ino
        endpoint.chmod(0o600)
        assert stat.S_IMODE(saved.stat().st_mode) == 0o640
        layers.snapshot_before(1, upper, changed_paths=['/monitor.sock'])
        (upper / 'independent').write_text('retained')
        layers.snapshot_before(2, upper, changed_paths=['/independent'])
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
            client.sendto(b'live', str(endpoint))
            assert server.recv(4) == b'live'
        # A filesystem restore recreates the inode, not a listening process.
        layers.restore_paths(0, upper, ['/monitor.sock'])
        assert stat.S_ISSOCK(endpoint.stat().st_mode)
        assert stat.S_IMODE(endpoint.stat().st_mode) == 0o640
        assert (upper / 'independent').read_text() == 'retained'
        layers.restore_before(1, upper)
        assert stat.S_ISSOCK(endpoint.stat().st_mode)
        assert stat.S_IMODE(endpoint.stat().st_mode) == 0o600


def test_socket_metadata_creation_and_deletion_are_ledger_fingerprints(tmp_path):
    pool = object.__new__(SharedSemisolate)
    pool.sandbox_dir = tmp_path / 'session'
    pool.workspace = tmp_path / 'app'
    upper = pool.sandbox_dir / 'upperdir'
    endpoint = upper / 'app' / 'monitor.sock'
    endpoint.parent.mkdir(parents=True)
    os.mknod(endpoint, stat.S_IFSOCK | 0o640)
    first = pool.upperdir_digests()['/app/monitor.sock']
    endpoint.chmod(0o600)
    assert pool.upperdir_digests()['/app/monitor.sock'] != first
    endpoint.unlink()
    assert '/app/monitor.sock' not in pool.upperdir_digests()
