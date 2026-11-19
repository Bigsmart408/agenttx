#!/usr/bin/env python3
"""Persistent Codex controlled-campaign monitor with five-minute reports."""
from pathlib import Path
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('AGENTTX_DOCKER_HOST', 'unix:///run/agenttx-home-docker/docker.sock')
from scripts.maintenance import monitor_system_microbenchmark_20261030 as base

original_report = base.write_report
original_sample = base.sample


def sample(args, cache):
    row = original_sample(args, cache)
    commits = [json.loads(p.read_text()) for p in (args.cohort / 'recovery').glob('*/commit.json')]
    row['archived_infrastructure_attempts'] = sum(len(c['attempts']) for c in commits)
    row['archived_infrastructure_known_tokens'] = sum(c['known_api_tokens'] for c in commits)
    row['archived_infrastructure_unknown_requests'] = sum(c['unknown_usage_requests'] for c in commits)
    supervisor = args.cohort / 'recovery-supervisor.json'
    if supervisor.exists():
        row['recovery_supervisor'] = json.loads(supervisor.read_text())
        if row['recovery_supervisor'].get('state') == 'review_required':
            row['alerts'].append('infrastructure_recovery_review_required')
    return row


def write_report(output, row):
    original_report(output, row)
    path = output / 'latest-report.md'
    text = path.read_text().replace('# 补充对照与上下文消融进展', '# Codex 6,000 次受控实验进展')
    text = text.replace('三组对照有效完成', '五种策略已结束')
    text = '\n'.join(line for line in text.splitlines()
                     if not line.startswith('按最近一小时速度估算剩余')) + '\n'
    counts = row.get('counts', {})
    gates = counts.get('runtime_gate', {})
    canaries = counts.get('model_canary', {})
    text += (f"\n运行时预检：通过 {gates.get('passed', 0)}/{sum(gates.values())}；"
             f"模型试跑：通过 {canaries.get('passed', 0)}，任务失败 {canaries.get('task_failed', 0)}，"
             f"基础设施异常 {canaries.get('infrastructure_error', 0)}，"
             f"运行中 {canaries.get('running', 0)}，待运行 {canaries.get('pending', 0)}。\n")
    text += (f"\n已结束主实验的已知 API 用量：{row.get('known_finished_main_tokens', 0):,} tokens；"
             f"用量完整：{row.get('complete_usage_main', 0)}/{row.get('completed_main_audited', 0)}。\n")
    if row.get('archived_infrastructure_attempts'):
        text += (f"\n另保留断流尝试 {row['archived_infrastructure_attempts']} 次："
                 f"已知用量 {row['archived_infrastructure_known_tokens']:,} tokens，"
                 f"{row['archived_infrastructure_unknown_requests']} 个请求用量未知。"
                 "这些尝试独立归档，不计入已完成单元。\n")
    temp = path.with_suffix('.tmp')
    temp.write_text(text)
    temp.replace(path)


if __name__ == '__main__':
    base.sample = sample
    base.write_report = write_report
    base.main()
