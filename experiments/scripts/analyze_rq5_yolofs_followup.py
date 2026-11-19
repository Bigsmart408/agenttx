#!/usr/bin/env python3
"""Summarize the current same-campaign AgentTX/OverlayFS/YoloFS comparison."""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
import statistics


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    manifest=json.loads((args.source/'manifest.json').read_text())
    records=[json.loads(p.read_text()) for p in sorted((args.source/'sessions').glob('*.json'))]
    assert len(records)==len(manifest['plan'])==153
    assert all(r['status']=='passed' for r in records)
    groups=collections.defaultdict(lambda:collections.defaultdict(list))
    for record in records:
        if record['phase']=='measured':
            groups[json.dumps(record['case'],sort_keys=True)][record['variant']].append(record)
    rows=[]
    for case,variants in groups.items():
        row=dict(case=json.loads(case),systems={})
        for variant,samples in variants.items():
            assert len(samples)==5
            metrics={}
            for key in ['setup','operation_and_snapshots','finish','active_workflow','lifecycle']:
                values=[r[key+'_ns']/1e6 for r in samples]
                metrics[key]=dict(mean_ms=statistics.mean(values),median_ms=statistics.median(values),
                    stdev_ms=statistics.stdev(values),samples_ms=values)
            if row['case']['kind']=='snapshot':
                values=[r['snapshot_total_ns']/len(r['snapshots'])/1e6 for r in samples]
                metrics['snapshot']=dict(mean_ms=statistics.mean(values),median_ms=statistics.median(values),
                    stdev_ms=statistics.stdev(values),samples_ms=values)
            row['systems'][variant]=metrics
        rows.append(row)
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'summary.json').write_text(json.dumps(dict(passed=len(records),rows=rows,
        phases=dict(collections.Counter(r['phase'] for r in records))),indent=2)+'\n')
    lines=['# 当前 AgentTX、OverlayFS 与 YoloFS 的同批比较','',
        '153/153 个会话通过：15 个功能预检、23 个独立预热、115 个正式测量。',
        '每配置五次测量，固定随机顺序，全部样本保留。底层同一 Ext4 guest，文件负载来自固定上游 Rust 程序，每文件 4 KiB。',
        'YoloFS 权限开启、所需路径预先允许、自动快照关闭，快照边界由负载显式请求；文件系统与 CLI 未作优化或修改。',
        '', '## 包含初始化、执行、发布和全部暂存清理的完整会话','',
        '| 一万文件负载 | AgentTX 默认 s | OverlayFS 普通 s | YoloFS s | ATX / OverlayFS | ATX / YoloFS |',
        '|---|---:|---:|---:|---:|---:|']
    for row in rows:
        if row['case']['kind']!='commit':continue
        s=row['systems']
        a,o,y=[s[v]['lifecycle']['mean_ms'] for v in ['agenttx','overlay','yolofs-api']]
        lines.append(f"| {row['case']['operation']} | {a/1000:.3f} | {o/1000:.3f} | {y/1000:.3f} | {a/o:.3f}× | {a/y:.3f}× |")
    lines += ['','## Rename 的 metacopy 配置','',
        '以下使用相同批次的 rename 负载及 v12 代码，内容补齐的成本也计入。',
        '', '| 配置 | 文件操作 s | 发布与收尾 s | 完整会话 s |',
        '|---|---:|---:|---:|']
    rename=next(row for row in rows if row['case'].get('operation')=='rename')['systems']
    for variant in ['agenttx','overlay','agenttx-metacopy','overlay-metacopy','yolofs-api']:
        m=rename[variant]
        lines.append('| '+variant+' | '+' | '.join(f"{m[key]['mean_ms']/1000:.3f}" for key in
                     ['operation_and_snapshots','finish','lifecycle'])+' |')
    lines += ['','## 快照 API 与完整会话','',
        '快照统一走进程内 API，YoloFS 使用与其原生 CLI 一致的 snapshot ioctl。',
        '', '| 深度 | AgentTX ms/次 | OverlayFS ms/次 | YoloFS ms/次 | ATX完整会话 ms | Overlay完整会话 ms | Yolo完整会话 ms |',
        '|---:|---:|---:|---:|---:|---:|---:|']
    for row in rows:
        if row['case']['kind']!='snapshot':continue
        s=row['systems'];columns=['agenttx','overlay','yolofs-api']
        values=[s[v]['snapshot']['mean_ms'] for v in columns]+[s[v]['lifecycle']['mean_ms'] for v in columns]
        lines.append(f"| {row['case']['depth']} | "+' | '.join(f'{v:.3f}' for v in values)+' |')
    lines += ['', '普通 OverlayFS 与 AgentTX 共享挂载、层保留、压缩及发布原语；metacopy 的二者共享数据补齐和发布原语。',
        '接近 1 或略低于 1 的比值只表示测量均值，不解释为新增记录机制让底层原语变快。',
        '初始化和初始文件准备计入完整会话；最终输出验证与删除实验输出目录不计时。模型、agent、定位均未调用。',
        'YoloFS 最终提交使用原生 CLI，完成计时包含提交、卸载和暂存状态删除。',
        '', '首次预检发现适配器导入时的类覆盖，错误发生在 snapshot 分派，尚未开始正式测量。',
        '修正适配器并清理该预检挂载后，完整重跑为 results-v2；失败记录与原始脚本保存在 preflight-adapter-v1/，没有修改文件系统实现。',
        '', '逐次样本及标准差见 summary.json；设置、源码与二进制哈希见 raw/results-v2/manifest.json。']
    (args.output/'RESULTS.md').write_text('\n'.join(lines)+'\n')


if __name__=='__main__':
    main()
