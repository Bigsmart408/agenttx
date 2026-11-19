#!/usr/bin/env python3
"""Summarize every sample of the RQ5 fairness audit and its timing corrections."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import statistics


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stats(values):
    return dict(mean_ms=statistics.mean(values), median_ms=statistics.median(values),
                stdev_ms=statistics.stdev(values), samples_ms=values)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    source, output = args.source, args.output
    results = source/'results-v1'
    manifest = json.loads((results/'manifest.json').read_text())
    progress = json.loads((results/'progress.json').read_text())
    assert progress == dict(state='complete', completed=162, total=162), progress
    paths = sorted((results/'sessions').glob('*.json'))
    assert len(paths) == len(manifest['plan']) == 162
    assert sha(source/'bench_rq5_storage_fairness.py') == manifest['script_sha256']
    frozen = json.loads((source/'source-sha256.json').read_text())
    assert all(sha(source/name) == expected for name, expected in frozen.items())
    groups, phases = defaultdict(list), Counter()
    for path, item in zip(paths, manifest['plan']):
        r = json.loads(path.read_text())
        assert all(r[name] == value for name, value in item.items()), r['serial']
        assert r['status'] == 'passed' and r['model_calls'] == 0, r['serial']
        assert 'error' not in r and 'cleanup_error' not in r, r['serial']
        phases[r['phase']] += 1
        case = r['case']
        snapshots = r['snapshots']
        expected_depth = case.get('depth', 0)
        assert [s['index'] for s in snapshots] == list(range(1, expected_depth+1))
        assert r['snapshot_total_ns'] == sum(s['snapshot_ns'] for s in snapshots)
        if r['phase'] == 'history-canary':
            assert [h['index'] for h in r['history_checks']] == [1, 3]
            assert all(h['index'] == h['distinct_generation'] for h in r['history_checks'])
        else:
            expected = 110 if case['kind'] == 'snapshot' else (
                0 if case['operation'] == 'unlink' else case['files'])
            assert r['commit_validation'] == dict(validated_files=expected, validated_bytes=expected*4096)
            assert r['active_workflow_ns'] == r['operation_and_snapshots_ns']+r['finish_ns']
            assert r['lifecycle_ns'] == r['setup_ns']+r['active_workflow_ns']
        if r['phase'] == 'measured':
            groups[json.dumps(case, sort_keys=True), r['variant']].append(r)
    assert phases == Counter(measured=130, warmup=26, **{'history-canary': 6})
    rows = []
    for (case_json, variant), records in sorted(groups.items()):
        records.sort(key=lambda r: r['repeat'])
        assert [r['repeat'] for r in records] == list(range(5))
        case = json.loads(case_json)
        row = dict(case=case, variant=variant)
        for metric in ('setup_ns', 'operation_and_snapshots_ns', 'finish_ns',
                       'active_workflow_ns', 'lifecycle_ns'):
            row[metric.removesuffix('_ns')] = stats([r[metric]/1e6 for r in records])
        if case['kind'] == 'snapshot':
            row['snapshot_per_call'] = stats([r['snapshot_total_ns']/case['depth']/1e6 for r in records])
        rows.append(row)
    lookup = {(json.dumps(r['case'], sort_keys=True), r['variant']): r for r in rows}
    def get(case, variant, metric, statistic='mean_ms'):
        return lookup[json.dumps(case, sort_keys=True), variant][metric][statistic]
    summary = dict(rows=rows, target_1_2_demonstrated=False,
                   original_conclusion_withdrawn=True,
                   reason='CLI/API、同步卸载/保留引用、提交与收尾的计时范围不一致。',
                   sample_policy='All five repeats, including long-latency samples; means and medians retained.')
    output.mkdir(parents=True, exist_ok=True)
    (output/'summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False)+'\n')
    validation = dict(sessions=162, passed=162, failed=0, phase_counts=dict(phases),
                      all_manifest_entries_matched=True, all_final_states_validated=True,
                      all_six_history_canaries_passed=True, model_calls=0,
                      repeats_per_configuration=5, formal_campaign_paused=True)
    (output/'validation.json').write_text(json.dumps(validation, indent=2)+'\n')
    (output/'measurement-sha256.json').write_text(json.dumps({str(p.relative_to(source)): sha(p)
        for p in [*paths, results/'manifest.json', results/'progress.json', source/'source-sha256.json',
                  *(source/name for name in frozen)]}, indent=2)+'\n')
    lines = ['# RQ5 公平性核查与统一口径补测', '',
             '**原“所有配置相对OverlayFS和YoloFS均不超过1.2倍”结论撤回。**'
             '原始时间和文件校验是真实的，但比较没有隔离新增机制的开销。'
             '本次没有调整数据来满足目标，也没有改论文数值或重启暂停的正式批次。', '',
             '## 发现的问题', '',
             '1. **调用方式不同。** AgentTX在Python进程内调用libc，OverlayFS每次派生mount/umount，'
             'YoloFS每次启动CLI。毫秒级进程启动时间进入对照，不能解释为文件系统本身的成本。',
             '2. **快照层的生命周期不同。** AgentTX将旧挂载只读化、保留引用并detach；原OverlayFS同步卸载。'
             '同为OverlayFS，释放superblock的时机不同。补测提供相同保留策略的纯OverlayFS基线。',
             '3. **计时截断。** AgentTX的seal及引用释放排除在commit之外；历史验证还在计时外挂载和卸载旧层。'
             '原YoloFS commit内部包含恢复空视图和清理artifact。因此只比较原commit_ns无法说明完整恢复结束前的成本。',
             '4. **提交实现不同。** 原OverlayFS对照使用作者Rust目录重放器，AgentTX使用direct rename和4线程批量删除。'
             '这个比较包含发布器优化收益。补测的纯OverlayFS与AgentTX共享同一发布原语，用于隔离记录开销。',
             '5. **缺少直接Ext4基线。** “OverlayFS”原本已经是带叠层快照和提交包装的方案，不能当作“什么都不加”。'
             '本轮新增Ext4直接执行同一Rust文件操作；它没有快照和独立提交阶段。', '',
             '## 补测设置', '',
             '沿用同一4 vCPU、12 GiB KVM guest、Ext4、固定YoloFS源码与Rust负载。'
             'AgentTX数值指SnapshotSession快照/提交专用模式。'
             '每配置独立预热一次、测量五次，固定随机顺序，保留全部样本。', '',
             '- 快照扫描深度1/10/30，100个4 KiB目标文件、10个种子文件，每层覆盖种子文件。',
             '- AgentTX与纯OverlayFS使用同一mount/metadata/merge原语；纯OverlayFS不写AgentTX快照日志。',
             '- YoloFS API通过与上游CLI相同的24字节snapshot ioctl结构调用，包含open/ioctl/close；独立历史预检确认返回的是有效快照。',
             '- “完成阶段”计入seal、发布、close、卸载和暂存目录销毁。正式计时过程中不先读取验证所有文件或travel历史。',
             '- 单独6个预检验证历史版本；每次计时结束后校验最终路径、内容和权限。',
             '- 原始buffered语义，没有额外fsync。原生Ext4直接执行文件操作，其独立完成阶段不适用。', '',
             '## 快照API均值，ms/次', '',
             '| 深度 | AgentTX API | OverlayFS 相同保留方式 | YoloFS API | ATX/YoloFS API |',
             '|---:|---:|---:|---:|---:|']
    for depth in (1, 10, 30):
        case = dict(kind='snapshot', depth=depth)
        a, o, y = [get(case, v, 'snapshot_per_call') for v in
                   ('agenttx', 'overlay-retained-api', 'yolofs-api')]
        lines.append(f'| {depth} | {a:.3f} | {o:.3f} | {y:.3f} | {a/y:.2f}× |')
    lines += ['', '## 调用方式与卸载方式的影响，ms/次', '',
              '| 深度 | OverlayFS 同步卸载API | OverlayFS 同步卸载CLI | YoloFS API | YoloFS CLI |',
              '|---:|---:|---:|---:|---:|']
    for depth in (1, 10, 30):
        case = dict(kind='snapshot', depth=depth)
        values = [get(case, v, 'snapshot_per_call') for v in
                  ('overlay-eager-api', 'overlay-eager-cli', 'yolofs-api', 'yolofs-cli')]
        lines.append(f'| {depth} | '+' | '.join(f'{v:.3f}' for v in values)+' |')
    lines += ['', '## 文件操作到完整结束，ms/session', '',
              '下表包含真实文件操作、快照（如有）及完整完成阶段，不包含初始化与初始数据准备。'
              '单元格为“均值 / 中位数”；中位数用于展示长尾，不代替均值，也不删除长尾样本。', '',
              '| 负载 | AgentTX | OverlayFS 相同保留方式 | YoloFS | 直接Ext4 |',
              '|---|---:|---:|---:|---:|']
    cases = [(dict(kind='snapshot', depth=d), f'{d}次快照') for d in (1, 10, 30)]
    cases += [(dict(kind='commit', operation=op, files=10000), f'{op} 1万文件') for op in ('create', 'unlink')]
    for case, name in cases:
        variants = ['agenttx', 'overlay-retained-api', 'yolofs-api' if case['kind']=='snapshot' else 'yolofs-cli']
        cells = [f"{get(case, v, 'active_workflow'):.2f} / {get(case, v, 'active_workflow', 'median_ms'):.2f}" for v in variants]
        if case['kind'] == 'commit':
            cells.append(f"{get(case, 'ext4', 'active_workflow'):.2f} / {get(case, 'ext4', 'active_workflow', 'median_ms'):.2f}")
        else:
            cells.append('不适用')
        lines.append(f'| {name} | '+' | '.join(cells)+' |')
    lines += ['', '## 可支持的结论', '',
              '原来快于OverlayFS一大截的主要来源是不同的调用入口和层释放策略；'
              '原来快于YoloFS的快照结论在统一为进程内API后反转。'
              '当前实现没有证明满足相对YoloFS的1.2倍目标。', '',
              'AgentTX与使用相同原语的纯OverlayFS总体处于相近量级。'
              '部分均值仍出现AgentTX更快，但两者的底层操作相同，'
              '且五次重复中有明显长尾，不能据此声称新增记录机制让底层原语变快。'
              '例如30次快照的AgentTX完整结束样本包含334 ms，纯OverlayFS删除样本包含1463 ms；全部保留。', '',
              '直接Ext4执行create/unlink没有快照、发布及卸载阶段，本轮明显快于AgentTX的完整流程。'
              'YoloFS较轻的快照原语与当前必须切换OverlayFS挂载层的实现之间仍有实际差距。', '',
              '本轮162/162个session通过：130次计时、26次预热、6个历史预检。'
              '`summary.json`保存逐次样本、均值、中位数、标准差、初始化、执行、完成及生命周期时间；'
              '`validation.json`和`measurement-sha256.json`提供矩阵和文件核验。', '',
              '复算命令：', '', '```bash',
              'python3 experiments/scripts/analyze_rq5_storage_fairness.py \\',
              '  --source review/rq5-storage-fairness-20261115/raw \\',
              '  --output review/rq5-storage-fairness-20261115', '```', '']
    (output/'RESULTS.md').write_text('\n'.join(lines))
    print(json.dumps(validation, indent=2))


if __name__ == '__main__':
    main()
