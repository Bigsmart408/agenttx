#!/usr/bin/env python3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agenttx.ledger import Effect, EffectKind, Ledger


def test_raw_and_cascade():
    led = Ledger()
    led.add_step("w1", [Effect("/a", EffectKind.WRITE)])
    led.add_step("r1", [Effect("/a", EffectKind.READ), Effect("/b", EffectKind.WRITE)])
    led.add_step("w2", [Effect("/b", EffectKind.WRITE)])
    assert led.steps[1].parents == {0}
    assert 1 in led.steps[2].parents
    targets = led.cascade_rollback_targets(0)
    assert targets == [0, 1, 2]
    assert led.causal_dependents(0) == [0, 1, 2]


def test_temporal_vs_causal():
    led = Ledger()
    led.add_step("w0", [Effect("/a", EffectKind.WRITE)])
    led.add_step("w1", [Effect("/c", EffectKind.WRITE)])
    led.add_step("r2", [Effect("/a", EffectKind.READ)])
    assert led.cascade_rollback_targets(0) == [0, 1, 2]
    assert led.causal_dependents(0) == [0, 2]


def test_frontier_blocks_rollback():
    led = Ledger()
    led.add_step("w1", [Effect("/a", EffectKind.WRITE)])
    led.advance_frontier(0)
    try:
        led.cascade_rollback_targets(0)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_roundtrip():
    led = Ledger()
    led.add_step("t", [Effect("/x", EffectKind.WRITE)])
    data = led.to_dict()
    led2 = Ledger.from_dict(data)
    assert led2.steps[0].effects[0].path == "/x"


if __name__ == "__main__":
    test_raw_and_cascade()
    test_temporal_vs_causal()
    test_frontier_blocks_rollback()
    test_roundtrip()
    print("test_ledger OK")
