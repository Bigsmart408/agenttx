#!/usr/bin/env python3
"""Integration checks on a real YoloFS module, not a mocked filesystem."""

from __future__ import annotations

import argparse
import errno
import json
import os
from pathlib import Path
import signal
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from agenttx.ledger import EffectKind
from agenttx_yolofs.runtime import YoloAgentTX
from agenttx_yolofs.storage import YoloSession


class Recovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agenttx-yolofs-test-", dir="/var/lib")
        self.root = Path(self.temp.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.tx = None

    def tearDown(self):
        if self.tx is not None:
            saved = REPORT_ROOT / self._testMethodName
            saved.mkdir(parents=True, exist_ok=True)
            for path in self.tx.pool.session_dir.glob("*.log"):
                shutil.copy2(path, saved / path.name)
            for path in [self.tx.pool.session_dir / "agenttx.json", self.tx.pool.history.file]:
                if path.exists():
                    shutil.copy2(path, saved / path.name)
            for path in saved.iterdir():
                path.chmod(0o644)
            self.tx.close(destroy=True)
        self.temp.cleanup()

    def begin(self):
        self.tx = YoloAgentTX.begin(self.work, self.root / "state")
        return self.tx

    def call(self, script):
        record = self.tx.call(["sh", "-c", script])
        self.assertEqual(record.returncode, 0, record.stderr)
        return record

    def content(self, name):
        return (self.tx.pool.storage.view / name).read_text()

    def assert_absent(self, *names):
        for name in names:
            self.assertFalse(os.path.lexists(self.tx.pool.storage.view / name), name)

    def test_file_and_context_closure_remount_commit(self):
        (self.work / "a").write_text("baseline")
        self.begin()
        root = self.call("printf bad >a; printf generated-output-7p9")
        independent = self.call("printf keep >b")
        dependent = self.call("cat a >c")
        context = self.call("printf generated-output-7p9 >d")
        read = self.call("cat b")
        targets = self.tx.rollback_causal_from(root.step_id)
        self.assertEqual(set(targets), {root.step_id, dependent.step_id, context.step_id})
        self.assertEqual(self.content("a"), "baseline")
        self.assertEqual(self.content("b"), "keep")
        self.assert_absent("c", "d")
        self.assertEqual(set(self.tx.conversation.active_step_ids()), {independent.step_id, read.step_id})
        journal = (self.tx.pool.storage.artifact / "journal").read_bytes()
        self.assertNotIn(b"P\0", journal, "commands must not imply native snapshots")
        self.tx.pool.storage.remount()
        self.assertEqual(self.content("a"), "baseline")
        self.assertEqual(self.content("b"), "keep")
        self.assert_absent("c", "d")
        self.assertEqual((self.work / "a").read_text(), "baseline")
        self.assertFalse((self.work / "b").exists())
        self.tx.commit()
        self.assertEqual((self.work / "a").read_text(), "baseline")
        self.assertEqual((self.work / "b").read_text(), "keep")
        self.assertEqual(sorted(p.name for p in self.work.iterdir()), ["a", "b"])

    def test_same_generation_retains_earlier_valid_version(self):
        (self.work / "a").write_text("initial")
        self.begin()
        correct = self.call("printf correct >a")
        mtime = (self.tx.pool.storage.view / "a").stat().st_mtime_ns
        root = self.call("printf faulty >a")
        independent = self.call("printf retained >b")
        dependent = self.call("cat a >c")
        targets = self.tx.rollback_causal_from(root.step_id)
        self.assertEqual(set(targets), {root.step_id, dependent.step_id})
        self.assertEqual(self.content("a"), "correct")
        self.assertEqual((self.tx.pool.storage.view / "a").stat().st_mtime_ns, mtime)
        self.assertEqual(self.content("b"), "retained")
        self.assert_absent("c")
        self.assertIn(correct.step_id, self.tx.conversation.active_step_ids())
        self.assertIn(independent.step_id, self.tx.conversation.active_step_ids())
        self.tx.pool.storage.remount()
        self.assertEqual(self.content("a"), "correct")
        self.tx.commit()
        self.assertEqual((self.work / "a").read_text(), "correct")

    def test_directory_rename_delete_symlink_permissions(self):
        tree = self.work / "tree"
        tree.mkdir()
        (tree / "original").write_text("history")
        (tree / "original").chmod(0o640)
        (tree / "alias").symlink_to("original")
        tree.chmod(0o750)
        self.begin()
        root = self.call("mv tree moved; rm moved/original; printf faulty >moved/new")
        independent = self.call("printf keep >independent")
        self.tx.rollback_causal_from(root.step_id)
        view = self.tx.pool.storage.view
        self.assertEqual((view / "tree/original").read_text(), "history")
        self.assertEqual(os.readlink(view / "tree/alias"), "original")
        self.assertEqual((view / "tree/original").stat().st_mode & 0o777, 0o640)
        self.assertEqual((view / "tree").stat().st_mode & 0o777, 0o750)
        self.assertEqual(self.content("independent"), "keep")
        self.assert_absent("moved")
        self.tx.pool.storage.remount()
        self.tx.commit()
        self.assertEqual((self.work / "tree/original").read_text(), "history")
        self.assertEqual(os.readlink(self.work / "tree/alias"), "original")

    def test_base_file_rename(self):
        (self.work / "source").write_text("original")
        self.begin()
        root = self.call("mv source destination")
        self.call("printf keep >other")
        self.call("cat destination >derived")
        self.tx.rollback_causal_from(root.step_id)
        self.assertEqual(self.content("source"), "original")
        self.assertEqual(self.content("other"), "keep")
        self.assert_absent("destination", "derived")
        self.tx.pool.storage.remount()
        self.tx.commit()
        self.assertEqual(sorted(p.name for p in self.work.iterdir()), ["other", "source"])

    def test_deleted_file_negative_dependency(self):
        (self.work / "a").write_text("present")
        self.begin()
        root = self.call("rm a")
        self.call("printf keep >b")
        dependent = self.call("if test ! -e a; then printf derived >c; fi")
        self.assertTrue(any(e.kind == EffectKind.NEGATIVE and e.path == str(self.work / "a")
                            for e in dependent.effects))
        self.assertEqual(set(self.tx.rollback_causal_from(root.step_id)), {0, 2})
        self.assertEqual(self.content("a"), "present")
        self.assertEqual(self.content("b"), "keep")
        self.assert_absent("c")

    def test_multiple_roots(self):
        (self.work / "a").write_text("a0")
        (self.work / "c").write_text("c0")
        self.begin()
        self.call("printf wrong >a")
        self.call("printf keep >b")
        self.call("printf wrong >c")
        self.call("cat a c >joined")
        self.assertEqual(set(self.tx.rollback_causal_many([0, 2])), {0, 2, 3})
        self.assertEqual(self.content("a"), "a0")
        self.assertEqual(self.content("c"), "c0")
        self.assertEqual(self.content("b"), "keep")
        self.assert_absent("joined")

    def test_snapshot_boundary_then_reopen_continue(self):
        (self.work / "a").write_text("a0")
        self.begin()
        self.call("printf faulty >a")
        self.tx.pool.storage.snapshot("before-independent")
        self.call("printf keep >b")
        self.call("cat a >c")
        self.tx.rollback_causal_from(0)
        state = self.tx.pool.session_dir
        self.tx.close(destroy=False)
        self.tx = YoloAgentTX.load(state)
        self.assertEqual(self.content("a"), "a0")
        self.assertEqual(self.content("b"), "keep")
        self.assert_absent("c")
        result = self.call("cat b >d")
        self.assertEqual(result.step_id, 3)
        self.assertEqual(self.content("d"), "keep")
        self.tx.rollback_causal_from(1)
        self.assert_absent("b", "d")
        self.tx.commit()
        self.assertEqual(sorted(p.name for p in self.work.iterdir()), ["a"])

    def test_read_only_call_does_not_create_content_delta(self):
        (self.work / "a").write_text("read-only")
        self.begin()
        self.call("cat a")
        self.assertEqual(self.tx.pool.history.data["events"][-1]["delta"], {})
        self.assertEqual(len(list(self.tx.pool.history.blobs.iterdir())), 1)

    def test_metadata_and_xattrs_survive_recovery_and_publication(self):
        original = self.work / "a"
        original.write_text("initial")
        original.chmod(0o640)
        os.setxattr(original, 'user.agenttx-test', b'original-attribute')
        before = original.stat().st_mtime_ns
        self.begin()
        self.call("printf faulty >a; chmod 600 a")
        self.call("printf keep >b")
        self.tx.rollback_causal_from(0)
        entry = self.tx.pool.storage.view / 'a'
        self.assertEqual(entry.stat().st_mode & 0o777, 0o640)
        self.assertEqual(entry.stat().st_mtime_ns, before)
        self.tx.pool.storage.remount()
        self.tx.commit()
        self.assertEqual(original.read_text(), 'initial')
        self.assertEqual(original.stat().st_mtime_ns, before)
        self.assertEqual(os.getxattr(original, 'user.agenttx-test'), b'original-attribute')

    def test_fork_and_verifier_search_use_isolated_yolofs_mounts(self):
        (self.work / "a").write_text("correct")
        self.begin()
        root = self.call("printf faulty >a")
        self.call("printf retained >b")
        with self.tx.fork() as branch:
            self.assertIsInstance(branch, YoloAgentTX)
            self.assertNotEqual(branch.pool.storage.root, self.tx.pool.storage.root)
            self.assertEqual((branch.pool.storage.view / 'a').read_text(), 'faulty')
            branch.rollback_causal_from(root.step_id)
            self.assertEqual((branch.pool.storage.view / 'a').read_text(), 'correct')
            self.assertEqual(self.content('a'), 'faulty')
        verifier = self.tx.run_verifier(['sh','-c','test "$(cat a)" = correct'])
        self.assertNotEqual(verifier.returncode, 0)
        plan = self.tx.plan_recovery(verifier, max_probes=6, allow_fallback=False)
        self.assertEqual(plan.validation, 'passed', plan)
        self.assertEqual(list(plan.root_step_ids), [root.step_id])
        self.assertEqual(self.content('a'), 'faulty', 'candidate checks must leave parent unchanged')
        self.tx.rollback_causal_many(plan.root_step_ids)
        self.assertEqual(self.content('a'), 'correct')
        self.assertEqual(self.content('b'), 'retained')

    def test_interrupted_recovery_finishes_on_load(self):
        (self.work / "a").write_text("initial")
        self.begin()
        self.call("printf faulty >a")
        self.call("printf retained >b")
        restore = self.tx.pool.rollback_causal_groups
        def interrupted(*args):
            restore(*args)
            raise RuntimeError("simulated controller interruption")
        with patch.object(self.tx.pool, "rollback_causal_groups", side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, "simulated"):
                self.tx.rollback_causal_from(0)
        state = self.tx.pool.session_dir
        self.tx.close(destroy=False)
        self.tx = YoloAgentTX.load(state)
        self.assertEqual(self.content("a"), "initial")
        self.assertEqual(self.content("b"), "retained")
        self.assertEqual(self.tx.ledger.steps[0].status, "rolled_back")
        self.assertEqual(self.tx.conversation.active_step_ids(), [1])
        self.assertFalse((state / "recovery-intent.json").exists())
        self.tx.commit()
        self.assertEqual((self.work / "b").read_text(), "retained")

    def test_timeout_effect_is_recorded_and_can_be_rolled_back(self):
        (self.work / "a").write_text("initial")
        self.begin()
        result = self.tx.call(["sh", "-c", "printf partial >a; sleep 30"], timeout_s=.2)
        self.assertEqual(result.returncode, 124)
        self.assertEqual(self.content("a"), "partial")
        self.tx.rollback_causal_from(result.step_id)
        self.assertEqual(self.content("a"), "initial")

    def test_background_process_survives_selective_recovery(self):
        (self.work / "a").write_text("a0")
        self.begin()
        self.call("printf faulty >a")
        result = self.call("sleep 30 >/dev/null 2>&1 & echo $!")
        pid = int(result.stdout.strip())
        os.kill(pid, 0)
        self.tx.rollback_causal_from(0)
        os.kill(pid, 0)
        self.assertEqual(self.content("a"), "a0")
        os.kill(pid, signal.SIGTERM)

    def test_snapshot_generations_and_writable_fd_gate(self):
        storage = YoloSession(self.work, self.root / "storage")
        try:
            points = []
            for generation in (1, 2, 3):
                (storage.view / "seed").write_bytes(bytes([generation]) * 4096)
                points.append(storage.snapshot(f"generation-{generation}"))
            (storage.view / "seed").write_bytes(b"\xfe" * 4096)
            for generation, point in enumerate(points, 1):
                storage.travel(point)
                self.assertEqual((storage.view / "seed").read_bytes(), bytes([generation]) * 4096)
            with (storage.view / "seed").open("r+b") as stream:
                stream.write(b"x")
                with self.assertRaises(OSError) as failure:
                    storage.snapshot("busy")
                self.assertEqual(failure.exception.errno, errno.EBUSY)
            storage.snapshot("after-close")
            storage.remount()
            self.assertEqual((storage.view / "seed").read_bytes()[0:1], b"x")
        finally:
            storage.close(destroy=True)

    def test_external_write_cannot_be_published(self):
        self.begin()
        outside = self.root / "work-sibling"
        outside.write_text("base")
        result = self.call(f"printf modified >{outside}")
        with self.assertRaisesRegex(ValueError, "outside workspace"):
            self.tx.commit()
        self.assertEqual(outside.read_text(), "base")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--test")
    args = parser.parse_args()
    REPORT_ROOT = args.output.with_suffix("")
    suite = (unittest.defaultTestLoader.loadTestsFromName(args.test, Recovery) if args.test else
             unittest.defaultTestLoader.loadTestsFromTestCase(Recovery))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = dict(tests=result.testsRun, passed=result.testsRun-len(result.failures)-len(result.errors),
                  failures=[dict(test=str(test), traceback=detail) for test, detail in result.failures],
                  errors=[dict(test=str(test), traceback=detail) for test, detail in result.errors],
                  model_calls=0, real_filesystem="yolofs", skipped=len(result.skipped))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    raise SystemExit(0 if result.wasSuccessful() and not result.skipped else 1)
