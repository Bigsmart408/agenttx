#!/usr/bin/env python3
"""Recompute native Motivation from the retained Clean effect captures.

Only flat native_traces exports are read. Runtime sessions are never traversed.
One trace per task: full sweep first, then the listed preflight/repair captures
with exactly the full-sweep SWE-agent configuration. Selection never uses the
verifier result, trace length, or measured dependency opportunity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from experiments.scripts.analyze_native_dependency_opportunity import analyze_trace, write_csv

ROOT = Path(__file__).resolve().parents[2]
CAPTURES = Path('/mnt/data/agenttx-runtime/results/motivation/real_trajectory_audit')
CONFIG = '4719135d5dbe6ccfd09eea834dc4a593836dcb4fe1e7082b12c7f0622bde6977'
# Primary records always take precedence, including prefixes and failed tasks.
SOURCE_ORDER = (
    'full_sweagent_swe_20261006', 'full_sweagent_tb_20261006',
    'clean_native_swe_full_preflight_20261006',
    'clean_native_swe_matplotlib_fontcache_20261006',
    'clean_native_swe_tb_retry_20261006',
    'clean_native_official_container_phase1_20261006',
)
OTHER_CAPTURES = (
    'clean_native_official_container_smoke_20261006',
    'clean_native_official_container_validated_20261006',
    'clean_native_swe_image_preflight_20261006',
    'clean_native_swe_matplotlib_retry_20261006',
)


def summarize(tasks: list[dict], roots: list[dict]) -> dict:
    return dict(
        traces=len(tasks),
        with_cross_turn_dependency=sum(t['cross_turn_edges'] > 0 for t in tasks),
        with_edge_at_least_five_turns=sum(t['max_edge_turn_span'] >= 5 for t in tasks),
        with_later_independent_write=sum(t['roots_with_independent_mutating_work'] > 0 for t in tasks),
        with_interleaved_independent_write=sum(t['roots_with_interleaved_long_chain'] > 0 for t in tasks),
        candidate_roots=len(roots),
        roots_with_independent_write=sum(r['temporal_extra_mutating_steps'] > 0 for r in roots),
        roots_without_independent_write=sum(r['temporal_extra_mutating_steps'] == 0 for r in roots),
        roots_with_interleaved_write=sum(bool(r['long_chain_with_interleaved_independent_work']) for r in roots),
        roots_with_no_later_dependent_step=sum(r['closure_steps'] == 1 for r in roots),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture-root', type=Path, default=CAPTURES)
    parser.add_argument('--swe-manifest', type=Path, default=Path('/mnt/data/agenttx-runtime/cache/swe_bench/lite_test_manifest.json'))
    parser.add_argument('--tb-sources', type=Path, default=ROOT / 'review/realworld-results-20261030/source-attempts.json')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'review/native-clean-opportunity-20261101')
    args = parser.parse_args()
    swe = json.loads(args.swe_manifest.read_bytes())
    sources = json.loads(args.tb_sources.read_bytes())['attempts']
    cohorts = {
        'swe_bench_lite': {t['instance_id'] for t in swe},
        'terminal_bench': {p.split('/cells/')[1].split('/')[0] for p in sources
                           if '/cells/' in p and '/sweagent/clean_recovery/' in p},
    }
    assert len(cohorts['swe_bench_lite']) == 300
    assert len(cohorts['terminal_bench']) == 241
    selected: dict[tuple[str, str], dict] = {}
    inventory, task_rows, root_rows = [], [], []
    unreadable_tasks = set()
    for source in SOURCE_ORDER + OTHER_CAPTURES:
        print(f'Analyzing {source}', flush=True)
        for path in sorted((args.capture_root / source / 'native_traces').glob('*.json')):
            raw = path.read_bytes()
            item = dict(source=source, path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                        suite='', task='', config_hash='', status='', error='')
            inventory.append(item)
            try:
                data = json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                item['status'] = 'unreadable'
                item['error'] = type(exc).__name__
                suite = 'terminal_bench' if path.stem.startswith('tb__') else 'swe_bench_lite'
                task = path.stem.split('__', 1)[1]
                item.update(suite=suite, task=task)
                unreadable_tasks.add((suite, task))
                continue
            suite, task = data['suite'], data['task']
            item.update(suite=suite, task=task, config_hash=(data.get('config') or {}).get('sweagent_config_hash', ''))
            key = (suite, task)
            if task not in cohorts.get(suite, set()):
                item['status'] = 'outside_cohort'
            elif item['config_hash'] != CONFIG or source not in SOURCE_ORDER:
                item['status'] = 'other_configuration_or_preflight'
            elif key in selected:
                item['status'] = 'duplicate_not_selected'
            else:
                item['status'] = 'selected'
                selected[key] = item
                summary, roots = analyze_trace(data)
                summary.update(trace_path=str(path), trace_sha256=item['sha256'], capture_source=source)
                task_rows.append(summary)
                for row in roots:
                    row.update(suite=suite, trace_path=str(path), trace_sha256=item['sha256'])
                root_rows.extend(roots)
    coverage = []
    failures = {}
    for suite, short in [('swe_bench_lite', 'swe'), ('terminal_bench', 'tb')]:
        path = args.capture_root / f'full_sweagent_{short}_20261006' / f'capture_failures_{short}.json'
        for row in json.loads(path.read_bytes()):
            failures[suite, row['task']] = row['error']
    by_task = {(t['suite'], t['task']): t for t in task_rows}
    for suite, ids in cohorts.items():
        for task in sorted(ids):
            row = by_task.get((suite, task))
            status = row['trace_quality'] if row else ('unreadable_export' if (suite, task) in unreadable_tasks else 'no_eligible_effect_capture')
            coverage.append(dict(suite=suite, task=task, status=status,
                                 trace_path=row['trace_path'] if row else '',
                                 capture_error=failures.get((suite, task), '')))
    report = dict(
        schema='agenttx.native_clean_full_opportunity/v1',
        corpus='Dedicated fault-free SWE-agent effect-capture sweep (2026-10-06), retained exports audited 2026-11-01',
        config_hash=CONFIG, source_order=list(SOURCE_ORDER),
        selection='One record per task; primary full-sweep export before fixed-config supplements; no selection by outcome or opportunity.',
        metric='Unchanged observed-path-v2 last-writer graph; complete traces form the paper denominator; prefixes and invalid records reported separately.',
        interpretation='Observed workspace-path closure only. Unobserved context/aliases/external effects may add dependencies. Hypothetical roots measure structure, not actual fault frequency, recovery success, token savings, or certified safe retention.',
        later_clean_campaign='Recent 300-SWE / 241-TB Clean model transcripts are a separate corpus. Retention removed detailed effect ledgers; model transcripts alone do not supply the observed dynamic file accesses required by this analysis.',
        inputs={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__), Path(__file__).with_name('analyze_native_dependency_opportunity.py'), args.swe_manifest, args.tb_sources]},
        inventory_status=dict(Counter(i['status'] for i in inventory)), suites={},
    )
    for suite, ids in cohorts.items():
        tasks = [t for t in task_rows if t['suite'] == suite]
        roots = [r for r in root_rows if r['suite'] == suite]
        subsets = {}
        for quality in ['complete', 'prefix', 'invalid']:
            subsets[quality] = summarize([t for t in tasks if t['trace_quality'] == quality],
                                          [r for r in roots if r['trace_quality'] == quality])
        report['suites'][suite] = dict(cohort_tasks=len(ids), selected_effect_traces=len(tasks),
            coverage_status=dict(Counter(c['status'] for c in coverage if c['suite'] == suite)),
            all_observed=summarize(tasks, roots), **subsets)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in [('capture_inventory', inventory), ('coverage', coverage), ('task_summary', task_rows), ('root_opportunities', root_rows)]:
        write_csv(args.output_dir / f'{name}.csv', rows)
    (args.output_dir / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report['suites'], indent=2))


if __name__ == '__main__':
    main()
