"""Effect extraction from try summary / optional trace logs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List

from .ledger import Effect, EffectKind

_SUMMARY_LINE = re.compile(r"^(?P<path>/.*?) \((?P<kind>added|modified|deleted)\)$")
_TRACE_LINE = re.compile(r"^(?P<op>[rwdRWD])\s+(?P<path>/.*)$")


@dataclass(frozen=True)
class SummaryEntry:
    path: str
    kind: str


def parse_try_summary(text: str) -> Dict[str, SummaryEntry]:
    out: Dict[str, SummaryEntry] = {}
    for line in text.splitlines():
        line = line.strip()
        m = _SUMMARY_LINE.match(line)
        if not m:
            continue
        out[m.group("path")] = SummaryEntry(path=m.group("path"), kind=m.group("kind"))
    return out


def parse_summary_text(text: str) -> List[Effect]:
    effects: List[Effect] = []
    for ent in parse_try_summary(text).values():
        if ent.kind == "deleted":
            effects.append(Effect(path=ent.path, kind=EffectKind.DELETE))
        else:
            effects.append(Effect(path=ent.path, kind=EffectKind.WRITE))
    return effects


def parse_trace_text(text: str) -> List[Effect]:
    effects: List[Effect] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _TRACE_LINE.match(line)
        if not m:
            continue
        op = m.group("op").lower()
        path = m.group("path")
        if op == "r":
            effects.append(Effect(path=path, kind=EffectKind.READ))
        elif op == "w":
            effects.append(Effect(path=path, kind=EffectKind.WRITE))
        elif op == "d":
            effects.append(Effect(path=path, kind=EffectKind.DELETE))
    return effects


def diff_summaries(before, after):
    effects = []
    before_paths = set(before)
    after_paths = set(after)
    for path in sorted(after_paths - before_paths):
        kind = after[path].kind
        effects.append(Effect(path=path, kind=EffectKind.DELETE if kind == "deleted" else EffectKind.WRITE))
    for path in sorted(before_paths & after_paths):
        if before[path].kind == after[path].kind:
            continue
        kind = after[path].kind
        effects.append(Effect(path=path, kind=EffectKind.DELETE if kind == "deleted" else EffectKind.WRITE))
    return effects


def effects_from_paths(writes=(), deletes=(), reads=()):
    out = []
    for p in reads:
        out.append(Effect(path=p, kind=EffectKind.READ))
    for p in writes:
        out.append(Effect(path=p, kind=EffectKind.WRITE))
    for p in deletes:
        out.append(Effect(path=p, kind=EffectKind.DELETE))
    return out
