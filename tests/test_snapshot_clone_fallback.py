import os
from subprocess import CompletedProcess
from unittest.mock import patch

from agenttx.layers import _clone_snapshot_tree


def test_native_clone_failure_falls_back_once_for_nested_tree(tmp_path):
    source = tmp_path / "source"
    nested = source / "a" / "b"
    nested.mkdir(parents=True)
    (nested / "file").write_text("snapshot content")
    (nested / "alias").symlink_to("file")
    destination = tmp_path / "destination"

    def fail_native_clone(*args, **kwargs):
        # A failed copy may already have created part of the destination.
        destination.mkdir()
        (destination / "partial").write_text("incomplete")
        return CompletedProcess(args[0], 1, stderr=b"hard-link limit")

    with patch("agenttx.layers.shutil.which", return_value="/usr/bin/cp"), \
            patch("agenttx.layers.subprocess.run", side_effect=fail_native_clone) as native:
        _clone_snapshot_tree(source, destination)

    native.assert_called_once()
    assert not (destination / "partial").exists()
    saved = destination / "a" / "b"
    assert (saved / "file").read_text() == "snapshot content"
    assert os.stat(saved / "file").st_ino == os.stat(nested / "file").st_ino
    assert (saved / "alias").is_symlink()
    assert os.readlink(saved / "alias") == "file"
