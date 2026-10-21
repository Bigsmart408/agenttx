#!/usr/bin/env python3
"""Read-only progress and paired historical comparisons; never retry experiments."""
from collections import Counter
from datetime import datetime, timezone
import csv
import fcntl
import hashlib
import json
from pathlib import Path
import statistics
import time
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[2]
DATA=Path('/mnt/data/agenttx-runtime')
RUN=DATA/'results/sweagent-swe-local-monitored-20261018-full-r1'
BASELINE=DATA/'results/sweagent_luna_max_swe_fair_20261012'
AUDIT=Path('/home/bfq/agenttx-recovery-20261016/reports/official_cell_status.csv')


def read(path):
    return json.loads(path.read_text()) if path.exists() else {}


def number(value):
    try:return float(value)
    except (TypeError,ValueError):return 0.0


def combined_tokens(row):
    return number(row.get('total_tokens'))+number(row.get('doc_replay_tokens'))


def campaign_root():
    pointer=read(DATA/'validation/active-swe-full-cohort.json')
    name=pointer.get('cohort',RUN.name)
    assert Path(name).name==name and name.startswith('sweagent-swe-local-monitored-')
    return DATA/'results'/name


def controls_comparison(progress, results, registration=None):
    registration=registration or {}
    contexts=registration.get('context_by_policy',{})
    def context_matches(causal,control,policy):
        if contexts:
            return (causal.get('recovery_context_variant')==contexts.get('causal')
                    and control.get('recovery_context_variant')==contexts.get(policy))
        return causal.get('recovery_context_variant')==control.get('recovery_context_variant')
    valid={(r['task'],r['policy']) for r in progress.get('tasks',[]) if r['state']=='completed'}
    causal={task:results[(task,policy)] for task,policy in valid if policy=='causal' and (task,policy) in results}
    report={}
    for policy in ('clean_recovery','crash_no_rollback','temporal_checkpoint','whole_branch_abort','ideal_oracle'):
        pairs=[(task,row,results[(task,policy)]) for task,row in causal.items()
               if (task,policy) in valid and (task,policy) in results
               and context_matches(row,results[(task,policy)],policy)]
        both=[(task,c,b) for task,c,b in pairs if c.get('success') is True and b.get('success') is True
              and combined_tokens(c)>0 and combined_tokens(b)>0]
        ct=sum(combined_tokens(c) for _,c,b in both);bt=sum(combined_tokens(b) for _,c,b in both)
        report[policy]=dict(registered_causal_context=contexts.get('causal',registration.get('context_variant')),
            registered_control_context=contexts.get(policy,registration.get('context_variant')),
            context_profile=registration.get('context_profile','legacy'),
            paired_finished=len(pairs),causal_successes=sum(c.get('success') is True for _,c,b in pairs),
            control_successes=sum(b.get('success') is True for _,c,b in pairs),both_success_pairs=len(both),
            causal_tokens=ct,control_tokens=bt,token_reduction_pct=100*(1-ct/bt) if bt else None,
            median_pair_ratio=statistics.median(combined_tokens(c)/combined_tokens(b) for _,c,b in both) if both else None,
            causal_cheaper_tasks=sum(combined_tokens(c)<combined_tokens(b) for _,c,b in both),
            source_cohorts=sorted({c.get('cohort','unknown') for _,c,b in pairs}|{b.get('cohort','unknown') for _,c,b in pairs}))
    return report


def compare(progress, results, baseline, statuses):
    by_policy={}
    for policy in sorted({r['policy'] for r in progress.get('tasks',[])}):
        attempts=[r for r in progress['tasks'] if r['policy']==policy and r['state'] in ('completed','infrastructure_error')]
        valid=[r for r in attempts if r['state']=='completed']
        pairs=[];costs=[];all_known=[]
        for attempt in attempts:
            key=(attempt['task'],policy)
            if key in statuses:all_known.append(dict(task=key[0],old_status=statuses[key],new_state=attempt['state'],new_success=attempt.get('success')))
        for attempt in valid:
            key=(attempt['task'],policy);status=statuses.get(key)
            if status not in ('completed_success','completed_task_failure'):continue
            new=results.get(key,{})
            pairs.append((attempt.get('success') is True,status=='completed_success'))
            old=baseline.get(key,{})
            if pairs[-1]==(True,True) and combined_tokens(new)>0 and combined_tokens(old)>0:
                costs.append((combined_tokens(new),combined_tokens(old)))
        new_success=sum(a for a,b in pairs);old_success=sum(b for a,b in pairs)
        by_policy[policy]=dict(attempted=len(attempts),valid_completed=len(valid),
            successful=sum(r.get('success') is True for r in valid),infrastructure_errors=len(attempts)-len(valid),
            paired_valid_cells=len(pairs),paired_new_successes=new_success,paired_old_successes=old_success,
            paired_success_rate_delta_pp=round(100*(new_success-old_success)/len(pairs),2) if pairs else None,
            paired_both_success_with_usage=len(costs),
            paired_success_token_ratio=sum(a for a,b in costs)/sum(b for a,b in costs) if costs else None,
            paired_success_token_ratio_median=statistics.median(a/b for a,b in costs) if costs else None,
            historical_status_for_all_new_attempts=all_known)
    return by_policy


