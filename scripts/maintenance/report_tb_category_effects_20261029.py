"""Read-only category breakdown of compatible, complete cumulative token pairs."""
import collections
import csv
import datetime
import hashlib
import json
from pathlib import Path

import yaml

D = Path('/mnt/data/agenttx-runtime')
POLICIES = ('crash_no_rollback', 'temporal_checkpoint', 'whole_branch_abort',
            'clean_recovery', 'ideal_oracle')
LABELS = dict(crash_no_rollback='不回滚', temporal_checkpoint='Checkpoint',
              whole_branch_abort='整分支', clean_recovery='Clean', ideal_oracle='Ideal')
GROUPS = {
    '软件开发与调试': ('software-engineering', 'debugging', 'reproducible-builds'),
    '系统运维与文件': ('system-administration', 'file-operations', 'file_operations', 'file-system'),
    '安全与协议': ('security', 'protocol-analysis'),
    '数据处理与查询': ('data-science', 'data-processing', 'data-querying'),
    '数学算法与科学计算': ('scientific-computing', 'mathematics', 'math', 'algorithms', 'optimization'),
    '机器学习与训练': ('machine-learning', 'model-training', 'computer-vision'),
    '游戏': ('games', 'game'),
    '多媒体、助理与研究': ('audio-processing', 'video-processing', 'personal-assistant', 'research'),
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def summarize(rows):
    a = sum(r['causal_tokens'] for r in rows)
    b = sum(r['control_tokens'] for r in rows)
    return dict(pairs=len(rows), causal_tokens=a, control_tokens=b,
                saved_tokens=b-a, saving_pct=100*(1-a/b) if b else None)


def main():
    pointer = json.loads((D/'validation/active-tb-dual-native.json').read_text())
    root = Path(pointer['root'])
    report_bytes = (root/'retry-report.json').read_bytes()
    progress_bytes = (root/'progress.json').read_bytes()
    report, progress = json.loads(report_bytes), json.loads(progress_bytes)
    now = datetime.datetime.now(datetime.timezone.utc)
    audit = D/'validation/tb-category-effects-20261029'/now.strftime('%Y%m%dT%H%M%SZ')
    audit.mkdir(parents=True)
    (audit/'source-retry-report.json').write_bytes(report_bytes)
    (audit/'source-progress.json').write_bytes(progress_bytes)
    metadata_root = D/'cache/terminal_bench/terminal-bench-1/original-tasks'
    inverse = {category:group for group, categories in GROUPS.items() for category in categories}
    task_names = sorted({r['task'] for r in progress['tasks']})
    metadata = {}
    for task in task_names:
        path = metadata_root/task/'task.yaml'
        content = path.read_bytes()
        category = yaml.safe_load(content)['category']
        assert category in inverse, (task, category)
        metadata[task] = dict(task=task, original_category=category,
                              category_group=inverse[category], metadata_path=str(path),
                              metadata_sha256=digest(content))
    histories = collections.defaultdict(list)
    for r in progress['tasks']+progress['retry_tasks']:
        if r['state'] not in ('completed', 'infrastructure_error'):
            continue
        if r.get('finished_at', '') > report['checked_at']:
            continue
        path = Path(r['result_path'])
        content = path.read_bytes()
        result = json.loads(content)
        histories[(r['task'], r['harness'], r['policy'])].append(dict(
            result_path=str(path), result_sha256=digest(content),
            usage=result.get('api_usage'), success=result.get('success'),
            official_success=result.get('official_success')))
    pairs = []
    seen = set()
    for cid, group in report['cumulative_configuration_groups'].items():
        for harness, policies in group['cumulative_paired_success'].items():
            for policy, expected in policies.items():
                subset = []
                for task in expected['tasks']:
                    identity = (task, harness, policy)
                    assert identity not in seen
                    seen.add(identity)
                    sides = {}
                    for side in ('causal', policy):
                        attempts = histories[(task, harness, side)]
                        assert attempts and all(a['usage'] and a['usage'].get('complete') for a in attempts)
                        assert attempts[-1]['success'] is True
                        paths = [a['result_path'] for a in attempts]
                        assert len(paths) == len(set(paths)), identity
                        sides[side] = dict(tokens=sum(a['usage']['total_tokens'] for a in attempts),
                                           attempts=attempts)
                    row = dict(**metadata[task], harness=harness, control=policy,
                               configuration_id=cid, configuration=group['configuration'],
                               causal_tokens=sides['causal']['tokens'],
                               control_tokens=sides[policy]['tokens'], sides=sides)
                    row['saving_pct'] = 100*(1-row['causal_tokens']/row['control_tokens'])
                    subset.append(row)
                total = summarize(subset)
                assert total['pairs'] == expected['both_success_pairs']
                assert total['causal_tokens'] == expected['causal_cumulative_tokens'], (harness, policy, cid)
                assert total['control_tokens'] == expected['control_cumulative_tokens'], (harness, policy, cid)
                pairs.extend(subset)
    aggregate = {}
    categories = {}
    original_categories = {}
    for harness in ('sweagent', 'codex'):
        aggregate[harness] = {policy:summarize([r for r in pairs if r['harness']==harness and r['control']==policy])
                              for policy in POLICIES}
        categories[harness] = {
            category:{policy:summarize([r for r in pairs if r['harness']==harness
                                       and r['control']==policy and r['category_group']==category])
                      for policy in POLICIES} for category in GROUPS}
        original_categories[harness] = {
            category:{policy:summarize([r for r in pairs if r['harness']==harness
                                       and r['control']==policy and r['original_category']==category])
                      for policy in POLICIES} for category in sorted(inverse)}
    counts = collections.Counter(r['category_group'] for r in metadata.values())
    result = dict(at=now.isoformat(), report_at=report['checked_at'], cohort=pointer['cohort'],
                  progress_updated_at=progress['updated_at'], counts=progress['counts'],
                  retry_counts=progress['retry_counts'], active_workers=len(progress['active']),
                  aggregate=aggregate, categories=categories, original_categories=original_categories,
                  taxonomy=GROUPS, benchmark_tasks_by_category=dict(counts),
                  task_classification=list(metadata.values()), pairs=pairs,
                  source_report_sha256=digest(report_bytes), source_progress_sha256=digest(progress_bytes),
                  known_cumulative_tokens=report['known_cumulative_tokens'],
                  attempts_with_incomplete_usage=report['attempts_with_incomplete_usage'],
                  exclusions=report['cumulative_pair_exclusions'],
                  definition='Sum all recorded terminal main+replay+failed+retry costs per cell. '
                  'Use the live report eligible pair list, requiring both full success, complete accounting, '
                  'and a single compatible configuration throughout each paired cell. '
                  'Aggregate disjoint pairs across configuration strata; do not pair across incompatible strata. '
                  'Positive means Causal uses fewer tokens. This is a ratio of token sums, not a mean of case percentages.',
                  protocol_validation_only=True, formal_eligible=False)
    (audit/'effects.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    with (audit/'task-classification.csv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(next(iter(metadata.values()))))
        writer.writeheader()
        writer.writerows(metadata.values())
    with (audit/'paired-task-costs.csv').open('w') as stream:
        fields = ['task','harness','control','original_category','category_group','configuration_id',
                  'causal_tokens','control_tokens','saving_pct']
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(pairs)
    def cell(v):
        return f"{v['saving_pct']:+.1f}%（{v['pairs']}）" if v['pairs'] else '—（0）'
    lines = [f"TB 分类优势快照：{report['checked_at']}（UTC）", '',
             '正值为 Causal 节省；括号为配对题数。包含 main、replay、失败及重试费用。',
             '只比较同题、兼容配置、双方完整成功、历次费用齐全的单元。总体按兼容配置分层后汇总。', '',
             '| 对照 | SWE-agent | Codex |','|---|---:|---:|']
    lines.extend(f"| {LABELS[p]} | {cell(aggregate['sweagent'][p])} | {cell(aggregate['codex'][p])} |" for p in POLICIES)
    for harness in ('sweagent','codex'):
        lines += ['', harness, '', '| 类别（全部题数） | 不回滚 | Checkpoint | 整分支 | Clean | Ideal |',
                  '|---|---:|---:|---:|---:|---:|']
        for category in GROUPS:
            lines.append(f"| {category}（{counts[category]}） | "+' | '.join(cell(categories[harness][category][p]) for p in POLICIES)+' |')
    lines += ['', f"历史费用不全尝试 {report['attempts_with_incomplete_usage']} 次，未填零，相关单元不进入完整费用配对。",
              'C4 等混合环境合同的单元暂不进入兼容费用配对，其历史支出仍保留。',
              '分类按原始 task.yaml 合并，非按结果选类；精细原类别结果见 effects.json。',
              '当前为协议验证结果，正式实验资格 formal_eligible=false。']
    (audit/'report.md').write_text('\n'.join(lines)+'\n')
    (audit.parent/'latest.json').write_text(json.dumps(dict(root=str(audit),at=now.isoformat()),indent=2)+'\n')
    print(json.dumps(dict(root=str(audit),aggregate=aggregate,categories=categories,
                         benchmark_tasks_by_category=dict(counts)),ensure_ascii=False))


if __name__ == '__main__':
    main()
