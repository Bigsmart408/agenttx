#!/usr/bin/env python3
"""Validate and summarize all samples from the matched rename retest."""
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
    records = [json.loads(p.read_text()) for p in sorted((args.source/'sessions').glob('*.json'))]
    assert progress == dict(state='complete', completed=42, total=42)
    assert len(records) == len(manifest['plan']) == 42
    groups = collections.defaultdict(list)
    for record, planned in zip(records, manifest['plan']):
        assert all(record[key] == value for key, value in planned.items())
        assert record['status'] == 'passed' and record['model_calls'] == 0
        assert record['lifecycle_ns'] == record['setup_ns']+record['active_workflow_ns']
        assert record['active_workflow_ns'] == record['operation_and_snapshots_ns']+record['finish_ns']
        evidence = json.loads((args.source/'mount-evidence'/f"s{record['serial']:06}.json").read_text())
        assert evidence['variant'] == record['variant']
        if record['variant'].endswith('metacopy'):
            assert 'metacopy=on' in evidence['mount_info']
            assert 'redirect_dir=on' in evidence['mount_info']
        if record['phase'] == 'measured':
            assert record['case'] == dict(kind='commit', operation='rename', files=10000)
            groups[record['variant']].append(record)
    assert len(groups) == 6
    rows = {}
    for variant, samples in groups.items():
        assert len(samples) == 5
        metrics = {}
        for name in ['setup', 'operation_and_snapshots', 'finish', 'active_workflow', 'lifecycle']:
            values = [r[name+'_ns']/1e9 for r in samples]
            metrics[name] = dict(mean_s=statistics.mean(values), median_s=statistics.median(values),
                                 stdev_s=statistics.stdev(values), samples_s=values)
        rows[variant] = metrics
    args.output.mkdir(parents=True, exist_ok=True)
    summary = dict(passed=42, measured=30, configurations=rows)
    (args.output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    names = {'ext4':'Ext4', 'yolofs-api':'YoloFS', 'agenttx':'AgentTX 普通模式',
             'overlay':'OverlayFS 普通模式', 'agenttx-metacopy':'AgentTX metacopy',
             'overlay-metacopy':'OverlayFS metacopy'}
    lines = ['# metacopy 重命名复测', '',
             '42/42 会话通过；六组各一次 32 文件预检、一次 10,000 文件预热、五次正式测量。全部样本保留。',
             '保留当前 v14 自有后端；YoloFS 公开 Rust rename-files 负载，一万个 4 KiB 底层已有文件。',
             '完整会话包含初始文件准备、挂载初始化、文件操作、内容补齐、提交、卸载和暂存清理。最终内容、名称和权限验证在计时外。',
             '两组 metacopy 均逐次核验实际挂载参数 metacopy=on、redirect_dir=on。', '',
             '| 系统 | 初始化 s | 重命名执行 s | 提交及收尾 s | 完整会话 s | 完整会话标准差 s |',
             '|---|---:|---:|---:|---:|---:|']
    for variant in names:
        metrics = rows[variant]
        times = ' | '.join(f"{metrics[key]['mean_s']:.3f}" for key in
                           ['setup', 'operation_and_snapshots', 'finish', 'lifecycle'])
        lines.append(f"| {names[variant]} | {times} | {metrics['lifecycle']['stdev_s']:.3f} |")
    values = {key:val['lifecycle']['mean_s'] for key,val in rows.items()}
    a = values['agenttx-metacopy']
    lines += ['', f"AgentTX metacopy 相对同配置 OverlayFS 的额外耗时：{(a/values['overlay-metacopy']-1)*100:+.2f}%。",
              f"AgentTX metacopy 耗时为 Ext4 的 {a/values['ext4']:.2f} 倍、YoloFS 的 {a/values['yolofs-api']:.2f} 倍。",
              f"相对同批普通模式，AgentTX 完整会话缩短 {(1-a/values['agenttx'])*100:.2f}%，OverlayFS 缩短 {(1-values['overlay-metacopy']/values['overlay'])*100:.2f}%。",
              '', '原始记录、逐次挂载参数与冻结脚本见 raw/；各阶段所有样本及标准差见 summary.json。']
    (args.output/'RESULTS.md').write_text('\n'.join(lines)+'\n')


if __name__ == '__main__':
    main()