def main():
    lock=(DATA/'validation/swe-full-comparison.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    statuses={(r['task'],r['mode']):r['status'] for r in csv.DictReader(AUDIT.open())
              if r['run']==BASELINE.name and r['suite']=='swe'}
    raw=BASELINE/'merged/official_tasks.json'
    baseline={(r['task'],r['mode']):r for r in read(raw)['rows'] if int(r.get('repeat',0))==0}
    provenance=dict(audit=str(AUDIT),audit_sha256=hashlib.sha256(AUDIT.read_bytes()).hexdigest(),
                    raw=str(raw),raw_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
                    historical_scope_status_counts=dict(Counter(statuses.values())),historical_raw_cells=len(baseline))
    while True:
        try:
            run=campaign_root()
            progress=read(run/'progress.json');registration=read(run/'registration.json')
            raw_counts=progress.get('counts',{})
            environment_audit=read(run/'environment-audit.json')
            invalid=set(environment_audit.get('environment_invalid_tasks',[]))
            for row in progress.get('tasks',[]):
                if row['task'] in invalid and row['state']=='completed':row['state']='infrastructure_error'
            progress['counts']=dict(Counter(r['state'] for r in progress.get('tasks',[])))
            results={}
            for row in progress.get('tasks',[]):
                if row['state'] in ('completed','infrastructure_error'):
                    path=Path(row['result_path']) if row.get('inherited_from') else run/'cells'/row['task']/row['policy']/'result.json'
                    results[(row['task'],row['policy'])]=read(path)
            comparisons=compare(progress,results,baseline,statuses)
            controls=controls_comparison(progress,results,registration)
            tb_repair=read(DATA/'validation/tb-stage-repair-20261018-r1/progress.json')
            tb_ready=218+int(tb_repair.get('state')=='image_ready')
            stamp=datetime.now(timezone.utc).isoformat()
            actual_contexts=dict(Counter(r.get('recovery_context_variant','unknown') for r in results.values()))
            snapshot=dict(checked_at=stamp,cohort=run.name,scope=registration.get('cell_count',1500),counts=progress.get('counts',{}),raw_counts=raw_counts,
                registered_context=registration.get('context_variant'),actual_result_contexts=actual_contexts,
                context_by_policy=registration.get('context_by_policy',{}),context_profile=registration.get('context_profile','legacy'),
                comparison_estimand=registration.get('comparison_estimand'),
                active=progress.get('active',[]),stop_reason=progress.get('stop_reason'),
                finished=(run/'execution.json').exists(),formal_eligible=False,
                environment_audit=environment_audit,within_cohort_controls=controls,
                token_definition='SWE-agent model_stats text estimates: total_tokens + doc_replay_tokens; API reasoning is not fully counted. See separate logged API usage audit.',
                finalized_combined_tokens=sum(combined_tokens(r) for r in results.values()),
                tb_images_ready_unique=tb_ready,tb_unresolved=241-tb_ready,
                baseline_provenance=provenance,by_policy=comparisons,
                protocol_differences=['new 900s/session vs historical 1800s/session',
                                      'new fault seed 20261018 vs historical 20260930',
                                      'repaired local runtime and changed code; historical audit cohort'],
                limitations='Paired historical diagnostics, not a controlled equivalence claim. Task failures and infrastructure errors remain visible; token ratios use paired successful cells only.')
            dest=run/'historical-comparison.json';tmp=dest.with_suffix('.tmp');tmp.write_text(json.dumps(snapshot,indent=2)+'\n');tmp.replace(dest)
            with (run/'historical-comparison-history.jsonl').open('a') as stream:stream.write(json.dumps(snapshot)+'\n')
            local=datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
            lines=['# SWE-agent 全量与历史对照','',f'更新时间：{local}（北京时间）；每 60 秒刷新。','',
                f"本机 r760-ssd，{registration.get('task_count',300)} 题 × {len(registration.get('policies',[]))} 策略 = {snapshot['scope']} 单元，最多 8 路并行。代码冻结于系统盘；运行数据在 /mnt/data/agenttx-runtime。",'',
                f"当前批次：`{run.name}`；审计后状态：`{progress.get('counts',{})}`（原始：`{raw_counts}`）；停止原因：`{progress.get('stop_reason')}`；已结束：{snapshot['finished']}。",'',
                f"实际结果上下文：`{actual_contexts}`；逐策略登记：`{registration.get('context_by_policy',registration.get('context_variant'))}`。selective-compact-v1 比较 Causal+compact 与 full 对照的整体效果。r1/r2 漏传环境变量导致登记 compact、实际 full；依据实际结果解释。clean 是无故障参照上限，ideal 是精确恢复参照；不得把小样本总 token 更低解读为超过上限。",'',
                '此监控的 token 是 SWE-agent model_stats 文本估算：主会话 total_tokens + 单列 doc_replay_tokens，不能视为完整 API 用量，推理 token 未完整计入。完整日志用量另用 audit_swe_token_usage.py 核对；见 HARNESS_TB_DIFFERENCE_ANALYSIS_20261018.md。环境无效单元单列；续跑单元注明来源批次，原始记录保持不变。','',
                '## Causal 与本批对照组','',
                '| 对照组 | 有效结束配对 | Causal / 对照成功数 | 双方成功配对 | Causal token 减少 | Causal 更省的题数 |',
                '| --- | ---: | ---: | ---: | ---: | ---: |']
            for policy,r in controls.items():
                saving='—' if r['token_reduction_pct'] is None else f"{r['token_reduction_pct']:.1f}%"
                lines.append(f"| {policy} | {r['paired_finished']} | {r['causal_successes']} / {r['control_successes']} | {r['both_success_pairs']} | {saving} | {r['causal_cheaper_tasks']} |")
            lines+=['','同题、同策略条件及相同崩溃种子配对。成本只比较双方成功的单元，成功率同时列出；逐题倍率中位数保存在 JSON。样本量小时不宣称统计显著；跨修复批次的来源在 JSON 中保留。','',
                '## 历史诊断对照','',
                '| 策略 | 已尝试 | 有效结束 | 成功 | 环境错误 | 历史有效配对数 | 成功率差（百分点） | 双方成功 token 比（新/旧） |',
                '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
            for policy,r in comparisons.items():
                delta='—' if r['paired_success_rate_delta_pp'] is None else str(r['paired_success_rate_delta_pp'])
                ratio='—' if r['paired_success_token_ratio'] is None else f"{r['paired_success_token_ratio']:.3f} (n={r['paired_both_success_with_usage']})"
                lines.append(f"| {policy} | {r['attempted']} | {r['valid_completed']} | {r['successful']} | {r['infrastructure_errors']} | {r['paired_valid_cells']} | {delta} | {ratio} |")
            lines+=['','历史总体状态：`'+json.dumps(provenance['historical_scope_status_counts'],ensure_ascii=False)+'`。历史原始指标可用 '+str(len(baseline))+' 单元；缺少原始指标的任务仍保留其审计状态。','',
                '配对键为相同任务、相同策略、repeat=0。成功率配对包含双方有效的成功及任务失败；token 比仅统计双方成功且都有用量的配对。环境错误单列，不把未运行计为任务失败。','',
                '本批沿用已验证的 900 秒会话时限和种子 20261018；历史批次为 1800 秒和种子 20260930，代码与环境也不同。因此差值用于历史诊断，不能直接证明严格复现或策略因果优势。硬 token 预算、策略 payload 配平与双框架统一验证尚待完成，formal_eligible=false；全量运行与最终论文合规批次分开登记。','',
                '遇基础设施错误停止添加新单元、保留运行中的任务和原始证据；不按任务成败自动重跑。','',
                f'TB：首轮已正常结束，计入独立修复后去重共 {tb_ready}/241，尚余 {241-tb_ready} 项环境错误。','',
                '执行登记：`'+str(run/'registration.json')+'`。完整对照：`'+str(dest)+'`。','']
            (ROOT/'docs/SWE_FULL_COMPARISON_20261018.md').write_text('\n'.join(lines))
            print(json.dumps(dict(checked_at=stamp,counts=snapshot['counts'],paired=sum(r['paired_valid_cells'] for r in comparisons.values()),stop_reason=snapshot['stop_reason'])),flush=True)
            pending=read(DATA/'validation/swe-full-continuation-pending.json')
            if snapshot['finished'] and pending.get('previous_cohort')!=run.name:break
        except Exception as exc:print(type(exc).__name__+': '+str(exc),flush=True)
        time.sleep(60)


if __name__=='__main__':main()
