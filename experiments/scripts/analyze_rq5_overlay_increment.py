#!/usr/bin/env python3
"""Validate all samples and compare AgentTX with matching OverlayFS operations."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random
import statistics


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stats(values):
    return dict(mean_ms=statistics.mean(values), median_ms=statistics.median(values),
                stdev_ms=statistics.stdev(values), samples_ms=values)


def ratio_interval(numerators, denominators):
    rng = random.Random(20261115)
    values = []
    for _ in range(5000):
        indices = rng.choices(range(len(numerators)), k=len(numerators))
        values.append(sum(numerators[i] for i in indices)/sum(denominators[i] for i in indices))
    values.sort()
    return [values[124], values[4874]]


def label(case):
    if case['kind'] == 'snapshot':
        return f"快照深度 {case['depth']}"
    return f"{case['operation']} {case['files']:,} 文件"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source, output = args.source, args.output
    results = source/'results-v10'
    manifest = json.loads((results/'manifest.json').read_text())
    progress = json.loads((results/'progress.json').read_text())
    plan = manifest['plan']
    repeats = manifest['repeats']
    assert repeats == 10 and len(plan) == 338
    assert progress == dict(state='complete', completed=338, total=338), progress
    assert manifest['yolofs_measured'] is False and manifest['model_calls'] == 0
    for name, expected in manifest['scripts_sha256'].items():
        assert sha(source/name) == expected, name
    for name, expected in manifest['runtime_sha256'].items():
        assert sha(source/'candidate-v10'/name) == expected, name
    assert '25 passed' in (source/'tests-v10.log').read_text()
    files = sorted((results/'sessions').glob('*.json'))
    assert len(files) == len(plan)
    groups, phases = defaultdict(list), Counter()
    for file, item in zip(files, plan):
        row = json.loads(file.read_text())
        assert all(row[name] == value for name, value in item.items()), row['serial']
        assert row['status'] == 'passed' and row['model_calls'] == 0
        assert 'error' not in row and 'cleanup_error' not in row
        phases[row['phase']] += 1
        case = row['case']
        assert row['snapshot_total_ns'] == sum(s['snapshot_ns'] for s in row['snapshots'])
        assert [s['index'] for s in row['snapshots']] == list(range(1, case.get('depth', 0)+1))
        if row['phase'] == 'history-canary':
            assert [h['index'] for h in row['history_checks']] == [1, 3]
            assert all(h['index'] == h['distinct_generation'] for h in row['history_checks'])
        else:
            count = 110 if case['kind'] == 'snapshot' else (
                0 if case['operation'] == 'unlink' else case['files'])
            assert row['commit_validation'] == dict(validated_files=count, validated_bytes=count*4096)
            assert row['active_workflow_ns'] == row['operation_and_snapshots_ns']+row['finish_ns']
            assert row['lifecycle_ns'] == row['setup_ns']+row['active_workflow_ns']
        if row['phase'] == 'measured':
            groups[json.dumps(case, sort_keys=True), row['variant']].append(row)
    assert phases == Counter(measured=290, warmup=29, **{'history-canary':3, 'commit-canary':16})
    cases = [dict(kind='snapshot', depth=d) for d in (1, 10, 30)]
    cases += [dict(kind='commit', operation=op, files=10000)
              for op in ('create', 'overwrite', 'rename', 'unlink')]
    cases += [dict(kind='commit', operation='create', files=100000)]
    rows = []
    metrics = ['setup', 'operation_and_snapshots', 'finish', 'active_workflow', 'lifecycle']
    for case in cases:
        systems = {}
        variants = ['agenttx-before', 'agenttx', 'overlay-compact-api']
        if case['kind'] == 'commit':
            variants.append('ext4')
        for variant in variants:
            data = sorted(groups[json.dumps(case, sort_keys=True), variant], key=lambda r:r['repeat'])
            assert [r['repeat'] for r in data] == list(range(repeats))
            systems[variant] = {metric: stats([r[metric+'_ns']/1e6 for r in data]) for metric in metrics}
            if case['kind'] == 'snapshot':
                systems[variant]['snapshot_per_call'] = stats([
                    r['snapshot_total_ns']/case['depth']/1e6 for r in data])
                if variant != 'overlay-compact-api':
                    systems[variant]['lower_counts'] = [
                        [s['lower_count'] for s in r['snapshots']] for r in data]
                    if variant == 'agenttx':
                        assert max(max(widths) for widths in systems[variant]['lower_counts']) <= 3
        ratio_metrics = ['active_workflow', 'lifecycle']
        if case['kind'] == 'snapshot':
            ratio_metrics.append('snapshot_per_call')
        ratios = {}
        for metric in ratio_metrics:
            new = systems['agenttx'][metric]
            baseline = systems['overlay-compact-api'][metric]
            ratios[metric] = dict(mean_ratio=new['mean_ms']/baseline['mean_ms'],
                paired_bootstrap_95=ratio_interval(new['samples_ms'], baseline['samples_ms']))
        rows.append(dict(case=case, systems=systems, ratios_to_overlayfs=ratios,
                         measured_means_at_most_1_2=all(r['mean_ratio']<=1.2 for r in ratios.values())))
    maxima = {metric: max(r['ratios_to_overlayfs'][metric]['mean_ratio']
                         for r in rows if metric in r['ratios_to_overlayfs'])
              for metric in ('snapshot_per_call', 'active_workflow', 'lifecycle')}
    summary = dict(rows=rows, maximum_ratios_to_overlayfs=maxima,
                   all_measured_means_at_most_1_2=all(r['measured_means_at_most_1_2'] for r in rows),
                   target='Only matching OverlayFS; YoloFS is excluded by user instruction.',
                   semantics='Normal OverlayFS; all writers quiescent at snapshots; no volatile option.',
                   sample_policy='All ten repeats; no latency filtering; per-configuration means and paired bootstrap intervals.')
    validation = dict(sessions=338, passed=338, failed=0, phase_counts=dict(phases),
                      repeats_per_configuration=10, correctness_tests_passed=25,
                      all_manifest_entries_matched=True, all_final_states_validated=True,
                      history_canaries_passed=3, commit_canaries_passed=16,
                      bounded_current_lower_stack_verified=True, model_calls=0, yolofs_runs=0,
                      formal_campaign_paused=True)
    output.mkdir(parents=True, exist_ok=True)
    for name, value in [('summary.json',summary), ('validation.json',validation)]:
        (output/name).write_text(json.dumps(value, indent=2, ensure_ascii=False)+'\n')
    hashes = {str(p.relative_to(source)):sha(p) for p in [*files, results/'manifest.json',
        results/'progress.json', source/'tests-v10.log', *(source/name for name in manifest['scripts_sha256']),
        source/'candidate-v10/manifest.json']}
    (output/'measurement-sha256.json').write_text(json.dumps(hashes, indent=2)+'\n')
    lines = ['# OverlayFS 增量开销优化结果', '',
             '本轮按用户要求，只以匹配实现的OverlayFS作为优化目标。'
             'YoloFS没有新增运行，先前关于YoloFS的公平性核查与撤回结论保留。', '',
             f"8组配置的全部主指标均值{'均达到' if summary['all_measured_means_at_most_1_2'] else '未全部达到'}1.2倍目标。"
             f"最大耗时比：快照API {maxima['snapshot_per_call']:.3f}倍，"
             f"执行至完整收尾 {maxima['active_workflow']:.3f}倍，"
             f"包含初始化的完整会话 {maxima['lifecycle']:.3f}倍。", '',
             '## 实现变化', '',
             '- 每层直接使用uN/wN，省去重复构建绝对路径目录。',
             '- 最新upper层的小型平坦目录最多检查257项；完全被更新层遮住的旧层退出当前挂载组合。'
             '历史文件和挂载引用继续保留，每个历史点保存自己的lower集合。目录或较大层沿用原叠层路径。',
             '- 提交只发布仍贡献当前状态的层，省去完全被覆盖的旧版本的反复发布。无快照会话直接发布当前upper。',
             '- 当前视图在30个历史点后最多使用3个lower，而历史点仍可独立读取。', '',
             '## 相同设置与验证', '',
             '新AgentTX和纯OverlayFS共享挂载、元数据、层压缩和文件发布原语。'
             '纯OverlayFS不写AgentTX快照记录，也不构造AgentTX的快照对象。'
             '旧版本AgentTX v7独立冻结，用来核对这次修改的收益。', '',
             '- 相同4 vCPU、12 GiB KVM guest、Ext4、固定Rust文件负载和4 KiB文件。',
             '- 相同进程内调用、uN/wN布局及index=off/metacopy=off/redirect_dir=off；普通挂载，未使用volatile。',
             '- 完成阶段包括seal、发布、close、卸载和状态目录销毁；所有系统的收尾均计入。',
             '- 每配置独立预热1次，测量10次，随机轮换顺序。均值包含全部样本，逐次数据及置信区间保留。',
             '- 实际操作后直接完成提交和清理，再校验结果；历史读取验证由独立预检执行。',
             '- 只测SnapshotSession快照/提交模式；不运行agent、模型或错误定位。', '',
             '25项正确性测试通过。338/338个session通过，包含290次正式计时、29次预热、'
             '3个不同历史内容预检和16个提交预检。', '',
             '## 快照接口，ms/次', '',
             '| 深度 | 旧AgentTX | 新AgentTX | 匹配OverlayFS | 新ATX/OverlayFS |',
             '|---:|---:|---:|---:|---:|']
    for row in rows[:3]:
        systems=row['systems']
        means=[systems[v]['snapshot_per_call']['mean_ms'] for v in ('agenttx-before','agenttx','overlay-compact-api')]
        ratio=row['ratios_to_overlayfs']['snapshot_per_call']['mean_ratio']
        lines.append(f"| {row['case']['depth']} | {means[0]:.3f} | {means[1]:.3f} | {means[2]:.3f} | {ratio:.3f}× |")
    lines += ['', '## 执行至完整收尾，ms/session', '',
              '包含文件操作、快照（如有）、seal、发布、卸载与暂存目录清理；初始化另列。', '',
              '| 负载 | 旧AgentTX | 新AgentTX | 匹配OverlayFS | 新ATX/OverlayFS |',
              '|---|---:|---:|---:|---:|']
    for row in rows:
        means=[row['systems'][v]['active_workflow']['mean_ms'] for v in ('agenttx-before','agenttx','overlay-compact-api')]
        ratio=row['ratios_to_overlayfs']['active_workflow']['mean_ratio']
        lines.append(f"| {label(row['case'])} | {means[0]:.2f} | {means[1]:.2f} | {means[2]:.2f} | {ratio:.3f}× |")
    lines += ['', '## 初始化及完整会话，ms/session', '',
              '完整会话将初始化也加回；overwrite/rename/unlink的初始化包括构造初始文件。', '',
              '| 负载 | ATX初始化 | Overlay初始化 | ATX完整会话 | Overlay完整会话 | 完整会话比值 |',
              '|---|---:|---:|---:|---:|---:|']
    for row in rows:
        a,b=row['systems']['agenttx'],row['systems']['overlay-compact-api']
        ratio=row['ratios_to_overlayfs']['lifecycle']['mean_ratio']
        lines.append(f"| {label(row['case'])} | {a['setup']['mean_ms']:.2f} | {b['setup']['mean_ms']:.2f} | "
                     f"{a['lifecycle']['mean_ms']:.2f} | {b['lifecycle']['mean_ms']:.2f} | {ratio:.3f}× |")
    lines += ['', '同一原语的部分均值低于1属于测量波动，不能解释为新增记录机制让底层文件系统更快。'
              '上述结论按每个配置分别计算均值，不将不同负载合并为一个平均数。', '',
              '原始数据、源码冻结版本、未采用的探索性诊断与测试日志在raw/。'
              'profile-flat-volatile.json属于同步语义不同的探索记录，未用于本轮实现或性能结论。'
              '原正式RQ5批次保持暂停，论文数据未替换。', '',
              '复算命令：', '', '```bash',
              'python3 experiments/scripts/analyze_rq5_overlay_increment.py \\',
              '  --source review/rq5-overlay-increment-20261115/raw \\',
              '  --output review/rq5-overlay-increment-20261115', '```', '']
    (output/'RESULTS.md').write_text('\n'.join(lines))
    print(json.dumps(dict(maximum_ratios_to_overlayfs=maxima,
                         all_measured_means_at_most_1_2=summary['all_measured_means_at_most_1_2']),indent=2))


if __name__ == '__main__':
    main()
