#!/usr/bin/env python3
"""Add the completed Codex/SWE campaign to frozen real-workload statistics.

Raw runs are read-only. Keep the previous three cohorts unchanged; pair only
successful terminal runs with returned model usage, including document replay.
"""
from collections import Counter
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'experiments/scripts'))
from analyze_realworld_final import comparison

RAW = Path('/mnt/data/agenttx-runtime/results/codex-swe-full-recovery-20261104-r1')
BASE = ROOT / 'review/realworld-results-20261101/analysis.json'
TIME = ROOT / 'review/realworld-time-check-20261102/analysis.json'
OUT = ROOT / 'review/realworld-results-20261106'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sources = {}

    def read(path):
        content = path.read_bytes()
        sources[str(path)] = hashlib.sha256(content).hexdigest()
        return json.loads(content)

    base = read(BASE)
    times = read(TIME)
    registration = read(RAW / 'registration.json')
    progress = read(RAW / 'progress.json')
    rows = read(RAW / 'cells.json')
    assert len(rows) == registration['cell_count'] == 1800
    assert not progress['active'] and progress['stop_reason'] is None
    assert Counter(r['state'] for r in rows) == {'completed': 1758, 'infrastructure_error': 42}
    assert len({(r['task'], r['policy']) for r in rows}) == 1800
    results, audit, outcomes = {}, [], {}
    for row in rows:
        path = RAW / 'cells' / row['task'] / row['policy'] / 'result.json'
        raw = read(path) if path.exists() else None
        complete = False
        if raw:
            assert raw['task'] == row['task'] and raw['mode'] == row['policy']
            assert raw['source_sha256'] == registration['source_sha256']
            assert raw['success'] == row['success']
            matches = re.findall(r'"type":\s*"turn.completed",\s*"usage":\s*(\{[^}]+\})', raw.get('harness_stdout', ''))
            if len(matches) == 1:
                usage = json.loads(matches[0])
                complete = (usage['input_tokens'] + usage['output_tokens'] == raw['total_tokens']
                            and raw['harness_returncode'] == 0 and raw['finished']
                            and raw['usage_source'] == 'stdout_jsonl')
            assert raw['doc_replay_tokens'] == raw['doc_replay_prompt_tokens'] + raw['doc_replay_completion_tokens']
            if raw['doc_replay_needed']:
                complete = complete and raw['doc_replay_tokens'] > 0 and raw['documents_valid']
            if raw['success']:
                assert row['state'] == 'completed' and complete
                assert raw['tests_ok'] and raw['finish_called'] and raw['committed']
                assert raw['wall_s'] > 0 and raw['total_tokens'] > 0
            results[row['task'], row['policy']] = raw
        audit.append(dict(task=row['task'], policy=row['policy'], state=row['state'],
                          success=row['success'], result_path=str(path),
                          result_sha256=sources.get(str(path)), complete_usage=bool(complete),
                          main_tokens=raw['total_tokens'] if complete else None,
                          replay_tokens=raw['doc_replay_tokens'] if complete else None,
                          workflow_s=raw.get('wall_s') if raw else None))
    for policy in base['policy_order']:
        selected = [r for r in audit if r['policy'] == policy]
        assert len(selected) == 300
        outcomes[policy] = dict(planned=300, success=sum(r['success'] is True for r in selected),
                                completed=sum(r['state'] == 'completed' for r in selected),
                                infrastructure_error=sum(r['state'] == 'infrastructure_error' for r in selected),
                                complete_usage_cells=sum(r['complete_usage'] for r in selected))
    spec = importlib.util.spec_from_file_location('time_audit', TIME.parent / 'analyze.py')
    time_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(time_module)
    comparisons, time_comparisons = {}, {}
    for i, policy in enumerate(base['control_order']):
        pairs, time_pairs = [], []
        for task in sorted({r['task'] for r in rows}):
            a, b = results.get((task, 'causal')), results.get((task, policy))
            if not a or not b or not a['success'] or not b['success']:
                continue
            pairs.append(dict(task=task, configuration_id=RAW.name,
                              causal_tokens=a['total_tokens'] + a['doc_replay_tokens'],
                              control_tokens=b['total_tokens'] + b['doc_replay_tokens']))
            time_pairs.append(dict(task=task, configuration_id=RAW.name,
                                   causal_s=a['wall_s'], control_s=b['wall_s']))
        comparisons[policy] = comparison(pairs, 20261106 + i, 20_000)
        if policy == 'ideal_oracle':
            continue
        a, b = sum(r['causal_s'] for r in time_pairs), sum(r['control_s'] for r in time_pairs)
        time_comparisons[policy] = dict(n=len(pairs), causal_total_s=a, control_total_s=b,
            causal_mean_s=a/len(pairs), control_mean_s=b/len(pairs), saving_pct=100*(1-a/b),
            ci95_pct=time_module.interval(time_pairs, 20261406+i), bootstrap_seed=20261406+i,
            slower_tasks=sum(r['causal_s'] > r['control_s'] for r in time_pairs), pairs=time_pairs)
    added = dict(benchmark='SWE-Bench Lite', harness='Codex', planned_per_policy=300,
                 cohort=RAW.name, outcomes=outcomes, comparisons=comparisons,
                 accounting='One attempt; returned main API usage plus document replay. All terminal outcomes retained. Successful cost pairs require complete returned usage.')
    combined = copy.deepcopy(base)
    # Keep SWE groups adjacent and preserve every historical cohort's statistics.
    combined['cohorts'] = {k:v for k,v in [('swe_sweagent', base['cohorts']['swe_sweagent']),
        ('swe_codex', added), ('tb_sweagent', base['cohorts']['tb_sweagent']), ('tb_codex', base['cohorts']['tb_codex'])]}
    times['cohorts']['swe_codex'] = time_comparisons
    combined['sources'].extend(dict(path=p, sha256=h) for p,h in sources.items()
                              if not p.endswith('/result.json') and p != str(BASE))
    combined['codex_swe_audit'] = dict(terminal=1800, completed=1758, infrastructure_error=42,
        missing_results=sum(r['result_sha256'] is None for r in audit),
        model_session_timeout_s=registration['model_wall_timeout_per_session_s'],
        cell_timeout_s=registration['cell_wall_timeout_s'], source_sha256=registration['source_sha256'],
        formal_eligible=registration['formal_eligible'],
        usage_method='Main counts cross-checked with terminal turn.completed input+output usage; replay input+output checked separately. Unknown final usage is never imputed as zero.')
    for key in base['cohorts']:
        assert combined['cohorts'][key] == base['cohorts'][key]
    for name, data in [('analysis.json',combined), ('time-analysis.json',times),
                       ('codex-swe-cell-audit.json',audit), ('source-hashes.json',sources)]:
        (OUT / name).write_text(json.dumps(data, indent=2)+'\n')
    lines=['# Completed SWE-Bench Lite / Codex campaign', '',
           '300 tasks per policy; one attempt per cell. All 1,800 cells terminal; 42 infrastructure errors.', '',
           '| Control | Successful pairs | Token savings | 95% CI | Time savings | 95% CI |',
           '|---|---:|---:|---|---:|---|']
    for p,c in comparisons.items():
        if p not in time_comparisons:
            continue
        t=time_comparisons[p]
        lines.append(f"| {p} | {c['n']} | {c['saving_pct']:.2f}% | {c['ci95_pct']} | {t['saving_pct']:.2f}% | {t['ci95_pct']} |")
    (OUT/'README.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))
    print(json.dumps(outcomes, indent=2))


if __name__ == '__main__':
    main()
