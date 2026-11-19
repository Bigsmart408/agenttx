#!/usr/bin/env python3
"""Audit saved microbenchmark files directly, without runtime recovery helpers."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from experiments.scripts.bench_external_recovery_artifacts import motivation_400_cases
from experiments.scripts.run_rq1_strategy_only_20261110 import read, save


def audit_cell(output, plan):
    state = read(output / 'session/agenttx.json')
    upper = output / 'session/upperdir' / state['workspace'].lstrip('/')
    lower = output / 'workspace'
    truth = set(plan.oracle_closure)
    kept = set(range(len(plan.steps))) - truth
    # Rebuild canonical bytes solely from the immutable workflow manifest.
    expected = []
    for step in plan.steps:
        expected.append(b''.join(expected[p] for p in step.parent_indices)
                        + f'artifact-{step.index:03d}-{step.role}\n'.encode())
    residual, changed, lower_changed = [], [], []
    for step, want in zip(plan.steps, expected):
        path = upper / step.relative_path
        base = lower / step.relative_path
        if base.exists() or base.is_symlink():
            lower_changed.append(step.index)
        if step.index in truth:
            if path.exists() or path.is_symlink():
                residual.append(step.index)
        elif not path.is_file() or path.is_symlink() or path.read_bytes() != want:
            changed.append(step.index)
    ledger = read(output / 'recovered-ledger.json')['steps']
    active = {s['step_id'] for s in ledger if s['status'] != 'rolled_back'}
    conversation = read(output / 'recovered-conversation.json')
    visible = {c['step_id'] for t in conversation['turns'] if t.get('status') != 'rolled_back'
               for c in t.get('calls', []) if c['status'] == 'active'}
    row = read(output / 'result.json')
    checks = dict(affected_files_absent=not residual, independent_bytes_preserved=not changed,
                  lower_unchanged=not lower_changed, ledger_matches_reference=active == kept,
                  context_matches_reference=visible == kept,
                  captured_reference_edges=all(p in row['captured_parents'][str(s.index)]
                      for s in plan.steps for p in s.parent_indices),
                  no_model_calls=row['model_calls'] == row['api_requests'] == 0,
                  file_only_input=set(row['policy_input']) == {'kind', 'files', 'checkpoint_step'}
                      and row['policy_input']['kind'] == 'file')
    return dict(valid=all(checks.values()), checks=checks, residual_artifact_ids=residual,
                changed_independent_artifacts=changed, lower_artifacts=lower_changed,
                invalid_count=len(truth), independent_count=len(kept),
                policy_wall_s=row['timings']['policy_wall_s'])


def sample(cohort, plans, cache):
    cells = read(cohort / 'cells.json')
    progress = read(cohort / 'progress.json')
    for phase, entries in cells.items():
        for cell in entries:
            key = phase + '/' + cell['key']
            if cell.get('result') and key not in cache:
                output = Path(cell['result']).parent
                try:
                    audit = audit_cell(output, plans[cell['trace_id']])
                    audit['result_hash_matches'] = hashlib.sha256(
                        (output / 'result.json').read_bytes()).hexdigest() == cell['result_sha256']
                    audit['valid'] &= audit['result_hash_matches']
                except Exception as exc:
                    audit = dict(valid=False, error=f'{type(exc).__name__}: {exc}')
                cache[key] = dict(phase=phase, family=cell['family'], **audit)
    grouped = defaultdict(list)
    for item in cache.values():
        if item['phase'] == 'main':
            grouped[item['family']].append(item)
    grouped['all'] = [item for rows in grouped.values() for item in rows]
    families = {}
    for family, rows in grouped.items():
        timings = sorted(r['policy_wall_s'] for r in rows if 'policy_wall_s' in r)
        families[family] = dict(audited=len(rows), passed=sum(r['valid'] for r in rows),
            affected_commands=sum(r.get('invalid_count', 0) for r in rows),
            independent_commands=sum(r.get('independent_count', 0) for r in rows),
            recovery_median_ms=1000 * statistics.median(timings) if timings else None,
            recovery_p95_ms=1000 * timings[min(len(timings)-1, int(.95*len(timings)))] if timings else None)
    result = dict(updated_at=datetime.now(timezone.utc).isoformat(), progress=progress,
                  families=families, audited=len(cache),
                  failures={k: v for k, v in cache.items() if not v['valid']})
    save(cohort / 'independent-audit.json', result)
    names = dict(mb2_task_length='任务长度', mb3_fault_position='故障位置', mb4_dependency='依赖比例', all='合计')
    lines = ['# 当前代码固定轨迹 microbenchmark', '',
             f"更新时间：{result['updated_at']}；阶段：{progress['phase']}；完成 {progress['completed']}/1200。", '',
             '仅 AgentTX；400 条轨迹 × 3 次；strace 自动捕获依赖；输入为问题文件和初始 checkpoint。', '',
             '| 扫描 | 独立复核通过 | 受影响命令 | 独立命令 | 恢复中位数 (ms) | P95 (ms) |',
             '|---|---:|---:|---:|---:|---:|']
    for family, r in families.items():
        if not r['audited']:
            continue
        lines.append(f"| {names[family]} | {r['passed']}/{r['audited']} | {r['affected_commands']} | "
                     f"{r['independent_commands']} | {r['recovery_median_ms']:.1f} | {r['recovery_p95_ms']:.1f} |")
    lines += ['', f"独立复核异常：{len(result['failures'])}；模型调用与 API 请求：0。", '',
              '独立复核直接读取保留的 upperdir、lower、ledger 与 conversation，',
              '按原始工作流生成真值；检查受影响文件和命令残留、独立文件字节、上下文与文件状态。',
              '恢复耗时包括计划、隔离试运行、审计和实际回滚；不包括轨迹执行。',
              '这是受控轨迹上的恢复正确性结果，不测模型解题率，也未包含其他策略。']
    (cohort / 'REPORT.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps(dict(phase=progress['phase'], completed=progress['completed'],
                         counts=progress['counts'], audited=len(cache),
                         failures=len(result['failures']), families=families)), flush=True)
    return progress['phase'] in {'finished', 'stopped'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', type=Path, required=True)
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    plans = {c.plan.trace_id: c.plan for c in motivation_400_cases()}
    cache = {}
    while True:
        done = sample(args.cohort, plans, cache)
        if done or not args.watch:
            break
        time.sleep(30)


if __name__ == '__main__':
    main()
