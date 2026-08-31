#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agenttx.ledger import Effect, EffectKind, Ledger
from agenttx.policy import CommitPolicy


def _exercise_policy(ws: Path) -> None:
    pol = CommitPolicy(workdir=ws)
    ok = pol.check_path(str(ws / "a.txt"))
    assert ok.allowed, ok
    bad = pol.check_path("/etc/passwd")
    assert not bad.allowed, bad

    led = Ledger()
    led.add_step("w", [Effect(str(ws / "a.txt"), EffectKind.WRITE)])
    pol.assert_committable(led, 0)

    led2 = Ledger()
    led2.add_step("evil", [Effect("/etc/passwd", EffectKind.WRITE)])
    try:
        pol.assert_committable(led2, 0)
        raise AssertionError("expected PermissionError")
    except PermissionError:
        pass


def test_default_policy_allows_workspace_and_denies_system_paths(tmp_path: Path) -> None:
    _exercise_policy(tmp_path)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agenttx-pol-") as directory:
        _exercise_policy(Path(directory))
    print("test_policy OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def test_external_writes_are_opt_in_and_keep_hard_denies(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("AGENTTX_ALLOW_EXTERNAL_WRITES", raising=False)
    closed = CommitPolicy(workdir=tmp_path)
    assert not closed.check_path("/tmp/grid.py").allowed

    monkeypatch.setenv("AGENTTX_ALLOW_EXTERNAL_WRITES", "1")
    opened = CommitPolicy(workdir=tmp_path)
    external = opened.check_path("/tmp/grid.py")
    assert external.allowed, external
    assert "external writes" in external.reason
    denied = opened.check_path("/etc/passwd")
    assert not denied.allowed, denied



def test_fontconfig_cache_writes_are_ignored(tmp_path: Path) -> None:
    pol = CommitPolicy(workdir=tmp_path)
    path = "/home/pengpeng/miniconda3/envs/agenttx/var/cache/fontconfig/foo.cache-12"
    assert pol.is_ignored(path)
    led = Ledger()
    led.add_step("fc", [Effect(path, EffectKind.WRITE)])
    pol.assert_committable(led, 0)


def test_tmp_build_log_is_ignored(tmp_path: Path) -> None:
    pol = CommitPolicy(workdir=tmp_path)
    path = "/tmp/build.log"
    assert pol.is_ignored(path)
    led = Ledger()
    led.add_step("log", [Effect(path, EffectKind.WRITE)])
    pol.assert_committable(led, 0)


def test_agenttx_recovery_receipt_is_not_materialized(tmp_path: Path) -> None:
    pol = CommitPolicy(workdir=tmp_path)
    path = str(tmp_path / ".agenttx/recovery_manifest.json")
    assert pol.is_ignored(str(tmp_path / ".agenttx"))
    assert pol.is_ignored(path)
    led = Ledger()
    led.add_step("manifest", [Effect(path, EffectKind.WRITE)])
    pol.assert_committable(led, 0)
