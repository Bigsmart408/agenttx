#!/usr/bin/env python3
"""Unit tests for causal ledger (no try required)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agenttx.ledger import Effect, EffectKind, Ledger


def test_raw_and_cascade() -> None:
    led = Ledger()
    led.add_step("w1", [Effect("/a", EffectKind.WRITE)])
    led.add_step("r1", [Effect("/a", EffectKind.READ), Effect("/b", EffectKind.WRITE)])
    led.add_step("w2", [Effect("/b", EffectKind.WRITE)])
    assert led.steps[1].parents == {0}
    assert led.steps[2].parents == {1}
    assert led.cascade_rollback_targets(0) == [0, 1, 2]
    assert led.cascade_rollback_targets(1) == [1, 2]


def test_frontier() -> None:
    led = Ledger()
    led.add_step("w1", [Effect("/a", EffectKind.WRITE)])
    led.add_step("w2", [Effect("/b", EffectKind.WRITE)])
    led.advance_frontier(0)
    assert led.steps[0].status == "committed"
    assert led.committed_frontier == 0
    s = led.add_step("w3", [Effect("/a", EffectKind.WRITE)])
    assert s.parents == set()


def main() -> int:
    test_raw_and_cascade()
    test_frontier()
    print("test_ledger: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())