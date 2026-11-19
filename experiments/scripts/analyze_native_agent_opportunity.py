#!/usr/bin/env python3
"""Audit retained native SWE-agent and Codex captures with common step metrics.

Read only explicitly selected flat exports. Preserve the SWE-agent selection
from the 2026-11-01 audit; use each Codex primary capture without replacing
invalid or partial attempts with successful retries. Do not invent inner
model turns for the single Codex exec turn.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path

from experiments.scripts.analyze_native_dependency_opportunity import analyze_trace, write_csv

ROOT = Path(__file__).resolve().parents[2]
CAPTURES = Path('/mnt/data/agenttx-runtime/results/motivation/real_trajectory_audit')
PREVIOUS = ROOT / 'review/native-clean-opportunity-20261101'
OUT = ROOT / 'review/native-agent-opportunity-20261102'


def summarize(tasks, roots):
    return dict(
        traces=len(tasks),
        with_tool_dependency=sum(t['observed_workspace_edges'] > 0 for t in tasks),
        with_cross_turn_dependency=sum(t['cross_turn_edges'] > 0 for t in tasks),
        with_later_independent_write=sum(t['roots_with_independent_mutating_work'] > 0 for t in tasks),
        with_interleaved_independent_write=sum(t['roots_with_interleaved_work'] > 0 for t in tasks),
        with_cross_turn_interleaved_write=sum(t['roots_with_interleaved_long_chain'] > 0 for t in tasks),
        candidate_roots=len(roots),
        roots_with_independent_write=sum(r['temporal_extra_mutating_steps'] > 0 for r in roots),
        roots_with_interleaved_write=sum(r['interleaved_independent_work'] for r in roots),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture-root', type=Path, default=CAPTURES)
    parser.add_argument('--output-dir', type=Path, default=OUT)
    args = parser.parse_args()
    coverage_before = list(csv.DictReader((PREVIOUS / 'coverage.csv').open()))
    cohort_ids = {suite: {r['task'] for r in coverage_before if r['suite'] == suite}
                  for suite in ('swe_bench_lite', 'terminal_bench')}
    # Existing SWE-agent selection is fixed independently of measured results.
    selected = [(r['suite'], r['task'], 'sweagent', Path(r['trace_path']))
                for r in csv.DictReader((PREVIOUS / 'coverage.csv').open()) if r['trace_path']]
    # Read primary Codex captures only, retaining incomplete/invalid attempts.
    for suite, short in [('swe_bench_lite', 'swe'), ('terminal_bench', 'tb')]:
        for path in sorted((args.capture_root / f'full_codex_{short}_20261006' / 'native_traces').glob('*.json')):
            selected.append((suite, path.stem.split('__', 2)[-1], 'codex', path))
    tasks, roots, inventory = [], [], []
    seen = set()
    config_counts = Counter()
    for i, (suite_hint, task_hint, harness, path) in enumerate(selected):
        raw = path.read_bytes()
        entry = dict(harness=harness, suite=suite_hint, task=task_hint,
                     path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                     status='', capture_exclusions='')
        inventory.append(entry)
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError):
            entry['status'] = 'unreadable_export'
            continue
        suite, task = data['suite'], data['task']
        entry.update(suite=suite, task=task)
        assert suite == suite_hint and task in cohort_ids[suite], (path, suite, task)
        assert not data.get('fault_injected') and not data.get('precrash_progress_seeded'), path
        key = (harness, suite, task)
        assert key not in seen, key
        seen.add(key)
        config = data.get('config', {})
        if harness == 'codex':
            assert str(data.get('source_system', '')).startswith('Codex native'), path
            assert config.get('model') == 'gpt-5.6-luna' and config.get('reasoning_effort') == 'max', path
            assert data['turn_boundary'] == 'one native codex exec turn; tool events are steps', path
            assert len(data['conversation']['turns']) == 1, path
        else:
            assert config.get('sweagent_config_hash') == '4719135d5dbe6ccfd09eea834dc4a593836dcb4fe1e7082b12c7f0622bde6977', path
        config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        config_counts[harness, config_hash] += 1
        summary, trace_roots = analyze_trace(data)
        summary.update(harness=harness, trace_path=str(path), trace_sha256=entry['sha256'])
        tasks.append(summary)
        for row in trace_roots:
            row.update(harness=harness, suite=suite, trace_path=str(path), trace_sha256=entry['sha256'])
        roots.extend(trace_roots)
        entry.update(status=summary['trace_quality'],
                     capture_exclusions=';'.join(data.get('capture_exclusions') or []))
        if (i + 1) % 50 == 0:
            print(f'Analyzed {i + 1}/{len(selected)} captures', flush=True)
    for harness in ('sweagent', 'codex'):
        assert len({cfg for (h, cfg) in config_counts if h == harness}) == 1
    groups, coverage = {}, []
    old_summary = json.loads((PREVIOUS / 'summary.json').read_text())
    for harness in ('sweagent', 'codex'):
        for suite, ids in cohort_ids.items():
            tt = [t for t in tasks if t['harness'] == harness and t['suite'] == suite]
            rr = [r for r in roots if r['harness'] == harness and r['suite'] == suite]
            by_task = {t['task']: t for t in tt}
            unreadable = {e['task'] for e in inventory if e['harness'] == harness and e['suite'] == suite and e['status'] == 'unreadable_export'}
            for task in sorted(ids):
                t = by_task.get(task)
                coverage.append(dict(harness=harness, suite=suite, task=task,
                                     status=t['trace_quality'] if t else ('unreadable_export' if task in unreadable else 'no_selected_effect_capture'),
                                     trace_path=t['trace_path'] if t else ''))
            stats = dict(harness=harness, suite=suite, cohort_tasks=len(ids),
                         selected_captures=len(tt),
                         turn_metric_available=harness == 'sweagent',
                         complete=summarize([t for t in tt if t['trace_quality'] == 'complete'],
                                            [r for r in rr if r['trace_quality'] == 'complete']),
                         quality_counts=dict(Counter(t['trace_quality'] for t in tt)),
                         excluded_capture_reasons=dict(Counter(reason for e in inventory
                             if e['harness']==harness and e['suite']==suite
                             for reason in e['capture_exclusions'].split(';') if reason)))
            groups[f'{suite}_{harness}'] = stats
            if harness == 'sweagent':
                old = old_summary['suites'][suite]['complete']
                for new_key, old_key in [('traces','traces'),('with_cross_turn_dependency','with_cross_turn_dependency'),
                    ('with_cross_turn_interleaved_write','with_interleaved_independent_write'),
                    ('candidate_roots','candidate_roots'),('roots_with_independent_write','roots_with_independent_write')]:
                    assert stats['complete'][new_key] == old[old_key], (suite, new_key)
    report = dict(schema='agenttx.native_agent_opportunity/v1',
        population='Live-model native SWE-agent and Codex runs without injected faults; one fixed capture per task and harness.',
        metrics='Observed path last-writer dependencies and conservative opaque-context barriers. Interleaving requires a write outside the closure strictly between a root and an affected descendant, independent of exported turn grouping.',
        turn_semantics='Codex exports one outer exec turn. Cross-turn statistics are available only for SWE-agent; shared figure metrics use tool steps.',
        selection='SWE-agent selection unchanged from 2026-11-01; Codex primary full-sweep exports only. Complete traces include task-test failures; invalid and prefix captures excluded from incidence denominators and reported separately. Zero-opportunity complete traces and roots included.',
        configuration_hash_counts=[dict(harness=h, sha256=cfg, captures=count) for (h,cfg),count in sorted(config_counts.items())],
        inputs={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__), Path(__file__).with_name('analyze_native_dependency_opportunity.py'), PREVIOUS/'coverage.csv',PREVIOUS/'summary.json']},
        groups=groups)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in [('capture_inventory',inventory),('task_summary',tasks),('root_opportunities',roots),('coverage',coverage)]:
        write_csv(args.output_dir / f'{name}.csv', rows)
    (args.output_dir/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(groups,indent=2))


if __name__ == '__main__':
    main()
