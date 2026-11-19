#!/usr/bin/env python3
"""Audit the three-benchmark component campaign and retain a readable report."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from experiments.scripts.analyze_rq5_components import analyze
from experiments.scripts.experiment_provenance import source_manifest
from experiments.scripts.run_recovery_cross_ablation import read, save, sha


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort',type=Path,required=True)
    parser.add_argument('--coordinator-unit')
    args=parser.parse_args()
    cohort=args.cohort
    reg=read(cohort/'registration.json')
    source=Path(reg['frozen_source'])
    while True:
        try:
            assert source_manifest(source)['sha256']==reg['source_sha256']
            for item in read(cohort/'auxiliary-manifest.json')['files']:
                assert sha(source/item['path'])==item['sha256']
            analysis=analyze(cohort)
            save(cohort/'analysis.json',analysis)
            progress=read(cohort/'progress.json') if (cohort/'progress.json').exists() else {}
            service_state=None
            if args.coordinator_unit:
                service_state=subprocess.run(['systemctl','--user','is-active',args.coordinator_unit],
                    capture_output=True,text=True,timeout=10).stdout.strip()
            reason=progress.get('stop_reason')
            if service_state in ('failed','inactive','unknown') and not (cohort/'execution.json').exists():
                reason='coordinator_service_'+service_state
            state='attention_required' if reason else 'running'
            if (cohort/'execution.json').exists() and not progress.get('active'):
                state='finished' if not progress.get('stop_reason') else 'attention_required'
            status=dict(checked_at=datetime.now(timezone.utc).isoformat(),state=state,
                        counts=analysis['counts'],active=len(progress.get('active',[])),
                        workers=reg['workers'],stop_reason=reason,coordinator_service=service_state,
                        complete_triads={k:v['complete_triads'] for k,v in analysis['groups'].items()},
                        evidence_issues=analysis['issues'])
            status['infrastructure_exceptions']=analysis['counts'].get('infrastructure_error',0)
            save(cohort/'monitor/latest.json',status)
            with (cohort/'monitor/history.jsonl').open('a') as stream:
                stream.write(json.dumps(status,ensure_ascii=False)+'\n')
            if state=='attention_required':
                save(cohort/'monitor/attention.json',status)
            lines=['# RQ5 两部分回滚的收益', '',
                   f"更新时间：{datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')}（北京时间）；状态：{state}；运行 {status['active']}/8。", '',
                   f"已完成 {analysis['counts'].get('completed',0):,}/{analysis['registered_cells']:,}；基础设施异常 {status['infrastructure_exceptions']}。", '',
                   '三组：仅 FS 回滚、仅 agent 上下文回滚、同步回滚。',
                   '任务、故障、模型预算及 checkpoint 数量与设置沿用原实验；不合并旧定位协议结果。', '',
                   '| Benchmark / agent | 已记录 | 完整三组 | 增加 FS 的节约 token | 增加 agent 回滚的节约 token |',
                   '|---|---:|---:|---:|---:|']
            for group,value in analysis['groups'].items():
                fs=value['fs_benefit_given_agent_rollback']
                context=value['agent_benefit_given_fs_rollback']
                def display(item):
                    return f"{item['saved_tokens']} (n={item['pairs']})" if item['pairs'] else '待匹配'
                lines.append(f"| {group} | {sum(v['recorded'] for v in value['variants'].values())} | "
                             f"{value['complete_triads']} | {display(fs)} | {display(context)} |")
            lines+=['', '费用仅比较双方成功且 API 用量完整的任务；失败和未知用量原样保留。',
                    '两项是增加另一组件的条件收益，不计算两项相加的贡献比例。', '',
                    '停止原因：'+str(status['stop_reason'] or '无')]
            (cohort/'REPORT.md').write_text('\n'.join(lines)+'\n')
            print(json.dumps(status),flush=True)
            if state=='finished':
                return
        except Exception as exc:
            save(cohort/'monitor/latest.json',dict(state='audit_error',error=f'{type(exc).__name__}: {exc}'))
            print(type(exc).__name__+': '+str(exc),flush=True)
        time.sleep(60)


if __name__=='__main__':
    main()
