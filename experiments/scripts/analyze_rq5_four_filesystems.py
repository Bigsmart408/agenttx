#!/usr/bin/env python3
"""Report complete sessions for the four filesystems on identical upstream workloads."""
from __future__ import annotations

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
    assert len(records) == len(manifest['plan']) == progress['completed'] == 168
    assert all(row['status'] == 'passed' and row['model_calls'] == 0 for row in records)
    for record, planned in zip(records, manifest['plan']):
        assert all(record[key] == value for key, value in planned.items())
        assert record['active_workflow_ns'] == record['operation_and_snapshots_ns']+record['finish_ns']
        assert record['lifecycle_ns'] == record['setup_ns']+record['active_workflow_ns']
    groups = collections.defaultdict(lambda: collections.defaultdict(list))
    for record in records:
        if record['phase'] == 'measured':
            assert record['case']['files'] == 10000
            groups[record['case']['operation']][record['variant']].append(record)
    rows = []
    for operation in ('create', 'overwrite', 'rename', 'unlink'):
        configurations = {}
        for variant, samples in groups[operation].items():
            assert len(samples) == 5
            metrics = {}
            for key in ('setup', 'operation_and_snapshots', 'finish', 'active_workflow', 'lifecycle'):
                values = [row[key+'_ns']/1e9 for row in samples]
                metrics[key] = dict(mean_s=statistics.mean(values), median_s=statistics.median(values),
                                    stdev_s=statistics.stdev(values), samples_s=values)
            configurations[variant] = metrics
        rows.append(dict(operation=operation, configurations=configurations))
    args.output.mkdir(parents=True, exist_ok=True)
    summary = dict(passed=len(records), phases=dict(collections.Counter(r['phase'] for r in records)), rows=rows)
    (args.output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    names = dict(create='创建', overwrite='覆盖', rename='重命名', unlink='删除')
    lines = ['# 同一 YoloFS 负载下的四文件系统对照', '',
        '本轮保留 AgentTX 与 OverlayFS 的当前自有后端，复用 YoloFS 公开评测中的固定 Rust 文件操作负载。',
        '168/168 会话通过：24 个功能预检、24 次预热、120 次正式测量。每配置五次正式测量，固定随机顺序，全部样本保留。',
        '每负载一万个 4 KiB 文件，底层同一 Ext4 guest。下列时间包含初始数据准备、挂载初始化、文件操作、发布、seal/卸载、挂载引用释放及暂存目录销毁。',
        '各会话开始前统一 sync；当前会话自身的准备、发布与清理全部保留在计时内。输出内容、名称和权限验证在计时之后。', '',
        '## 默认普通挂载配置', '',
        '| 操作 | Ext4 s | OverlayFS s | YoloFS s | AgentTX s | ATX / Ext4 | ATX / OverlayFS | ATX / YoloFS |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for row in rows:
        values = {key: value['lifecycle']['mean_s'] for key, value in row['configurations'].items()}
        a, e, o, y = [values[key] for key in ('agenttx', 'ext4', 'overlay', 'yolofs-api')]
        lines.append(f"| {names[row['operation']]} | {e:.3f} | {o:.3f} | {y:.3f} | {a:.3f} | {a/e:.3f}× | {a/o:.3f}× | {a/y:.3f}× |")
    lines += ['', '## metacopy 配置', '',
        'AgentTX 与 OverlayFS 同时启用 metacopy。四个负载均使用这个固定配置，表内不按负载挑选不同模式。', '',
        '| 操作 | Ext4 s | OverlayFS metacopy s | YoloFS s | AgentTX metacopy s | ATX / Ext4 | ATX / OverlayFS | ATX / YoloFS |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for row in rows:
        values = {key: value['lifecycle']['mean_s'] for key, value in row['configurations'].items()}
        a, e, o, y = [values[key] for key in ('agenttx-metacopy', 'ext4', 'overlay-metacopy', 'yolofs-api')]
        lines.append(f"| {names[row['operation']]} | {e:.3f} | {o:.3f} | {y:.3f} | {a:.3f} | {a/e:.3f}× | {a/o:.3f}× | {a/y:.3f}× |")
    lines += ['', '## 分阶段时间', '',
        '| 操作 | 配置 | 初始准备与初始化 s | 文件操作 s | 发布与全部收尾 s | 完整会话 s |',
        '|---|---|---:|---:|---:|---:|']
    for row in rows:
        for variant in ('ext4', 'agenttx', 'overlay', 'yolofs-api', 'agenttx-metacopy', 'overlay-metacopy'):
            metrics = row['configurations'][variant]
            lines.append(f"| {names[row['operation']]} | {variant} | "+' | '.join(
                f"{metrics[key]['mean_s']:.3f}" for key in ('setup', 'operation_and_snapshots', 'finish', 'lifecycle'))+' |')
    lines += ['', '## 负载与实现', '',
        '四种负载分别是固定 perf-eval 源码中的 write-files、overwrite-files、rename-files 和 unlink-files。源码构造器均为 10,000 个文件；每文件 4096 字节。',
        '创建写入零字节模式，覆盖写入 0xff 模式，重命名保持内容，删除移除全部文件。相同的 Rust exec-workload 在各系统中执行。',
        'AgentTX 使用 v14 SnapshotSession；OverlayFS 使用当前 OverlaySession，双方在每对配置中共享挂载和最终发布原语。',
        'YoloFS 使用未改动的原生模块和 CLI，权限开启、路径预先允许、自动快照关闭。Ext4 直接执行相同文件操作。',
        '本轮不调用原文 Backend::run_one；前一轮作者后端的阶段时间不混入本轮数据。没有模型、agent、故障注入或定位。',
        '逐次样本、中位数及标准差见 summary.json。源码、运行时、工作负载和 YoloFS 二进制哈希见 raw/results/manifest.json。']
    (args.output/'RESULTS.md').write_text('\n'.join(lines)+'\n')


if __name__ == '__main__':
    main()
