#!/usr/bin/env python3
"""Summarize all samples of the publisher inode-reuse comparison."""
import argparse
import collections
import json
from pathlib import Path
import statistics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.source/'manifest.json').read_text())
    progress = json.loads((args.source/'progress.json').read_text())
    records = [json.loads(path.read_text()) for path in sorted((args.source/'sessions').glob('*.json'))]
    assert progress['state'] == 'complete'
    assert len(records) == len(manifest['plan']) == progress['completed']
    assert all(record['status'] == 'passed' and record['model_calls'] == 0 for record in records)
    assert [record['serial'] for record in records] == list(range(len(records)))
    for record, planned in zip(records, manifest['plan']):
        assert all(record[key] == value for key, value in planned.items())
    groups = collections.defaultdict(lambda: collections.defaultdict(list))
    for record in records:
        if record['phase'] == 'measured':
            groups[json.dumps(record['case'], sort_keys=True)][record['label']].append(record)
    rows = []
    for case, configurations in groups.items():
        row = dict(case=json.loads(case), configurations={})
        for label, samples in configurations.items():
            assert len(samples) == 5
            metrics = {}
            for key in ('setup', 'operation_and_snapshots', 'finish', 'active_workflow', 'lifecycle'):
                values = [sample[key+'_ns']/1e9 for sample in samples]
                metrics[key] = dict(mean_s=statistics.mean(values), median_s=statistics.median(values),
                                    stdev_s=statistics.stdev(values), samples_s=values)
            if row['case']['kind'] == 'snapshot':
                values = [sample['snapshot_total_ns']/len(sample['snapshots'])/1e9 for sample in samples]
                metrics['snapshot'] = dict(mean_s=statistics.mean(values), median_s=statistics.median(values),
                                          stdev_s=statistics.stdev(values), samples_s=values)
            row['configurations'][label] = metrics
        rows.append(row)
    phases = dict(collections.Counter(record['phase'] for record in records))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'summary.json').write_text(json.dumps(dict(passed=len(records), phases=phases, rows=rows), indent=2)+'\n')
    rename = next(row['configurations'] for row in rows if row['case'].get('operation') == 'rename')
    before, after = [rename[label] for label in ('AgentTX v12 metacopy', 'AgentTX v14 metacopy')]
    lines = ['# 提交器原 inode 复用验证', '',
        f"{len(records)}/{len(records)} 个会话通过；阶段计数：{phases}。另有真实 OverlayFS 功能测试，见 tests.log。",
        '固定上游 Rust 负载：一万文件，每文件 4 KiB；每配置一次预热、五次测量，固定随机顺序，全部样本保留。',
        '同一 Ext4 guest；普通挂载参数及 metacopy 参数分别保持一致。没有 agent、模型或错误定位。', '',
        '## 重命名：分阶段与完整会话', '',
        '| 配置 | 文件操作 s | 发布与收尾 s | 完整会话 s |', '|---|---:|---:|---:|']
    for label, metrics in rename.items():
        lines.append('| '+label+' | '+' | '.join(f"{metrics[key]['mean_s']:.3f}" for key in
                     ('operation_and_snapshots', 'finish', 'lifecycle'))+' |')
    lines += ['', '优化前后使用相同 metacopy 参数，差别为提交器代码：']
    for key, title in [('finish', '发布与收尾'), ('lifecycle', '完整会话')]:
        a, b = before[key]['mean_s'], after[key]['mean_s']
        lines.append(f'- {title}：{a:.3f} s → {b:.3f} s，下降 {(1-b/a)*100:.1f}%，加速 {a/b:.2f}×。')
        a, b = before[key]['median_s'], after[key]['median_s']
        lines.append(f'  中位数：{a:.3f} s → {b:.3f} s。')
    other_operations = [row for row in rows if row['case']['kind'] == 'commit'
                        and row['case']['operation'] != 'rename']
    if other_operations:
        lines += ['', '## 其他文件操作回归', '',
                  '| 操作 | 配置 | 发布与收尾 s | 完整会话 s |', '|---|---|---:|---:|']
    for row in other_operations:
        for label, metrics in row['configurations'].items():
            lines.append(f"| {row['case']['operation']} | {label} | {metrics['finish']['mean_s']:.3f} | {metrics['lifecycle']['mean_s']:.3f} |")
    snapshot_rows = [row for row in rows if row['case']['kind'] == 'snapshot']
    if snapshot_rows:
        lines += ['', '## 快照回归', '',
                  '| 深度 | 配置 | 快照 ms/次 | 完整会话 s |', '|---:|---|---:|---:|']
    for row in snapshot_rows:
        for label, metrics in row['configurations'].items():
            lines.append(f"| {row['case']['depth']} | {label} | {metrics['snapshot']['mean_s']*1000:.3f} | {metrics['lifecycle']['mean_s']:.3f} |")
    lines += ['', '发布与收尾包含内容补齐、inode 暂存、发布、卸载、历史挂载引用释放及暂存目录删除；完整会话另含初始文件准备及挂载。',
        '输出内容、名称及权限验证放在计时之后。YoloFS 原生提交/卸载 CLI 与模块未改动，自动快照关闭，权限开启且所需路径预先允许。',
        'AgentTX 与纯 OverlayFS 共用同版本挂载和发布原语；优化没有通过削弱 OverlayFS 对照实现来制造差距。',
        '本次只改快照/提交接口，未更改完整因果恢复运行时，未恢复暂停的正式 RQ5 实验，未改论文数字。',
        '首次扩展 metacopy 历史预检时发现旧适配器按精确名称分派，错误调用 YoloFS 路径；修复适配器后重新执行整批。首次失败及脚本另存 preflight-adapter-v1。',
        '逐次数据、标准差和中位数见 summary.json；源码及二进制哈希见 raw/ 下相应批次的 manifest.json。']
    if manifest['settings'].get('sync_before_sample'):
        lines += ['', '本批各配置开始前统一执行 sync，排除上一轮验证和输出目录删除留下的待写回数据；',
            '该预处理发生在初始数据生成之前，不移出当前样本自身的数据生成、内容补齐、发布或卸载开销。',
            '每次预处理耗时独立保存在原始样本的 precondition_sync_ns 字段，全部正式样本保留。']
    (args.output/'RESULTS.md').write_text('\n'.join(lines)+'\n')


if __name__ == '__main__':
    main()
