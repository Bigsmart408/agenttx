import errno
import os
from pathlib import Path
import stat
import unittest
from unittest.mock import patch

from agenttx.layers import LayerStore


class WhiteoutLinkLimitTests(unittest.TestCase):
    def test_full_incremental_and_restore_keep_whiteouts_at_link_limit(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            upper = root / "upper"
            upper.mkdir()
            whiteout = upper / "deleted"
            try:
                os.mknod(whiteout, stat.S_IFCHR | 0o640, os.makedev(0, 0))
            except PermissionError:
                self.skipTest("requires CAP_MKNOD")
            store = LayerStore(root / "layers")
            real_link = os.link

            def limited_link(source, destination, **kwargs):
                node = Path(source).lstat()
                if stat.S_ISCHR(node.st_mode) and node.st_rdev == 0:
                    raise OSError(errno.EMLINK, "Too many links", str(source))
                return real_link(source, destination, **kwargs)

            with patch("agenttx.layers.os.link", side_effect=limited_link):
                first = store.snapshot_before(0, upper)
                (upper / "valid").write_text("independent")
                second = store.snapshot_before(1, upper, changed_paths=["/valid"])
                (upper / "other").write_text("retained")
                store.restore_paths(0, upper, ["/deleted"])
                self.assertEqual((upper / "other").read_text(), "retained")
                store.restore_before(1, upper)
            for node in (first / "deleted", second / "deleted", upper / "deleted"):
                saved = node.lstat()
                self.assertTrue(stat.S_ISCHR(saved.st_mode))
                self.assertEqual(saved.st_rdev, 0)
                self.assertEqual(stat.S_IMODE(saved.st_mode), 0o640)
            self.assertEqual((upper / "valid").read_text(), "independent")

    def test_non_link_limit_errors_propagate(self):
        from agenttx.layers import _link_whiteout

        failure = OSError(errno.ENOSPC, "No space left")
        with patch("agenttx.layers.os.link", side_effect=failure), \
                patch("agenttx.layers._copy_special_node") as copy_node:
            with self.assertRaises(OSError) as caught:
                _link_whiteout(Path("source"), Path("destination"), None)
            self.assertIs(caught.exception, failure)
            copy_node.assert_not_called()


if __name__ == "__main__":
    unittest.main()
