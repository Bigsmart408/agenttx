#!/usr/bin/env python3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agenttx.effects import (
    diff_summaries,
    parse_summary_text,
    parse_trace_text,
    parse_try_summary,
)
from agenttx.ledger import EffectKind


def test_summary():
    text = """
/home/u/a.txt (modified)
/home/u/b.txt (added)
/home/u/c.txt (deleted)
"""
    eff = parse_summary_text(text)
    kinds = {e.path: e.kind for e in eff}
    assert kinds["/home/u/a.txt"] == EffectKind.WRITE
    assert kinds["/home/u/b.txt"] == EffectKind.WRITE
    assert kinds["/home/u/c.txt"] == EffectKind.DELETE


def test_trace():
    text = "r /tmp/x\nw /tmp/y\nd /tmp/z\n"
    eff = parse_trace_text(text)
    assert [e.kind for e in eff] == [EffectKind.READ, EffectKind.WRITE, EffectKind.DELETE]


def test_try_summary_and_diff():
    text = "/x (added)\n/y (modified)\n"
    parsed = parse_try_summary(text)
    assert parsed["/x"].kind == "added"
    before = {"/y": parsed["/y"]}
    after = parse_try_summary("/y (deleted)\n/z (added)\n")
    kinds = {e.path: e.kind for e in diff_summaries(before, after)}
    assert kinds["/y"] == EffectKind.DELETE
    assert kinds["/z"] == EffectKind.WRITE


if __name__ == "__main__":
    test_summary()
    test_trace()
    test_try_summary_and_diff()
    print("test_effects OK")
