#!/usr/bin/env python3
"""Verify every planned RQ5 result, retain all samples and report comparisons."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import random
import statistics


def stats(values):
    return dict(n=len(values), mean=statistics.mean(values), median=statistics.median(values),
                stdev=statistics.stdev(values) if len(values)>1 else 0,
                min=min(values), max=max(values), samples=values)


def comparison(port, control):
    assert port.keys()==control.keys()
    pairs = [(port[key],control[key]) for key in sorted(port)]
    rng = random.Random(20261115)
    ratios = []
    for _ in range(5000):
        selected = [rng.choice(pairs) for _ in pairs]
        ratios.append(sum(p for p,c in selected)/sum(c for p,c in selected))
    ratios.sort()
    return dict(ratio_of_means=sum(port.values())/sum(control.values()),
                paired_ratio_95ci=[ratios[125],ratios[4874]],
                median_pair_ratio=statistics.median(p/c for p,c in pairs),
                pairs=len(pairs))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('results',type=Path)
    parser.add_argument('--output',type=Path)
    args = parser.parse_args()
    output = args.output or args.results
    output.mkdir(parents=True,exist_ok=True)
    manifest = json.loads((args.results/'manifest.json').read_text())
    complete = json.loads((args.results/'complete.json').read_text())
    paths = sorted((args.results/'sessions').glob('*.json'))
    assert len(paths)==len(manifest['plan'])==complete['total']
    assert complete['passed']==len(paths) and complete['failed']==0
    assert complete['all_source_hashes_unchanged'] is True
    rows = [json.loads(path.read_text()) for path in paths]
    assert {r['serial'] for r in rows}==set(range(len(rows)))
    for expected,row in zip(manifest['plan'],rows):
        assert all(row[k]==v for k,v in expected.items()),row['serial']
        assert row['status']=='passed' and row['model_calls']==0,row['serial']
        if row['phase']=='history-canary':
            assert len(row['history_checks'])==3
            assert [p['distinct_seed_byte'] for p in row['history_checks']]==[1,2,3]
        else:
            case = row['case']
            assert row['commit_validation']['validated_files']==(
                110 if case['kind']=='snapshot' else 0 if case['operation']=='unlink' else case['files'])
            assert len(row['snapshots'])==(case['depth'] if case['kind']=='snapshot' else 0)
    groups = defaultdict(list)
    measured = [row for row in rows if row['phase']=='measured']
    for row in measured:
        case = row['case']
        key = f"{case['kind']}/{case.get('operation',case.get('depth'))}"
        groups[(key,row['variant'])].append(row)
    summary = dict(complete=complete, verified_sessions=len(rows), measured_sessions=len(measured),
        snapshot_calls_all_phases=sum(len(r['snapshots']) for r in rows),
        snapshot_calls_measured=sum(len(r['snapshots']) for r in measured),
        snapshot_sessions_measured=sum(r['case']['kind']=='snapshot' for r in measured),
        groups={}, comparisons={}, model_calls=0)
    for (key,variant),samples in groups.items():
        assert len(samples)==manifest['repeats'],(key,variant,len(samples))
        data = {field:stats([r[field] for r in samples]) for field in
                ('setup_ns','operation_and_snapshots_ns','finish_ns','active_workflow_ns','lifecycle_ns')}
        if samples[0]['case']['kind']=='snapshot':
            data['snapshot_ns_per_call']=stats([r['snapshot_total_ns']/r['case']['depth'] for r in samples])
        summary['groups'].setdefault(key,{})[variant]=data
    for key,variants in summary['groups'].items():
        port = {r['repeat']:r['lifecycle_ns'] for r in groups[(key,'agenttx-yolofs')]}
        summary['comparisons'][key] = {
            variant:comparison(port,{r['repeat']:r['lifecycle_ns'] for r in groups[(key,variant)]})
            for variant in variants if variant!='agenttx-yolofs'}
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    labels = {'agenttx-yolofs':'AgentTX/YoloFS','yolofs-api':'YoloFS',
        'agenttx-metacopy':'AgentTX/OverlayFS','overlay-metacopy':'OverlayFS','ext4':'Ext4',
        'try-native':'try C','try-commit-cli':'try commit','try-full':'try full'}
    variants = list(labels)
    lines = ['# YoloFS 移植版 RQ5 结果','',
        f"全部 {len(rows)}/{len(rows)} 会话通过；正式测量 {len(measured)} 会话，每配置 {manifest['repeats']} 次。所有样本保留。",'',
        '使用原有 Ext4 guest、原始 Rust 负载。提交负载每项 10,000 个 4 KiB 文件；AgentTX/OverlayFS 和 OverlayFS 均开启 metacopy。',
        'RQ5 测量存储 API 的提交与显式快照；选择性恢复、依赖捕获和 agent 上下文同步另由真实挂载集成测试验证。','',
        '## 文件提交完整流程（秒，均值）','',
        '| 操作 | '+' | '.join(labels[v] for v in variants)+' |',
        '|---|'+'---:|'*len(variants)]
    for op,title in [('create','创建'),('overwrite','覆盖'),('rename','重命名'),('unlink','删除')]:
        data = summary['groups'][f'commit/{op}']
        lines.append('| '+title+' | '+' | '.join(f"{data[v]['lifecycle_ns']['mean']/1e9:.3f}" for v in variants)+' |')
    lines += ['','流程包含初始文件准备、初始化、执行、发布及会话清理；结果校验和删除已提交测试文件的动作在计时之外。',
        '提交组不请求中间快照；快照组按负载指定的边界实际调用快照接口。','',
        '## 快照 API（毫秒/次，均值）','',
        '| 快照数 | AgentTX/YoloFS | YoloFS | AgentTX/OverlayFS | OverlayFS |',
        '|---|---:|---:|---:|---:|']
    for depth in (1,10,30):
        data = summary['groups'][f'snapshot/{depth}']
        lines.append(f'| {depth} | '+' | '.join(f"{data[v]['snapshot_ns_per_call']['mean']/1e6:.4f}" for v in variants[:4])+' |')
    lines += ['','原生 YoloFS 和移植版均使用进程内 ioctl、持续打开的控制 FD；OverlayFS 两组沿用相同的 v14 原语。','',
        '## 快照负载完整流程（毫秒，均值）','',
        '| 快照数 | AgentTX/YoloFS | YoloFS | AgentTX/OverlayFS | OverlayFS |',
        '|---|---:|---:|---:|---:|']
    for depth in (1,10,30):
        data = summary['groups'][f'snapshot/{depth}']
        lines.append(f'| {depth} | '+' | '.join(f"{data[v]['lifecycle_ns']['mean']/1e6:.3f}" for v in variants[:4])+' |')
    lines += ['',f"正式快照会话 {summary['snapshot_sessions_measured']} 个，共 {summary['snapshot_calls_measured']} 次实际快照；含预热、历史核验共 {summary['snapshot_calls_all_phases']} 次。",'',
        '## 移植版相对原生 YoloFS 的完整流程比值','',
        '| 负载 | 均值比值 | 配对 bootstrap 95% 区间 |',
        '|---|---:|---|']
    for key,data in summary['comparisons'].items():
        c = data['yolofs-api']
        lines.append(f"| {key} | {c['ratio_of_means']:.3f}× | [{c['paired_ratio_95ci'][0]:.3f}, {c['paired_ratio_95ci'][1]:.3f}] |")
    lines += ['','完整分布、中位数、长尾、各阶段耗时和逐次样本保存在 `summary.json`，原始结果在 `sessions/`。',
        '比值基于本轮同批测量，不据此推算完整 agent 执行的开销。','']
    (output/'RESULTS.md').write_text('\n'.join(lines))
    raw_hashes = {str(path.relative_to(args.results)):hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in [args.results/'manifest.json',args.results/'complete.json',*paths]}
    (output/'raw-sha256.json').write_text(json.dumps(raw_hashes,indent=2)+'\n')
    print(json.dumps({k:summary[k] for k in ('verified_sessions','measured_sessions','snapshot_calls_measured')}))


if __name__=='__main__':
    main()
