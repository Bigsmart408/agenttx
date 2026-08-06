#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agenttx.ledger import Effect, EffectKind, Ledger
from agenttx.policy import CommitPolicy


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agenttx-pol-") as d:
        ws = Path(d)
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
    print("test_policy OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
