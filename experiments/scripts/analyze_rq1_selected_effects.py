#!/usr/bin/env python3
"""Audit the Selected effects supplement, including saved bytes and call records."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from experiments.scripts import run_rq1_strategy_only_20261110 as rq1


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_cell(cohort, cell, plan):
    path = Path(cell['result'])
    result = rq1.read(path)
    assert cell['state'] == 'passed' and not cell['error']
    assert sha(path) == cell['result_sha256']
    assert result['trace_id'] == plan.trace_id == cell['trace_id']
    assert result['manifest_hash'] == plan.manifest_hash == cell['manifest_hash']
    assert result['policy'] == cell['policy'] == 'root_only'
    assert result['runtime_only'] and result['model_calls'] == result['api_requests'] == 0
    assert not result['native_agent_started'] and result['policy_contract_passed']
    assert all(rq1.audit_policy(result, plan, 'root_only').values())
    selected, affected = set(plan.fault_roots), set(plan.oracle_closure)
    assert set(result['rollback_ids']) == selected
    assert set(result['injected_invalid_ids']) == affected

    # Reconstruct both the correct workload and its injected version independently.
    correct, injected = [], []
    for step in plan.steps:
        payload = f'artifact-{step.index:03d}-{step.role}\n'
        correct.append(''.join(correct[i] for i in step.parent_indices) + payload)
        injected.append(''.join(injected[i] for i in step.parent_indices) +
                        ('prior-version-value\n' if step.index in selected else payload))
    assert {i for i, (a, b) in enumerate(zip(correct, injected)) if a != b} == affected
    folder = path.parent
    upper = folder / 'session/upperdir/results' / folder.relative_to(cohort) / 'workspace'
    invalid_from_bytes = set()
    for step in plan.steps:
        entry = upper / step.relative_path
        if step.index in selected:
            assert not entry.is_file()
            assert not (folder / 'workspace' / step.relative_path).is_file()
        else:
            assert entry.read_bytes() == injected[step.index].encode()
            if entry.read_bytes() != correct[step.index].encode():
                invalid_from_bytes.add(step.index)
    assert invalid_from_bytes == affected - selected == set(result['invalid_retained'])

    ledger = rq1.read(folder / 'recovered-ledger.json')['steps']
    assert {s['step_id'] for s in ledger if s['status'] == 'rolled_back'} == selected
    conversation = rq1.read(folder / 'recovered-conversation.json')
    active = {c['step_id'] for t in conversation['turns'] for c in t['calls']
              if c['status'] == 'active'}
    assert active == set(range(len(plan.steps))) - selected
    assert result['recovery_complete'] == (not invalid_from_bytes)
    assert result['invalid_removal'] == len(selected) / len(affected)
    assert result['valid_retention'] == 1
    row = dict(key=cell['key'], trace_id=plan.trace_id, family=cell['family'],
               repeat=cell['repeat'], policy='root_only', operation_count=len(plan.steps),
               invalid_count=len(affected), valid_count=len(plan.steps) - len(affected),
               rollback_calls=len(selected), valid_discarded_calls=0,
               invalid_retained_calls=len(invalid_from_bytes),
               invalid_removal=result['invalid_removal'], valid_retention=1.0,
               recovery_complete=result['recovery_complete'],
               **{k: result['timings'][k] for k in
                  ('materialize_wall_s', 'policy_wall_s', 'full_wall_s')},
               search_wall_s='', total_probes=0, first_passing_trial='',
               retained_bytes_preserved=True, context_matches=True)
    return row


def aggregate(rows):
    return dict(runs=len(rows), cases=len({r['trace_id'] for r in rows}),
                complete_recoveries=sum(r['recovery_complete'] for r in rows),
                invalid_removal_mean_pct=100 * statistics.mean(r['invalid_removal'] for r in rows),
                valid_retention_mean_pct=100.0, valid_discarded_mean_pct=0.0,
                mean_rollback_calls=statistics.mean(r['rollback_calls'] for r in rows),
                mean_valid_discarded_calls=0.0,
                mean_original_calls=statistics.mean(r['operation_count'] for r in rows))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    cohort = args.cohort.resolve()
    registration = rq1.read(cohort / 'registration.json')
    assert registration['policies'] == ['root_only']
    assert registration['expected_cells'] == 1200 and registration['repeats'] == 3
    source = Path(registration['frozen_source'])
    inventory = rq1.read(cohort / 'source-manifest.json')['files']
    assert all(sha(source / item['path']) == item['sha256'] for item in inventory)
    stages = rq1.read(cohort / 'cells.json')
    assert len(stages['runtime_gate']) == 4 and len(stages['main']) == 1200
    assert all(c['state'] == 'passed' for group in stages.values() for c in group)
    cases = {c.plan.trace_id: c.plan for c in rq1.cases()}
    for cell in stages['runtime_gate']:
        audit_cell(cohort, cell, cases[cell['trace_id']])
    rows = [audit_cell(cohort, cell, cases[cell['trace_id']]) for cell in stages['main']]
    assert len({r['key'] for r in rows}) == 1200
    assert Counter(r['trace_id'] for r in rows) == dict.fromkeys(cases, 3)
    assert all({r['repeat'] for r in rows if r['trace_id'] == trace} == {0, 1, 2}
               for trace in cases)
    summary = dict(cohort=str(cohort), main_runs=1200, runtime_gate_passed=4,
                   execution_errors=[], model_calls=0, api_requests=0,
                   cells_sha256=sha(cohort / 'cells.json'),
                   policies={'root_only': aggregate(rows)},
                   families={family: {'root_only': aggregate([r for r in rows if r['family'] == family])}
                             for family in sorted({r['family'] for r in rows})},
                   saved_filesystem_and_history_audited=1204,
                   source_sha256=registration['source_sha256'],
                   analysis_script_sha256=sha(Path(__file__)))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    with (args.output / 'runs.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (args.output / 'result-hashes.json').write_text(json.dumps(
        {c['key']: c['result_sha256'] for c in stages['main']}, indent=2) + '\n')
    stats = summary['policies']['root_only']
    lines = ['# RQ1 Selected effects：最终结果', '',
             '400 条固定轨迹 × 3 次重复 = 1,200 次实际运行；4 次额外运行前检查通过。',
             '执行异常为 0，模型调用与 API 请求均为 0。', '',
             '| 指标 | 结果 |', '|---|---:|',
             f"| 平均受影响调用清除率 | {stats['invalid_removal_mean_pct']:.2f}% |",
             f"| 完整清除影响 | {stats['complete_recoveries']}/1200 |",
             '| 有效工作保留率 | 100% |', '| 平均丢弃有效调用 | 0 |',
             f"| 平均撤销调用 | {stats['mean_rollback_calls']:.2f} |", '',
             'Selected effects 只撤销选定错误根，不扩展依赖闭包。完整清除影响的案例均没有依赖后继。',
             '逐个核对了 1,204 次主实验及运行前检查的结果哈希、manifest、实际恢复集合、',
             '保存的文件字节、台账与可见调用历史。残留错误集合从实际保留字节独立重算。', '',
             '- [完整统计](summary.json)', '- [逐次结果](runs.csv)',
             '- [原始结果哈希](result-hashes.json)',
             f'- 原始结果目录：`{cohort}`', '']
    (args.output / 'README.md').write_text('\n'.join(lines))
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
