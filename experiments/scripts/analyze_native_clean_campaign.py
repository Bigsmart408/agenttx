#!/usr/bin/env python3
"""Audit the completed autonomous SWE-agent/Codex Clean campaign.

Read exactly the registered cell exports, without replaying tasks or calling a
model. Keep incomplete/invalid captures separate and include every complete
trace, irrespective of task outcome or observed preservation opportunity.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
from pathlib import Path
import statistics

from experiments.scripts.analyze_native_dependency_opportunity import analyze_trace, write_csv
from experiments.scripts.analyze_native_agent_opportunity import summarize

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COHORT = ROOT.parent / 'agenttx-experiment-data/results/native-clean-swe-tb-20261102-r1'
DEFAULT_OUTPUT = ROOT / 'review/native-clean-campaign-20261104'


def digest(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def analyze_cell(cell: dict) -> tuple[dict, list[dict], dict, str]:
    path = Path(cell['trace_path'])
    raw = path.read_bytes()
    data = json.loads(raw)
    expected_suite = {'swe': 'swe_bench_lite', 'tb': 'terminal_bench'}[cell['suite']]
    assert (data['suite'], data['task']) == (expected_suite, cell['task']), path
    assert not data['fault_injected'] and not data['precrash_progress_seeded'], path
    assert data['config']['model'] == 'gpt-5.6-luna', path
    assert data['config']['reasoning_effort'] == 'max', path
    if cell['harness'] == 'codex':
        assert data['turn_boundary'] == 'one native codex exec turn; tool events are steps'
        assert len(data['conversation']['turns']) == 1
    config_hash = hashlib.sha256(json.dumps(data['config'], sort_keys=True).encode()).hexdigest()
    summary, trace_roots = analyze_trace(data)
    result_path = path.with_name('result.json')
    result = json.loads(result_path.read_bytes())
    assert result['completed'] and len(data['ledger']['steps']) == cell['trace_steps'], path
    common = dict(harness=cell['harness'], trace_path=str(path),
                  trace_sha256=hashlib.sha256(raw).hexdigest())
    summary.update(common, official_success=result['official_success'])
    for row in trace_roots:
        row.update(common, suite=expected_suite)
    entry = dict(suite=expected_suite, harness=cell['harness'], task=cell['task'],
                 **{k: common[k] for k in ('trace_path', 'trace_sha256')},
                 bytes=len(raw), trace_quality=summary['trace_quality'],
                 capture_valid=data.get('capture_valid', True),
                 capture_exclusions=';'.join(data.get('capture_exclusions') or []),
                 result_path=str(result_path), result_sha256=digest(result_path),
                 config_sha256=config_hash)
    return summary, trace_roots, entry, config_hash


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', type=Path, default=DEFAULT_COHORT)
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    registration = json.loads((args.cohort / 'registration.json').read_bytes())
    cells = json.loads((args.cohort / 'cells.json').read_bytes())
    assert len(cells) == registration['expected_cells'] == 1082
    assert not registration['fault_injected'] and not registration['precrash_progress_seeded']
    assert registration['model'] == 'gpt-5.6-luna' and registration['reasoning_effort'] == 'max'
    assert all(c['state'] == 'finished' for c in cells)
    tasks, roots, inventory = [], [], []
    seen = set()
    configurations = Counter()
    for cell in cells:
        key = (cell['suite'], cell['harness'], cell['task'])
        assert key not in seen, key
        seen.add(key)
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        for index, (summary, trace_roots, entry, config_hash) in enumerate(executor.map(analyze_cell, cells)):
            tasks.append(summary)
            roots.extend(trace_roots)
            inventory.append(entry)
            configurations[entry['harness'], config_hash] += 1
            if (index + 1) % 50 == 0:
                print(f'Analyzed {index + 1}/{len(cells)} Clean traces', flush=True)
    groups = {}
    for suite in ('swe_bench_lite', 'terminal_bench'):
        for harness in ('sweagent', 'codex'):
            tt = [t for t in tasks if (t['suite'], t['harness']) == (suite, harness)]
            rr = [r for r in roots if (r['suite'], r['harness']) == (suite, harness)]
            expected = 300 if suite == 'swe_bench_lite' else 241
            assert len(tt) == expected
            subsets = {}
            for quality in ('complete', 'prefix', 'invalid'):
                tq = [t for t in tt if t['trace_quality'] == quality]
                rq = [r for r in rr if r['trace_quality'] == quality]
                stats = summarize(tq, rq)
                stats.update(tool_steps=sum(t['steps'] for t in tq),
                             mutating_steps=sum(t['mutating_steps'] for t in tq),
                             with_temporal_extra_calls=sum(t['roots_with_temporal_waste'] > 0 for t in tq),
                             with_at_least_two_mutating_steps=sum(t['mutating_steps'] >= 2 for t in tq),
                             median_steps=statistics.median(t['steps'] for t in tq) if tq else 0,
                             max_dependency_call_span=max((t['max_edge_step_span'] for t in tq), default=0),
                             longest_dependency_path=max((t['longest_observed_dependency_path'] for t in tq), default=0))
                for metric in ('with_tool_dependency', 'with_cross_turn_dependency',
                               'with_later_independent_write', 'with_interleaved_independent_write',
                               'with_temporal_extra_calls'):
                    stats[f'{metric}_pct'] = 100 * stats[metric] / len(tq) if tq else None
                subsets[quality] = stats
            groups[f'{suite}_{harness}'] = dict(
                suite=suite, harness=harness, registered_tasks=expected,
                finished_executions=len(tt), official_successes=sum(t['official_success'] for t in tt),
                quality_counts=dict(Counter(t['trace_quality'] for t in tt)),
                turn_metric_available=harness == 'sweagent', **subsets)
    report = dict(
        schema='agenttx.native_clean_campaign_opportunity/v1',
        cohort=str(args.cohort), registered_executions=len(cells),
        population='One registered autonomous fault-free live-model execution per task and agent; 300 SWE-Bench Lite and 241 Terminal-Bench tasks per agent.',
        selection='All registered cells; no substitution of task attempts. Complete captures form incidence denominators, including failed tasks and zero-opportunity traces. Prefix and invalid captures are reported separately.',
        metrics='Unchanged observed-path-v2 last-writer graph and opaque-context barriers. Every mutating call with later work is a hypothetical root. Interleaving requires a mutating call outside its closure strictly between the root and an affected descendant.',
        turn_semantics='Codex has one exported outer exec turn. Common paper panels use tool-call dependencies and interleaved writes, independent of turn export grouping.',
        registration=registration,
        configuration_counts=[dict(harness=h, sha256=cfg, traces=n) for (h, cfg), n in sorted(configurations.items())],
        inputs={str(p): digest(p) for p in (args.cohort / 'cells.json', args.cohort / 'registration.json',
                Path(__file__), Path(__file__).with_name('analyze_native_dependency_opportunity.py'),
                Path(__file__).with_name('analyze_native_agent_opportunity.py'))},
        groups=groups)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in [('task_summary', tasks), ('root_opportunities', roots), ('capture_inventory', inventory)]:
        write_csv(args.output_dir / f'{name}.csv', rows)
    (args.output_dir / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(groups, indent=2), flush=True)


if __name__ == '__main__':
    main()
