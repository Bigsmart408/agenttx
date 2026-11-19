#!/usr/bin/env python3
"""Validate commits, explicit snapshot events and upstream try comparisons."""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
import random
import statistics

COMMIT_VARIANTS = ('agenttx-metacopy', 'overlay-metacopy', 'yolofs-api', 'ext4',
                   'try-native', 'try-commit-cli', 'try-full')
SNAPSHOT_VARIANTS = COMMIT_VARIANTS[:3]


def metric(values):
    return dict(mean=statistics.mean(values), median=statistics.median(values),
                stdev=statistics.stdev(values), min=min(values), max=max(values), samples=values)


def paired(samples, key, divisor=1):
    pairs = []
    for repeat in range(10):
        a = next(r for r in samples if r['repeat'] == repeat and r['variant'] == COMMIT_VARIANTS[0])
        o = next(r for r in samples if r['repeat'] == repeat and r['variant'] == COMMIT_VARIANTS[1])
        assert abs(a['serial']-o['serial']) == 1
        assert (a['serial'] < o['serial']) == (a['pair_order'] == 'agenttx-first')
        pairs.append(dict(order=a['pair_order'], agenttx=a[key]/divisor, overlay=o[key]/divisor))
    assert collections.Counter(r['order'] for r in pairs) == {'agenttx-first': 5, 'overlay-first': 5}
    a = statistics.mean(p['agenttx'] for p in pairs)
    o = statistics.mean(p['overlay'] for p in pairs)
    rng = random.Random(2026111503)
    boot = []
    for _ in range(20000):
        selected = [pairs[rng.randrange(10)] for _ in pairs]
        boot.append((sum(p['agenttx'] for p in selected)/sum(p['overlay'] for p in selected)-1)*100)
    q = statistics.quantiles(boot, n=1000, method='inclusive')
    return dict(overhead_pct=(a/o-1)*100, ci95_pct=[q[24], q[974]], pairs=pairs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--deletion-retest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.source/'manifest.json').read_text())
    progress = json.loads((args.source/'progress.json').read_text())
    records = [json.loads(p.read_text()) for p in sorted((args.source/'sessions').glob('*.json'))]
    assert progress == dict(state='complete', completed=438, total=438)
    assert len(records) == len(manifest['plan']) == 438
    assert all(r['status'] == 'passed' for r in records)
    retry_manifest = json.loads((args.deletion_retest/'manifest.json').read_text())
    retry_progress = json.loads((args.deletion_retest/'progress.json').read_text())
    assert retry_progress == dict(state='complete', completed=77, total=77)
    assert retry_manifest['original_manifest_sha256'] == hashlib.sha256((args.source/'manifest.json').read_bytes()).hexdigest()
    retries = {r['serial']: r for r in [json.loads(p.read_text())
        for p in sorted((args.deletion_retest/'sessions').glob('*.json'))]}
    expected_retry = {r['serial'] for r in manifest['plan']
        if r['case'].get('operation') == 'unlink' and r['phase'] in ('warmup', 'measured')}
    assert set(retries) == expected_retry and len(retries) == 77
    records = [retries.get(r['serial'], r) for r in records]
    verification = json.loads((args.source.parent/'all-generation-verification.json').read_text())
    assert verification['runtime_sha256'] == manifest['runtime_sha256']
    assert {r['variant'] for r in verification['results']} == set(SNAPSHOT_VARIANTS)
    for row in verification['results']:
        assert row['status'] == 'passed' and row['model_calls'] == 0
        assert [r['generation'] for r in row['checks']] == [1, 2, 3]
        assert all(r['validated_files'] == 110 for r in row['checks'])
    measured = []
    for record, planned in zip(records, manifest['plan']):
        assert all(record[key] == value for key, value in planned.items())
        assert record['status'] == 'passed' and record['model_calls'] == 0
        snapshots = record['snapshots']
        expected = record['case'].get('depth', 0)
        assert len(snapshots) == expected
        assert [r['index'] for r in snapshots] == list(range(1, expected+1))
        assert record['snapshot_total_ns'] == sum(r['snapshot_ns'] for r in snapshots)
        if expected:
            assert all(r['snapshot_ns'] > 0 for r in snapshots)
        if record['phase'] != 'history-canary':
            assert record['lifecycle_ns'] == record['setup_ns']+record['active_workflow_ns']
            assert record['active_workflow_ns'] == record['operation_and_snapshots_ns']+record['finish_ns']
            record_source = args.deletion_retest if record['serial'] in retries else args.source
            evidence = json.loads((record_source/'mount-evidence'/f"s{record['serial']:06}.json").read_text())
            assert evidence['variant'] == record['variant']
            if record['variant'].endswith('metacopy'):
                assert 'metacopy=on' in evidence['mount_info'] and 'redirect_dir=on' in evidence['mount_info']
        if record['phase'] == 'measured':
            measured.append(record)
    assert len(measured) == 370
    groups = collections.defaultdict(list)
    for r in measured:
        case = r['case']
        groups[(case['kind'], case.get('operation', case.get('depth')))].append(r)
    rows = []
    for key in [('commit', op) for op in ('create', 'overwrite', 'rename', 'unlink')]+[
            ('snapshot', depth) for depth in (1, 10, 30)]:
        kind, value = key
        samples = groups[key]
        variants = COMMIT_VARIANTS if kind == 'commit' else SNAPSHOT_VARIANTS
        assert set(r['variant'] for r in samples) == set(variants)
        configs = {}
        for variant in variants:
            selected = [r for r in samples if r['variant'] == variant]
            assert len(selected) == 10
            metrics = {}
            for name in ['setup', 'operation_and_snapshots', 'finish', 'active_workflow', 'lifecycle']:
                metrics[name+'_s'] = metric([r[name+'_ns']/1e9 for r in selected])
            if kind == 'snapshot':
                metrics['snapshot_api_ms_per_call'] = metric([r['snapshot_total_ns']/value/1e6 for r in selected])
                metrics['snapshot_total_ms'] = metric([r['snapshot_total_ns']/1e6 for r in selected])
            configs[variant] = metrics
        row = dict(kind=kind, value=value, configurations=configs,
                   paired_lifecycle=paired(samples, 'lifecycle_ns'))
        if kind == 'snapshot':
            row['paired_snapshot_api'] = paired(samples, 'snapshot_total_ns', value)
        rows.append(row)
    summary = dict(original_campaign_passed=438, deletion_retest_passed=77,
                   primary_sessions=438, measured=370,
                   all_generation_verification_passed=3,
                   deletion_source=str(args.deletion_retest),
                   measured_snapshot_sessions=sum(r['case']['kind'] == 'snapshot' for r in measured),
                   measured_snapshot_events=sum(len(r['snapshots']) for r in measured), rows=rows)
    assert summary['measured_snapshot_sessions'] == 90
    assert summary['measured_snapshot_events'] == 1230
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    labels = dict(create='创建', overwrite='覆盖', rename='重命名', unlink='删除')
    names = dict(zip(COMMIT_VARIANTS, ['AgentTX', 'OverlayFS', 'YoloFS', 'Ext4',
        'try C 提交工具', 'try commit 命令', 'try 完整入口']))
    lines = ['# 显式快照、文件提交与 try 对照复测', '',
        '主批次 438/438 会话通过；删除组另行整体复测 77/77 会话。主要结果仍为每配置十次正式测量，固定次数，全部样本保留。',
        '历史验证曾与原删除组末尾短暂重叠，因此统一使用独立复测的全部删除样本，替换整组七种配置的一次预热和十次测量。原删除记录保留在 raw/results，不进入主要结果；复测在 raw/deletion-retest。其他负载未受影响。',
        'AgentTX 与 OverlayFS 采用 metacopy，同一当前自有 v14 后端；双方交替先后顺序各五次，邻接执行。其他系统运行块随机排序。',
        '所有系统复用固定 YoloFS perf-eval 的原始 Rust 负载。YoloFS 自动快照关闭，由快照负载显式请求原生 snapshot ioctl。', '',
        '## 四种文件操作的完整会话', '',
        '每项 10,000 个 4 KiB 文件。这组为原文独立提交负载，不创建中间快照。时间包含初始文件准备、初始化、执行、内容补齐、提交、卸载和暂存状态清理。', '',
        '| 操作 | AgentTX s | OverlayFS s | YoloFS s | Ext4 s |', '|---|---:|---:|---:|---:|']
    for row in rows[:4]:
        vals = [row['configurations'][v]['lifecycle_s']['mean'] for v in COMMIT_VARIANTS[:4]]
        lines.append(f"| {labels[row['value']]} | "+' | '.join(f'{v:.3f}' for v in vals)+' |')
    lines += ['', '## 同批 try 对照', '',
        'C 工具与 commit 命令两项沿用之前的普通 OverlayFS 输入和上游 try 提交入口；try 完整入口直接运行未经修改的 try -y 和原始 Rust 负载，使用 try 原生挂载配置。三项均为完整会话时间。', '',
        '| 操作 | try C 提交工具 s | try commit 命令 s | try 完整入口 s |', '|---|---:|---:|---:|']
    for row in rows[:4]:
        vals = [row['configurations'][v]['lifecycle_s']['mean'] for v in COMMIT_VARIANTS[4:]]
        lines.append(f"| {labels[row['value']]} | "+' | '.join(f'{v:.3f}' for v in vals)+' |')
    lines += ['', '## 显式快照 API 和完整流程', '',
        '快照负载先创建 100 个 target 和 10 个 seed，每文件 4 KiB；每轮覆盖 10 个 seed，再显式创建快照，最后提交全部文件状态。',
        '三种系统每会话均创建指定数量的真实快照；90 次正式快照会话合计 1,230 次 snapshot 调用。没有在计时流程中穿插历史 travel 或文件校验。', '',
        '| 快照数 | AgentTX ms/次 | OverlayFS ms/次 | YoloFS ms/次 | AgentTX 流程 ms | OverlayFS 流程 ms | YoloFS 流程 ms |',
        '|---:|---:|---:|---:|---:|---:|---:|']
    for row in rows[4:]:
        vals = [row['configurations'][v]['snapshot_api_ms_per_call']['mean'] for v in SNAPSHOT_VARIANTS]
        vals += [row['configurations'][v]['lifecycle_s']['mean']*1000 for v in SNAPSHOT_VARIANTS]
        lines.append(f"| {row['value']} | "+' | '.join(f'{v:.3f}' for v in vals)+' |')
    lines += ['', '三种系统另用独立会话逐一验证了全部三代不同非零内容的历史，后续覆盖没有改变已保存版本。',
        'Ext4 和未修改的 try 均不提供这些快照 API，因此快照项为不适用，不记为零成本。', '',
        '## AgentTX 相对 OverlayFS 的配对区间', '',
        '| 负载 | 指标 | 均值额外耗时 | 配对 bootstrap 95% 区间 |', '|---|---|---:|---:|']
    for row in rows:
        label = labels[row['value']] if row['kind'] == 'commit' else f"{row['value']} 次快照"
        for key, name in [('paired_lifecycle', '完整会话'), ('paired_snapshot_api', '快照 API')]:
            if key not in row:
                continue
            p = row[key]
            low, high = p['ci95_pct']
            lines.append(f"| {label} | {name} | {p['overhead_pct']:+.2f}% | [{low:+.2f}%, {high:+.2f}%] |")
    lines += ['', '## 完整会话的离散程度', '',
        '本轮存在明显长尾；例如 OverlayFS 删除的中位数约 0.995 秒，但一次样本为 7.128 秒。全部样本保留，均值高低不能单独作为稳定性能差异。', '',
        '| 负载 | 系统 | 均值 s | 中位数 s | 标准差 s | 最大值 s |',
        '|---|---|---:|---:|---:|---:|']
    for row in rows:
        label = labels[row['value']] if row['kind'] == 'commit' else f"{row['value']} 次快照"
        for variant, metrics in row['configurations'].items():
            values = metrics['lifecycle_s']
            lines.append(f"| {label} | {names[variant]} | "+' | '.join(
                f"{values[k]:.3f}" for k in ['mean', 'median', 'stdev', 'max'])+' |')
    lines += ['', '完整逐次数据、各阶段均值、中位数、标准差和所有配对结果见 summary.json。原始记录、冻结脚本、运行时及二进制哈希见 raw/。']
    (args.output/'RESULTS.md').write_text('\n'.join(lines)+'\n')


if __name__ == '__main__':
    main()
