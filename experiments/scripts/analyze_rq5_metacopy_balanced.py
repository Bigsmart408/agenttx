#!/usr/bin/env python3
"""Summarize every sample and paired uncertainty in the balanced storage rerun."""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
import random
import statistics

VARIANTS = ('agenttx-metacopy', 'overlay-metacopy', 'yolofs-api', 'ext4')
OPERATIONS = ('create', 'overwrite', 'rename', 'unlink')


def paired_summary(samples):
    pairs = []
    for repeat in range(20):
        a = next(r for r in samples if r['repeat'] == repeat and r['variant'] == VARIANTS[0])
        o = next(r for r in samples if r['repeat'] == repeat and r['variant'] == VARIANTS[1])
        assert abs(a['serial']-o['serial']) == 1
        assert a['pair_order'] == o['pair_order']
        assert (a['serial'] < o['serial']) == (a['pair_order'] == 'agenttx-first')
        pairs.append(dict(repeat=repeat, order=a['pair_order'],
                          agenttx_s=a['lifecycle_ns']/1e9, overlay_s=o['lifecycle_ns']/1e9))
    assert collections.Counter(p['order'] for p in pairs) == {
        'agenttx-first': 10, 'overlay-first': 10}
    a = statistics.mean(p['agenttx_s'] for p in pairs)
    o = statistics.mean(p['overlay_s'] for p in pairs)
    rng = random.Random(2026111502)
    boot = []
    for _ in range(20000):
        selected = [pairs[rng.randrange(len(pairs))] for _ in pairs]
        boot.append((sum(p['agenttx_s'] for p in selected)/
                     sum(p['overlay_s'] for p in selected)-1)*100)
    interval = statistics.quantiles(boot, n=1000, method='inclusive')
    by_order = {}
    for order in ['agenttx-first', 'overlay-first']:
        selected = [p for p in pairs if p['order'] == order]
        by_order[order] = dict(
            agenttx_mean_s=statistics.mean(p['agenttx_s'] for p in selected),
            overlay_mean_s=statistics.mean(p['overlay_s'] for p in selected))
    return dict(overhead_pct=(a/o-1)*100, ci95_pct=[interval[24], interval[974]],
                bootstrap_resamples=20000, by_order=by_order, pairs=pairs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.source/'manifest.json').read_text())
    progress = json.loads((args.source/'progress.json').read_text())
    records = [json.loads(p.read_text()) for p in sorted((args.source/'sessions').glob('*.json'))]
    assert progress == dict(state='complete', completed=352, total=352)
    assert len(records) == len(manifest['plan']) == 352
    groups = collections.defaultdict(list)
    for record, planned in zip(records, manifest['plan']):
        assert all(record[key] == value for key, value in planned.items())
        assert record['status'] == 'passed' and record['model_calls'] == 0
        assert record['lifecycle_ns'] == record['setup_ns']+record['active_workflow_ns']
        assert record['active_workflow_ns'] == record['operation_and_snapshots_ns']+record['finish_ns']
        evidence = json.loads((args.source/'mount-evidence'/f"s{record['serial']:06}.json").read_text())
        assert evidence['variant'] == record['variant']
        if record['variant'].endswith('metacopy'):
            assert 'metacopy=on' in evidence['mount_info'] and 'redirect_dir=on' in evidence['mount_info']
        if record['phase'] == 'measured':
            assert record['case']['files'] == 10000
            groups[record['case']['operation']].append(record)
    assert set(groups) == set(OPERATIONS)
    rows = []
    for operation in OPERATIONS:
        configs = {}
        for variant in VARIANTS:
            samples = [r for r in groups[operation] if r['variant'] == variant]
            assert len(samples) == 20
            metrics = {}
            for name in ['setup', 'operation_and_snapshots', 'finish', 'active_workflow', 'lifecycle']:
                values = [r[name+'_ns']/1e9 for r in samples]
                metrics[name] = dict(mean_s=statistics.mean(values), median_s=statistics.median(values),
                    stdev_s=statistics.stdev(values), min_s=min(values), max_s=max(values), samples_s=values)
            configs[variant] = metrics
        rows.append(dict(operation=operation, configurations=configs, paired=paired_summary(groups[operation])))
    args.output.mkdir(parents=True, exist_ok=True)
    summary = dict(passed=352, measured=320, repeats=20, rows=rows)
    (args.output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    labels = dict(create='创建', overwrite='覆盖', rename='重命名', unlink='删除')
    names = dict(zip(VARIANTS, ['AgentTX', 'OverlayFS', 'YoloFS', 'Ext4']))
    lines = ['# 四文件系统最新平衡顺序复测', '',
        '352/352 会话通过，包括 16 个功能预检、16 次预热、320 次正式测量。',
        '同一 Ext4 测试环境，自有 v14 后端不变；沿用 YoloFS 公开固定 Rust 负载，每项 10,000 个 4 KiB 文件。',
        'AgentTX 和 OverlayFS 均采用 metacopy，实际挂载参数逐次核验。每项各 20 次正式测量，十次 AgentTX 先运行、十次 OverlayFS 先运行，两者相邻执行。',
        'Ext4 和 YoloFS 同批各测 20 次，运行块顺序随机。每会话前统一 sync。固定测量次数，全部样本保留。',
        '总时间包含初始文件准备、初始化、文件操作、内容补齐、提交、卸载及暂存清理；最终内容、名称和权限验证在计时外。', '',
        '| 操作 | AgentTX s | OverlayFS s | YoloFS s | Ext4 s |', '|---|---:|---:|---:|---:|']
    for row in rows:
        values = [row['configurations'][v]['lifecycle']['mean_s'] for v in VARIANTS]
        lines.append(f"| {labels[row['operation']]} | "+' | '.join(f'{v:.3f}' for v in values)+' |')
    lines += ['', '## 相对耗时及配对不确定性', '',
        '| 操作 | 相对 OverlayFS 额外耗时 | 配对 bootstrap 95% 区间 | AgentTX / YoloFS | AgentTX / Ext4 |',
        '|---|---:|---:|---:|---:|']
    for row in rows:
        values = {v:row['configurations'][v]['lifecycle']['mean_s'] for v in VARIANTS}
        p = row['paired']
        low, high = p['ci95_pct']
        a = values[VARIANTS[0]]
        lines.append(f"| {labels[row['operation']]} | {p['overhead_pct']:+.2f}% | [{low:+.2f}%, {high:+.2f}%] | {a/values['yolofs-api']:.2f}× | {a/values['ext4']:.2f}× |")
    lines += ['', '## 各阶段均值和总时间离散程度', '',
        '| 操作 | 系统 | 初始化 s | 文件操作 s | 提交及收尾 s | 总时间标准差 s | 总时间中位数 s |',
        '|---|---|---:|---:|---:|---:|---:|']
    for row in rows:
        for v in VARIANTS:
            m = row['configurations'][v]
            vals = [m[k]['mean_s'] for k in ['setup', 'operation_and_snapshots', 'finish']]
            vals += [m['lifecycle']['stdev_s'], m['lifecycle']['median_s']]
            lines.append(f"| {labels[row['operation']]} | {names[v]} | "+' | '.join(f'{x:.3f}' for x in vals)+' |')
    lines += ['', '原始记录、冻结脚本、每次挂载参数见 raw/。summary.json 保留全部样本、先后顺序分组及配对 bootstrap 结果。']
    (args.output/'RESULTS.md').write_text('\n'.join(lines)+'\n')


if __name__ == '__main__':
    main()
