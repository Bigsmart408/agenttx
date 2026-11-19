#!/usr/bin/env python3
"""Validate and summarize the complete snapshot-only comparison without filtering."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import statistics


VARIANTS = ('agenttx', 'overlayfs', 'yolofs')
CASES = [dict(kind='commit', operation=operation, files=10000)
         for operation in ('create', 'overwrite', 'rename', 'unlink')]
CASES += [dict(kind='commit', operation='create', files=100000)]
CASES += [dict(kind='snapshot', depth=depth) for depth in (1, 10, 30)]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def key(case):
    return json.dumps(case, sort_keys=True)


def label(case):
    if case['kind'] == 'snapshot':
        return f"快照，深度 {case['depth']}"
    return f"{case['operation']} {case['files']:,} 文件"


def stats(samples):
    return dict(mean_ms=statistics.mean(samples),
                stdev_ms=statistics.stdev(samples), samples_ms=samples)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--version', default='v7')
    args = parser.parse_args()
    source, output = args.source, args.output
    result_dir = source/f'results-{args.version}'
    manifest = json.loads((result_dir/'manifest.json').read_text())
    environment = json.loads((source/f'environment-{args.version}.json').read_text())
    test_log = (source/f'guest-tests-{args.version}.log').read_text()
    test_result = re.search(r'(\d+) passed in ', test_log)
    if not test_result or re.search(r'\d+ (failed|error|skipped)', test_log):
        raise RuntimeError('Snapshot correctness tests did not all pass')
    passed_tests = int(test_result[1])
    progress = json.loads((result_dir/'progress.json').read_text())
    plan = manifest['plan']
    if progress != dict(state='complete', completed=159, total=159):
        raise RuntimeError(f'Incomplete run: {progress}')
    if manifest['agenttx_mode'] != 'snapshot-and-commit-only':
        raise RuntimeError('Unexpected runtime mode')
    files = sorted((result_dir/'sessions').glob('*.json'))
    if len(files) != len(plan) or len(plan) != 159:
        raise RuntimeError('Session count does not match the complete plan')
    records = [json.loads(path.read_text()) for path in files]
    groups, seals = defaultdict(list), defaultdict(list)
    phases = Counter()
    for item, record in zip(plan, records):
        for name, value in item.items():
            if record.get(name) != value:
                raise RuntimeError(f'Manifest mismatch: {item}, {name}')
        if record['status'] != 'passed' or record['run_pipeline_calls'] != 0:
            raise RuntimeError(f"Failed session or runtime call: {record['serial']}")
        if record.get('error') or record.get('cleanup_error'):
            raise RuntimeError(f"Unexpected error: {record['serial']}")
        phases[record['phase']] += 1
        case = record['case']
        snapshots = record['snapshots']
        if case['kind'] == 'snapshot':
            depth = case['depth']
            expected_indices = sorted({1, max(1, depth//2), depth})
            if [r['index'] for r in snapshots] != list(range(1, depth+1)):
                raise RuntimeError('Missing snapshot boundary')
            if [r['index'] for r in record['history_checks']] != expected_indices:
                raise RuntimeError('Missing historical validation')
            for check in record['history_checks']:
                if record['phase'] == 'history-canary':
                    if check['distinct_generation'] != check['index']:
                        raise RuntimeError('Historical content mismatch')
                elif (check['validated_files'], check['validated_bytes']) != (110, 450560):
                    raise RuntimeError('Historical file validation missing')
        else:
            if snapshots or record['history_checks']:
                raise RuntimeError('Unexpected snapshots in commit-only session')
        if record['snapshot_total_ns'] != sum(r['snapshot_ns'] for r in snapshots):
            raise RuntimeError('Snapshot timer mismatch')
        if any(r['snapshot_ns'] <= 0 for r in snapshots):
            raise RuntimeError('Invalid snapshot timing')
        if record['phase'] != 'history-canary':
            expected_files = (110 if case['kind'] == 'snapshot' else
                              0 if case['operation'] == 'unlink' else case['files'])
            for name in ('live_validation', 'commit_validation'):
                validation = record[name]
                if (validation['validated_files'], validation['validated_bytes']) != (
                        expected_files, expected_files*4096):
                    raise RuntimeError(f'Missing complete validation: {name}')
            if record['commit_ns'] <= 0:
                raise RuntimeError('Invalid commit timer')
        if record['phase'] == 'measured':
            timing = (record['commit_ns'] if case['kind'] == 'commit' else
                      record['snapshot_total_ns']/case['depth'])
            groups[key(case), record['variant']].append((record['repeat'], timing/1e6))
            if record['variant'] == 'agenttx':
                seals[key(case)].append(record['seal_ns']/1e6)
    expected_phases = dict(measured=120, warmup=24,
                           **{'history-canary': 3, 'commit-canary': 12})
    if dict(phases) != expected_phases:
        raise RuntimeError(f'Unexpected phases: {phases}')
    if manifest['script_sha256'] != digest(source/'bench_rq5_snapshot_only.py'):
        raise RuntimeError('Measured wrapper does not match archived wrapper')
    frozen = json.loads((source/f'candidate-{args.version}'/'manifest.json').read_text())
    if environment['runtime_sha256'] != frozen:
        raise RuntimeError('Measured runtime differs from frozen runtime')
    for name, expected in frozen.items():
        if digest(source/f'candidate-{args.version}'/name) != expected:
            raise RuntimeError(f'Frozen source hash mismatch: {name}')

    rows = []
    for case in CASES:
        systems = {}
        for variant in VARIANTS:
            values = sorted(groups[key(case), variant])
            if [repeat for repeat, _ in values] != list(range(5)):
                raise RuntimeError(f'Missing repeat: {case}, {variant}')
            systems[variant] = stats([value for _, value in values])
        ratios = {variant: systems['agenttx']['mean_ms']/systems[variant]['mean_ms']
                  for variant in VARIANTS[1:]}
        rows.append(dict(case=case, systems=systems, ratios=ratios,
                         at_most_1_2=all(value <= 1.2 for value in ratios.values()),
                         agenttx_seal_per_session=stats(seals[key(case)])))
    maxima = {variant: max(row['ratios'][variant] for row in rows)
              for variant in VARIANTS[1:]}
    summary = dict(version=args.version, mode=manifest['agenttx_mode'], rows=rows,
                   maximum_ratios=maxima, reported_stage_ratios_at_most_1_2=all(
                       row['at_most_1_2'] for row in rows),
                   comparison_valid_for_overhead_claim=False,
                   target_1_2_demonstrated=False,
                   note='调用方式与层释放策略不一致，且 seal 在计时外；这些原始阶段比值不支持额外开销或1.2倍达标结论。')
    validation = dict(sessions=159, phase_counts=dict(phases), passed=159, failed=0,
                      repeats_per_configuration=5, configurations=8, variants=list(VARIANTS),
                      all_plan_entries_matched=True, live_and_commit_states_checked=True,
                      distinct_historical_versions_checked=True, run_pipeline_calls=0,
                      frozen_runtime_hashes_checked=True, wrapper_hash_checked=True,
                      correctness_tests_passed=passed_tests,
                      formal_campaign_state=environment['formal_campaign'])
    output.mkdir(parents=True, exist_ok=True)
    write_json(output/'summary.json', summary)
    write_json(output/'validation.json', validation)
    hashes = {str(path.relative_to(source)): digest(path) for path in
              [result_dir/'manifest.json', result_dir/'progress.json', *files,
               source/f'environment-{args.version}.json',
               source/f'guest-tests-{args.version}.log',
               *(source/name for name in ('bench_rq5_snapshot_only.py',
                                        'bench_rq5_try_comparison.py',
                                        'bench_rq5_storage_primitives.py'))]}
    write_json(output/'measurement-sha256.json', hashes)

    lines = ['# RQ5 原分阶段测量：公平性核查后更正', '',
             '**原“8组均低于1.2倍”结论撤回。** AgentTX使用进程内调用和保留挂载引用，'
             '对照包含CLI启动和同步卸载，提交计时还排除了seal。'
             '相同数据、文件系统和正确性校验不能消除这些计时及实现差异。'
             '完整核查与统一口径补测见[公平性核查](../rq5-storage-fairness-20261115/RESULTS.md)。', '',
             f'下表保留{args.version}的原始阶段计时：最大原始比值为OverlayFS '
             f'{maxima["overlayfs"]:.3f}倍、YoloFS {maxima["yolofs"]:.3f}倍，'
             '仅用于复核原报告，不能解释为相对基础文件系统的额外开销。', '',
             '## 实现与测量范围', '',
             '`agenttx.snapshot.SnapshotSession` 是显式的快照/提交模式。'
             '不调用 agent、模型或错误定位，不构造因果依赖图、逐对象恢复索引或 agent 上下文。'
             '完整选择性恢复运行时的默认路径没有改为此模式。', '',
             '冻结旧视图为只读并保留挂载引用，切换新 upper，由 OverlayFS 进行写时复制；'
             '不再展开完整历史目录。提交普通文件直接 rename，删除路径使用可选原生批处理。'
             '批量删除在返回前全部完成。', '',
             '快照返回时旧版本不可变且可挂载，当前视图可继续写入。'
             '保留挂载引用将 superblock 的最终释放移到 seal；该阶段单列如下。'
             'SnapshotSession.commit() 直接调用会先执行 seal，不能将提交表中的数字解释为其包含 seal 的总耗时。', '',
             '## 公共设置', '',
             '- 同一 KVM guest：4 vCPU、12 GiB，Ext4 `/dev/vda1`，Linux 6.8.0-147-generic。',
             '- 相同 lower 数据、绝对路径 upper 布局、默认 OverlayFS 参数：index=N、redirect_dir=N、metacopy=N。',
             '- YoloFS `8848c243ba28ff91432c1da868671d6ed8910ed6`；perf-eval `bc32d0835ed8b9d28a799e59574f31855999a89a`。',
             '- 执行固定版本原始 Rust 工作负载；4 KiB 文件。十万文件组只改变数量参数。',
             '- 每配置独立预热一次、测量五次，固定随机轮换顺序；所有样本均保留。',
             '- 快照负载为 100 个目标文件和 10 个 seed 文件，每层覆盖 10 个 seed；深度 1、10、30。',
             '- 表中为外部单调时钟测得的 API 均值；对照的命令入口成本包含在内，AgentTX 使用进程内接口。',
             '- 准备、文件操作和校验不进入快照/提交计时。普通 buffered 状态，不增加 fsync 保证。',
             '- 延续既有比较的计时口径，OverlayFS 和 AgentTX 在提交计时前结束当前挂载；AgentTX seal 单列。',
             '- 原正式 RQ5 批次保持暂停；本结果不混入该批次，也未替换论文数据。', '',
             '## 按负载列出的结果', '',
             '快照单位为 ms/次，提交单位为 ms/批；比值越低越好。', '',
             '| 负载 | AgentTX | OverlayFS | YoloFS | ATX/OverlayFS | ATX/YoloFS |',
             '|---|---:|---:|---:|---:|---:|']
    for row in rows:
        means = [row['systems'][variant]['mean_ms'] for variant in VARIANTS]
        lines.append(f"| {label(row['case'])} | {means[0]:.3f} | {means[1]:.3f} | "
                     f"{means[2]:.3f} | {row['ratios']['overlayfs']:.3f}× | "
                     f"{row['ratios']['yolofs']:.3f}× |")
    lines += ['', '## seal：卸载当前视图并释放保留的只读挂载引用', '',
              '下列为 AgentTX 每个 session 的单独耗时，单位 ms。'
              '它包含卸载和引用释放，不是额外的逐文件回滚工作；不计入上表。'
              '当前对照未单独记录同一阶段，所以不据此声称完整生命周期也达到 1.2 倍。', '',
              '| 负载 | seal 均值 | 标准差 |', '|---|---:|---:|']
    for row in rows:
        seal = row['agenttx_seal_per_session']
        lines.append(f"| {label(row['case'])} | {seal['mean_ms']:.3f} | {seal['stdev_ms']:.3f} |")
    lines += ['', '## 验证与复现', '',
              f'最终版本 {passed_tests} 项测试通过；159/159 个 session 通过，包含 120 次正式测量、'
              '24 次预热和 15 次预检。每次测量完整校验当前文件与提交后的路径、内容、模式；'
              '快照检查首个、中间和最近历史点，独立预检检查不同版本内容互不污染。', '',
              '`summary.json` 保留逐次样本、均值和标准差；`validation.json` 记录矩阵核验，'
              '`measurement-sha256.json` 固定原始数据。`raw/` 保留全部试验版本、失败测试、'
              '源码清单、运行日志及诊断；旧版本结果不混入最终统计。', '',
              '复算命令：', '', '```bash',
              'python3 experiments/scripts/analyze_rq5_snapshot_only.py \\',
              f'  --source review/rq5-snapshot-only-20261115/raw --version {args.version} \\',
              '  --output review/rq5-snapshot-only-20261115', '```', '']
    (output/'RESULTS.md').write_text('\n'.join(lines))
    print(json.dumps(dict(maximum_reported_stage_ratios=maxima,
                         target_1_2_demonstrated=False,
                         sessions=159), indent=2))


if __name__ == '__main__':
    main()
