#!/usr/bin/env python3
"""Derive the paper's four-policy comparison from the audited full export."""
from __future__ import annotations

import ast
import csv
import hashlib
import json
from pathlib import Path

from analyze_controlled_both_agents import analyze, LABELS

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'review/controlled-both-agents-20261110'
OUT = BASE / 'four-policy'
POLICIES = ('causal', 'turn_checkpoint', 'temporal_checkpoint', 'whole_branch_abort')
STRINGS = {'key', 'trace_id', 'manifest_hash', 'family', 'policy', 'state',
           'harness', 'result_path', 'sha256', 'task_sha256'}


def main():
    source = BASE / 'runs.csv'
    with source.open() as f:
        raw = [r for r in csv.DictReader(f) if r['policy'] in POLICIES]
    rows = []
    for r in raw:
        typed = {}
        for k, v in r.items():
            if k in STRINGS:
                typed[k] = v
            elif k == 'violations':
                typed[k] = ast.literal_eval(v)
            elif v == '':
                typed[k] = None
            elif v in ('True', 'False'):
                typed[k] = v == 'True'
            else:
                typed[k] = float(v) if '.' in v or 'e' in v.lower() else int(v)
        rows.append(typed)
    assert len(rows) == 9600
    report = dict(source=str(source), source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  policies=POLICIES, runs=len(rows), passed=sum(r['completed'] for r in rows),
                  by_harness={h: analyze([r for r in rows if r['harness'] == h], POLICIES)
                              for h in ('sweagent', 'codex')},
                  pooled=analyze(rows, POLICIES),
                  failures=[r for r in rows if not r['completed']])
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / 'runs.csv').open('w') as f:
        w = csv.DictWriter(f, fieldnames=list(raw[0]))
        w.writeheader()
        w.writerows(raw)
    (OUT / 'analysis.json').write_text(json.dumps(report, indent=2) + '\n')
    lines = ['# Four-policy controlled comparison', '',
             '400 cases × 2 agents × 4 policies × 3 repeats = 9,600 runs.', '',
             '| Control | SWE-agent tokens saved | Codex tokens saved | Combined tokens saved | Combined workflow time saved |',
             '|---|---:|---:|---:|---:|']
    for p in POLICIES[1:]:
        a = report['by_harness']['sweagent']['comparisons'][p]['metrics']['total_tokens']['saving_pct']
        b = report['by_harness']['codex']['comparisons'][p]['metrics']['total_tokens']['saving_pct']
        c = report['pooled']['comparisons'][p]['metrics']
        lines.append(f"| {LABELS[p]} | {a:.2f}% | {b:.2f}% | {c['total_tokens']['saving_pct']:.2f}% | "
                     f"{c['full_wall_s']['saving_pct']:.2f}% |")
    (OUT / 'summary.md').write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines))
    print('Common successful groups:', report['pooled']['common_success_pairs'])
    print('Family token savings:', {k: v['temporal_checkpoint']['metrics']['total_tokens']['saving_pct']
                                   for k, v in report['pooled']['families'].items()})
    print('Fault-position time savings:', report['pooled']['families']['mb3_fault_position']['temporal_checkpoint']['metrics']['full_wall_s']['saving_pct'])
    print('Fault-position model-time savings:', report['pooled']['families']['mb3_fault_position']['temporal_checkpoint']['metrics']['model_wall_s']['saving_pct'])


if __name__ == '__main__':
    main()
