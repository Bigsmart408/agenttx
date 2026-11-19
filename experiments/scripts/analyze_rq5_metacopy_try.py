#!/usr/bin/env python3
"""Summarize every measured run of the OverlayFS/try audit by workload."""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
import statistics


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    manifest = json.loads((args.source/'manifest.json').read_text())
    records = [json.loads(path.read_text()) for path in sorted((args.source/'sessions').glob('*.json'))]
    assert len(records) == len(manifest['plan'])
    assert all(record['status'] == 'passed' for record in records)
    by_case = collections.defaultdict(lambda: collections.defaultdict(list))
    for record in records:
        if record['phase'] == 'measured':
            by_case[json.dumps(record['case'],sort_keys=True)][record['variant']].append(record)
    rows = []
    for case, systems in by_case.items():
        row = dict(case=json.loads(case), systems={})
        for name, samples in systems.items():
            metrics = {}
            for metric in ['setup','operation_and_snapshots','finish','active_workflow','lifecycle']:
                values = [sample[metric+'_ns']/1e6 for sample in samples]
                metrics[metric] = dict(mean_ms=statistics.mean(values),median_ms=statistics.median(values),
                    stdev_ms=statistics.stdev(values),samples_ms=values)
            if row['case']['kind']=='snapshot':
                values = [sample['snapshot_total_ns']/len(sample['snapshots'])/1e6 for sample in samples]
                metrics['snapshot'] = dict(mean_ms=statistics.mean(values),median_ms=statistics.median(values),
                    stdev_ms=statistics.stdev(values),samples_ms=values)
            row['systems'][name] = metrics
        def ratio(numerator,denominator,metric):
            return row['systems'][numerator][metric]['mean_ms']/row['systems'][denominator][metric]['mean_ms']
        row['ratios'] = {metric:dict(
            metacopy_to_current=ratio('agenttx-metacopy','agenttx',metric),
            current_to_overlay=ratio('agenttx','overlay',metric),
            metacopy_to_overlay_metacopy=ratio('agenttx-metacopy','overlay-metacopy',metric))
            for metric in ['operation_and_snapshots','finish','active_workflow','lifecycle']}
        if row['case']['kind']!='snapshot':
            for metric in ['active_workflow','lifecycle']:
                row['ratios'][metric]['metacopy_to_ext4'] = ratio('agenttx-metacopy','ext4',metric)
                row['ratios'][metric]['try_full_to_ext4'] = ratio('try-full','ext4',metric)
        rows.append(row)
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'summary.json').write_text(json.dumps(dict(rows=rows,passed=len(records),
        phases=dict(collections.Counter(r['phase'] for r in records)),model_calls=0),indent=2)+'\n')
    columns = ['ext4','overlay','agenttx','agenttx-metacopy','overlay-metacopy','try-native','try-commit-cli','try-full']
    lines = ['# OverlayFS 元数据复制优化与 try 开销核对','',
        f"{len(records)}/{len(manifest['plan'])} 个会话通过。逐负载保留全部五次测量；不合并不同负载。",'',
        '## 执行、发布与完整收尾（ms）','',
        '| 负载 | Ext4 | Overlay 普通 | ATX 普通 | ATX metacopy | Overlay metacopy | try C提交 | try CLI提交 | try完整入口 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    def label(case):
        if case['kind']=='snapshot':return f"快照深度 {case['depth']}"
        if case['kind']=='noop':return '空命令'
        return f"{case['operation']} / {case['files']:,}"
    for row in rows:
        if row['case']['kind']=='snapshot':continue
        lines.append('| '+label(row['case'])+' | '+' | '.join(
            f"{row['systems'][name]['active_workflow']['mean_ms']:.2f}" for name in columns)+' |')
    lines += ['','## 包含初始化和初始文件构造的完整会话（ms）','',
        '| 负载 | Ext4 | Overlay 普通 | ATX 普通 | ATX metacopy | Overlay metacopy | try C提交 | try CLI提交 | try完整入口 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for row in rows:
        if row['case']['kind']=='snapshot':continue
        lines.append('| '+label(row['case'])+' | '+' | '.join(
            f"{row['systems'][name]['lifecycle']['mean_ms']:.2f}" for name in columns)+' |')
    lines += ['','## 发布及完整收尾阶段（ms）','',
        '| 负载 | Overlay 普通 | ATX 普通 | ATX metacopy | Overlay metacopy | try C提交 | try CLI提交 |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for row in rows:
        if row['case']['kind']!='commit':continue
        lines.append('| '+label(row['case'])+' | '+' | '.join(
            f"{row['systems'][name]['finish']['mean_ms']:.2f}"
            for name in ['overlay','agenttx','agenttx-metacopy','overlay-metacopy','try-native','try-commit-cli'])+' |')
    lines += ['','## 快照接口与完整流程','',
        '| 深度 | ATX 普通 ms/次 | ATX metacopy ms/次 | Overlay metacopy ms/次 | 普通流程 ms | metacopy流程 ms |',
        '|---:|---:|---:|---:|---:|---:|']
    for row in rows:
        if row['case']['kind']!='snapshot':continue
        s=row['systems']
        lines.append(f"| {row['case']['depth']} | {s['agenttx']['snapshot']['mean_ms']:.3f} | "
                     f"{s['agenttx-metacopy']['snapshot']['mean_ms']:.3f} | {s['overlay-metacopy']['snapshot']['mean_ms']:.3f} | "
                     f"{s['agenttx']['active_workflow']['mean_ms']:.2f} | {s['agenttx-metacopy']['active_workflow']['mean_ms']:.2f} |")
    lines += ['','try C提交与 try CLI提交共享普通 OverlayFS 的文件操作。前者调用上游 C helper，后者调用上游 commit 命令（包括其空命令隔离路径）。try完整入口直接用未修改的 try -y 执行实际 Rust 负载。完整入口的环境创建在执行阶段内；其他系统的初始化另列，所以跨完整入口的主要比较应看完整会话。',
              '', 'metacopy 的数据补齐、挂载释放、发布与清理全部计入。所有模式保持普通挂载，未使用 volatile。metacopy 保留全部历史层以避免剪掉数据来源；普通模式继续使用既有层压缩。这里测试 SnapshotSession 存储接口，不运行完整因果恢复运行时、agent 或模型。',
              '', '全部原始样本、各阶段标准差和脚本哈希见 summary.json 与 raw/results-v11/。']
    (args.output/'RESULTS.md').write_text('\n'.join(lines)+'\n')


if __name__ == '__main__':
    main()
